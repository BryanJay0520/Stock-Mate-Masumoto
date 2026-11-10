# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current phase:** Phase 1 — Inventory MVP (functionally complete).
- **Progress:** Steps 1–7 complete — skeleton (`a9de3bc`), database
  (`c9e72ae`), auth & RBAC (`9aa5966`), inventory ledger (`8283161`),
  supplier/ingredient admin (`d57b34f`), frontend dashboard (`c1fa23e`),
  integration testing & stabilization (**92 backend + 5 Node unit +
  24/24 real browser tests passing**, uncommitted at time of writing).
  **Awaiting user approval to commit Step 7; Phase 2 has NOT started.**
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
2. `docs/DECISIONS.md` — binding decisions D1–D31. Supersede, never silently
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
- **Phase 1 Step 5** (uncommitted at time of writing — commit after user review):
  - **Safeguards first** (`tests/test_safeguards.py`, 6 tests; decisions
    D27/D28/D29): corrections are rejected with 409
    `correction_after_reconciliation` when a NEWER physical reconciliation
    exists for that ingredient; idempotency-key reuse with a different
    payload → 409 `idempotency_conflict` (semantic comparison via
    `_entry_matches` in `app/services/inventory.py`, checked on both the
    pre-check and unique-constraint race paths); read-only requests use
    deferred `BEGIN` while write paths use `BEGIN IMMEDIATE`, driven by a
    thread-local write-intent flag in `app/database.py`
    (`set_write_intent` / `_begin_write_transaction`).
    **Gotcha:** SQLAlchemy 2.1 made `Session.execution_options` a property,
    so the flag is thread-local, not session options. The flag is cleared in
    `_run_atomic`'s `finally` and on every early return path.
  - `app/schemas/catalog.py`, `app/services/catalog.py`,
    `app/api/v1/routes/suppliers.py`, `app/api/v1/routes/ingredients.py`:
    supplier CRUD/archive/reactivate with the archive guard (409
    `archive_blocked` when primary of an ACTIVE ingredient); ingredient CRUD
    with primary + alternative suppliers; `opening_quantity` becomes an
    `opening_balance` ledger entry atomically (D23); audit rows on every
    mutation. Staff have read-only access; mutations are admin + CSRF.
  - **Gotchas:**
    - Junction updates must be ordered add-links → set `primary_supplier_id`
      → remove-links inside one transaction (`_sync_junction`, D30) or the
      composite FK (D24) fails.
    - `expire_on_commit=False`: call `db.refresh(obj)` after `_run_atomic`
      when response serialization reads relationship collections (D31).
    - `Ingredient.unit` relationship (`lazy="joined"`) exists for the unit
      abbreviation in API responses — ORM-only, no migration needed.
    - The catalog service reuses the PRIVATE helpers `_begin_write_transaction`
      / `_run_atomic` from `app.services.inventory` on purpose — if those
      change, update both modules.
    - Error body shape is `{"error": {"code", "message"}}`.
    - `POST /inventory/entries` returns 200 (also for fresh entries) — no 201.
  - `tests/test_catalog.py` — 20 tests. **82/82 passing.**
- **Phase 1 Step 6** (uncommitted at time of writing — commit after user review):
  - Full responsive SPA in `frontend/`: `index.html` shell (sidebar, topbar,
    `#view`, toasts, modals), `css/base.css` (tokens per spec: bg #F7F9FC,
    sidebar #102B4E, primary #2563EB), `css/app.css` (layout + components,
    responsive tables→cards <720px, collapsible sidebar <900px).
  - `js/api.js` (`SM.api` + `SM.ApiError{status,code,message}`, CSRF header
    on mutations, 401 → `SM.app.showLogin`), `js/ui.js` (safe DOM via
    textContent only; `toMillis`/`fromMillis` integer-thousandths math for
    previews; modal/confirmDialog/toast), `js/auth.js` (in-memory user only),
    `js/data.js` (shared catalog loader + availability vs confidence badges),
    `js/app.js` (hash router `#/dashboard` etc., role-aware shell),
    `js/pages/{login,dashboard,inventory,entry,suppliers,ingredients,history,settings}.js`.
  - Entry page contract: fresh idempotency key per logical submission
    (`crypto.randomUUID` with fallback), SAME key on retry and on the
    confirmed re-submit after `confirmation_required`; button locked while
    pending. Corrections in history reuse that key generator
    (`SM.pages.entry.newKey()`).
  - **Gotchas:**
    - `GET /admin/users` returns `{"users": [...]}`, not a bare list.
    - `GET /health` fields are camelCase (`businessTimezone`).
    - `POST /inventory/entries` returns 200 (not 201); success body is
      `{transaction, idempotent_replay}`.
    - Server checks negative stock BEFORE large-adjustment confirmation.
    - New read-only endpoint `GET /api/v1/units` feeds the ingredient form.
    - `IngredientUpdate.primary_supplier_id = null` means "no change" —
      the ingredient form omits it when nothing is selected.
  - Tests: `backend/tests/test_frontend.py` (10 API-level integration tests
    driving the exact UI request flows + static assets), `frontend/tests/
    ui.test.js` (5 Node unit tests). **92 backend + 5 Node passing.**
    Run Node tests from repo root: `node --test "frontend/tests/*.test.js"`
    (Windows Node v24 works; WSL has NO node).
    **No browser/DOM automation exists — do not claim rendered-UI coverage.**
  - All JS passes `node --check`; live uvicorn smoke test (login, stock,
    units, assets) passed.
