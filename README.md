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

- Python 3.9 or newer
- No third-party Python packages
- A modern browser; speech input depends on browser support and microphone permission

## Start locally

```powershell
cd "C:\Users\Jy Wong\my-new-project"
python main.py
```

Open <http://127.0.0.1:8000>. The SQLite database is created as `skilltracker.sqlite3` on first start. Set `SKILLTRACKER_DB` to use a different database path, or set `HOST` and `PORT` when deploying behind a trusted HTTPS reverse proxy.

New visitors can create an account. Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes, and each account's skills, preferences, and practice history are scoped to its own user ID.

Skill entry detects clear category cues (for example, Piano is suggested as Other). Ambiguous or cross-disciplinary names are left to the user, and manually selected categories are preserved.

## Administrator account

Create the first local administrator from the project folder:

```powershell
python main.py --create-admin admin@example.com
```

The command prompts for a 14-character minimum password without echoing it. The admin view is accessible only to an authenticated account with the administrator flag; it exposes aggregate counts, average category decay, and in-memory average response time, not emails, responses, or other account-level records.

## Practice and evaluation boundaries

Scenarios are varied, category-aware prompts generated locally; language-category skills enable browser speech recognition. Responses are evaluated with a deterministic, transparent heuristic. Add one marking criterion per line to a skill to check whether those phrases appear in a response. The selected board/standard is context only: this app does not fetch official syllabus documents, validate clinical actions, or claim official exam-board marks. Do not use its scores as safety clearance, certification, or professional advice.

## Optional Gemini enhancement

Gemini is optional. Without a key, Masterify uses its deterministic scenario and evaluation paths. To enable cloud-generated scenarios and qualitative feedback, set `GEMINI_API_KEY` as a **secret environment variable** in Render (never commit it or put it in frontend settings). `GEMINI_MODEL` defaults to `gemini-2.5-flash-lite`; set it to a model available to your Google AI Studio key to change models. Masterify does not switch to another or paid model. Google free-tier model availability and quotas can change independently of this app.

Create a key in [Google AI Studio](https://aistudio.google.com/apikey), then add it to Render under **Dashboard → your Web Service → Environment** as `GEMINI_API_KEY`. For local PowerShell use `$env:GEMINI_API_KEY = "your-key"` in the server terminal before starting `python main.py`; never paste the key into source files or frontend settings. This application does not enable billing; check the Google project/key's billing status and current free-tier quotas in Google AI Studio.

The server applies request limits of 6 Gemini requests per user per minute, 80 per user per day, and 30 total per minute by default; adjust with `GEMINI_MAX_REQUESTS_PER_USER_PER_MINUTE`, `GEMINI_MAX_REQUESTS_PER_USER_PER_DAY`, and `GEMINI_MAX_REQUESTS_PER_MINUTE` if needed. `GEMINI_TIMEOUT_SECONDS` defaults to 12. API errors, quota limits, malformed JSON, and timeouts fall back to deterministic behavior. A practice submission ID makes retries idempotent, so a completed submission is not sent to Gemini again. Gemini can only add qualitative guidance; deterministic Masterify score, criteria deductions, and readiness remain authoritative.

`GET /api/ai-status` reports whether a key is configured and explicitly marks connectivity as `not_tested`; it does not claim the key or model works. An authenticated administrator can `POST /api/ai-test` to make one minimal live Gemini request. The response contains only success, HTTP status, model, error type, and a sanitized diagnostic; the test uses the configured model and consumes a Gemini request/quota. Ordinary users cannot call this diagnostic route.

When Gemini is enabled, skill context and the submitted answer are sent to Google's Gemini API for generation or qualitative feedback. The existing practice history remains stored by Masterify. Do not enter personal or sensitive information into skill descriptions or practice answers.

During an open practice, the response is drafted to browser local storage every three seconds and the backend receives a keep-alive request every four minutes. Drafts are device/browser-specific and are removed after successful submission.

## Deployment notes

For a Render Python Web Service, use these settings:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python main.py --host 0.0.0.0 --port $PORT`

The requirements file is intentionally dependency-free; it satisfies the build command without installing packages. This is a compact single-process development server, not a hardened internet-facing deployment. For public hosting, place it behind HTTPS, restrict registration as appropriate, back up the SQLite file, and use a production WSGI server. Render's default filesystem is ephemeral, so SQLite data can be lost when the service restarts or redeploys; use a persistent disk or an external database for data you need to keep. Do not expose the admin setup command or database file to public users.
