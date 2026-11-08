import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import logging
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
from database import connect_db, database_configuration, initialize_db, is_integrity_error, record_ai_diagnostic


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
SESSION_DAYS = 30
RESPONSE_TIMES = []
AI_LOGGER = logging.getLogger("masterify.ai")
CATEGORIES = ("Healthcare/Nursing", "Technical & Data", "Language", "Sciences & Math", "Humanities", "Engineering/Field", "Other")
SKILL_TYPES = ("Skills", "Studies", "Other")
ACADEMIC_CATEGORIES = {"Sciences & Math", "Humanities"}
MINIMUM_MEANINGFUL_WORDS = 12
RESPONSE_TIME_SCORE_LIMIT_SECONDS = 2 * 60 * 60
STUDY_RUBRIC = {
    "type": "Academic studies",
    "criteria": (
        ("Identify the relevant concept or question", ("concept", "question", "principle", "theory", "formula"), "Name the topic or concept that answers the question."),
        ("Explain reasoning using relevant evidence or working", ("because", "evidence", "example", "calculate", "working", "source"), "Show the reasoning and support it with evidence, an example, or calculation."),
        ("Connect the evidence to a clear conclusion", ("therefore", "conclude", "conclusion", "shows", "supports", "suggests"), "Explain how your evidence supports your conclusion."),
        ("Check assumptions, context, or limitations", ("assume", "assumption", "context", "limit", "compare", "however"), "Mention an assumption, context, limitation, or alternative interpretation."),
    ),
}
STRICT_GENERAL_RUBRIC = {
    "type": "Strict general practice",
    "criteria": (
        ("Answer the actual task with a relevant action or claim", (("i would", "i will", "we should", "first", "recommend", "prioritize", "decide"), ("identify", "describe", "explain", "solve", "show", "test", "compare", "calculate", "isolate", "assess", "choose")), "State a direct, task-relevant action or claim."),
        ("Explain the reasoning or method", (("because", "therefore", "so that", "reason", "method", "step"), ("cause", "risk", "result", "means", "supports", "leads", "if", "when")), "Explain why the action fits the evidence or what condition would change it."),
        ("Use specific, relevant detail or evidence", (("example", "evidence", "detail", "value", "source", "measurement", "observation", "calculation", "data", "logs", "symptom", "error", "test"),), "Include a concrete, relevant detail, observation, or piece of evidence."),
        ("Check the outcome, limitation, or next step", (("check", "verify", "confirm", "limit", "monitor", "review", "validate", "rollback", "escalate", "retest"), ("result", "outcome", "risk", "next", "if", "recovery", "change", "failure", "error", "regression")), "Explain how you would verify the outcome or what you would do next if it did not work."),
    ),
}
ASSESSMENT_RUBRICS = {
    "Healthcare/Nursing": {
        "type": "Practical skills",
        "criteria": (
            ("Identify the immediate concern or red flag", ("concern", "red flag", "symptom", "change", "unstable"), "Name the specific sign or change that determines your priority."),
            ("Describe safe first checks within your role", ("assess", "check", "verify", "vital", "allergy"), "State the first check you would perform and keep actions within your role."),
            ("Explain when and how you would escalate", ("escalate", "clinician", "supervisor", "emergency", "urgent"), "Say who you would contact and what finding would trigger escalation."),
            ("Close the loop through communication or documentation", ("handover", "communicate", "document", "report", "confirm"), "Explain how you would confirm the information was received and record the outcome."),
        ),
    },
    "Technical & Data": {
        "type": "Practical skills",
        "criteria": (
            ("Define the failure and gather evidence", ("failure", "error", "log", "evidence", "reproduce", "symptom"), "Describe the observable failure and the evidence you would collect first."),
            ("Isolate variables with a safe, testable check", ("isolate", "hypothesis", "test", "compare", "reproduce"), "Give one test that distinguishes between likely causes without increasing risk."),
            ("Validate the fix, including relevant edge cases", ("validate", "test", "edge", "regression", "verify"), "State how you would verify the fix and check at least one edge case."),
            ("Communicate risk and a rollback or next step", ("rollback", "revert", "communicate", "risk", "monitor"), "Name the signal that would make you stop, roll back, or escalate."),
        ),
    },
    "Language": {
        "type": "Practical skills",
        "criteria": (
            ("Respond directly to the other speaker", ("respond", "answer", "order", "ticket", "symptom", "request"), "Answer the other speaker's immediate question or request."),
            ("Use understandable target-language phrases", ("phrase", "sentence", "vocabulary", "language", "understand"), "Use a complete, understandable phrase in the selected practice language."),
            ("Ask a relevant follow-up question", ("question", "ask", "clarify", "which", "when", "where"), "Ask one focused question that obtains a missing detail."),
            ("Confirm the key detail or agreed next step", ("confirm", "repeat", "understood", "time", "departure", "order"), "Repeat or confirm the important detail before ending the exchange."),
        ),
    },
    "Sciences & Math": {
        "type": "Academic knowledge",
        "criteria": (
            ("Identify the relevant concept, rule, or relationship", ("concept", "formula", "equation", "relationship", "principle", "law"), "Name the rule or relationship that applies to this problem."),
            ("Show the reasoning or calculation in steps", ("because", "therefore", "calculate", "substitute", "derive", "step"), "Show how you move from the given information to the result."),
            ("Use evidence, quantities, and units appropriately", ("data", "evidence", "unit", "units", "value", "measurement"), "Connect your working to the provided evidence and include appropriate units."),
            ("Check assumptions, plausibility, or limitations", ("assume", "assumption", "check", "plausible", "error", "limit"), "Check whether the result is plausible and identify an assumption or likely error."),
        ),
    },
    "Humanities": {
        "type": "Academic knowledge",
        "criteria": (
            ("State a clear claim or interpretation", ("claim", "argue", "interpret", "conclude", "position"), "State your central claim or interpretation in a direct sentence."),
            ("Support the claim with relevant evidence", ("evidence", "source", "example", "quote", "data", "event"), "Use a relevant example or source as support, not just a general assertion."),
            ("Evaluate context, perspective, or a counterargument", ("perspective", "bias", "context", "counterargument", "however", "compare"), "Explain a source's perspective or address a reasonable alternative view."),
            ("Reach a reasoned conclusion linked to the evidence", ("because", "therefore", "conclusion", "conclude", "supports", "suggests"), "Explain how the evidence supports your final conclusion."),
        ),
    },
    "Engineering/Field": {
        "type": "Practical skills",
        "criteria": (
            ("Identify hazards and make the situation safe", ("hazard", "isolate", "safe", "safety", "risk", "protect"), "Name the immediate hazard and the step that makes the work area safer."),
            ("Follow a structured diagnostic or work sequence", ("inspect", "diagnose", "sequence", "measure", "check", "procedure"), "Describe the checks in a safe order before changing or repairing anything."),
            ("Use measurements, specifications, or standards", ("measure", "reading", "specification", "standard", "tolerance", "code"), "State which measurement or specification you would compare against."),
            ("Verify the result and communicate completion or escalation", ("verify", "test", "document", "report", "escalate", "handover"), "Explain how you would test the result and report any unresolved issue."),
        ),
    },
    "Other": {
        "type": "Practical skills",
        "criteria": (
            ("State the intended outcome", ("goal", "outcome", "aim", "result", "plan"), "Describe what a successful result should look or sound like."),
            ("Describe a relevant sequence of actions", ("first", "next", "then", "step", "sequence", "prepare"), "Set out the key actions in an order someone could follow."),
            ("Explain how you would check the result", ("check", "test", "review", "compare", "inspect", "listen"), "Name a concrete way to tell whether the result meets your goal."),
            ("Identify an adjustment if the check finds a problem", ("adjust", "correct", "change", "repeat", "improve", "fix"), "Say what you would change and try again if the result is not right."),
        ),
    },
}
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


