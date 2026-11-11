# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current phase:** Inventory counting enhancement before Phase 3.
- **Progress:** Phase 1 complete (through `a67d07e`). Phase 2 Step 1
  (`2a34b14`) and Step 2 (`f08e48b`) committed. Phase 2 Step 3 (delivery
  receiving & stock integration) is **implemented; Codex reconciliation
  and verification are recorded in PROJECT_STATUS.md**.
  Committed as `bfcb824` on 2026-11-12. The subsequent approved counting
  enhancement and guarded fictional development data reset are documented in
  `PROJECT_STATUS.md`; forecasting has NOT started. The demo database now has
  Korean Noodles in `pc` with 60 pieces per Box and 180 pieces opening stock.
- Development happens **incrementally, one step at a time** — never implement
  multiple steps without explicit user approval.

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
2. `docs/DECISIONS.md` — binding decisions D1–D38. Supersede, never silently
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
- **Phase 2 Step 1** (uncommitted at time of writing — commit after user review):
  - `app/models/scheduling.py` — `SupplierScheduleRule` (weekly delivery
    rule: weekday, cutoff offset 1–6 days + local time, optional order-open
    window, reminder, per-rule timezone, additive ingredient override (D32),
    structured contact action, special instructions) and
    `SupplierCalendarException` (date-specific, D34). Duplicate ACTIVE rules
    are blocked by service check (409 `conflicting_rule`) AND two partial
    unique indexes (`sqlite_where`/`postgresql_where`) — the drift-guard
    accepts them because both sides declare the same WHERE clause.
  - `Supplier` gained `contact_method` / `ordering_url` /
    `ordering_procedures` (general instructions); migration
    `0002_supplier_scheduling.py` adds them via `batch_alter_table`.
  - `app/services/scheduling.py` — validation + CRUD + the engine
    `compute_opportunities(db, supplier_id, now_utc, horizon_days)`:
    `now_utc` is injectable for pinned-clock tests. Statuses per D33
    (`upcoming`/`open`/`cutoff_reached`/`missed`/`unavailable_holiday`;
    NO `covered` until purchase orders exist). Cutoff is inclusive (D16):
    `now == cutoff` ⇒ `open`. Rules store local wall-clock times; the engine
    converts (delivery date − offset, time, rule tz) to UTC instants.
  - Routes `app/api/v1/routes/schedules.py`: `/suppliers/{id}/schedules`,
    `/suppliers/{id}/exceptions`, `/suppliers/{id}/opportunities`,
    `/schedules/{id}` (PATCH, deactivate/reactivate),
    `/exceptions/{id}` (same), `/ordering/opportunities` (all suppliers).
    Staff read; admin mutates with CSRF; audit-logged.
  - `app/seed.py` — `seed_demo_schedules()` with its OWN marker
    (`demo_schedules_seeded`) so pre-existing dev DBs pick schedules up on
    the next `python -m app.seed` without touching inventory. Tokyo Noodle
    models the §9 override example additively: Mon+Fri supplier-wide, Wed
    for Korean Noodles only.
  - Frontend `js/pages/supplier-schedules.js` (`SM.schedulesUI`) — wide
    modal from the Suppliers page "Schedules" button: general instructions,
    upcoming opportunities (real dates, tz-formatted via new
    `SM.ui.formatInTz`/`formatDateOnly`), rule table + form, exceptions
    table + form. Staff read-only. Supplier form gained the three contact
    fields (explicit null clears them on PATCH).
  - **Real bug fixed by browser testing:** the hash router rendered pages
    directly into `#view` while `await`ing, so two overlapping navigations
    tore the page (title "Suppliers" over dashboard content). `app.js`
    `navigate()` now renders into an off-DOM staging node with a navigation
    token and swaps only if still current.
  - **Gotchas:**
    - Pydantic v2: a model field named exactly like its type
      (`date: date | None = None`) breaks class-body evaluation — alias the
      import (`from datetime import date as date_cls`) in that case.
    - Browser tests: match row buttons by TEXT, never index (new buttons
      shift indexes); when a test opens a modal whose body loads async,
      wait for BODY content (a button), not the title which renders first.
    - The schedules panel modal is `760px` wide via `ui.modal({wide: true})`.
  - `backend/tests/test_schedules.py` — 36 tests (engine math with pinned
    clock, inclusive boundary, exceptions, RBAC, validation, audit).
  - Totals: 128 backend + 5 Node unit + 28 browser = 161 checks passing.