- **Phase 1 Step 7** (uncommitted at time of writing — commit after user review):
  - `frontend/tests/browser_smoke.mjs` — REAL browser testing, 24 tests,
    zero dependencies: headless Chrome via DevTools Protocol using Node's
    built-in fetch + WebSocket (Node >= 22). Requires Windows Node +
    Chrome/Edge (set `CHROME_PATH`) and a FRESHLY seeded backend on :8765
    (`STOCKMATE_TEST_URL` to override). Reseed before every run — the suite
    mutates data. Screenshots go to a temp dir (printed at the end).
  - Two REAL bugs found by rendered testing and fixed:
    1. DOM `.append(array)` stringifies arrays ("[object HTMLElement]") —
       broke the entry preview and the ingredient category dropdown.
       Fixed with spread in `entry.js` and `ingredients.js`. Never pass an
       array to `.append()`; `SM.ui.el` children DO flatten, `.append` does NOT.
    2. `.btn { display:inline-flex }` beat `.menu-button { display:none }`
       at equal specificity — fixed with `.topbar .menu-button` scoping.
  - Browser-test gotchas: Runtime.evaluate shares the page's global scope
    (wrap multi-statement bodies in an IIFE; add `return` when the value is
    used); page titles update BEFORE `#view` rerenders, so wait on
    page-specific CONTENT, not titles or row counts.
  - Security scans confirmed: no localStorage/sessionStorage, no innerHTML
    with API data, no credentials in frontend app code.
  - Full production readiness review is in `docs/PROJECT_STATUS.md`
    ("Production readiness review" section) — demo creds, dev secret, HTTPS
    cookies, per-process rate limit, backups, structured logging, and
    security headers are all Phase 6 items. NOT deployment-ready.
  - Totals: 92 backend + 5 Node unit + 24 browser = 121 checks passing.

## Suggested review areas for the next agent (OpenAI Codex)

- `backend/app/services/inventory.py` — ledger atomicity, D27/D28 semantics.
- `backend/app/services/catalog.py` — junction sync order (D30), archive guards.
- `frontend/js/pages/entry.js` — idempotency-key lifecycle and submit locking.
- `frontend/tests/browser_smoke.mjs` — extend scenarios when new pages arrive.
- Production-readiness gaps listed in `docs/PROJECT_STATUS.md` (Phase 6 scope).

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

- Approval to commit Phase 1 Step 7 (integration testing & stabilization).
- Approval to start Phase 2 (supplier schedules, purchase orders) — the
  Phase 1 checklist item 8 is docs-only and already maintained.

## Recommended next task

**Phase 2** per `docs/ARCHITECTURE.md`: supplier schedule rules, delivery
calendars, cutoff engine, purchase orders, delivery confirmation.

## Git

- `e53898d` — "Finalize StockMate Phase 0 architecture" (docs).
- `a9de3bc` — "Add StockMate backend foundation and health checks" (Step 1).
- `c9e72ae` — "Add database models, migrations, and demo seed data" (Step 2).
- `9aa5966` — "Add authentication, sessions, and role-based access control" (Step 3).
- `8283161` — "Add inventory ledger with dual stock entry and corrections" (Step 4).
- `d57b34f` — "Add supplier and ingredient administration APIs" (Step 5).
- `c1fa23e` — "Add responsive frontend dashboard and stock entry UI" (Step 6).
- Phase 1 Step 7 files are uncommitted, awaiting the user's commit approval.
- Suggested message: "Add browser integration tests and fix UI rendering bugs".
- **Do not push or merge without explicit user approval.**