def assessment_type_for(skill_type):
    if skill_type == "Studies":
        return "Academic studies"
    if skill_type == "Other":
        return "Other"
    return "Practical skills"


def skill_type_for(skill):
    return skill["skill_type"] if "skill_type" in skill.keys() else "Skills"


def skill_value(skill, key, default=""):
    return skill[key] if key in skill.keys() else default


def criteria_for_skill(skill, board=None):
    evaluation_mode = skill_value(skill, "evaluation_mode", "standard")
    if strict_mode_for_skill(skill, board):
        rubric = STRICT_GENERAL_RUBRIC
        label = "General AI standards" if evaluation_mode == "general" else f"External syllabus: {skill_value(skill, 'external_syllabus', 'Unspecified')}"
        return [
            {"criterion": name, "signal_groups": signal_groups, "improvement": improvement}
            for name, signal_groups, improvement in rubric["criteria"]
        ], f"Strict {rubric['type'].lower()} · {label}"
    skill_type = skill_type_for(skill)
    category = skill["category"]
    if skill_type == "Studies":
        rubric = ASSESSMENT_RUBRICS[category] if category in ACADEMIC_CATEGORIES else STUDY_RUBRIC
    elif skill_type == "Other":
        rubric = ASSESSMENT_RUBRICS["Other"]
    elif category in ACADEMIC_CATEGORIES:
        rubric = ASSESSMENT_RUBRICS["Other"]
    else:
        rubric = ASSESSMENT_RUBRICS.get(category, ASSESSMENT_RUBRICS["Other"])
    rubric_category = "Other" if skill_type == "Other" else category
    return [
        {"criterion": label, "signals": signals, "improvement": improvement, "require_all": False}
        for label, signals, improvement in rubric["criteria"]
    ], f"Built-in {rubric['type'].lower()} checklist for {rubric_category}"


