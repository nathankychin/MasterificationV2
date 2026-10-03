import json
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
import main
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

    def generate_json(self, user_id, system_instruction, context, schema, task):
        self.calls.append((user_id, task, context, schema))
        if self.error:
            raise self.error
        return self.scenario_result if task == "scenario" else self.feedback_result


class CategoryAndServiceTests(unittest.TestCase):
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
            original_path = main.DB_PATH
            try:
                main.DB_PATH = database_path
                main.initialize_db()
                with main.connect_db() as db:
                    practice = db.execute("SELECT response,readiness_score,submission_id FROM practices WHERE id=1").fetchone()
                    skill_columns = {row["name"] for row in db.execute("PRAGMA table_info(skills)")}
                self.assertEqual(practice["response"], "Old answer")
                self.assertEqual(practice["readiness_score"], 72)
                self.assertIsNone(practice["submission_id"])
                self.assertTrue({"description", "language_code"}.issubset(skill_columns))
            finally:
                main.DB_PATH = original_path

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
        scenario, source = service.generate_scenario(7, context, lambda: {"prompt": "fallback"})
        self.assertEqual(source, "gemini")
        self.assertEqual(scenario["input_mode"], "speech")
        self.assertIn("Madrid", scenario["prompt"])

    def test_scenario_failure_and_malformed_result_use_fallback(self):
        fake = FakeGemini()
        service = AIService(fake)
        context = {"scenario_type": "science", "language_code": "en-US"}
        fallback = {"prompt": "deterministic fallback", "input_mode": "text"}
        fake.error = AIProviderError("rate_limit")
        result, source = service.generate_scenario(2, context, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))

        fake.error = None
        fake.scenario_result = {"scenario": "incomplete"}
        result, source = service.generate_scenario(2, context, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))

    def test_ai_guidance_cannot_change_official_score_or_deductions(self):
        fake = FakeGemini()
        service = AIService(fake)
        skill = {"name": "Spanish", "description": "", "category": "Language", "marking_criteria": "ask a question", "language_code": "es-ES", "difficulty": 4}
        evaluation = main.evaluate(skill, "I ask a question and confirm the time.", 30, "")
        official_score = evaluation["score"]
        official_deductions = list(evaluation["deductions"])
        result, source = service.enhance_evaluation(4, {"user_response": "I ask a question"}, evaluation)
        self.assertEqual(source, "gemini")
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
        result, source = service.generate_scenario(1, {}, lambda: fallback)
        self.assertEqual((result, source), (fallback, "standard"))
        self.assertEqual(service.test_connection(1)["error_type"], "not_configured")

        malformed = BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}).encode())
        provider = GeminiProvider(api_key="test-secret", limiter=RequestLimiter())
        with patch("ai_provider.urlopen", return_value=malformed):
            with self.assertRaises(AIProviderError):
                provider.generate_json(1, "system", {}, {}, "scenario")

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
        self.assertEqual(success, {
            "success": True, "status": 200, "model": "test-model", "error_type": None,
            "message": "Gemini connectivity test succeeded.",
        })
        self.assertEqual(fake.calls[0][1], "connectivity_test")
        fake.error = AIProviderError("permission_denied", status=403, message="Project lacks model permission.")
        failure = service.test_connection(3)
        self.assertEqual(failure["success"], False)
        self.assertEqual(failure["status"], 403)
        self.assertEqual(failure["error_type"], "permission_denied")
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
        self.original_db_path = main.DB_PATH
        self.original_ai_service = main.ai_service
        self.fake = FakeGemini()
        main.ai_service = AIService(self.fake)
        main.DB_PATH = Path(self.temp_dir.name) / "test.sqlite3"
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
        main.DB_PATH = self.original_db_path
        main.ai_service = self.original_ai_service
        self.temp_dir.cleanup()

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        with self.client.open(request, timeout=5) as response:
            return response.status, json.loads(response.read())

    def test_ai_status_distinguishes_configuration_and_test_route_is_admin_only(self):
        self.request("/api/register", {"email": "diagnostic@example.test", "password": "LongTestPassword123", "remember": False})
        _, status = self.request("/api/ai-status")
        self.assertEqual(status, {"configured": True, "model": "test-model", "provider": "gemini", "connectivity": "not_tested"})

        request = Request(self.base + "/api/ai-test", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as denied:
            self.client.open(request, timeout=5)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()

        with main.connect_db() as db:
            db.execute("UPDATE users SET is_admin=1 WHERE email=?", ("diagnostic@example.test",))
        _, result = self.request("/api/ai-test", {})
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 200)
        self.assertNotIn("api_key", json.dumps(result).lower())

    def test_practice_retry_does_not_duplicate_ai_or_history(self):
        email = "idempotency@example.test"
        self.request("/api/register", {"email": email, "password": "LongTestPassword123", "remember": False})
        _, created = self.request("/api/skills", {
            "name": "Spanish conversation", "category": "Language", "language_code": "es-ES",
            "description": "Practice travel conversations", "marking_criteria": "ask a question",
        })
        _, scenario_result = self.request(f"/api/skills/{created['id']}/scenario")
        self.assertTrue(scenario_result["ai_configured"])
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

    def test_gemini_failure_still_saves_practice_and_answer(self):
        self.fake.error = AIProviderError("network_or_timeout")
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
        self.assertIn("still been completed", result["evaluation"]["ai_notice"])
        _, dashboard = self.request("/api/dashboard")
        _, history = self.request(f"/api/practices/{dashboard['recent'][0]['id']}")
        self.assertEqual(history["response"], response_text)


if __name__ == "__main__":
    unittest.main()
