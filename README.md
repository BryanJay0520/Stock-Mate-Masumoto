# StockMate

Smart restaurant inventory management and supply planning system.
Web-based, mobile-ready, designed initially for a ramen restaurant.

🚧 **Work in Progress — Phase 1 (Inventory MVP), Steps 1–6 complete**

The responsive web UI is live: login, dashboard, inventory, dual-mode daily
stock entry, supplier & ingredient admin, transaction history with
corrections, and account settings. Forecasting, purchase orders, and
notifications arrive in later phases.

## What it does (planned)

- Track ingredient stock with two entry modes: **remaining stock** (reconciliation) and **used stock** (consumption)
- Supplier-specific ordering schedules with cutoff/deadline detection
- Purchase orders with delivery confirmation (incoming vs. actual stock)
- Demand forecasting and a 7/14/30-day stock forecast calendar (green/yellow/red/gray)
- Stock Confidence Indicator (how trustworthy the current numbers are)
- Scheduled reminders (daily stock count, order deadlines, deliveries, shortages)
- Multi-channel notifications (in-app first; browser push, email, LINE later)
- Role-based access: **Administrator** and **Staff**

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Pydantic, Alembic, SQLite (dev) → PostgreSQL (prod)
- **Frontend:** HTML5, CSS3, vanilla JavaScript, responsive mobile-first (served by FastAPI)
- **Scheduler:** APScheduler (in-process, DB-persisted jobs)
- **Tests:** pytest
- **Mobile readiness:** versioned REST API (`/api/v1`), all business logic server-side; PWA and Capacitor packaging planned for later phases

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture, database schema, business logic
- [docs/DECISIONS.md](docs/DECISIONS.md) — architectural and business-rule decisions
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — current phase, completed and pending work
- [docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md) — handoff notes for coding agents

## Setup

**Development runs in WSL (Windows Subsystem for Linux).** Windows Smart App
Control on this machine blocks pip-installed native Python packages, so the
backend virtual environment lives in WSL while the code stays in the Windows
workspace (VS Code edits it normally; use the "WSL" extension for terminals).

Prerequisites: WSL2 with Ubuntu, Python 3.12 (already installed).

One-time environment setup (in a WSL terminal):

```bash
python3 -m venv --without-pip ~/.venvs/stockmate   # python3.12-venv workaround
curl -sS https://bootstrap.pypa.io/get-pip.py | ~/.venvs/stockmate/bin/python -
~/.venvs/stockmate/bin/python -m pip install -r /mnt/c/Users/bryan/Projects/StockMate/backend/requirements.txt
```

Run the application (from a WSL terminal, e.g. VS Code's WSL terminal):

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.seed                # one-time: migrate + fictional demo data
~/.venvs/stockmate/bin/python -m uvicorn app.main:app --reload
```

Then open:

- App (frontend shell): http://127.0.0.1:8000/
- Health check: http://127.0.0.1:8000/api/v1/health
- Interactive API docs: http://127.0.0.1:8000/docs

Run the tests:

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m pytest tests/ -v
```

Frontend unit tests (quantity math helpers, no dependencies — uses the
built-in Node.js test runner, run from the repo root in Windows or WSL):

```bash
node --test "frontend/tests/*.test.js"
```

## Authentication (added Step 3)

Login flow: `POST /api/v1/auth/login` with username + password → the server
sets an **HttpOnly session cookie** and a readable **CSRF cookie**, and
returns the user object. All mutating API calls must send the CSRF cookie's
value back in the `X-CSRF-Token` header (the `SM.api` helper in
`frontend/js/api.js` does this automatically). `POST /api/v1/auth/logout`
revokes the session server-side.

Demo accounts (dev only): `admin` / `demo-admin-password`,
`staff` / `demo-staff-password`.

Create a real administrator (no default credentials exist):

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.cli create-admin --username yourname
```

The password is typed hidden, strength-checked, and never printed or logged.

## Production security checklist

The development setup is intentionally convenient and **must never reach
production unchanged**. Before any public deployment (tracked in Phase 6):

- **Demo accounts** — `admin`/`demo-admin-password` and `staff`/`demo-staff-password`
  exist ONLY for local development. Never seed them into a production database;
  create real accounts via `python -m app.cli create-admin`.
- **Development secret key** — the default `secret_key` in `app/config.py` is
  publicly known. Production must set `STOCKMATE_SECRET_KEY` to a long random value.
- **HTTPS** — set `STOCKMATE_SECURE_COOKIES=true` so session cookies carry the
  `Secure` flag, and serve the app behind HTTPS only.
- **Login rate limiter** — currently per-process (decision D26). It must move
  to a shared store if more than one backend worker ever runs.
- **XSS surface** — the frontend must keep rendering API data via
  `textContent`/escaping only (never `innerHTML`); CSP headers must be
  configured at deployment.

## Data disclaimer

All seed/demo data is explicitly fictional and must never be treated as real restaurant inventory.
