# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current phase:** Phase 1 — Inventory MVP.
- **Progress:** Steps 1–4 complete — skeleton (`a9de3bc`), database
  (`c9e72ae`), auth & RBAC (`9aa5966`), inventory ledger service
  (**56/56 tests passing**, uncommitted at time of writing).
  **Awaiting user approval before Step 5** (supplier & ingredient admin APIs).
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
- **Phase 1 Step 3** (uncommitted at time of writing — commit after user review):
  - `app/auth/`: `passwords.py` (Argon2), `tokens.py` (DB stores SHA-256 of
    tokens only), `sessions.py` (12 h TTL, revoke on logout), `csrf.py`
    (signed double-submit token bound to the session token hash — D25),
    `rate_limit.py` (in-memory per-process sliding window — D26),
    `dependencies.py` (`get_session_and_user` is THE single auth entry point;
    the future native-mobile Bearer-token path goes inside it, D7).
  - `app/errors.py` — `ApiError` → `{"error": {code, message}}`, handler
    registered in `main.py`.
  - Routes: `auth.py` (login/logout/me) and `admin_users.py`
    (admin-only user list/create, CSRF-protected, audit-logged).
  - `app/cli.py` — `python -m app.cli create-admin`: getpass hidden entry,
    strength validation, never prints/logs passwords.
  - Config additions: `session_ttl_hours`, `secure_cookies`,
    `login_rate_limit_attempts`, `login_rate_limit_window_seconds`.
  - Cookie contract: `stockmate_session` (HttpOnly, SameSite=Lax, Secure
    when configured) + `stockmate_csrf` (readable — frontend echoes it in
    `X-CSRF-Token`; `frontend/js/api.js` `post()` does this automatically).
  - Login/logout are CSRF-exempt by design; every other mutating endpoint
    must include `dependencies=[Depends(csrf_protect)]`.
  - Tests: `test_auth.py` (19 tests) — **37/37 passing**; live curl smoke
    test verified login → me → staff-403 → logout → 401.
  - Gotcha: SQLite returns naive datetimes — `sessions._as_utc` normalizes
    before comparing expiries. Reuse that helper if you compare stored
    datetimes.
- **Phase 1 Step 4** (uncommitted at time of writing — commit after user review):
  - `app/services/inventory.py` — THE ledger. All stock changes go through
    `record_stock_entry` / `correct_transaction` / `repair_ledger` only.
    Never update `current_quantity` anywhere else.
  - `app/database.py` — engine `begin` event issues `BEGIN IMMEDIATE` and
    the DBAPI connection is set to autocommit (`isolation_level = None`) so
    SQLAlchemy controls transactions. Don't bypass this when creating engines.
  - Concurrency model (D15): BEGIN IMMEDIATE serializes all writers on
    SQLite; the conditional UPDATE's WHERE guard is defense-in-depth;
    bounded retries (3×, 50/100/150 ms) handle residual lock errors.
  - Corrections: append-only; delta = intended − recorded (recomputed from
    the original mode and its historical balance); prevented twice via the
    `supersedes_id` lookup inside the same write transaction; staff may
    correct only their own entries, admins any.
  - `large_adjustment_threshold` setting (default 500) triggers
    `confirmation_required` until the client re-submits `confirmed=true`.
  - API quantities are serialized as strings — parse with Decimal, never
    float, when adding client or server code.
  - `tests/test_inventory.py` gotcha: each role needs its OWN TestClient
    (shared clients share the cookie jar — see `make_client` fixture).
  - Test count: 56/56 passing.

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

- Approval to commit Phase 1 Step 4 and start Step 5 (supplier & ingredient
  admin APIs).

## Recommended next task

**Phase 1, Step 5** per `docs/PROJECT_STATUS.md`: supplier CRUD +
archive/reactivate with guards, ingredient admin endpoints with primary +
alternative supplier assignment (D19/D24), audit logging.

## Git

- `e53898d` — "Finalize StockMate Phase 0 architecture" (docs).
- `a9de3bc` — "Add StockMate backend foundation and health checks" (Step 1).
- `c9e72ae` — "Add database models, migrations, and demo seed data" (Step 2).
- `9aa5966` — "Add authentication, sessions, and role-based access control" (Step 3).
- Phase 1 Step 4 files are uncommitted, awaiting the user's commit approval.
- Suggested message: "Add inventory ledger with dual stock entry and corrections".
- **Do not push or merge without explicit user approval.**