- **Phase 2 Step 2** (uncommitted at time of writing — commit after user review):
  - `app/models/ordering.py` — `PurchaseOrder` + `PurchaseOrderItem`;
    migration `0003_purchase_orders.py`. Statuses: draft, ordered, confirmed,
    partially_delivered, delivered, delayed, cancelled. Item checks:
    ordered > 0, received ≥ 0, received ≤ ordered (D36 — Step 3 receipts
    cannot over-receive); one line per ingredient per order (unique).
  - `app/services/ordering.py` — create/update (draft-only), idempotent
    status transitions (`mark_ordered`, `confirm`, `delay`, `cancel`),
    `incoming_by_ingredient(db)` — SQL GROUP BY over pending =
    ordered − received per item, buckets ordered vs confirmed, earliest
    expected date, worst-case status. Item ingredients must be ACTIVE and
    linked to the supplier via the junction.
  - Routes `app/api/v1/routes/orders.py`: list (status csv filter,
    supplier_id, limit), create, detail, PATCH (draft only), mark-ordered /
    confirm / delay (staff+admin), cancel (ADMIN only). CSRF + audit.
  - `GET /inventory/stock` rows gained incoming fields per D36:
    `incoming_quantity` (+ ordered/confirmed split), `expected_delivery`,
    `incoming_status`, `projected_quantity`. Drafts/cancelled NEVER count.
  - Supplier archive guard now also blocks on open purchase orders (409
    `archive_blocked`). `test_reassign_then_archive_then_reactivate` was
    updated to cancel Tokyo's demo orders before archiving — this is the
    intended new rule, not a workaround.
  - `app/seed.py` — `seed_demo_orders()` with its OWN marker
    (`demo_orders_seeded`): Tokyo ORDERED (Korean Noodles 100 + Soup Base
    20), Masumoto CONFIRMED (Chashu 200 + Eggs 120), Dragon DRAFT (Chinese
    Noodles 50 — proves drafts never count).
  - Frontend: new `js/pages/orders.js` (`SM.pages.orders`, route `#/orders`)
    — list + status filter, detail modal with per-status action buttons,
    create/edit modal (ingredients limited to the supplier's junction links).
    Inventory page gained Incoming / Expected Arrival / Projected columns
    (`SM.data.incomingInfo`); dashboard gained an Upcoming Deliveries card
    (open orders only, links to `#/orders`).
  - **Gotchas:**
    - Decimal scale: `Decimal("0")` serializes as `"0"`, not `"0.000"` —
      use the `ZERO = Decimal("0.000")` constant / matching defaults when
      filling empty incoming buckets, or exact-string tests break.
    - `openDetail` pushes action buttons AFTER `ui.modal()` is created
      (the modal only renders actions present at call time), so the page
      appends its own `.button-row` to the body. Follow that pattern.
    - Pages are objects with `async render(view)`; `SM.ui.el` uses `class`
      (not `className`), `onclick` handlers, and varargs children (arrays
      ARE flattened, unlike raw `.append`).
  - `backend/tests/test_orders.py` — 21 tests (CRUD, transitions,
    idempotency, incoming aggregation, RBAC, archive guard, validation).
  - Totals: 149 backend + 5 Node unit + 32 browser = 186 checks passing.
- **Phase 2 Step 3** (approved for commit on 2026-11-12):
  - `app/models/receiving.py` — `DeliveryReceipt` (order FK, received_by,
    received_at, receipt_date, notes, unique idempotency key,
    `reversal_of_id` self-FK) + `DeliveryReceiptItem` (1:1 link to the
    ledger transaction it created; quantity positive on receipts, negative
    on reversals; `quantity_rejected` records damaged goods — never
    stocked, never fulfills). Migrations `0004_delivery_receipts.py` and
    `0005_receipt_rejected_qty.py` (additive column + widened nonzero check
    via batch_alter_table; 0004 untouched for already-migrated DBs).
  - `app/services/receiving.py` — `receive_delivery()` / `reverse_receipt()`.
    One atomic transaction per receipt: per line a `delivery_receipt`
    ledger row + cached-balance update (`counted_by=None` — confidence
    untouched) + `quantity_received` increment + receipt rows. Order status
    is recomputed from quantities only (D37): complete → delivered, some →
    partially_delivered, zero → ordered. Receivable only from ordered/
    confirmed/delayed/partially_delivered; over-receive → 409.
    Admin-only `reverse_receipt()`: appends a negative reversal receipt
    (original preserved; ledger rows supersede the original stock-in rows),
    refused if stock would go negative (409 `insufficient_stock`) — then an
    admin stock correction is the right tool.
  - Routes `app/api/v1/routes/receiving.py`:
    `POST /purchase-orders/{id}/receive` (staff+admin, 201),
    `GET /purchase-orders/{id}/receipts`,
    `POST /receipts/{id}/reverse` (admin only). CSRF + audit on mutations.
  - Frontend `js/pages/orders.js` — order detail gained "Receive Delivery"
    (receivable statuses only), a per-line receiving form (untouched lines
    = nothing received; confirm dialog before posting; idempotency key via
    `SM.pages.entry.newKey()`), and an immutable receipt history with
    admin-only Reverse buttons.
  - **Gotchas:**
    - New models with a plain `created_at` column must set it explicitly in
      the service (no TimestampMixin on DeliveryReceipt).
    - Receipt items must be appended to `receipt.items` (relationship), not
      just `db.add(...)` with a FK — otherwise the in-transaction audit
      snapshot serializes an empty item list.
    - Concurrency tests must log in their clients BEFORE starting threads:
      login writes a session row and contends with BEGIN IMMEDIATE.
    - Browser suite: never delete/reseed the SQLite file under a RUNNING
      server — stop uvicorn, reseed, restart (deleted inode vs new file).
  - `backend/tests/test_receiving.py` — 20 tests (full/partial/multiple
    receipts, over-receive, replay + conflict, concurrent race, draft/
    cancelled/delivered rejection, damaged/rejected quantities, multi-line
    atomic rollback, confidence untouched, ledger↔cache consistency, audit,
    reversal lifecycle, RBAC, CSRF).
  - Historical Kimi report: 169 backend + 5 Node unit + 35 browser.
    The working tree also contained migration `0006_receipt_staff_name` and
    four staff-name tests; the initial Codex backend run passed 173 tests.
    See the Codex reconciliation in `PROJECT_STATUS.md` for current results.
  - Codex preserved all existing migrations and receiving implementation.
    Added strict metadata replay checks, retries across business-day changes,
    arrived-total bounds, UTC timestamps, historical outstanding quantities,
    explicit Posted/Reversed/Reversal UI states, and repaired browser requests
    to supply the mandatory self-reported staff name. Reversal forms retain
    their idempotency key on retry. Removed vacuous duplicate name assertions.
  - Development database upgraded from 0005 to 0006 after backup at
    `/tmp/stockmate-before-0006-jylspag4/stockmate.db` (WSL).
    Existing rows in all 16 application tables were compared unchanged;
    `PRAGMA foreign_key_check` returned no violations. No reset or reseed.
    Legacy unknown staff names remain empty and display "Not recorded".

## Suggested review areas for the next agent (OpenAI Codex)

- `backend/app/services/scheduling.py` — engine math (offsets, timezones,
  inclusive cutoff), exception application, validation rules.
- `backend/app/services/inventory.py` — ledger atomicity, D27/D28 semantics.
- `backend/app/services/catalog.py` — junction sync order (D30), archive guards.
- `frontend/js/pages/supplier-schedules.js` — schedule/exception forms,
  safe DOM rendering.
- `frontend/js/app.js` — navigation token / staging-node race guard.
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

- Approval to start the next phase (Phase 3: demand forecasting) or any
  remaining Phase 2 items (reminder engine part).

## Recommended next task

**Phase 3 (forecasting)** or the remaining Phase 2 reminder engine, per the
user's roadmap. The ordering loop is complete end-to-end: schedules →
orders → incoming stock → receipts → ledger.

## Git

- `e53898d` — "Finalize StockMate Phase 0 architecture" (docs).
- `a9de3bc` — "Add StockMate backend foundation and health checks" (Step 1).
- `c9e72ae` — "Add database models, migrations, and demo seed data" (Step 2).
- `9aa5966` — "Add authentication, sessions, and role-based access control" (Step 3).
- `8283161` — "Add inventory ledger with dual stock entry and corrections" (Step 4).
- `d57b34f` — "Add supplier and ingredient administration APIs" (Step 5).
- `c1fa23e` — "Add responsive frontend dashboard and stock entry UI" (Step 6).
- `a67d07e` — "Add browser integration tests and fix UI rendering bugs" (Step 7).
- `2a34b14` — "Add supplier delivery scheduling and instructions" (Phase 2 Step 1).
- `f08e48b` — "Add purchase orders and incoming stock indicators" (Phase 2 Step 2).
- Phase 2 Step 3 commit approved on 2026-11-12 with message
  "Add delivery receiving with ledger integration".
- **Do not push or merge without explicit user approval.**
