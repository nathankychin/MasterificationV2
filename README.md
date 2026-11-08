# Skill Atrophy Prevention & Readiness Tracker

A private, SQLite-backed readiness tracker for high-stakes skills. It combines elapsed-time decay with recent applied accuracy and speed, then supports scenario practice, saved evaluations, and per-account history.

## Product context

Rarely used skills can lose applied fluency without an obvious signal. Static flashcard recall may not reveal whether someone can retrieve and apply a procedure accurately under pressure. In healthcare and field work that can affect safety; in technical and academic work it can delay delivery and degrade performance. The tracker makes readiness trends visible and prompts scenario-based retrieval, while scores remain practice signals rather than competence guarantees.

The scenarios and risk model are designed for distinct contexts:

- **Healthcare and nursing students:** clinical knowledge may be revisited often in theory but practiced infrequently under realistic time pressure. Short triage and handover scenarios prompt recall of assessment, escalation, and communication steps.
- **Technical and data professionals:** incident response, debugging, and analytical workflows can fade between projects. Production-style prompts rehearse safe diagnosis, evidence gathering, validation, and rollback reasoning.
- **Second-language maintainers and exchange alumni:** conversational fluency can fade without regular spontaneous use after immersion ends. Local scenarios recreate practical exchanges, with browser speech input for spoken retrieval.
- **Certified engineers and field technicians:** rarely used procedures and safety checks may be needed unexpectedly on site. Field and diagnostic situations reinforce structured verification and escalation before action.

## Requirements

- Python 3.10 or newer
- `psycopg[binary]` is installed from `requirements.txt` for Neon/PostgreSQL connections
- A modern browser; speech input depends on browser support and microphone permission

## Start locally

```powershell
cd "C:\Users\Jy Wong\my-new-project"
python main.py
```

Open <http://127.0.0.1:8000>. With no `DATABASE_URL`, local development uses SQLite at `skilltracker.sqlite3` beside `main.py`; `SKILLTRACKER_DB` can override that local path. To use a development PostgreSQL database instead, set `DATABASE_URL` in the server process environment before starting Masterify. Do not put a real connection string in source control.

New visitors can create an account and choose an exam board or syllabus on the sign-in/sign-up screen; an existing account can change it there or under Settings. Leaving the sign-in field blank preserves the saved selection. Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes, and each account's skills, preferences, and practice history are scoped to its own user ID.

When adding an item, choose a **Practice type** (Practical skills, Academic knowledge, or Other practice) and a separate **Subject / domain** (such as Healthcare/Nursing, Sciences & Math, or Humanities). Clear subject cues suggest a category and can suggest Academic knowledge for academic topics; both selectors remain editable. Gemini checks the skill against the selected board after you enter it. A likely match or uncertain result uses the regular category checklist. A clear mismatch reveals a choice of strict general AI standards or an external syllabus name; strict mode only applies to a skill explicitly classified as outside the selected board. With no board selected, the result is uncertain and strict mode is not activated. Saving a skill displays a progress indicator and disables repeat submission until the board check and save finish. These are AI knowledge-based suggestions, not official syllabus lookups. Existing records default to Practical skills during migration.

## Administrator account

Create the first local administrator from the project folder:

```powershell
python main.py --create-admin admin@example.com
```

The command prompts for a 14-character minimum password without echoing it. The admin view is accessible only to an authenticated account with the administrator flag; it exposes aggregate counts, average category decay, and in-memory average response time, not emails, responses, or other account-level records.

## Practice and evaluation boundaries

Scenarios are varied, category-aware prompts generated locally; language-category skills enable browser speech recognition. Checklists differ by type and subject: Practical skills focus on procedures, decisions, checks, and safety where relevant; Academic knowledge focuses on concepts, reasoning, evidence, and conclusions; Other practice focuses on goals, process, quality checks, and adjustments. There is no separate user-entered marking-criteria box; automatic category checklists are used instead. Content criteria contribute 80% of every attempt assessment and response time contributes 20%. Timing credit decreases linearly from full credit at zero elapsed time to zero at two hours; attempts taking longer than two hours receive no timing credit. For a skill Gemini clearly classifies outside the selected board, the strict general rubric checks a direct task-relevant action, reasoning, specific evidence, and an outcome check or next step. Responses under 12 meaningful words receive no content signals in any category; response time is still scored separately. Explicit non-answer phrases such as “I have no idea” also receive no content credit unless followed by a substantive attempt. If the skill matches the board or the check is uncertain, the regular category checklist is used. Choosing an external syllabus only gives Gemini an unverified syllabus name as context; the app does not fetch or check official materials. Practice results show a qualitative readiness level and explain what the checklist signaled, what was missing, how response time contributes, and how to improve. Deterministic checks match explicit words and do not determine whether an answer is factually or professionally correct. Gemini, when available, adds answer-specific qualitative feedback but cannot change the tracked evidence. The dashboard shows a risk- and recall-difficulty-adjusted readiness level from 1–10, not an open-ended answer grade. New skills offer **Practice**; after the first attempt, the dashboard offers **View** to open the latest attempt, its evidence, and guidance before starting another practice. The internal retention estimate remains separate from the displayed readiness level. The app does not validate clinical actions or claim official exam-board marks. Do not use feedback as safety clearance, certification, or professional advice.

