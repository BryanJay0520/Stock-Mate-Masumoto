# StockMate

Smart restaurant inventory management and supply planning system.
Web-based, mobile-ready, designed initially for a ramen restaurant.

🚧 **Work in Progress — Phase 2 Steps 1–3 complete; forecasting has not started**

The responsive web UI includes login, dashboard, inventory, package-aware
daily stock entry, supplier and ingredient management, purchase orders and
delivery receiving, transaction history with corrections, and account
settings. Forecasting and notifications arrive in later phases.

## What it does (planned)

- Track ingredient stock with two entry modes: **remaining stock** (reconciliation) and **used stock** (consumption)
- Supplier-specific ordering schedules with cutoff/deadline detection
- Purchase orders with delivery confirmation (incoming vs. actual stock)
- Demand forecasting and a 7/14/30-day stock forecast calendar (green/yellow/red/gray)
- Stock Confidence Indicator (how trustworthy the current numbers are)
- Scheduled reminders (daily stock count, order deadlines, deliveries, shortages)
- Multi-channel notifications (in-app first; browser push, email, LINE later)
- Role-based access: **Administrator** and **Staff**

## Counting full packages and loose stock

In Ingredient Management, an administrator can enable a counting package,
enter its name (such as Box or Tray), and set its size in the ingredient's
existing stock unit. Stock Entry then lets staff enter full packages plus
loose units, or a direct total in stock units. Count everything in opened or
partially filled packages as loose units; do not include the contents of full
packages twice. The preview shows the converted total before submission.
Remaining Stock reconciles the physical count, while Used Stock deducts the
converted amount. Both use the same canonical inventory ledger and retain
the original count breakdown in history. Supplier purchasing package sizes
are configured separately through the admin API for future recommendations.

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

### Reset fictional development data

Stop the local StockMate server first. This command accepts only the exact
`backend/stockmate.db` path in the default `development` environment, with no
database URL override or `.env` file. It verifies the fictional seed markers,
demo catalog, accounts, migration head, and foreign keys, then creates a
timestamped checked backup in `backend/backups/`. It replaces operational
demo records in one transaction while preserving the schema, migrations,
users, login sessions, units, and unrelated application settings. It will
refuse an unknown database or unexpected catalog record.

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.reset_demo \
  --database /mnt/c/Users/bryan/Projects/StockMate/backend/stockmate.db \
  --confirm RESET_FICTIONAL_STOCKMATE_DATA
```

The reseeded Korean Noodles use `pc` with 60 pieces per Box and 180 pieces
of opening stock. Chinese Noodles use 50 pieces per Box, Eggs 30 pieces per
Tray, Soup Base 10 packs per Case, and Chashu 20 pieces per Pack. These sizes
are editable. Supplier purchase packages are set separately on each demo
supplier link. Opening stock is recorded through `opening_balance` ledger rows.

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

Real browser tests (headless Chrome/Edge via the DevTools Protocol, zero
dependencies, Windows Node >= 22). Requires a FRESHLY seeded backend on
port 8765 — the suite mutates data, so reseed before every run:

```bash
# In WSL, use a fresh isolated temporary database:
cd /mnt/c/Users/bryan/Projects/StockMate/backend
test_dir=$(mktemp -d /tmp/stockmate-browsertest-XXXXXX)
STOCKMATE_DATABASE_URL="sqlite:///$test_dir/demo.db" ~/.venvs/stockmate/bin/python -m app.seed
STOCKMATE_DATABASE_URL="sqlite:///$test_dir/demo.db" ~/.venvs/stockmate/bin/python -m uvicorn app.main:app --port 8765

# In a Windows terminal (repo root), while the server runs:
node frontend/tests/browser_smoke.mjs
```

Screenshots are saved to a temp directory whose path is printed at the end.

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
