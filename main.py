import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import re
import secrets
import getpass
from math import exp
import threading
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from ai_provider import FALLBACK_NOTICE, ai_service
from database import connect_db, database_configuration, initialize_db, is_integrity_error


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSION_DAYS = 30
RESPONSE_TIMES = []
CATEGORIES = ("Healthcare/Nursing", "Technical & Data", "Language", "Sciences & Math", "Humanities", "Engineering/Field", "Other")
CATEGORY_PATTERNS = {
    "Healthcare/Nursing": (r"\bnurs(?:e|es|ing)\b", r"\bclinical\b", r"\btriage\b", r"\bpatient\b", r"\bmedication\b", r"\bcpr\b", r"\bfirst aid\b", r"\banatomy\b", r"\bvital signs\b", r"\bwound care\b"),
    "Technical & Data": (r"\bsql\b", r"\bpython\b", r"\bjavascript\b", r"\bprogramming\b", r"\bcoding\b", r"\bdata analysis\b", r"\bsoftware\b", r"\bdebugging\b", r"\bnetworking\b", r"\bcybersecurity\b"),
    "Language": (r"\blanguage\b", r"\bspanish\b", r"\bfrench\b", r"\bjapanese\b", r"\bmandarin\b", r"\bchinese\b", r"\barabic\b", r"\bmalay\b", r"\bgerman\b", r"\bitalian\b", r"\bkorean\b", r"\bportuguese\b", r"\bconversation(?:al)?\b", r"\btranslation\b"),
    "Sciences & Math": (r"\bmathematics\b", r"\bmaths?\b", r"\balgebra\b", r"\bcalculus\b", r"\bgeometry\b", r"\bstatistics\b", r"\bphysics\b", r"\bchemistry\b", r"\bbiology\b", r"\bscience\b", r"\bstoichiometry\b"),
    "Humanities": (r"\bhistory\b", r"\bliterature\b", r"\bphilosophy\b", r"\bethics\b", r"\bpolitics\b", r"\beconomics\b", r"\bgeography\b", r"\bsociology\b", r"\bpsychology\b", r"\banthropology\b"),
    "Engineering/Field": (r"\bengineering\b", r"\belectrician\b", r"\belectrical\b", r"\bmechanical\b", r"\bfield technician\b", r"\bhvac\b", r"\bwelding\b", r"\bworkshop\b", r"\bsite safety\b"),
    "Other": (r"\bpiano\b", r"\bmusic\b", r"\bguitar\b", r"\bviolin\b", r"\bpainting\b", r"\bdrawing\b", r"\bsculpture\b", r"\bpottery\b", r"\bcooking\b", r"\bwoodworking\b", r"\bphotography\b", r"\bdance\b")
}
_submission_lock_guard = threading.Lock()
_submission_locks = {}


def suggest_skill_category(skill_name):
    normalized = skill_name.casefold()
    matches = [category for category, patterns in CATEGORY_PATTERNS.items() if any(re.search(pattern, normalized) for pattern in patterns)]
    return matches[0] if len(matches) == 1 else None


@contextmanager
def submission_lock(user_id, submission_id):
    if not submission_id:
        yield
        return
    key = (user_id, submission_id)
    with _submission_lock_guard:
        entry = _submission_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
        lock = entry[0]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _submission_lock_guard:
            entry[1] -= 1
            if entry[1] == 0:
                _submission_locks.pop(key, None)


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0)


def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"{salt.hex()}${derived.hex()}"