## Optional Gemini enhancement

Gemini is optional. Without a key, Masterify uses its deterministic scenario and evaluation paths. To enable cloud-generated scenarios and qualitative feedback, set `GEMINI_API_KEY` as a **secret environment variable** in Render (never commit it or put it in frontend settings). `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. The unavailable `gemini-2.5-flash-lite` setting is mapped to this replacement; other explicit model settings are used as configured. Confirm the selected model's availability and current quotas for your Google AI Studio key.

Create a key in [Google AI Studio](https://aistudio.google.com/apikey), then add it to Render under **Dashboard → your Web Service → Environment** as `GEMINI_API_KEY`. For local PowerShell use `$env:GEMINI_API_KEY = "your-key"` in the server terminal before starting `python main.py`; never paste the key into source files or frontend settings. This application does not enable billing; check the Google project/key's billing status and current free-tier quotas in Google AI Studio.

The server applies request limits of 6 Gemini requests per user per minute, 80 per user per day, and 30 total per minute by default; adjust with `GEMINI_MAX_REQUESTS_PER_USER_PER_MINUTE`, `GEMINI_MAX_REQUESTS_PER_USER_PER_DAY`, and `GEMINI_MAX_REQUESTS_PER_MINUTE` if needed. `GEMINI_TIMEOUT_SECONDS` defaults to 30 and can be set from 3 to 60 seconds. API errors, quota limits, malformed JSON, and timeouts fall back to deterministic behavior. A practice submission ID makes retries idempotent, so a completed submission is not sent to Gemini again. Gemini can only add qualitative guidance; deterministic Masterify score, criteria deductions, and readiness remain authoritative.

`GET /api/ai-status` reports whether a key is configured and explicitly marks connectivity as `not_tested`; it does not claim the key or model works. An authenticated administrator can `POST /api/ai-test` to make one minimal live Gemini request. The response contains only success, HTTP status, model, error type, and a sanitized diagnostic; the test uses the configured model and consumes a Gemini request/quota. Ordinary users cannot call this diagnostic route.

When Gemini is enabled, skill context and the submitted answer are sent to Google's Gemini API for generation or qualitative feedback. The existing practice history remains stored by Masterify. Do not enter personal or sensitive information into skill descriptions or practice answers.

## Neon PostgreSQL

Production uses PostgreSQL when `DATABASE_URL` is set. Create a Neon project and database, and use its pooled connection string with SSL enabled. Locally, set `DATABASE_URL` in your shell; if it is unset, SQLite remains the local development fallback. On Render, `DATABASE_URL` is required: the server refuses to silently create a SQLite database there.

The app applies versioned PostgreSQL schema migrations at startup. To migrate an existing SQLite database, first make a separate backup and provision an empty Neon database. A dry run only reads and reports the source:

```powershell
python migrate_sqlite_to_postgres.py --sqlite "C:\path\to\skilltracker.sqlite3"
```

After verifying the source counts and setting `DATABASE_URL` to the dedicated, empty Neon target, apply explicitly:

```powershell
python migrate_sqlite_to_postgres.py --sqlite "C:\path\to\skilltracker.sqlite3" --apply --confirm-empty-target
```

The importer preserves IDs, password hashes, skills, settings, practice responses, evaluations, and readiness values; it converts timestamps to UTC-aware PostgreSQL timestamps and intentionally does not migrate sessions. It refuses a target containing application rows and never deletes or edits the SQLite source. Retain the SQLite backup until production verification is complete.

PostgreSQL integration tests are opt-in. Point `MASTERIFY_TEST_DATABASE_URL` at a separate disposable PostgreSQL database, not production. Tests create and drop a uniquely named temporary schema. If that variable is absent, those tests are reported as skipped.

During an open practice, the response is drafted to browser local storage every three seconds and the backend receives a keep-alive request every four minutes. Drafts are device/browser-specific and are removed after successful submission.

## Deployment notes

For a Render Python Web Service, use these settings:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python main.py --host 0.0.0.0 --port $PORT`

Add `DATABASE_URL` as a secret environment variable in Render, using the Neon pooled SSL connection string. Do not configure `SKILLTRACKER_DB` for production; Render without `DATABASE_URL` fails closed instead of falling back to ephemeral SQLite. No persistent Render disk is required for PostgreSQL data. This is a compact single-process development HTTP server, not a hardened internet-facing deployment. Do not expose the admin setup command or database credentials.
