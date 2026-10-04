import json
import os
import sqlite3
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from unittest.mock import patch

import ai_provider
import database
import main
import migrate_sqlite_to_postgres
from ai_provider import AIProviderError, AIService, GeminiProvider, RequestLimiter


class FakeGemini:
    configured = True
    model = "test-model"

    def __init__(self):
        self.calls = []
        self.scenario_result = {
            "scenario": "A customer in Madrid needs help changing a rail ticket after a platform change.",
            "instructions": "Respond naturally in Spanish and confirm the replacement details.",
            "difficulty": "intermediate",
            "skill_tested": "Spanish conversation",
            "expected_response_type": "spoken dialogue",
        }
        self.feedback_result = {
            "strengths": ["You confirmed the key details."],
            "weaknesses": ["Explain the alternative you would choose."],
            "where_marks_were_lost": ["The response did not state the follow-up question."],
            "improvement_steps": ["Ask one clarifying question before confirming."],
            "explanation": "The response is clear but could check the new departure time.",
            "confidence": "medium",
            "score": 0,
        }
        self.error = None
        self.model_capabilities = {"model_found": True, "generate_content_supported": True}
        self.model_capability_calls = []

    def get_model_capabilities(self, user_id):
        self.model_capability_calls.append(user_id)
        if self.error:
            raise self.error
        return self.model_capabilities

    def generate_json(self, user_id, system_instruction, context, schema, task):
        self.calls.append((user_id, task, context, schema))
        if self.error:
            raise self.error
        return self.scenario_result if task == "scenario" else self.feedback_result


