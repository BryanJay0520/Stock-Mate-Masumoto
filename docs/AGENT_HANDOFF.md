# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current phase:** Phase 1 — Inventory MVP.
- **Progress:** Step 1 (skeleton, commit `a9de3bc`) and Step 2 (database
  models + Alembic migration + idempotent fictional seed) **complete and
  tested (18/18)**. **Awaiting user approval before Step 3** (auth & RBAC).
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
- **Phase 1 Step 1** (commit `a9de3bc`): backend skeleton — `config.py`
  (env settings), `database.py` (engine/session factories, WAL + busy
  timeout + `foreign_keys=ON` pragmas, `get_db`), `main.py` (app factory,
  `/api/v1` + static frontend), health endpoint, `requirements.txt`,
  `.env.example`, frontend placeholder shell, pytest infrastructure.
- **Phase 1 Step 2** (uncommitted at time of writing — commit after user review):
  - `app/models/`: `user.py`, `session.py` (UserSession), `unit.py`,
    `supplier.py` (Supplier + SupplierIngredient junction), `ingredient.py`
    (Ingredient catalog + IngredientStockState), `inventory.py`
    (InventoryTransaction ledger), `audit.py`, `settings.py`, `mixins.py`.
    Ingredient has NO stock column; the only cached balance is
    `ingredient_stock_state.current_quantity` (D14).
  - **Gotcha:** the composite FK on `ingredients.id` disables SQLAlchemy's
    implicit autoincrement detection — `Ingredient.id` therefore declares
    `autoincrement=True` explicitly. Don't remove it.
  - Circular FK (`fk_ingredients_primary_supplier`) uses `use_alter=True` in
    the model and is added in the migration via `batch_alter_table` AFTER
    both tables exist (valid on SQLite and PostgreSQL, D24).
  - `alembic.ini` (no DB URL inside), `alembic/env.py`,
    `versions/0001_initial_schema.py`; `app/db_admin.py::run_migrations` is
    the single programmatic migration entry point (CLI, seed, tests).
  - `app/seed.py` — idempotent via `app_settings` marker; demo login
    admin/`demo-admin-password`, staff/`demo-staff-password` (dev only);
    opening quantities as `opening_balance` transactions with deterministic
    idempotency keys (D23); Korean Noodles demonstrates primary (S1) +
    alternative (S2) suppliers.
  - Tests: `test_schema.py`, `test_seed.py` added; conftest now migrates a
    temp DB per test (`run_migrations`) and offers `client` (empty DB) and
    `seeded_client` fixtures. **18/18 passing.**
  - A drift-guard test (`test_migration_matches_models`) fails the suite if
    models and migrations ever diverge — keep both in sync when changing
    schema.

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

- Approval to commit Phase 1 Step 2 and start Step 3 (auth & RBAC).

## Recommended next task

**Phase 1, Step 3** per `docs/PROJECT_STATUS.md`: authentication & RBAC —
login/logout/me endpoints, server-side sessions, HttpOnly cookies, CSRF
token, `require_staff` / `require_admin` dependencies.

## Git

- `e53898d` — "Finalize StockMate Phase 0 architecture" (docs).
- `a9de3bc` — "Add StockMate backend foundation and health checks" (Step 1).
- Phase 1 Step 2 files are uncommitted, awaiting the user's commit approval.
- Suggested message: "Add database models, migrations, and demo seed data".
- **Do not push or merge without explicit user approval.**
