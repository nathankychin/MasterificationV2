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

## Administrator account

Create the first local administrator from the project folder:

```powershell
python main.py --create-admin admin@example.com
```

The command prompts for a 14-character minimum password without echoing it. The admin view is accessible only to an authenticated account with the administrator flag; it exposes aggregate counts, average category decay, and in-memory average response time, not emails, responses, or other account-level records.

## Practice and evaluation boundaries

Scenarios are varied, category-aware prompts generated locally; language-category skills enable browser speech recognition. Responses are evaluated with a deterministic, transparent heuristic. Add one marking criterion per line to a skill to check whether those phrases appear in a response. The selected board/standard is context only: this app does not fetch official syllabus documents, validate clinical actions, or claim official exam-board marks. Do not use its scores as safety clearance, certification, or professional advice.

During an open practice, the response is drafted to browser local storage every three seconds and the backend receives a keep-alive request every four minutes. Drafts are device/browser-specific and are removed after successful submission.

## Deployment notes

For a Render Python Web Service, use these settings:

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python main.py --host 0.0.0.0 --port $PORT`

The requirements file is intentionally dependency-free; it satisfies the build command without installing packages. This is a compact single-process development server, not a hardened internet-facing deployment. For public hosting, place it behind HTTPS, restrict registration as appropriate, back up the SQLite file, and use a production WSGI server. Render's default filesystem is ephemeral, so SQLite data can be lost when the service restarts or redeploys; use a persistent disk or an external database for data you need to keep. Do not expose the admin setup command or database file to public users.