class CategoryAndServiceTests(unittest.TestCase):
    def test_database_adapter_converts_placeholders_and_sqlite_timestamps(self):
        from datetime import datetime, timezone

        timestamp = datetime(2026, 10, 3, tzinfo=timezone.utc)
        sqlite_adapter = database.DatabaseConnection(object(), postgres=False)
        postgres_adapter = database.DatabaseConnection(object(), postgres=True)
        self.assertEqual(sqlite_adapter._sql("SELECT * FROM sessions WHERE user_id=?"), "SELECT * FROM sessions WHERE user_id=?")
        self.assertEqual(postgres_adapter._sql("SELECT * FROM sessions WHERE user_id=?"), "SELECT * FROM sessions WHERE user_id=%s")
        self.assertEqual(sqlite_adapter._parameters((timestamp,)), ("2026-10-03T00:00:00+00:00",))
        self.assertEqual(postgres_adapter._parameters((timestamp,)), (timestamp,))

    def test_render_fails_closed_when_database_url_is_missing(self):
        with patch.dict(os.environ, {"DATABASE_URL": "", "RENDER": "true", "RENDER_SERVICE_ID": "srv-test"}):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL must be configured"):
                with database.connect_db():
                    self.fail("Render should not open a local SQLite file")

    def test_additive_schema_migration_preserves_existing_practice(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "legacy.sqlite3"
            legacy_db = sqlite3.connect(database_path)
            try:
                db = legacy_db
                db.executescript("""
                    CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
                    CREATE TABLE skills (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL, risk_level REAL NOT NULL DEFAULT 5, difficulty REAL NOT NULL DEFAULT 5, initial_score REAL NOT NULL DEFAULT 1, accuracy_score REAL NOT NULL DEFAULT 0.7, speed_score REAL NOT NULL DEFAULT 0.7, marking_criteria TEXT NOT NULL DEFAULT '', last_practiced TEXT, created_at TEXT NOT NULL);
                    CREATE TABLE practices (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, skill_id INTEGER NOT NULL, scenario TEXT NOT NULL, response TEXT NOT NULL, evaluation_json TEXT NOT NULL, readiness_score REAL NOT NULL, elapsed_seconds INTEGER NOT NULL, created_at TEXT NOT NULL);
                    INSERT INTO users VALUES (1, 'legacy@example.test', 'hashed', 0, '2026-01-01T00:00:00+00:00');
                    INSERT INTO skills(id,user_id,name,category,created_at) VALUES (1,1,'Legacy skill','Other','2026-01-01T00:00:00+00:00');
                    INSERT INTO practices(id,user_id,skill_id,scenario,response,evaluation_json,readiness_score,elapsed_seconds,created_at) VALUES (1,1,1,'Old scenario','Old answer','{}',72,60,'2026-01-01T00:00:00+00:00');
                """)
            finally:
                legacy_db.close()
            original_path = database.DB_PATH
            try:
                with patch.dict(os.environ, {"DATABASE_URL": ""}):
                    database.DB_PATH = database_path
                    main.initialize_db()
                    with main.connect_db() as db:
                        practice = db.execute("SELECT response,readiness_score,submission_id FROM practices WHERE id=1").fetchone()
                        skill_columns = {row["name"] for row in db.execute("PRAGMA table_info(skills)")}
                    self.assertEqual(practice["response"], "Old answer")
                    self.assertEqual(practice["readiness_score"], 72)
                    self.assertIsNone(practice["submission_id"])
                    self.assertTrue({"description", "language_code"}.issubset(skill_columns))
            finally:
                database.DB_PATH = original_path

    def test_sqlite_importer_dry_run_reads_without_writing(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DATABASE_URL": ""}):
            original_path = database.DB_PATH
            source_path = Path(directory) / "source.sqlite3"
            try:
                database.DB_PATH = source_path
                main.initialize_db()
                with main.connect_db() as db:
                    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,?,?,?)", ("import@example.test", "preserved-hash", main.now_iso()))
                    db.execute("INSERT INTO settings(user_id,study_board) VALUES(1,?)", ("IGCSE",))
                    db.execute("INSERT INTO skills(id,user_id,name,category,created_at) VALUES(1,1,?,?,?)", ("Imported skill", "Other", main.now_iso()))
                    db.execute("INSERT INTO practices(id,user_id,skill_id,scenario,response,evaluation_json,readiness_score,elapsed_seconds,created_at) VALUES(1,1,1,?,?,?,?,?,?)", ("Scenario", "Answer", "{}", 70, 60, main.now_iso()))
                    db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", ("hashed-session", 1, main.now_iso()))
                result = migrate_sqlite_to_postgres.import_source(source_path)
                self.assertEqual(result["mode"], "dry_run")
                self.assertEqual(result["source_rows"]["users"], 1)
                self.assertEqual(result["source_rows"]["practices"], 1)
                self.assertEqual(result["source_rows"]["sessions_not_migrated"], 1)
            finally:
                database.DB_PATH = original_path

    def test_skill_category_detection_is_conservative(self):
        self.assertEqual(main.suggest_skill_category("Piano"), "Other")
        self.assertEqual(main.suggest_skill_category("IGCSE Chemistry Stoichiometry"), "Sciences & Math")
        self.assertEqual(main.suggest_skill_category("Spanish conversation"), "Language")
        self.assertIsNone(main.suggest_skill_category("Piano therapy for nurses"))

    def test_scenario_context_covers_language_and_external_topics(self):
        language_skill = {"name": "Conversational Malay", "description": "Real travel situations", "category": "Language", "language_code": "ms-MY", "marking_criteria": "", "difficulty": 4}
        language_context = main.scenario_context(language_skill, "", ["Old scenario"])
        self.assertEqual(language_context["scenario_type"], "language")
        self.assertEqual(language_context["language_code"], "ms-MY")
        self.assertEqual(language_context["recent_scenarios"], ["Old scenario"])

        technical_skill = {"name": "Electrical Engineering", "description": "", "category": "Engineering/Field", "language_code": "en-US", "marking_criteria": "", "difficulty": 7}
        technical_context = main.scenario_context(technical_skill, "IGCSE Chemistry")
        self.assertTrue(technical_context["external_board_topic"])
        self.assertEqual(technical_context["standard_context"], "Universal Technical Standards")

    def test_scenario_generation_validates_and_preserves_language_input(self):
        fake = FakeGemini()
        service = AIService(fake)
        context = {"skill_name": "Spanish conversation", "scenario_type": "language", "language_code": "es-ES"}
        scenario, source, diagnostic = service.generate_scenario(7, context, lambda: {"prompt": "fallback"})
        self.assertEqual(source, "gemini")
        self.assertEqual(scenario["input_mode"], "speech")
        self.assertIn("Madrid", scenario["prompt"])
        self.assertTrue(diagnostic["success"])
        self.assertEqual(diagnostic["task"], "scenario")
        self.assertFalse(diagnostic["fallback_used"])

    def test_scenario_failure_and_malformed_result_use_fallback(self):
        fake = FakeGemini()
        service = AIService(fake)
        context = {"scenario_type": "science", "language_code": "en-US"}
        fallback = {"prompt": "deterministic fallback", "input_mode": "text"}
        fake.error = AIProviderError("application_rate_limit")
        result, source, diagnostic = service.generate_scenario(2, context, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))
        self.assertEqual(diagnostic["error_category"], "application_rate_limit")
        self.assertTrue(diagnostic["fallback_used"])

        fake.error = None
        fake.scenario_result = {"scenario": "incomplete"}
        result, source, diagnostic = service.generate_scenario(2, context, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))
        self.assertEqual(diagnostic["error_category"], "invalid_model_output")

    def test_ai_guidance_cannot_change_official_score_or_deductions(self):
        fake = FakeGemini()
        service = AIService(fake)
        skill = {"name": "Spanish", "description": "", "category": "Language", "marking_criteria": "ask a question", "language_code": "es-ES", "difficulty": 4}
        evaluation = main.evaluate(skill, "I ask a question and confirm the time.", 30, "")
        official_score = evaluation["score"]
        official_deductions = list(evaluation["deductions"])
        result, source, diagnostic = service.enhance_evaluation(4, {"user_response": "I ask a question"}, evaluation)
        self.assertEqual(source, "gemini")
        self.assertTrue(diagnostic["success"])
        self.assertEqual(result["score"], official_score)
        self.assertEqual(result["deductions"], official_deductions)
        self.assertIn("ai_guidance", result)
        self.assertNotIn("score", result["ai_guidance"])

    def test_missing_key_and_invalid_json_fail_safely(self):
        provider = GeminiProvider(api_key="", limiter=RequestLimiter())
        self.assertFalse(provider.configured)
        with self.assertRaises(AIProviderError):
            provider.generate_json(1, "system", {}, {}, "scenario")

        service = AIService(provider)
        fallback = {"prompt": "standard"}
        result, source, diagnostic = service.generate_scenario(1, {}, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))
        self.assertEqual(diagnostic["error_category"], "not_configured")
        self.assertEqual(service.test_connection(1)["error_category"], "not_configured")

        malformed = BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}).encode())
        provider = GeminiProvider(api_key="test-secret", limiter=RequestLimiter())
        with patch("ai_provider.urlopen", return_value=malformed):
            with self.assertRaises(AIProviderError) as invalid_model_json:
                provider.generate_json(1, "system", {}, {}, "scenario")
        self.assertEqual(invalid_model_json.exception.reason, "invalid_model_json")

        malformed_api_response = BytesIO(b"not json")
        with patch("ai_provider.urlopen", return_value=malformed_api_response):
            with self.assertRaises(AIProviderError) as invalid_api_response:
                provider.generate_json(1, "system", {}, {}, "scenario")
        self.assertEqual(invalid_api_response.exception.reason, "invalid_api_response")

    def test_model_capability_check_uses_models_endpoint_without_returning_model_list(self):
        provider = GeminiProvider(api_key="test-secret", model="gemini-2.5-flash-lite", limiter=RequestLimiter())
        payload = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-lite",
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        }
        with patch("ai_provider.urlopen", return_value=BytesIO(json.dumps(payload).encode())) as open_url:
            result = provider.get_model_capabilities(8)
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models")
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.get_header("X-goog-api-key"))
        self.assertIsNone(request.data)
        self.assertEqual(result, {"model_found": True, "generate_content_supported": True})
        self.assertNotIn("models", result)

        unsupported = {
            "models": [
                {
                    "name": "models/gemini-2.5-flash-lite",
                    "supportedGenerationMethods": ["embedContent"],
                }
            ]
        }
        with patch("ai_provider.urlopen", return_value=BytesIO(json.dumps(unsupported).encode())):
            self.assertEqual(
                provider.get_model_capabilities(8),
                {"model_found": True, "generate_content_supported": False},
            )

        with patch("ai_provider.urlopen", side_effect=[
            BytesIO(json.dumps({"models": [], "nextPageToken": "next/page"}).encode()),
            BytesIO(json.dumps(payload).encode()),
        ]) as paged_open_url:
            self.assertEqual(
                provider.get_model_capabilities(8),
                {"model_found": True, "generate_content_supported": True},
            )
        self.assertEqual(
            paged_open_url.call_args_list[1].args[0].full_url,
            "https://generativelanguage.googleapis.com/v1beta/models?pageToken=next%2Fpage",
        )

    def test_provider_keeps_key_in_header_and_parses_json(self):
        body = {"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]}
        provider = GeminiProvider(api_key="private-test-key", model="gemini-test", limiter=RequestLimiter())
        with patch("ai_provider.urlopen", return_value=BytesIO(json.dumps(body).encode())) as request_mock:
            self.assertEqual(provider.generate_json(1, "system", {}, {}, "scenario"), {"ok": True})
        request = request_mock.call_args.args[0]
        self.assertNotIn("private-test-key", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "private-test-key")
        self.assertNotIn("private-test-key", request.data.decode())
        self.assertEqual(provider.model, "gemini-test")

    def test_provider_distinguishes_http_statuses_and_redacts_messages(self):
        provider = GeminiProvider(api_key="private-test-key", limiter=RequestLimiter(per_minute=20, per_day=30, global_per_minute=40))
        cases = {
            400: "bad_request",
            401: "unauthenticated",
            403: "permission_denied",
            404: "model_or_endpoint_not_found",
            429: "quota_or_rate_limit",
            500: "gemini_server_error",
            503: "gemini_server_error",
        }
        for status, expected_reason in cases.items():
            with self.subTest(status=status):
                body = json.dumps({"error": {"message": f"API key: private-test-key; provider detail for status {status}"}}).encode()
                error = HTTPError("https://example.test", status, "provider error", {}, BytesIO(body))
                with self.assertLogs("masterify.ai", level="WARNING") as logs:
                    with patch("ai_provider.urlopen", side_effect=error):
                        with self.assertRaises(AIProviderError) as captured:
                            provider.generate_json(1, "system", {}, {}, "feedback")
                self.assertEqual(captured.exception.reason, expected_reason)
                self.assertEqual(captured.exception.status, status)
                self.assertNotIn("private-test-key", captured.exception.message)
                log_text = " ".join(logs.output)
                self.assertIn(f"status={status}", log_text)
                self.assertIn("model=gemini-2.5-flash-lite", log_text)
                self.assertIn("endpoint=generate_content", log_text)
                self.assertIn("task=feedback", log_text)
                self.assertNotIn("private-test-key", log_text)

    def test_provider_maps_timeout_and_network_failure(self):
        provider = GeminiProvider(api_key="private-test-key", limiter=RequestLimiter())

        with patch("ai_provider.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(AIProviderError) as timeout:
                provider.generate_json(1, "system", {}, {}, "feedback")
        self.assertEqual(timeout.exception.reason, "timeout")
        self.assertIsNone(timeout.exception.status)

        with patch("ai_provider.urlopen", side_effect=ai_provider.URLError("private connection detail")):
            with self.assertRaises(AIProviderError) as connection:
                provider.generate_json(1, "system", {}, {}, "scenario")
        self.assertEqual(connection.exception.reason, "connection_failure")
        self.assertNotIn("private connection detail", str(connection.exception))

    def test_connectivity_probe_returns_safe_success_and_failure(self):
        fake = FakeGemini()
        service = AIService(fake)
        success = service.test_connection(3)
        self.assertEqual(success["success"], True)
        self.assertEqual(success["http_status"], 200)
        self.assertEqual(success["error_category"], None)
        self.assertEqual(success["task"], "connectivity_test")
        self.assertTrue(success["model_found"])
        self.assertTrue(success["generate_content_supported"])
        self.assertNotIn("error_type", success)
        self.assertEqual(fake.model_capability_calls, [3])
        fake.model_capabilities = {"model_found": False, "generate_content_supported": False}
        missing = service.test_connection(3)
        self.assertFalse(missing["success"])
        self.assertEqual(missing["error_category"], "model_or_endpoint_not_found")
        self.assertFalse(missing["model_found"])
        self.assertFalse(missing["generate_content_supported"])
        fake.model_capabilities = {"model_found": True, "generate_content_supported": False}
        unsupported = service.test_connection(3)
        self.assertFalse(unsupported["success"])
        self.assertEqual(unsupported["error_category"], "unsupported_method")
        self.assertTrue(unsupported["model_found"])
        self.assertFalse(unsupported["generate_content_supported"])
        fake.error = AIProviderError("permission_denied", status=403, message="Project lacks model permission.")
        failure = service.test_connection(3)
        self.assertEqual(failure["success"], False)
        self.assertEqual(failure["http_status"], 403)
        self.assertEqual(failure["error_category"], "permission_denied")
        self.assertIsNone(failure["model_found"])
        self.assertIsNone(failure["generate_content_supported"])
        self.assertNotIn("api_key", json.dumps(failure).lower())

    def test_request_limiter_rejects_excess_usage(self):
        clock = [1000.0]
        limiter = RequestLimiter(per_minute=1, per_day=2, global_per_minute=3, clock=lambda: clock[0])
        limiter.consume(12)
        with self.assertRaises(AIProviderError):
            limiter.consume(12)
        clock[0] += 61
        limiter.consume(12)


class PracticeIdempotencyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_env = patch.dict(os.environ, {"DATABASE_URL": ""})
        self.database_env.start()
        self.original_db_path = database.DB_PATH
        self.original_ai_service = main.ai_service
        self.fake = FakeGemini()
        main.ai_service = AIService(self.fake)
        database.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
        main.initialize_db()
        self.server = main.ThreadingHTTPServer(("127.0.0.1", 0), main.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.client = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        database.DB_PATH = self.original_db_path
        main.ai_service = self.original_ai_service
        self.database_env.stop()
        self.temp_dir.cleanup()

    def request(self, path, body=None, headers=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
        with self.client.open(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_admin_auth_debug_is_protected_read_only_and_limited(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"SKILLTRACKER_ADMIN_SETUP_KEY": "diagnostic-test-secret"}):
            self.request("/api/register", {"email": "diagnostic@example.test", "password": "LongTestPassword123", "remember": False})

            request = Request(self.base + "/api/admin/auth-debug?email=diagnostic%40example.test")
            with self.assertRaises(HTTPError) as denied:
                self.client.open(request, timeout=5)
            self.assertEqual(denied.exception.code, 403)
            denied.exception.close()

            _, result = self.request(
                "/api/admin/auth-debug?email=%20Diagnostic%40Example.test%20",
                headers={"X-Admin-Setup-Key": "diagnostic-test-secret"},
            )

            request = Request(self.base + "/api/admin/auth-debug?email=diagnostic%40example.test&password=never-send")
            request.add_header("X-Admin-Setup-Key", "diagnostic-test-secret")
            with self.assertRaises(HTTPError) as password_rejected:
                self.client.open(request, timeout=5)
            self.assertEqual(password_rejected.exception.code, 400)
            password_rejected.exception.close()

        self.assertEqual(result["normalized_email"], "diagnostic@example.test")
        self.assertTrue(result["user_exists"])
        self.assertFalse(result["is_admin"])
        self.assertTrue(result["settings_row_exists"])
        self.assertIn(result["database_path_type"], ("configured_env", "default"))
        self.assertEqual(set(result), {
            "database_path_configured", "database_path_type", "database_backend", "normalized_email",
            "user_exists", "is_admin", "settings_row_exists",
        })
        serialized = json.dumps(result).lower()
        for forbidden in ("password", "hash", "session", "token", "secret", "api_key", "filesystem"):
            self.assertNotIn(forbidden, serialized)

    def test_ai_status_distinguishes_configuration_and_test_route_is_admin_only(self):
        self.request("/api/register", {"email": "diagnostic@example.test", "password": "LongTestPassword123", "remember": False})
        with self.assertRaises(HTTPError) as status_denied:
            self.client.open(Request(self.base + "/api/ai-status"), timeout=5)
        self.assertEqual(status_denied.exception.code, 403)
        status_denied.exception.close()

        request = Request(self.base + "/api/ai-test", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as denied:
            self.client.open(request, timeout=5)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        with self.assertRaises(HTTPError) as diagnostics_denied:
            self.client.open(Request(self.base + "/api/admin/ai-diagnostics"), timeout=5)
        self.assertEqual(diagnostics_denied.exception.code, 403)
        diagnostics_denied.exception.close()

        with main.connect_db() as db:
            db.execute("UPDATE users SET is_admin=1 WHERE email=?", ("diagnostic@example.test",))
        _, status = self.request("/api/ai-status")
        self.assertEqual(status, {"configured": True, "model": "test-model", "provider": "gemini", "connectivity": "not_tested"})
        _, result = self.request("/api/ai-test", {})
        self.assertTrue(result["success"])
        self.assertEqual(result["http_status"], 200)
        self.assertTrue(result["model_found"])
        self.assertTrue(result["generate_content_supported"])
        self.assertEqual(self.fake.model_capability_calls, [1])
        self.assertNotIn("api_key", json.dumps(result).lower())
        self.fake.error = AIProviderError("permission_denied", status=403, message="private-test-key provider detail")
        _, failure = self.request("/api/ai-test", {})
        self.assertEqual(failure["error_category"], "permission_denied")
        self.assertEqual(failure["http_status"], 403)
        self.assertIsNone(failure["model_found"])
        self.assertIsNone(failure["generate_content_supported"])
        self.assertNotIn("private-test-key", json.dumps(failure))
        with main.connect_db() as db:
            rows = db.execute("SELECT task,success,error_category,http_status,model,latency_ms,fallback_used,safe_message FROM ai_diagnostics ORDER BY id").fetchall()
            diagnostic_columns = {row["name"] for row in db.execute("PRAGMA table_info(ai_diagnostics)")}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["task"], "connectivity_test")
        self.assertTrue(rows[0]["success"])
        self.assertEqual(rows[1]["error_category"], "permission_denied")
        self.assertEqual(rows[1]["http_status"], 403)
        self.assertNotIn("private-test-key", json.dumps([dict(row) for row in rows]))
        self.assertFalse({"user_id", "response", "prompt", "api_key"}.intersection(diagnostic_columns))

    def test_practice_retry_does_not_duplicate_ai_or_history(self):
        email = "idempotency@example.test"
        self.request("/api/register", {"email": email, "password": "LongTestPassword123", "remember": False})
        _, created = self.request("/api/skills", {
            "name": "Spanish conversation", "category": "Language", "language_code": "es-ES",
            "description": "Practice travel conversations", "marking_criteria": "ask a question",
        })
        _, scenario_result = self.request(f"/api/skills/{created['id']}/scenario")
        self.assertNotIn("ai_configured", scenario_result)
        self.assertEqual(scenario_result["ai_provider"], "gemini")

        submission = {
            "skill_id": created["id"], "scenario": scenario_result["scenario"]["prompt"],
            "response": "I ask a question and confirm the departure time.",
            "submission_id": "test-submission-identifier-001", "elapsed_seconds": 30,
        }
        status, first = self.request("/api/practices", submission)
        self.assertEqual(status, 201)
        status, duplicate = self.request("/api/practices", submission)
        self.assertEqual(status, 200)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["evaluation"]["score"], duplicate["evaluation"]["score"])
        self.assertEqual(len(self.fake.calls), 2)

        _, dashboard = self.request("/api/dashboard")
        self.assertEqual(len(dashboard["recent"]), 1)
        _, history = self.request(f"/api/practices/{dashboard['recent'][0]['id']}")
        self.assertEqual(history["response"], submission["response"])
        self.assertGreater(history["readiness_after"], 0)
        with main.connect_db() as db:
            diagnostic_rows = db.execute("SELECT task,success,fallback_used FROM ai_diagnostics ORDER BY id").fetchall()
        self.assertEqual([row["task"] for row in diagnostic_rows], ["scenario", "feedback"])
        self.assertTrue(all(row["success"] and not row["fallback_used"] for row in diagnostic_rows))

    def test_gemini_failure_still_saves_practice_and_answer(self):
        self.fake.error = AIProviderError("connection_failure")
        self.request("/api/register", {"email": "fallback@example.test", "password": "LongTestPassword123", "remember": False})
        _, created = self.request("/api/skills", {
            "name": "Spanish conversation", "category": "Language", "language_code": "es-ES",
        })
        _, scenario_result = self.request(f"/api/skills/{created['id']}/scenario")
        self.assertEqual(scenario_result["ai_provider"], "standard")
        response_text = "I would ask the station attendant to confirm the new departure time."
        _, result = self.request("/api/practices", {
            "skill_id": created["id"], "scenario": scenario_result["scenario"]["prompt"],
            "response": response_text, "submission_id": "fallback-submission-identifier-01", "elapsed_seconds": 20,
        })
        self.assertEqual(result["evaluation"]["ai_provider"], "standard")
        self.assertEqual(result["evaluation"]["ai_notice"], ai_provider.FALLBACK_NOTICE)
        self.assertNotIn("http_status", json.dumps(result).lower())
        self.assertNotIn("error_category", json.dumps(result).lower())
        self.assertNotIn("connection_failure", json.dumps(result))
        with main.connect_db() as db:
            diagnostic_rows = db.execute("SELECT task,success,error_category,fallback_used FROM ai_diagnostics ORDER BY id").fetchall()
        self.assertEqual([row["task"] for row in diagnostic_rows], ["scenario", "feedback"])
        self.assertTrue(all(not row["success"] and row["fallback_used"] for row in diagnostic_rows))
        self.assertTrue(all(row["error_category"] == "connection_failure" for row in diagnostic_rows))
        _, dashboard = self.request("/api/dashboard")
        _, history = self.request(f"/api/practices/{dashboard['recent'][0]['id']}")
        self.assertEqual(history["response"], response_text)


if __name__ == "__main__":
    unittest.main()