def board_match_status_for_skill(skill, board=None):
    assessment_board = str(skill_value(skill, "assessment_board")).strip()
    if board is not None and assessment_board.casefold() != str(board).strip().casefold():
        return "unknown"
    return skill_value(skill, "board_match_status", "unknown")


def strict_mode_for_skill(skill, board=None):
    return (
        skill_value(skill, "evaluation_mode", "standard") in ("general", "external")
        and board_match_status_for_skill(skill, board) == "not_found"
        and bool(str(skill_value(skill, "assessment_board")).strip())
    )


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


def readiness_band_for_evaluation(evaluation):
    label = evaluation.get("readiness_level", "")
    if label in ("No demonstrated recall", "Insufficient evidence", "Start with guided practice"):
        return "critical"
    if label in ("Building recall", "Developing recall"):
        return "warning"
    if label in ("Nearly ready for varied practice", "Consistent recall"):
        return "optimal"
    return None


def readiness_level_for(skill, score=None, evaluation=None):
    readiness_score = readiness(skill) if score is None else float(score)
    risk = clamp(skill["risk_level"], 1, 10)
    difficulty = clamp(skill["difficulty"], 1, 10)
    adjustment = 1 - ((risk - 1) * 0.04) - ((difficulty - 1) * 0.03)
    estimated_level = round(readiness_score * adjustment / 10)
    if evaluation is None:
        return max(1, min(10, estimated_level))

    label = evaluation.get("readiness_level", "")
    coverage = clamp(evaluation.get("criterion_coverage", 0), 0, 1)
    if label == "No demonstrated recall":
        level, bounds = 1, (1, 3)
    elif label == "Insufficient evidence":
        level, bounds = 2, (1, 3)
    elif label == "Start with guided practice":
        level, bounds = 3, (1, 3)
    elif label == "Building recall":
        level, bounds = 4, (4, 6)
    elif label == "Developing recall":
        level, bounds = (5 if coverage < 0.625 else 6), (4, 6)
    elif label == "Nearly ready for varied practice":
        level, bounds = 7 + round((coverage - 0.75) * 8), (7, 10)
    elif label == "Consistent recall":
        level, bounds = 10, (7, 10)
    else:
        return max(1, min(10, estimated_level))

    risk_adjustment = round(((5 - risk) + (5 - difficulty)) / 5)
    return max(bounds[0], min(bounds[1], level + risk_adjustment))


def persist_ai_diagnostic(diagnostic):
    try:
        record_ai_diagnostic(diagnostic)
    except Exception:
        AI_LOGGER.warning("[Gemini] Diagnostic persistence failed task=%s", diagnostic.get("task", "unknown"))


def failed_deterministic_evaluation_diagnostic(model):
    return {
        "task": "feedback",
        "success": False,
        "error_category": "application_error",
        "http_status": None,
        "model": model,
        "latency_ms": 0,
        "fallback_used": False,
        "safe_message": "Masterify's deterministic evaluator raised an application error.",
    }


def status_for(score):
    return "critical" if score <= 3 else "warning" if score <= 6 else "optimal"


