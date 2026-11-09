# StockMate

Smart restaurant inventory management and supply planning system.
Web-based, mobile-ready, designed initially for a ramen restaurant.

🚧 **Work in Progress — Phase 0 (Planning & Architecture)**

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

## Data disclaimer

All seed/demo data is explicitly fictional and must never be treated as real restaurant inventory.