def password_matches(password, encoded):
    try:
        salt_hex, expected = encoded.split("$", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def readiness(skill, at=None):
    last = skill["last_practiced"]
    last_practiced = last if isinstance(last, datetime) else datetime.fromisoformat(last) if last else None
    if last_practiced and last_practiced.tzinfo is None:
        last_practiced = last_practiced.replace(tzinfo=timezone.utc)
    elapsed = max(0.0, (datetime.now(timezone.utc) - last_practiced).total_seconds() / 86400) if last_practiced else 0.0
    decay_rate = 0.018 + (float(skill["risk_level"]) * 0.003) + (float(skill["difficulty"]) * 0.0015)
    decay = float(skill["initial_score"]) * (2.718281828459045 ** (-decay_rate * elapsed))
    applied = float(skill["accuracy_score"]) * 0.55 + float(skill["speed_score"]) * 0.45
    return round(clamp((decay * 0.65) + (applied * 0.35)) * 100, 1)


def status_for(score):
    return "critical" if score < 40 else "warning" if score < 70 else "optimal"


def is_external_board_topic(skill, board):
    if not board or not re.search(r"\b(igcse|gcse|a[- ]level|ib|international baccalaureate)\b", board, re.IGNORECASE):
        return False
    category = skill["category"]
    if category == "Engineering/Field":
        return True
    return category == "Technical & Data" and "computer science" not in board.lower()


SCENARIOS = {
    "language": [
        "You are at a neighborhood cafe in {place}. The menu is unfamiliar, and the barista asks what you would like and whether you have any dietary restrictions. Respond naturally, ask one follow-up question, and confirm the order.",
        "You are visiting a clinic in {place} and need to explain a symptom, say when it began, and ask what to do next. Continue the conversation naturally and check that you understood the instructions.",
        "Your train ticket has the wrong destination in {place}. Explain the problem to the station attendant, ask about alternatives, and confirm the departure details.",
        "A host in {place} invites you to a local event. Ask about the time and location, explain one scheduling constraint, and agree on a plan."
    ],
    "science": [
        "A lab team measuring {skill} records an unexpected result under time pressure. State the governing relationship, show the calculation or reasoning, check units, and identify one likely source of error.",
        "A field sample related to {skill} differs from the expected range. Interpret the evidence, explain the method you would use to verify it, and state what conclusion the data does not support.",
        "A design must satisfy a numerical constraint involving {skill}. Work through the calculation, state assumptions, and explain how you would check whether the result is physically or mathematically plausible."
    ],
    "humanities": [
        "You are advising a review panel examining a disputed claim about {skill}. Separate the claim from its evidence, assess the source perspective, and give a reasoned conclusion with a relevant counterargument.",
        "A community decision concerning {skill} has competing historical or ethical interpretations. Compare two plausible perspectives, identify the evidence each relies on, and defend a balanced recommendation.",
        "A new source changes part of an established account of {skill}. Explain how you would assess its reliability and context, then revise or retain the interpretation with reasons."
    ],
    "technical": [
        "A production system using {skill} starts failing intermittently just before a release. Describe the first safe diagnostic steps, the evidence you would collect, and how you would validate a fix before rollout.",
        "A colleague reports an inconsistent result in {skill}, but the issue cannot be reproduced on demand. Form a testable hypothesis, isolate variables, and explain how you would communicate the risk while investigating.",
        "A time-critical change involving {skill} passes a happy-path test but may affect edge cases. Identify high-risk cases, propose a verification plan, and state the rollback signal."
    ],
    "healthcare": [
        "During a busy shift, a patient connected to {skill} shows a concerning change while another task is waiting. Describe your immediate assessment, escalation and safety checks, and how you would communicate and document the decision.",
        "A handover about {skill} contains an unclear value and a possible medication or procedure risk. Explain what you verify before acting, who you contact, and how you close the communication loop.",
        "A patient situation involving {skill} changes after the initial plan. State the red flags you reassess, the safe next actions within your role, and the information you pass to the responsible clinician."
    ]
}


def scenario_type_for(category):
    category = category.lower()
    kind = "language" if "language" in category else "healthcare" if any(word in category for word in ("health", "nurs")) else "science" if any(word in category for word in ("science", "math")) else "humanities" if "humanit" in category else "technical"
    return kind


def scenario_context(skill, board, recent_scenarios=()):
    kind = scenario_type_for(skill["category"])
    return {
        "skill_name": skill["name"],
        "skill_description": skill["description"],
        "category": skill["category"],
        "subject_or_topic": skill["name"],
        "study_board": board,
        "marking_criteria": skill["marking_criteria"][:2000],
        "difficulty": skill["difficulty"],
        "scenario_type": kind,
        "language_code": skill["language_code"],
        "external_board_topic": is_external_board_topic(skill, board),
        "standard_context": "Universal Technical Standards" if is_external_board_topic(skill, board) else board or "General practice",
        "recent_scenarios": [scenario[:500] for scenario in recent_scenarios[:4]],
    }


def scenario_for(skill, board, recent_scenarios=()):
    kind = scenario_type_for(skill["category"])
    place = secrets.choice(("Tokyo", "Madrid", "Lisbon", "Seoul", "Mexico City"))
    options = [template.format(skill=skill["name"], place=place) for template in SCENARIOS[kind]]
    fresh_options = [option for option in options if not any(option in previous for previous in recent_scenarios)]
    text = secrets.choice(fresh_options or options)
    language_names = {"es-ES": "Spanish", "ja-JP": "Japanese", "fr-FR": "French", "de-DE": "German", "it-IT": "Italian", "pt-PT": "Portuguese", "ko-KR": "Korean", "zh-CN": "Mandarin Chinese", "ms-MY": "Malay", "en-US": "English"}
    if kind == "language":
        text += f" Conduct your side of the conversation in {language_names.get(skill['language_code'], 'the selected language')} rather than English."
    if skill["description"].strip():
        text += f" Focus on this learner context: {skill['description'][:240]}"
    if board and is_external_board_topic(skill, board):
        text += f" This topic is outside {board}; apply relevant universal technical standards and safety codes."
    elif board:
        text += f" Use the level and terminology expected for {board}."
    return {"prompt": text, "input_mode": "speech" if kind == "language" else "text", "scenario_type": kind, "language_code": skill["language_code"]}


def evaluate(skill, response, elapsed_seconds, board):
    text = response.strip()
    lower = text.lower()
    words = re.findall(r"[\w'-]+", lower)
    criteria = [part.strip() for part in skill["marking_criteria"].splitlines() if part.strip()]
    if criteria:
        matched = [criterion for criterion in criteria if any(term in lower for term in re.findall(r"[\w'-]+", criterion.lower()) if len(term) > 3)]
        criterion_total = len(criteria)
        criterion_score = len(matched) / criterion_total
        deductions = [{"criterion": item, "deduction": round(80 / criterion_total, 1)} for item in criteria if item not in matched]
    else:
        matched = []
        criterion_score = 0.0
        deductions = []
    completeness = clamp(len(words) / 90, 0.25, 1.0)
    accuracy = criterion_score if criteria else clamp(0.35 + min(len(set(words)) / 80, 0.45) + (0.2 if len(words) >= 35 else 0))
    speed = clamp(1 - max(0, elapsed_seconds - 90) / 900)
    score = round((accuracy * 0.8 + speed * 0.2) * 100, 1)
    if speed < 1:
        deductions.append({"criterion": "Response-time component", "deduction": round((1 - speed) * 20, 1)})
    strengths = []
    if len(words) >= 25:
        strengths.append("You gave enough detail to assess your reasoning rather than only a short conclusion.")
    if matched:
        strengths.append("Your response addressed: " + ", ".join(matched) + ".")
    if not strengths:
        strengths.append("You completed a retrieval attempt; that gives you a concrete starting point for the next drill.")
    improvements = []
    if deductions:
        improvements.extend("Add an explicit explanation of: " + item["criterion"] for item in deductions[:4])
    if len(words) < 35:
        improvements.append("Expand the response with the reasoning, a check, and the action you would take next.")
    if speed < 0.7:
        improvements.append("Use a short timed recall drill, then repeat the scenario and compare the steps you can retrieve promptly.")
    if not improvements:
        improvements.append("Maintain readiness with another scenario after the risk-weighted reminder interval.")
    external_topic = is_external_board_topic(skill, board)
    alignment = {
        "selected_standard": "Universal Technical Standards" if external_topic else board or "Universal technical / general rubric",
        "basis": "User-entered skill criteria" if criteria else "Universal technical heuristic" if external_topic else "Transparent general completeness and response-time rubric",
        "criteria_total": len(criteria),
        "criteria_met": len(matched),
        "official_scheme_verified": False,
        "note": "This topic is treated as external to the selected school board and assessed against a universal technical rubric. No official board mark scheme is embedded or fetched. Add exact applicable criteria to this skill for transparent checks; this score is not an official mark." if external_topic else "No official board mark scheme is embedded or fetched. Add the exact criteria to this skill to assess alignment; this score is a transparent heuristic, not an official mark."
    }
    return {
        "score": score,
        "accuracy": round(accuracy * 100, 1),
        "speed": round(speed * 100, 1),
        "strengths": strengths,
        "deductions": deductions,
        "improvements": improvements,
        "alignment": alignment,
        "rubric_note": "Automated deterministic rubric; not clinical, safety, certification, or official exam-board sign-off."
    }


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "ReadinessTracker/1.0"

    def log_message(self, format_string, *args):
        return

    def handle_one_request(self):
        started = time.perf_counter()
        try:
            super().handle_one_request()
        finally:
            RESPONSE_TIMES.append((time.perf_counter() - started) * 1000)

    def send_json(self, payload, status=HTTPStatus.OK, headers=None):
        encoded = json.dumps(payload, ensure_ascii=True, default=self.json_default).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def json_default(value):
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    def body_json(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 100_000:
                raise ApiError("Request body is too large.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return json.loads(self.rfile.read(size) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ApiError("Request body must be valid JSON.")

    def current_user(self, required=True):
        cookie = self.headers.get("Cookie", "")
        token = next((part.strip().split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("session=")), None)
        if not token:
            if required:
                raise ApiError("Sign in to continue.", HTTPStatus.UNAUTHORIZED)
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with connect_db() as db:
            user = db.execute("SELECT users.id, users.email, users.is_admin FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?", (digest, now_iso())).fetchone()
        if user is None and required:
            raise ApiError("Your session expired. Please sign in again.", HTTPStatus.UNAUTHORIZED)
        return user

    def issue_session(self, user_id, remember):
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS if remember else 1)
        with connect_db() as db:
            db.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
        max_age = SESSION_DAYS * 86400 if remember else 86400
        return {"Set-Cookie": f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"}

    def skill_for_user(self, db, skill_id, user_id):
        skill = db.execute("SELECT * FROM skills WHERE id=? AND user_id=?", (skill_id, user_id)).fetchone()
        if skill is None:
            raise ApiError("Skill not found.", HTTPStatus.NOT_FOUND)
        return skill

    def create_practice(self, user, data):
        skill_id = int(data.get("skill_id", 0))
        response = str(data.get("response", "")).strip()
        elapsed = int(clamp(data.get("elapsed_seconds", 0), 0, 86400))
        scenario = str(data.get("scenario", "")).strip()
        submission_id = str(data.get("submission_id", "")).strip()
        if not response or not scenario:
            raise ApiError("A response and scenario are required.")
        if len(response) > 20000 or len(scenario) > 2400:
            raise ApiError("The response or scenario is too long.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", submission_id):
            raise ApiError("A valid practice submission ID is required.")

        with submission_lock(user["id"], submission_id):
            with connect_db() as db:
                previous = db.execute("SELECT evaluation_json,readiness_after FROM practices WHERE user_id=? AND submission_id=?", (user["id"], submission_id)).fetchone()
                if previous:
                    return self.send_json({"evaluation": json.loads(previous["evaluation_json"]), "readiness": previous["readiness_after"], "duplicate": True}, HTTPStatus.OK)
                skill = self.skill_for_user(db, skill_id, user["id"])
                setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                board = setting["study_board"] if setting else ""

            evaluation = evaluate(skill, response, elapsed, board)
            official_score = evaluation["score"]
            context = {
                "skill_name": skill["name"],
                "skill_description": skill["description"],
                "category": skill["category"],
                "study_board": board,
                "standard_context": evaluation["alignment"]["selected_standard"],
                "marking_criteria": skill["marking_criteria"][:2000],
                "scenario": scenario[:1800],
                "user_response": response[:12000],
                "deterministic_result": {
                    "accuracy": evaluation["accuracy"],
                    "speed": evaluation["speed"],
                    "criteria_met": evaluation["alignment"]["criteria_met"],
                    "criteria_total": evaluation["alignment"]["criteria_total"],
                    "deductions": evaluation["deductions"][:8],
                },
            }
            evaluation, feedback_source = ai_service.enhance_evaluation(user["id"], context, evaluation)
            if evaluation["score"] != official_score:
                evaluation["score"] = official_score
            evaluation["ai_provider"] = feedback_source
            if feedback_source != "gemini":
                evaluation["ai_notice"] = FALLBACK_NOTICE if ai_service.configured else "Gemini is not configured. Your standard Masterify evaluation has still been completed."

            practiced_at = now_iso()
            with connect_db() as db:
                db.execute("UPDATE skills SET accuracy_score=?,speed_score=?,last_practiced=? WHERE id=? AND user_id=?", (official_score / 100, evaluation["speed"] / 100, practiced_at, skill_id, user["id"]))
                updated = self.skill_for_user(db, skill_id, user["id"])
                new_score = readiness(updated)
                db.execute("INSERT INTO practices(user_id,skill_id,scenario,response,evaluation_json,readiness_score,readiness_after,submission_id,elapsed_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (user["id"], skill_id, scenario, response, json.dumps(evaluation), official_score, new_score, submission_id, elapsed, practiced_at))
            return self.send_json({"evaluation": evaluation, "readiness": new_score, "duplicate": False}, HTTPStatus.CREATED)

    def do_GET(self):
        try:
            path = urlparse(self.path).path
            if path == "/api/ping":
                return self.send_json({"ok": True, "time": now_iso()})
            if path == "/api/admin/auth-debug":
                setup_key = os.environ.get("SKILLTRACKER_ADMIN_SETUP_KEY", "")
                provided_key = self.headers.get("X-Admin-Setup-Key", "")
                if not setup_key or not hmac.compare_digest(provided_key, setup_key):
                    raise ApiError("Administrator diagnostic access required.", HTTPStatus.FORBIDDEN)
                query = parse_qs(urlparse(self.path).query)
                email_values = query.get("email", [])
                if set(query) != {"email"} or len(email_values) != 1:
                    raise ApiError("Provide only one email query parameter.")
                email = email_values[0].strip().lower()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                    raise ApiError("Provide a valid email query parameter.")
                database_config = database_configuration()
                with connect_db() as db:
                    user = db.execute("SELECT id,is_admin FROM users WHERE email=?", (email,)).fetchone()
                    settings_exists = bool(user and db.execute("SELECT 1 FROM settings WHERE user_id=?", (user["id"],)).fetchone())
                return self.send_json({
                    "database_path_configured": database_config["configured"],
                    "database_path_type": database_config["path_type"],
                    "database_backend": database_config["backend"],
                    "normalized_email": email,
                    "user_exists": user is not None,
                    "is_admin": bool(user["is_admin"]) if user else False,
                    "settings_row_exists": settings_exists,
                })
            if path == "/api/ai-status":
                return self.send_json({"configured": ai_service.configured, "model": ai_service.model, "provider": "gemini", "connectivity": "not_tested"})
            if path == "/api/me":
                user = self.current_user(False)
                return self.send_json({"user": dict(user) if user else None})
            if path == "/api/dashboard":
                user = self.current_user()
                with connect_db() as db:
                    setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                    rows = db.execute("SELECT * FROM skills WHERE user_id=? ORDER BY LOWER(name)", (user["id"],)).fetchall()
                    skills = [dict(row) | {"readiness": readiness(row), "status": status_for(readiness(row))} for row in rows]
                    recent = db.execute("SELECT p.id,p.skill_id,s.name AS skill_name,p.readiness_score,p.readiness_after,p.created_at,p.evaluation_json FROM practices p JOIN skills s ON s.id=p.skill_id WHERE p.user_id=? ORDER BY p.created_at DESC LIMIT 12", (user["id"],)).fetchall()
                return self.send_json({"skills": skills, "recent": [dict(row) | {"evaluation": json.loads(row["evaluation_json"])} for row in recent], "study_board": setting["study_board"] if setting else ""})
            match = re.fullmatch(r"/api/skills/(\d+)/scenario", path)
            if match:
                user = self.current_user()
                with connect_db() as db:
                    skill = self.skill_for_user(db, int(match.group(1)), user["id"])
                    setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                    recent = db.execute("SELECT scenario FROM practices WHERE user_id=? AND skill_id=? ORDER BY created_at DESC LIMIT 4", (user["id"], skill["id"])).fetchall()
                recent_scenarios = [row["scenario"] for row in recent]
                board = setting["study_board"] if setting else ""
                context = scenario_context(skill, board, recent_scenarios)
                scenario, provider = ai_service.generate_scenario(user["id"], context, lambda: scenario_for(skill, board, recent_scenarios))
                return self.send_json({"skill": dict(skill), "scenario": scenario, "ai_provider": provider, "ai_configured": ai_service.configured})
            match = re.fullmatch(r"/api/practices/(\d+)", path)
            if match:
                user = self.current_user()
                with connect_db() as db:
                    row = db.execute("SELECT p.*,s.name AS skill_name FROM practices p JOIN skills s ON s.id=p.skill_id WHERE p.id=? AND p.user_id=?", (match.group(1), user["id"])).fetchone()
                if row is None:
                    raise ApiError("Practice not found.", HTTPStatus.NOT_FOUND)
                return self.send_json(dict(row) | {"evaluation": json.loads(row["evaluation_json"])})
            if path == "/admin" or path.startswith("/api/admin"):
                user = self.current_user()
                if not user["is_admin"]:
                    raise ApiError("Administrator access required.", HTTPStatus.FORBIDDEN)
                if path == "/admin":
                    return self.serve_static("index.html")
                if path == "/api/admin/analytics":
                    with connect_db() as db:
                        users = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
                        active = db.execute("SELECT COUNT(DISTINCT user_id) AS total FROM sessions WHERE expires_at>?", (now_iso(),)).fetchone()["total"]
                        sessions = db.execute("SELECT COUNT(*) AS total FROM practices").fetchone()["total"]
                        if db.is_postgres:
                            decay_rows = db.execute("SELECT category,initial_score,risk_level,difficulty,(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-COALESCE(last_practiced,created_at)))/86400.0)::double precision AS elapsed_days FROM skills").fetchall()
                        else:
                            decay_rows = db.execute("SELECT category,initial_score,risk_level,difficulty,julianday('now')-julianday(COALESCE(last_practiced,created_at)) AS elapsed_days FROM skills").fetchall()
                    durations = RESPONSE_TIMES[-500:]
                    avg_ms = round(sum(durations) / len(durations), 2) if durations else 0
                    grouped = {}
                    for row in decay_rows:
                        rate = 0.018 + row["risk_level"] * 0.003 + row["difficulty"] * 0.0015
                        remaining = clamp(row["initial_score"] * exp(-rate * max(0, row["elapsed_days"] or 0)))
                        grouped.setdefault(row["category"], []).append(1 - remaining)
                    category_decay = [{"category": category, "decay": sum(values) / len(values)} for category, values in grouped.items()]
                    return self.send_json({"users_total": users, "active_users": active, "practice_sessions": sessions, "category_decay": category_decay, "avg_response_ms": avg_ms})
            if path.startswith("/api/"):
                raise ApiError("Route not found.", HTTPStatus.NOT_FOUND)
            return self.serve_static("index.html" if path == "/" else path.lstrip("/"))
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)
        except Exception:
            self.send_json({"error": "The server could not complete the request."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            data = self.body_json()
            if path == "/api/register":
                email = str(data.get("email", "")).strip().lower()
                password = str(data.get("password", ""))
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 10:
                    raise ApiError("Enter a valid email and a password of at least 10 characters.")
                try:
                    with connect_db() as db:
                        cursor = db.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?) RETURNING id", (email, password_hash(password), now_iso()))
                        user_id = cursor.fetchone()["id"]
                        db.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
                except Exception as error:
                    if is_integrity_error(error):
                        raise ApiError("An account with that email already exists.", HTTPStatus.CONFLICT) from None
                    raise
                return self.send_json({"ok": True}, HTTPStatus.CREATED, self.issue_session(user_id, bool(data.get("remember"))))
            if path == "/api/login":
                email = str(data.get("email", "")).strip().lower()
                with connect_db() as db:
                    user = db.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
                if not user or not password_matches(str(data.get("password", "")), user["password_hash"]):
                    raise ApiError("Email or password is incorrect.", HTTPStatus.UNAUTHORIZED)
                return self.send_json({"ok": True}, HTTPStatus.OK, self.issue_session(user["id"], bool(data.get("remember"))))
            if path == "/api/logout":
                cookie = self.headers.get("Cookie", "")
                token = next((part.strip().split("=", 1)[1] for part in cookie.split(";") if part.strip().startswith("session=")), None)
                if token:
                    with connect_db() as db:
                        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
                return self.send_json({"ok": True}, headers={"Set-Cookie": "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            if path == "/api/ai-test":
                user = self.current_user()
                if not user["is_admin"]:
                    raise ApiError("Administrator access required.", HTTPStatus.FORBIDDEN)
                return self.send_json(ai_service.test_connection(user["id"]))
            if path == "/api/admin/create":
                setup_key = os.environ.get("SKILLTRACKER_ADMIN_SETUP_KEY")
                if not setup_key or not hmac.compare_digest(self.headers.get("X-Admin-Setup-Key", ""), setup_key):
                    raise ApiError("Admin setup is disabled or the setup key is invalid.", HTTPStatus.FORBIDDEN)
                email, password = str(data.get("email", "")).strip().lower(), str(data.get("password", ""))
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 14:
                    raise ApiError("Admin account requires a valid email and a 14-character password.")
                with connect_db() as db:
                    count = db.execute("SELECT COUNT(*) AS total FROM users WHERE is_admin=TRUE").fetchone()["total"]
                    if count:
                        raise ApiError("An administrator already exists; setup is closed.", HTTPStatus.CONFLICT)
                    cursor = db.execute("INSERT INTO users(email,password_hash,is_admin,created_at) VALUES(?,?,?,?) RETURNING id", (email, password_hash(password), True, now_iso()))
                    user_id = cursor.fetchone()["id"]
                    db.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
                return self.send_json({"ok": True}, HTTPStatus.CREATED, self.issue_session(user_id, True))
            user = self.current_user()
            if path == "/api/skills":
                name = str(data.get("name", "")).strip()
                category = str(data.get("category", "Other"))
                suggested_category = suggest_skill_category(name)
                category_adjusted = bool(suggested_category and suggested_category != category and not data.get("category_override", False))
                if category_adjusted:
                    category = suggested_category
                language_code = str(data.get("language_code", "en-US"))
                description = str(data.get("description", "")).strip()[:1000]
                if not name or len(name) > 100 or category not in CATEGORIES:
                    raise ApiError("Provide a skill name (up to 100 characters) and a listed category.")
                if language_code not in ("en-US", "es-ES", "ja-JP", "fr-FR", "de-DE", "it-IT", "pt-PT", "ko-KR", "zh-CN", "ms-MY"):
                    raise ApiError("Select a supported language locale.")
                risk, difficulty = clamp(data.get("risk_level", 5), 1, 10), clamp(data.get("difficulty", 5), 1, 10)
                with connect_db() as db:
                    cursor = db.execute("INSERT INTO skills(user_id,name,category,description,risk_level,difficulty,language_code,marking_criteria,created_at) VALUES(?,?,?,?,?,?,?,?,?) RETURNING id", (user["id"], name, category, description, risk, difficulty, language_code, str(data.get("marking_criteria", "")).strip()[:4000], now_iso()))
                    skill_id = cursor.fetchone()["id"]
                return self.send_json({"id": skill_id, "category": category, "category_adjusted": category_adjusted}, HTTPStatus.CREATED)
            if path == "/api/practices":
                return self.create_practice(user, data)
            if path == "/api/settings":
                board = str(data.get("study_board", "")).strip()[:120]
                with connect_db() as db:
                    db.execute("INSERT INTO settings(user_id,study_board) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET study_board=excluded.study_board", (user["id"], board))
                return self.send_json({"ok": True})
            raise ApiError("Route not found.", HTTPStatus.NOT_FOUND)
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)
        except (ValueError, TypeError):
            self.send_json({"error": "Invalid request values."}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self.send_json({"error": "The server could not complete the request."}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self):
        try:
            user = self.current_user()
            match = re.fullmatch(r"/api/skills/(\d+)", urlparse(self.path).path)
            if not match:
                raise ApiError("Route not found.", HTTPStatus.NOT_FOUND)
            with connect_db() as db:
                cursor = db.execute("DELETE FROM skills WHERE id=? AND user_id=?", (int(match.group(1)), user["id"]))
            if cursor.rowcount == 0:
                raise ApiError("Skill not found.", HTTPStatus.NOT_FOUND)
            return self.send_json({"ok": True})
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)

    def serve_static(self, relative):
        requested = (STATIC_DIR / relative).resolve()
        if not requested.is_relative_to(STATIC_DIR.resolve()) or not requested.is_file():
            return self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        content_type = "text/html; charset=utf-8" if requested.suffix == ".html" else "text/css; charset=utf-8" if requested.suffix == ".css" else "text/javascript; charset=utf-8" if requested.suffix == ".js" else "application/octet-stream"
        content = requested.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)


def create_admin(email):
    password = getpass.getpass("Admin password (14+ characters): ")
    if len(password) < 14:
        raise SystemExit("Password must be at least 14 characters.")
    with connect_db() as db:
        cursor = db.execute("INSERT INTO users(email,password_hash,is_admin,created_at) VALUES(?,?,?,?) RETURNING id", (email.lower(), password_hash(password), True, now_iso()))
        user_id = cursor.fetchone()["id"]
        db.execute("INSERT INTO settings(user_id) VALUES(?)", (user_id,))
    print("Administrator account created.")


def main():
    parser = argparse.ArgumentParser(description="Skill Atrophy Prevention & Readiness Tracker")
    parser.add_argument("--create-admin", metavar="EMAIL", help="create the first local administrator account")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    initialize_db()
    if args.create_admin:
        create_admin(args.create_admin)
        return
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Readiness Tracker running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
