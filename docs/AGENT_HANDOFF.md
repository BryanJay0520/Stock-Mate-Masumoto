# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current phase:** Phase 1 — Inventory MVP.
- **Progress:** Step 1 (project skeleton) **complete and tested**.
  **Awaiting user approval before Step 2** (database models + migrations + seed).
- Development happens **incrementally, one step at a time** — never implement
  multiple Phase 1 steps without explicit user approval.

## Environment (important!)

- **Development runs in WSL2 (Ubuntu), not native Windows.** Windows Smart
  App Control (enforce mode) blocks pip-installed native Python packages on
  the Windows side, so the Windows `.venv` was removed. Do not recreate it.
- Python 3.12 venv: `~/.venvs/stockmate` (created with `--without-pip` +
  get-pip.py because `python3.12-venv` is not installed and sudo needs a
  password).
- Code lives at `/mnt/c/Users/bryan/Projects/StockMate` (Windows workspace,
  edited in VS Code as usual).
- Run tests: `cd backend && ~/.venvs/stockmate/bin/python -m pytest tests/ -v`
  (from a WSL shell).
- Run the app: `~/.venvs/stockmate/bin/python -m uvicorn app.main:app --reload`
  from `backend/`; open http://127.0.0.1:8000/ and `/api/v1/health`.
- When running commands through Git Bash on Windows, wrap them:
  `wsl.exe -- bash -lc "<command>"`.

## Read these before doing anything

1. `docs/ARCHITECTURE.md` — full architecture, database schema, business logic.
2. `docs/DECISIONS.md` — binding decisions D1–D24. Supersede, never silently
   change. (Renumbered during the Phase 0 revision — cite current text.)
3. `docs/PROJECT_STATUS.md` — roadmap + the ordered Phase 1 checklist with
   per-step status.

## Completed so far

- **Phase 0** (commit `e53898d` "Finalize StockMate Phase 0 architecture"):
  all planning docs, including the three final requirements:
  - D15 — SQLite-safe concurrency (`BEGIN IMMEDIATE` + atomic conditional
    UPDATE with rowcount check + bounded retries; no `FOR UPDATE` reliance).
  - D23 — all starting quantities (incl. demo seed stock) are
    `opening_balance` ledger entries; the ledger always reconstructs the cache.
  - D24 — composite FK `ingredients(primary_supplier_id, id)` →
    `supplier_ingredients(supplier_id, ingredient_id)`; service validates
    supplier `active` status. SQLite runs with `PRAGMA foreign_keys=ON`.
- **Phase 1 Step 1** (uncommitted at time of writing — commit after user review):
  - `backend/app/`: `config.py` (pydantic-settings, `STOCKMATE_` env prefix),
    `database.py` (engine/session factories, WAL + busy timeout + FK pragmas,
    `get_db`), `main.py` (app factory; API under `/api/v1`, static frontend at
    `/`), `api/v1/routes/health.py`, empty packages `models/`, `schemas/`,
    `services/`, `auth/`, `scheduler/`.
  - `backend/tests/`: `conftest.py` (per-test temp SQLite DB via
    `create_app(settings)`), `test_health.py` — **4/4 passing**.
  - `backend/requirements.txt` (every dependency documented),
    `backend/.env.example`.
  - `frontend/`: `index.html` placeholder, `css/base.css` (design tokens:
    navy sidebar, blue primary, green/yellow/red/gray indicators),
    `js/api.js` fetch wrapper.
  - `.gitignore`: local SQLite files (`*.db`, `*.db-wal`, `*.db-shm`).
  - README: WSL setup/run/test instructions.
  - Live smoke test passed (health endpoint + frontend over uvicorn).

## Key rules for all agents

- One step at a time; wait for user approval between steps and phases.
- All business logic server-side (`backend/app/services/`); frontend is a thin
  client; the API stays client-agnostic (mobile-ready, D1).
- Stock changes only via the immutable ledger (D2) through the atomic write
  protocol (ARCHITECTURE.md §5.1); entry modes stay distinct (D3).
- The only cached balance is `ingredient_stock_state.current_quantity` (D14).
- Decimal quantities only (D4); UTC + configurable business tz/day boundary (D5/D18).
- Idempotency keys on stock mutations and delivery confirmations (D6).
- Never commit secrets; demo data must be labeled fictional (D23 covers its
  opening balances).
- After each completed task: update `docs/PROJECT_STATUS.md` and this file.

## Pending decisions (waiting on user)

- Approval to commit Phase 1 Step 1 and start Step 2 (database foundation).

## Recommended next task

**Phase 1, Step 2** per `docs/PROJECT_STATUS.md`: SQLAlchemy models for all
Phase 1 tables (with the D24 composite FK), Alembic init + first migration,
seed script with fictional demo data recorded as `opening_balance`
transactions (D23).

## Git

- `e53898d` — "Finalize StockMate Phase 0 architecture" (docs).
- Phase 1 Step 1 files are staged/untracked, awaiting the user's commit approval.
- Suggested message: "feat: add Phase 1 Step 1 backend skeleton with health endpoint".
- **Do not push or merge without explicit user approval.**
