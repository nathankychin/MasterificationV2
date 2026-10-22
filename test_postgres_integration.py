import json
import os
import threading
import unittest
import uuid
from datetime import timedelta
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

import database
import main
from ai_provider import AIProviderError, AIService


TEST_DATABASE_URL = os.environ.get("MASTERIFY_TEST_DATABASE_URL", "").strip()


class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TEST_DATABASE_URL:
            raise unittest.SkipTest("Set MASTERIFY_TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.")
        application_url = os.environ.get("DATABASE_URL", "").strip()
        if application_url:
            app_info = conninfo_to_dict(application_url)
            test_info = conninfo_to_dict(TEST_DATABASE_URL)
            app_host = app_info.get("host", "").replace("-pooler", "").lower()
            test_host = test_info.get("host", "").replace("-pooler", "").lower()
            same_database = (
                app_host == test_host
                and app_info.get("dbname", "") == test_info.get("dbname", "")
            )
            if application_url == TEST_DATABASE_URL or same_database:
                raise unittest.SkipTest("Refusing to run PostgreSQL tests against the configured application database; use a separate test database.")

        cls.schema = "masterify_test_" + uuid.uuid4().hex
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin_connection:
            admin_connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.scoped_url = make_conninfo(TEST_DATABASE_URL, options=f"-c search_path={cls.schema}")
        cls.database_env = patch.dict(os.environ, {
            "DATABASE_URL": cls.scoped_url,
            "RENDER": "false",
            "RENDER_SERVICE_ID": "",
        })
        cls.database_env.start()
        cls.original_ai_service = main.ai_service
        main.ai_service = AIService(_FakeProvider())
        try:
            main.initialize_db()
            cls.server = main.ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
            cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.server_thread.start()
            cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"
        except Exception:
            main.ai_service = cls.original_ai_service
            cls.database_env.stop()
            with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin_connection:
                admin_connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))
            raise

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=3)
        main.ai_service = cls.original_ai_service
        cls.database_env.stop()
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as admin_connection:
            admin_connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))
        with database.connect_db() as db:
            db.execute("TRUNCATE practices, skills, settings, sessions, users RESTART IDENTITY CASCADE")

    def request(self, path, body=None, headers=None, client=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        opener = client or self.client
        with opener.open(request, timeout=12) as response:
            return response.status, json.loads(response.read()), dict(response.headers)

    def assert_http_error(self, path, expected_status, body=None, headers=None, client=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        with self.assertRaises(HTTPError) as captured:
            (client or self.client).open(request, timeout=12)
        self.assertEqual(captured.exception.code, expected_status)
        captured.exception.close()

    def register(self, email="person@example.test", remember=False):
        return self.request("/api/register", {"email": email, "password": "PostgresTestPassword-123", "remember": remember})

    def create_skill(self, name="Spanish conversation"):
        return self.request("/api/skills", {
            "name": name,
            "category": "Language",
            "language_code": "es-ES",
            "marking_criteria": "ask a follow-up question",
        })

    def create_practice(self, skill_id, submission_id="pg-test-submission-id-0001"):
        return self.request("/api/practices", {
            "skill_id": skill_id,
            "scenario": "Confirm a changed train departure in Spanish.",
            "response": "I would ask a follow-up question and confirm the new time.",
            "submission_id": submission_id,
            "elapsed_seconds": 45,
        })

    def test_registration_duplicate_email_login_logout_and_remember(self):
        status, _, headers = self.register("Person@Example.test", remember=False)
        self.assertEqual(status, 201)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("Max-Age=86400", headers["Set-Cookie"])
        self.assert_http_error("/api/register", 409, {
            "email": "person@example.test", "password": "PostgresTestPassword-123", "remember": False,
        })

        other_client = build_opener(HTTPCookieProcessor(CookieJar()))
        status, _, headers = self.request("/api/login", {
            "email": "  PERSON@example.test ", "password": "PostgresTestPassword-123", "remember": True,
        }, client=other_client)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=2592000", headers["Set-Cookie"])
        self.assert_http_error("/api/login", 401, {
            "email": "person@example.test", "password": "incorrect-password", "remember": False,
        }, client=build_opener(HTTPCookieProcessor(CookieJar())))

        self.request("/api/logout", {}, client=other_client)
        self.assert_http_error("/api/dashboard", 401, client=other_client)

    def test_session_expiry_is_enforced(self):
        self.register("expiry@example.test")
        with database.connect_db() as db:
            db.execute("UPDATE sessions SET expires_at=? WHERE user_id=(SELECT id FROM users WHERE email=?)", (main.now_iso() - timedelta(seconds=1), "expiry@example.test"))
        self.assert_http_error("/api/dashboard", 401)

    def test_skills_practices_history_readiness_and_user_isolation(self):
        self.register("owner@example.test")
        _, skill, _ = self.create_skill()
        skill_id = skill["id"]
        self.assertGreater(skill_id, 0)
        self.request("/api/settings", {"study_board": "IGCSE Chemistry"})
        _, first, _ = self.create_practice(skill_id)
        _, duplicate, _ = self.create_practice(skill_id)
        self.assertEqual(first["evaluation"]["score"], duplicate["evaluation"]["score"])
        self.assertTrue(duplicate["duplicate"])

        _, dashboard, _ = self.request("/api/dashboard")
        self.assertEqual(len(dashboard["recent"]), 1)
        self.assertEqual(dashboard["study_board"], "IGCSE Chemistry")
        self.assertEqual(dashboard["skills"][0]["id"], skill_id)
        practice_id = dashboard["recent"][0]["id"]
        _, history, _ = self.request(f"/api/practices/{practice_id}")
        self.assertEqual(history["readiness_score"], first["evaluation"]["score"])
        self.assertGreater(history["readiness_after"], 0)
        self.assertIsInstance(history["created_at"], str)

        other_client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.request("/api/register", {"email": "other@example.test", "password": "PostgresTestPassword-123", "remember": False}, client=other_client)
        _, other_dashboard, _ = self.request("/api/dashboard", client=other_client)
        self.assertEqual(other_dashboard["skills"], [])
        self.assert_http_error(f"/api/practices/{practice_id}", 404, client=other_client)

    def test_admin_creation_authorization_and_analytics(self):
        with patch.dict(os.environ, {"SKILLTRACKER_ADMIN_SETUP_KEY": "test-only-setup-key"}):
            status, _, headers = self.request("/api/admin/create", {
                "email": "admin@example.test", "password": "PostgresAdminTest-123", "remember": True,
            }, headers={"X-Admin-Setup-Key": "test-only-setup-key"})
        self.assertEqual(status, 201)
        self.assertIn("Max-Age=2592000", headers["Set-Cookie"])
        status, analytics, _ = self.request("/api/admin/analytics")
        self.assertEqual(status, 200)
        self.assertIn("users_total", analytics)
        self.assertIn("category_decay", analytics)

        regular_client = build_opener(HTTPCookieProcessor(CookieJar()))
        self.request("/api/register", {"email": "regular@example.test", "password": "PostgresTestPassword-123", "remember": False}, client=regular_client)
        self.assert_http_error("/api/admin/analytics", 403, client=regular_client)
        self.assert_http_error("/api/admin/ai-diagnostics", 403, client=regular_client)

    def test_settings_upsert_generated_ids_reconnect_and_transaction_rollback(self):
        self.register("db-ops@example.test")
        _, first_settings, _ = self.request("/api/settings", {"study_board": "First"})
        _, second_settings, _ = self.request("/api/settings", {"study_board": "Second"})
        self.assertTrue(first_settings["ok"] and second_settings["ok"])
        _, skill, _ = self.create_skill("SQL analytics")
        self.assertIsInstance(skill["id"], int)

        for _ in range(3):
            with database.connect_db() as db:
                self.assertEqual(db.execute("SELECT study_board FROM settings").fetchone()["study_board"], "Second")

        with self.assertRaises(RuntimeError):
            with database.connect_db() as db:
                db.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", ("rollback@example.test", "hash", main.now_iso()))
                raise RuntimeError("force rollback")
        with database.connect_db() as db:
            self.assertIsNone(db.execute("SELECT id FROM users WHERE email=?", ("rollback@example.test",)).fetchone())

    def test_postgres_migrations_are_idempotent(self):
        main.initialize_db()
        main.initialize_db()
        with database.connect_db() as db:
            versions = db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual([row["version"] for row in versions], [1, 2])
        with database.connect_db() as db:
            columns = {row["column_name"] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='ai_diagnostics'").fetchall()}
        self.assertTrue({"task", "success", "error_category", "http_status", "model", "latency_ms", "fallback_used"}.issubset(columns))


class _FakeProvider:
    configured = True
    model = "test-model"

    def generate_json(self, user_id, system_instruction, context, schema, task):
        return {"status": "ok"}


if __name__ == "__main__":
    unittest.main()