def is_external_board_topic(skill, board):
    return bool(board and strict_mode_for_skill(skill, board))


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
    criteria, _ = criteria_for_skill(skill, board)
    evaluation_mode = skill_value(skill, "evaluation_mode", "standard") if strict_mode_for_skill(skill, board) else "standard"
    return {
        "skill_name": skill["name"],
        "skill_description": skill["description"],
        "skill_type": skill_type_for(skill),
        "category": skill["category"],
        "subject_or_topic": skill["name"],
        "assessment_board": skill_value(skill, "assessment_board"),
        "board_match_status": board_match_status_for_skill(skill, board),
        "board_match_note": skill_value(skill, "board_match_note"),
        "evaluation_mode": evaluation_mode,
        "external_syllabus": skill_value(skill, "external_syllabus"),
        "study_board": board,
        "marking_criteria": "\n".join(item["criterion"] for item in criteria),
        "difficulty": skill["difficulty"],
        "scenario_type": kind,
        "language_code": skill["language_code"],
        "external_board_topic": is_external_board_topic(skill, board),
        "standard_context": "Strict general practice standards" if is_external_board_topic(skill, board) else board or "General practice",
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
        text += f" This skill was classified outside {board}; apply strict general practice standards appropriate to the skill type and domain, without claiming board alignment."
    elif board:
        text += f" Use the level and terminology expected for {board}."
    if strict_mode_for_skill(skill) and skill_value(skill, "evaluation_mode") == "external":
        text += f" The learner selected {skill_value(skill, 'external_syllabus')} as an external syllabus. Treat this as unverified context, not an official mark scheme."
    elif strict_mode_for_skill(skill) and skill_value(skill, "evaluation_mode") == "general":
        text += " Use strict general standards because this skill is outside the learner's selected study board."
    return {"prompt": text, "input_mode": "speech" if kind == "language" else "text", "scenario_type": kind, "language_code": skill["language_code"]}


def evaluate(skill, response, elapsed_seconds, board):
    text = response.strip()
    lower = text.lower()
    criteria, basis = criteria_for_skill(skill, board)
    strict_mode = strict_mode_for_skill(skill, board)
    evaluation_mode = skill_value(skill, "evaluation_mode", "standard") if strict_mode else "standard"

    def meaningful_word_count(value):
        excluded = {"a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from", "i", "in", "is", "it", "of", "on", "or", "the", "this", "to", "was", "with", "would"}
        return sum(word not in excluded for word in re.findall(r"[\w'-]+", value))

    def criterion_is_signaled(item, value):
        signals = item.get("signals", ())
        if "signal_groups" in item:
            return all(
                any(re.search(rf"\b{re.escape(signal)}\b", value) for signal in group)
                for group in item["signal_groups"]
            )
        if item.get("require_all"):
            return bool(signals) and all(re.search(rf"\b{re.escape(signal)}\b", value) for signal in signals)
        return any(re.search(rf"\b{re.escape(signal)}\b", value) for signal in signals)

    refusal_match = re.search(
        r"\b(?:i\s+(?:have\s+)?(?:(?:absolutely|genuinely|really|honestly|literally|"
        r"completely|simply|truly|remotely)\s+){0,3}(?:no idea|no clue)|no idea|no clue|"
        r"i\s+(?:do not|don['’]t)\s+know|i\s+(?:am not sure|(?:'m|am)\s+unsure)|"
        r"i\s+(?:cannot|can['’]t)\s+answer|i\s+give\s+up)\b",
        lower,
    )
    grading_text = lower
    no_answer = False
    if refusal_match:
        continuation = re.search(
            r"\b(?:but|however|instead|although)\b(.+)$",
            lower[refusal_match.end():],
        )
        attempted_text = continuation.group(1).strip() if continuation else ""
        attempted_substance = (
            meaningful_word_count(attempted_text) >= MINIMUM_MEANINGFUL_WORDS
            and any(criterion_is_signaled(item, attempted_text) for item in criteria)
        )
        if attempted_substance:
            grading_text = attempted_text
        else:
            no_answer = True
    insufficient_substance = meaningful_word_count(grading_text) < MINIMUM_MEANINGFUL_WORDS
    criterion_feedback = []
    for item in criteria:
        is_met = criterion_is_signaled(item, grading_text)
        if insufficient_substance or no_answer:
            is_met = False
        criterion_feedback.append({
            "criterion": item["criterion"],
            "met": bool(is_met),
            "improvement": item["improvement"],
        })
    matched = [item["criterion"] for item in criterion_feedback if item["met"]]
    missing = [item for item in criterion_feedback if not item["met"]]
    criterion_total = len(criteria)
    criterion_score = len(matched) / criterion_total
    accuracy = criterion_score
    deductions = [{"criterion": item["criterion"], "deduction": round(80 / criterion_total, 1)} for item in missing]
    speed = clamp(1 - elapsed_seconds / RESPONSE_TIME_SCORE_LIMIT_SECONDS)
    score = round((accuracy * 0.8 + speed * 0.2) * 100, 1)
    if speed < 1:
        deductions.append({"criterion": "Response-time component", "deduction": round((1 - speed) * 20, 1)})
    coverage = len(matched) / criterion_total
    if no_answer:
        readiness_level = "No demonstrated recall"
        readiness_explanation = "The response says you do not know the answer and does not provide an attempted solution. Review the skill, then explain a relevant action, reason, or piece of evidence in your own words."
    elif insufficient_substance:
        readiness_level = "Insufficient evidence"
        readiness_explanation = "This response is too brief to demonstrate task-specific reasoning, detail, and checks. Time contributes separately to the response-time component but cannot replace missing content."
    elif coverage == 1:
        readiness_level = "Consistent recall"
        readiness_explanation = "Every checklist item was clearly signaled in this response. Test recall again with a different scenario; this is practice evidence, not proof of competence."
    elif coverage >= 0.75:
        readiness_level = "Nearly ready for varied practice"
        readiness_explanation = "Most checklist items were signaled. Review the item still missing, then try a less familiar scenario."
    elif coverage >= 0.5:
        readiness_level = "Developing recall"
        readiness_explanation = "Some important checklist items were signaled, but gaps remain. Review the missing items and practise explaining them in your own words."
    elif coverage > 0:
        readiness_level = "Building recall"
        readiness_explanation = "A starting point is present, but several checklist items were not clearly demonstrated. Use the feedback below to guide a focused review."
    else:
        readiness_level = "Start with guided practice"
        readiness_explanation = "The checklist items were not clearly signaled in this response. Review the skill steps, then try again one item at a time."
    strengths = [f"Clearly signaled: {item['criterion']}." for item in criterion_feedback if item["met"]]
    improvements = [f"{item['criterion']}: {item['improvement']}" for item in missing]
    if not strengths:
        strengths.append("You made a practice attempt; use the missing-item checklist to structure your next response.")
    if not improvements:
        improvements.append("Try a new scenario and explain why each step is appropriate, not just what you would do.")
    external_topic = is_external_board_topic(skill, board)
    if evaluation_mode == "external":
        selected_standard = f"{skill_value(skill, 'external_syllabus')} (AI knowledge, unverified)"
    elif evaluation_mode == "general":
        selected_standard = "Strict general AI standards"
    else:
        selected_standard = skill_value(skill, "assessment_board") or board or "General practice"
    alignment = {
        "selected_standard": selected_standard,
        "basis": basis,
        "criteria_total": criterion_total,
        "criteria_met": len(matched),
        "official_scheme_verified": False,
        "note": (
            "Gemini uses general knowledge only; no official syllabus source is connected or verified. Content criteria contribute 80% and response time contributes 20%; time cannot substitute for a substantive answer."
            if evaluation_mode in ("general", "external") else
            "This topic is treated as external to the selected school board; no official board mark scheme is embedded or fetched. "
            "Checklist matching uses explicit words and is only a practice aid."
            if external_topic else
            "Checklist matching is based on explicit words and is only a practice aid; it does not judge meaning or verify an official mark scheme."
            if basis.startswith("Built-in") else
            "User-entered criteria are checked for explicit wording. This is not an official mark scheme or a judgment of full meaning."
        )
    }
    return {
        "score": score,
        "elapsed_seconds": elapsed_seconds,
        "accuracy": round(accuracy * 100, 1),
        "speed": round(speed * 100, 1),
        "strict_mode": strict_mode,
        "category": skill["category"],
        "skill_type": skill_type_for(skill),
        "assessment_type": assessment_type_for(skill_type_for(skill)),
        "evaluation_mode": evaluation_mode,
        "assessment_board": skill_value(skill, "assessment_board"),
        "board_match_status": board_match_status_for_skill(skill, board),
        "board_match_note": skill_value(skill, "board_match_note"),
        "external_syllabus": skill_value(skill, "external_syllabus"),
        "readiness_level": readiness_level,
        "readiness_band": readiness_band_for_evaluation({"readiness_level": readiness_level}),
        "readiness_explanation": readiness_explanation,
        "criterion_coverage": coverage,
        "criterion_feedback": criterion_feedback,
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
                skill = self.skill_for_user(db, skill_id, user["id"])
                previous = db.execute("SELECT evaluation_json,readiness_after FROM practices WHERE user_id=? AND submission_id=?", (user["id"], submission_id)).fetchone()
                if previous:
                    previous_evaluation = json.loads(previous["evaluation_json"])
                    return self.send_json({
                        "evaluation": previous_evaluation,
                        "readiness": previous["readiness_after"],
                        "readiness_level": readiness_level_for(skill, previous["readiness_after"], previous_evaluation),
                        "readiness_band": readiness_band_for_evaluation(previous_evaluation) or status_for(readiness_level_for(skill, previous["readiness_after"], previous_evaluation)),
                        "duplicate": True,
                    }, HTTPStatus.OK)
                setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                board = setting["study_board"] if setting else ""

            try:
                evaluation = evaluate(skill, response, elapsed, board)
            except Exception:
                persist_ai_diagnostic(failed_deterministic_evaluation_diagnostic(ai_service.model))
                AI_LOGGER.warning("[Gemini] Practice evaluation stopped category=application_error")
                raise
            official_score = evaluation["score"]
            context = {
                "skill_name": skill["name"],
                "skill_description": skill["description"],
                "skill_type": skill_type_for(skill),
                "category": skill["category"],
                "assessment_board": skill_value(skill, "assessment_board"),
                "board_match_status": evaluation["board_match_status"],
                "board_match_note": skill_value(skill, "board_match_note"),
                "evaluation_mode": evaluation["evaluation_mode"],
                "external_syllabus": skill_value(skill, "external_syllabus"),
                "assessment_type": evaluation["assessment_type"],
                "study_board": board,
                "standard_context": evaluation["alignment"]["selected_standard"],
                "marking_criteria": "\n".join(item["criterion"] for item in evaluation["criterion_feedback"]),
                "criterion_feedback": evaluation["criterion_feedback"],
                "scenario": scenario[:1800],
                "user_response": response[:12000],
            }
            evaluation, feedback_source, diagnostic = ai_service.enhance_evaluation(user["id"], context, evaluation)
            persist_ai_diagnostic(diagnostic)
            if evaluation["score"] != official_score:
                evaluation["score"] = official_score
            evaluation["ai_provider"] = feedback_source
            if feedback_source != "gemini":
                evaluation["ai_notice"] = FALLBACK_NOTICE

            practiced_at = now_iso()
            with connect_db() as db:
                db.execute("UPDATE skills SET accuracy_score=?,speed_score=?,last_practiced=? WHERE id=? AND user_id=?", (evaluation["accuracy"] / 100, evaluation["speed"] / 100, practiced_at, skill_id, user["id"]))
                updated = self.skill_for_user(db, skill_id, user["id"])
                new_score = readiness(updated)
                new_level = readiness_level_for(updated, new_score, evaluation)
                evaluation["readiness_rating"] = new_level
                db.execute("INSERT INTO practices(user_id,skill_id,scenario,response,evaluation_json,readiness_score,readiness_after,submission_id,elapsed_seconds,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (user["id"], skill_id, scenario, response, json.dumps(evaluation), official_score, new_score, submission_id, elapsed, practiced_at))
            return self.send_json({
                "evaluation": evaluation,
                "readiness": new_score,
                "readiness_level": new_level,
                "readiness_band": readiness_band_for_evaluation(evaluation) or status_for(new_level),
                "duplicate": False,
            }, HTTPStatus.CREATED)

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
                user = self.current_user()
                if not user["is_admin"]:
                    raise ApiError("Administrator access required.", HTTPStatus.FORBIDDEN)
                return self.send_json({"configured": ai_service.configured, "model": ai_service.model, "provider": "gemini", "connectivity": "not_tested"})
            if path == "/api/me":
                user = self.current_user(False)
                return self.send_json({"user": dict(user) if user else None})
            if path == "/api/dashboard":
                user = self.current_user()
                with connect_db() as db:
                    setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                    rows = db.execute("SELECT * FROM skills WHERE user_id=? ORDER BY LOWER(name)", (user["id"],)).fetchall()
                    attempts = db.execute(
                        """
                        SELECT latest.skill_id,latest.id AS latest_practice_id,
                               summary.attempt_count,latest.evaluation_json
                        FROM practices latest
                        JOIN (
                            SELECT skill_id,MAX(id) AS latest_id,COUNT(*) AS attempt_count
                            FROM practices
                            WHERE user_id=?
                            GROUP BY skill_id
                        ) summary ON summary.latest_id=latest.id
                        WHERE latest.user_id=?
                        """,
                        (user["id"], user["id"]),
                    ).fetchall()
                    attempts_by_skill = {row["skill_id"]: row for row in attempts}
                    skills = []
                    for row in rows:
                        score = readiness(row)
                        latest_attempt = attempts_by_skill.get(row["id"])
                        latest_evaluation = json.loads(latest_attempt["evaluation_json"]) if latest_attempt else None
                        level = readiness_level_for(row, score, latest_evaluation)
                        skills.append(dict(row) | {
                            "readiness": level,
                            "readiness_level": level,
                            "readiness_score": score,
                            "attempt_count": latest_attempt["attempt_count"] if latest_attempt else 0,
                            "latest_practice_id": latest_attempt["latest_practice_id"] if latest_attempt else None,
                            "status": status_for(level),
                        })
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
                scenario, provider, diagnostic = ai_service.generate_scenario(user["id"], context, lambda: scenario_for(skill, board, recent_scenarios))
                persist_ai_diagnostic(diagnostic)
                return self.send_json({"skill": dict(skill), "scenario": scenario, "ai_provider": provider})
            match = re.fullmatch(r"/api/practices/(\d+)", path)
            if match:
                user = self.current_user()
                with connect_db() as db:
                    row = db.execute("SELECT p.*,s.name AS skill_name,s.risk_level,s.difficulty FROM practices p JOIN skills s ON s.id=p.skill_id WHERE p.id=? AND p.user_id=?", (match.group(1), user["id"])).fetchone()
                if row is None:
                    raise ApiError("Practice not found.", HTTPStatus.NOT_FOUND)
                evaluation = json.loads(row["evaluation_json"])
                readiness_level = readiness_level_for(row, row["readiness_after"], evaluation)
                return self.send_json(dict(row) | {
                    "readiness_level": readiness_level,
                    "readiness_band": readiness_band_for_evaluation(evaluation) or status_for(readiness_level),
                    "evaluation": evaluation,
                })
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
                if path == "/api/admin/ai-diagnostics":
                    with connect_db() as db:
                        summary = db.execute("""
                            SELECT
                                COUNT(*) AS total_attempts,
                                COALESCE(SUM(CASE WHEN task='feedback' AND success=TRUE THEN 1 ELSE 0 END), 0) AS ai_evaluations,
                                COALESCE(SUM(CASE WHEN task='feedback' AND fallback_used=TRUE THEN 1 ELSE 0 END), 0) AS fallback_evaluations,
                                COALESCE(SUM(CASE WHEN success=FALSE THEN 1 ELSE 0 END), 0) AS failed_attempts
                            FROM ai_diagnostics
                        """).fetchone()
                        recent = db.execute("SELECT created_at,task,success,error_category,http_status,model,latency_ms,fallback_used,safe_message FROM ai_diagnostics ORDER BY created_at DESC,id DESC LIMIT 50").fetchall()
                        latest_test = db.execute("SELECT created_at,success,error_category,http_status,model,latency_ms,fallback_used,safe_message FROM ai_diagnostics WHERE task='connectivity_test' ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()
                        latest_evaluation = db.execute("SELECT created_at,success,error_category,http_status,model,latency_ms,fallback_used,safe_message FROM ai_diagnostics WHERE task='feedback' ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()
                        latest_success = db.execute("SELECT created_at,task,model,latency_ms FROM ai_diagnostics WHERE success=TRUE ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()
                        latest_failure = db.execute("SELECT created_at,task,error_category,http_status,model,latency_ms,fallback_used,safe_message FROM ai_diagnostics WHERE success=FALSE ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()
                    latest_event = recent[0] if recent else None
                    if latest_event is None:
                        provider_status = "unknown"
                    else:
                        provider_status = "healthy" if latest_event["success"] else "failing"
                    return self.send_json({
                        "provider": "gemini",
                        "configured": ai_service.configured,
                        "model": ai_service.model,
                        "provider_status": provider_status,
                        "latest_test": dict(latest_test) if latest_test else None,
                        "latest_evaluation": dict(latest_evaluation) if latest_evaluation else None,
                        "latest_success": dict(latest_success) if latest_success else None,
                        "latest_failure": dict(latest_failure) if latest_failure else None,
                        "counts": dict(summary),
                        "recent_events": [dict(row) for row in recent],
                    })
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
                study_board = str(data.get("study_board", "")).strip()
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 10:
                    raise ApiError("Enter a valid email and a password of at least 10 characters.")
                if len(study_board) > 120:
                    raise ApiError("Study board / syllabus must be 120 characters or fewer.")
                try:
                    with connect_db() as db:
                        cursor = db.execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?) RETURNING id", (email, password_hash(password), now_iso()))
                        user_id = cursor.fetchone()["id"]
                        db.execute("INSERT INTO settings(user_id,study_board) VALUES(?,?)", (user_id, study_board))
                except Exception as error:
                    if is_integrity_error(error):
                        raise ApiError("An account with that email already exists.", HTTPStatus.CONFLICT) from None
                    raise
                return self.send_json({"ok": True}, HTTPStatus.CREATED, self.issue_session(user_id, bool(data.get("remember"))))
            if path == "/api/login":
                email = str(data.get("email", "")).strip().lower()
                study_board = str(data.get("study_board", "")).strip()
                if len(study_board) > 120:
                    raise ApiError("Study board / syllabus must be 120 characters or fewer.")
                with connect_db() as db:
                    user = db.execute("SELECT id,password_hash FROM users WHERE email=?", (email,)).fetchone()
                if not user or not password_matches(str(data.get("password", "")), user["password_hash"]):
                    raise ApiError("Email or password is incorrect.", HTTPStatus.UNAUTHORIZED)
                if study_board:
                    with connect_db() as db:
                        db.execute("INSERT INTO settings(user_id,study_board) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET study_board=excluded.study_board", (user["id"], study_board))
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
                result = ai_service.test_connection(user["id"])
                persist_ai_diagnostic({**result, "task": "connectivity_test"})
                return self.send_json(result)
            if path == "/api/skill-board-check":
                user = self.current_user()
                name = str(data.get("name", "")).strip()
                skill_type = str(data.get("skill_type", "Skills"))
                category = str(data.get("category", "Other"))
                if not name or len(name) > 100 or skill_type not in SKILL_TYPES or category not in CATEGORIES:
                    raise ApiError("Provide a skill name, a listed type, and a listed category.")
                with connect_db() as db:
                    setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                board = setting["study_board"] if setting else ""
                if not board:
                    result = {
                        "status": "uncertain",
                        "explanation": "No study board is selected, so this skill cannot be matched to a syllabus. Standard category criteria will be used.",
                        "external_syllabus_suggestions": [],
                        "study_board": "",
                    }
                else:
                    result = ai_service.classify_skill_board(user["id"], {
                        "skill_name": name,
                        "skill_type": skill_type,
                        "category": category,
                        "study_board": board,
                    })
                    result["study_board"] = board
                return self.send_json(result)
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
                skill_type = str(data.get("skill_type", "Skills"))
                category = str(data.get("category", "Other"))
                suggested_category = suggest_skill_category(name)
                category_adjusted = bool(suggested_category and suggested_category != category and not data.get("category_override", False))
                if category_adjusted:
                    category = suggested_category
                language_code = str(data.get("language_code", "en-US"))
                description = str(data.get("description", "")).strip()[:1000]
                if not name or len(name) > 100 or skill_type not in SKILL_TYPES or category not in CATEGORIES:
                    raise ApiError("Provide a skill name, a listed type, and a listed category.")
                requested_mode = str(data.get("evaluation_mode", "standard"))
                if requested_mode not in ("standard", "general", "external"):
                    raise ApiError("The selected evaluation mode is invalid.")
                external_syllabus = str(data.get("external_syllabus", "")).strip()
                if len(external_syllabus) > 120:
                    raise ApiError("The external syllabus name is too long.")
                if language_code not in ("en-US", "es-ES", "ja-JP", "fr-FR", "de-DE", "it-IT", "pt-PT", "ko-KR", "zh-CN", "ms-MY"):
                    raise ApiError("Select a supported language locale.")
                risk, difficulty = clamp(data.get("risk_level", 5), 1, 10), clamp(data.get("difficulty", 5), 1, 10)
                with connect_db() as db:
                    setting = db.execute("SELECT study_board FROM settings WHERE user_id=?", (user["id"],)).fetchone()
                    board = setting["study_board"] if setting else ""
                if board:
                    board_check = ai_service.classify_skill_board(user["id"], {
                        "skill_name": name,
                        "skill_type": skill_type,
                        "category": category,
                        "study_board": board,
                    })
                else:
                    board_check = {
                        "status": "uncertain",
                        "explanation": "No study board is selected, so this skill cannot be matched to a syllabus. Standard category criteria will be used.",
                        "external_syllabus_suggestions": [],
                    }
                board_match_status = board_check["status"]
                board_match_note = board_check["explanation"]
                if board_match_status == "not_found":
                    if requested_mode not in ("general", "external"):
                        raise ApiError("Choose general standards or an external syllabus for this unmatched skill.")
                    if requested_mode == "external" and not external_syllabus:
                        raise ApiError("Enter the name of the external syllabus (up to 120 characters).")
                    evaluation_mode = requested_mode
                else:
                    evaluation_mode = "standard"
                with connect_db() as db:
                    cursor = db.execute(
                        "INSERT INTO skills(user_id,name,skill_type,category,description,risk_level,difficulty,language_code,marking_criteria,assessment_board,board_match_status,board_match_note,evaluation_mode,external_syllabus,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                        (
                            user["id"], name, skill_type, category, description, risk, difficulty, language_code, "",
                            board, board_match_status, board_match_note, evaluation_mode,
                            external_syllabus if evaluation_mode == "external" else "", now_iso(),
                        ),
                    )
                    skill_id = cursor.fetchone()["id"]
                return self.send_json({
                    "id": skill_id,
                    "skill_type": skill_type,
                    "category": category,
                    "category_adjusted": category_adjusted,
                    "evaluation_mode": evaluation_mode,
                    "board_match_status": board_match_status,
                }, HTTPStatus.CREATED)
            if path == "/api/practices":
                return self.create_practice(user, data)
            if path == "/api/settings":
                board = str(data.get("study_board", "")).strip()
                if len(board) > 120:
                    raise ApiError("Study board / syllabus must be 120 characters or fewer.")
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
