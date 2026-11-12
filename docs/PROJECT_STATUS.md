# StockMate — Project Status

## Current phase

**Phase 3 Step 1 — Editable ingredient demand configuration: complete, approved for commit**

- **Step 1 (skeleton): ✅ COMPLETE** (commit `a9de3bc`)
- **Step 2 (database models + migrations + seed): ✅ COMPLETE** (commit `c9e72ae`)
- **Step 3 (auth & RBAC): ✅ COMPLETE** (commit `9aa5966`)
- **Step 4 (inventory ledger service): ✅ COMPLETE** (commit `8283161`)
- **Safeguards (D27/D28/D29): ✅ COMPLETE** — correction-vs-newer-reconciliation
  guard, idempotency-payload conflict detection, deferred BEGIN for reads.
- **Step 5 (supplier & ingredient admin): ✅ COMPLETE** (commit `d57b34f`)
- **Step 6 (frontend dashboard): ✅ COMPLETE** (commit `c1fa23e`)
- **Step 7 (integration testing & stabilization): ✅ COMPLETE** (commit `a67d07e`)

**Phase 2 — Supplier Scheduling & Ordering: IN PROGRESS**

- **Step 1 (supplier delivery scheduling): ✅ COMPLETE** (commit `2a34b14`) —
  per-supplier weekly schedule rules (cutoff offset+time, order window,
  reminder, contact action, instructions, timezone), additive ingredient
  overrides (D32), date-specific calendar exceptions (D34), scheduling
  engine with real tz-aware dates and statuses (D33), supplier general
  instructions, scheduling UI in the Supplier Management page. Also fixed a
  genuine router race (torn render on overlapping navigations).
- **Step 2 (purchase orders + incoming stock): ✅ COMPLETE** (commit
  `f08e48b`) — purchase orders with line items (statuses draft → ordered →
  confirmed → partially delivered / delayed → delivered, cancelled),
  staff create/advance orders, admin-only cancel; incoming-stock indicators
  on Inventory (Available vs Incoming vs clearly-labeled Projected, D36) and
  an Upcoming Deliveries card on the dashboard; drafts/cancelled never count
  as incoming; physical stock untouched until Step 3 receipts; supplier
  archive blocked while open orders exist; migration `0003_purchase_orders`.
- **Step 3 (delivery receiving): ✅ COMPLETE** (commit `bfcb824`) —
  receipt confirmation per order line (partial deliveries, over-receive
  rejection), atomic ledger integration (`delivery_receipt` rows linked 1:1
  to receipt lines), damaged/rejected quantities tracked separately (never
  stocked, never fulfill the obligation), receipt-level idempotency,
  admin-only reversal receipts, order status derived from received
  quantities only (D37), confidence untouched by receipts; Receive Delivery
  UI with confirm dialog + receipt history in the order detail; migrations
  `0004_delivery_receipts` + `0005_receipt_rejected_qty` +
  `0006_receipt_staff_name`. See the current verification below; earlier
  Kimi totals were superseded during reconciliation.

### Codex Step 3 reconciliation (2026-11-12)

- Preserved the existing uncommitted receiving models, service, routes,
  frontend, migrations 0004/0005/0006, and tests. Branch `main`, HEAD
  `f08e48b`; no commit, push, deployment, or later-phase work.
- Both earlier reports were incomplete: migration 0005 and rejected quantity
  handling existed, as did migration 0006 and four staff-name tests. The
  initial backend run passed 173 tests.
- Fixed idempotency comparison of staff names/notes on receipts and reversals,
  retries across a business-day boundary, total arrived quantity validation,
  and reversal form retry keys. History now shows UTC-aware receipt timestamps,
  accepted/rejected quantities (including zero), historical outstanding,
  receipt status, notes, self-reported names, and the separate account actor.
- Received By rejects missing/empty/whitespace names, trims valid names,
  persists each partial receiver separately, and preserves both names when
  reversed. Historical records without a staff name display "Not recorded".
- Rejected goods never enter stock or fulfill the order; accepted plus
  rejected in a single arrival cannot exceed the current outstanding amount.
  Original receipt and reversal history remain append-only through the API.
- Development schema and repository head are `0006_receipt_staff_name`.
  Before applying 0006, backed up SQLite to
  `/tmp/stockmate-before-0006-jylspag4/stockmate.db` in WSL. All existing rows
  in 16 application tables remained unchanged, with valid foreign keys.
  Tests used isolated temporary databases; development data was never reseeded.
- Chrome: **35/35 passed**, including whitespace-name rejection, accepted vs
  rejected stock effects, history, over-receive protection, and 390px receipt
  history. Artifacts: Windows temp `stockmate-browser-uwNZeR`.
- Node: **5/5 passed**. Both changed JS files pass `node --check`;
  `git diff --check` is clean. Final full WSL backend suite:
  **180 passed, 1 warning in 201.42s**. Includes migration/model drift,
  populated legacy upgrades, concurrent distinct-key and same-key receipts,
  atomic rollback, ledger/cache agreement, reversal and staff-name regressions.
- Frontend skill static audit exited 1 for two actionless-button findings in
  unchanged `frontend/index.html` (menu/logout). Both have `addEventListener`
  handlers in `frontend/js/app.js:23-27`; these are static-analysis false
  positives. Report: WSL `/tmp/stockmate-ui-audit.json`.
- Limitations: identities are self-reported; legacy names cannot be recovered.
  PostgreSQL concurrency and broader production readiness remain unverified;
  this review covers the existing SQLite application. Pytest emits an existing
  Starlette/httpx deprecation warning.

### Inventory counting enhancement (2026-11-12, committed as `dd67047`)

- Added optional ingredient counting packages (`count_package_enabled`, name,
  positive canonical units per package, version) and a staff choice between
  full packages plus loose units and direct total stock units. Contents of
  opened packages are loose units; the UI warns against including the same
  contents in a full package count. The enhancement itself did not rewrite
  existing ingredient settings.
- The server converts exact decimal quantities before the unchanged
  remaining/used ledger protocol. Remaining counts still verify confidence;
  used entries deduct stock without refreshing confidence. Ledger rows retain
  the entered breakdown and conversion snapshot in `counting_json`.
  Idempotent retries compare that breakdown; stale unposted forms fail after
  configuration changes. No package balance is stored.
- Supplier-specific buying package size/name and whole-package requirement
  live on `supplier_ingredients`, independently of counting configuration.
  An admin API can set them. Phase 3 recommendation calculation has not begun;
  its rounding rule for a selected supplier is documented in D38.
- Migration `0007_inventory_count_packages` is additive to 0006. Existing
  stock units remain in place; changing a canonical unit after stock history
  exists is blocked. The development database is at head `0007`; its pre-upgrade
  backup is `/tmp/stockmate-before-0007-5e1wbmu0`. Tests use isolated
  databases, and development data was not reseeded. All pre-upgrade rows in
  16 application tables remain unchanged; the foreign-key check found no
  violations.
- Focused packaging and migration checks: 14 passed. Frontend Node: 5 passed;
  Chrome browser integration: 38 passed, including counting setup, 2 boxes +
  15 loose units, package-based usage, direct-unit entry, and layout checks.
  Final full backend suite: **193 passed, 1 existing deprecation warning**.
  `git diff --check` is clean. Frontend static audit reported only the two
  pre-existing menu/logout button false positives in `frontend/index.html`;
  both are wired in `frontend/js/app.js`. No commit, push, deployment, or
  Phase 3 calculations.

### Guarded fictional development data reset (2026-11-12, committed as `dd67047`)

- Confirmed `environment=development`, no `.env` or database URL override,
  and the exact local SQLite target `backend/stockmate.db` at migration
  `0007_inventory_count_packages`. All suppliers carried the fictional demo
  marker. Stopped the local development server before the reset.
- The guarded `python -m app.reset_demo` command requires the exact absolute
  database path and confirmation phrase. It checks seed markers, known demo
  accounts/catalog, migration head, and foreign keys; it creates a timestamped
  SQLite backup, then removes dependent operational rows and reseeds in one
  transaction. It never drops schema or touches an unknown/production URL.
  Verified backup:
  `backend/backups/stockmate-before-demo-reset-20261111T162914410711Z.db`.
- Preserved both account rows and password hashes, all 6 login sessions, the
  original 6 unit rows, Alembic revision, and application code/configuration.
  Added the `pack` unit. Removed 10 old demo transactions, 4 orders and their
  6 lines, 4 operational audit entries, 8 old ingredients and stock states,
  3 suppliers and 7 schedule rules; no receipts existed in this database.
- Reseeded 8 ingredients, 3 fictional suppliers, 7 schedule rules, and 3
  purchase orders (5 lines). The 8 opening balances are ledger entries.
  Korean Noodles: `pc`, Box × 60, 180 pcs opening; Chinese Noodles: `pc`,
  Box × 50; Eggs: `pc`, Tray × 30; Soup Base: `pack`, Case × 10; Chashu:
  `pc`, Pack × 20. Primary supplier purchasing packages are separate, and
  Korean Noodles' alternative supplier uses a 50-piece box. Other demo
  ingredients retain their suitable stock units without counting packages.
- Compared the live database with its backup: users, sessions, prior unit
  rows, and Alembic revision match exactly. SQLite quick check and foreign
  keys pass. Ledger balances agree with all stock states; 1 Box = 60 pcs and
  2 Boxes + 15 loose pcs = 135 pcs.
- Complete tests on isolated databases: **198 backend passed**, **5 frontend
  passed**, **38 browser integration passed**. The browser run covered dashboard,
  both stock modes, package counting, supplier scheduling, orders, receiving,
  history, and responsive layouts. Pytest emitted the existing Starlette/httpx
  deprecation warning. No commit, push, deployment, or production change.

### Phase 3 Step 1 — Editable ingredient demand configuration (approved for commit)

- Added optional per-ingredient `ingredient_demand_settings` rows with seven
  independent nullable weekday estimates, planning safety stock, enabled state,
  notes, version, and UTC timestamps. All quantities use the ingredient's
  existing unit and exact NUMERIC(14,3). Null remains distinct from explicit
  zero; partial configurations are allowed but not forecast-ready. Disabled
  and archived configurations are not forecast-ready either (D39).
- Added authenticated GET and admin/CSRF PATCH endpoints at
  `/ingredients/{id}/demand-settings`, with audit snapshots and stale-revision
  rejection. These operations never write inventory, packages, orders, or
  receipts. Planning safety stock is distinct from the inventory alert threshold.
- Ingredient Management now has Forecast Settings for each ingredient, including
  staff read-only access. The responsive dialog has a Monday–Friday shortcut,
  independent weekends, stock-unit labels, exact weekly preview, inline
  validation, retry, conflict recovery, dirty-discard protection, and focus
  containment/restoration. Shared dialogs are dismissed on session expiry;
  repeated toasts are deduplicated and cleared before another dialog opens.
- Korean Noodles was demonstrated only in isolated tests: 40 pc Monday–Friday,
  60 pc Saturday/Sunday, 320 pc weekly estimate, separate 60 pc planning buffer,
  existing Box × 60 counting configuration. Every input remains editable.
  No demand estimates were automatically seeded for any ingredient.
- Applied additive migration `0008_ingredient_demand_settings` after a checked
  backup at `backend/backups/stockmate-before-0008-20261112T001520171184Z.db`.
  Compared every pre-existing row in 16 application tables: all unchanged, no
  foreign-key violations. The new table is empty in development. No reset or
  reseed was performed. The development server was restored on port 8000.
- The guarded development reset now recognizes the migration head and deletes
  dependent demand rows before ingredient rows, within its existing safeguards.
- Verification: **214 backend tests passed** (601.19s, one existing
  Starlette/httpx deprecation warning), **8 frontend unit tests passed**, and
  **45 real-browser tests passed**. The focused demand/reset run passed 19 tests.
  Screenshots: Windows temp `stockmate-browser-oRmhht`. Static frontend audit
  reports only the two existing menu/logout action-detection false positives;
  real browser tests exercise both controls. DESIGN.md lint has zero errors
  and four unused-token warnings (those tokens map to existing CSS roles).
- New files: `backend/app/models/demand.py`, `schemas/demand.py`,
  `services/demand.py`, `api/v1/routes/demand.py`,
  `backend/alembic/versions/0008_ingredient_demand_settings.py`,
  `backend/tests/test_demand_settings.py`,
  `frontend/js/pages/ingredient-demand.js`, `frontend/tests/demand.test.js`,
  `DESIGN.md`, and `UX-CONTRACT.md`.
- Modified files: backend router/model exports, guarded reset and its test;
  frontend ingredient page, modal/feedback helpers, session UI, script entry,
  app CSS, browser suite; README, architecture, decisions, status, and handoff.
  No commit, push, deployment, projection engine, smart ordering, or Step 2 work.

Phase 0 was approved with three final requirements, now reflected in the docs:
D15 (SQLite-safe concurrency: `BEGIN IMMEDIATE` + atomic conditional UPDATE +
bounded retries, no `FOR UPDATE` dependency), D23 (all starting quantities —
including demo seed stock — recorded as `opening_balance` ledger entries),
D24 (composite FK makes `primary_supplier_id` always reference a valid
junction row; service validates supplier `active` status).

## Phase roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Planning, architecture, docs (incl. review revision) | ✅ Done (commit `e53898d`) |
| 1 | Inventory MVP: DB, auth, ingredients, **basic supplier CRUD + assignment**, dual stock entry, ledger, dashboard, confidence timestamps | 🔶 Steps 1–7 of 8 done |
| 2 | Supplier schedule rules, delivery calendars, cutoff engine, purchase orders, delivery confirmation, supplier reminders (engine part), alternative-supplier ordering | 🔶 Steps 1–3 committed (`2a34b14`, `f08e48b`, `bfcb824`) |
| 3 | Demand forecasting, forecast calendar, status colors, order suggestions, confidence indicators | 🔶 Step 1 demand configuration implemented; Step 2 not started |
| 4 | Scheduled reminders, in-app notifications, preferences, provider routing | ⬜ Not started |
| 5 | Analytics, reports, CSV export, simple statistics | ⬜ Not started |
| 6 | Deployment readiness: security, regression, backups | ⬜ Not started |
| — | PWA milestone (manifest, service worker) | ⬜ Future |
| — | Capacitor evaluation (Android, then iOS) | ⬜ Future |

## Phase 1 implementation checklist (ordered)

1. **Project skeleton — ✅ DONE (2026-11-09)**
   - `backend/` package per docs/ARCHITECTURE.md §2; `requirements.txt` with
     per-dependency documentation
   - `config.py` (env-based settings, `STOCKMATE_` prefix), `database.py`
     (engine factory with SQLite WAL + busy timeout + `foreign_keys=ON`,
     session factory, `get_db` dependency, `Base`)
   - FastAPI app factory serving `/api/v1` + static frontend mount
   - Health endpoint `GET /api/v1/health` (checks DB, reports tz settings)
   - Frontend placeholder shell (`frontend/index.html`, `css/base.css` design
     tokens, `js/api.js` fetch wrapper)
   - Test infrastructure: pytest + `TestClient`, per-test temporary database
   - **4/4 tests pass; live uvicorn smoke test OK**
   - **Environment note:** development runs in **WSL** (Windows Smart App
     Control blocks pip-installed native packages on the Windows side). Venv:
     `~/.venvs/stockmate` in WSL. See README §Setup.
2. **Database foundation — ✅ DONE (2026-11-09)**
   - Models (`app/models/`): `users`, `sessions`, `units`, `suppliers`,
     `supplier_ingredients` (composite PK), `ingredients` (catalog only —
     NO stock column; `primary_supplier_id` + composite FK per D24 with
     `autoincrement=True` workaround), `ingredient_stock_state` (the single
     canonical cached balance, D14), `inventory_transactions` (immutable
     ledger incl. `opening_balance`, D23), `audit_logs`, `app_settings`
   - Alembic: `backend/alembic.ini`, `alembic/env.py` (URL from settings,
     never hardcoded), `versions/0001_initial_schema.py` — circular FK added
     via `batch_alter_table` after both tables exist (valid SQLite + PG)
   - `app/db_admin.py::run_migrations` — one programmatic migration entry
     point shared by CLI, seed, and tests
   - `app/seed.py` — idempotent (marker row in `app_settings`); fictional
     demo data: users admin/staff, 6 units, 3 fictional suppliers, 8
     ingredients (Korean Noodles → S1, Chinese Noodles → S2, Soup Base → S1,
     Chashu/Eggs/Nori → S3, Green Onions/Menma → S1; Korean Noodles also has
     S2 as alternative). Every opening quantity is an `opening_balance`
     ledger transaction with a deterministic idempotency key (D23).
   - Tests: `test_schema.py` (FK enforcement, composite FK reject/accept,
     unique constraints, Decimal round-trip, **model↔migration drift
     guard**), `test_seed.py` (catalog counts, independent noodle stocks,
     required assignments, ledger↔balance reconstruction, idempotent reseed)
   - **18/18 tests pass** (incl. previous 4 health tests)
3. **Auth & RBAC — ✅ DONE (2026-11-09), 37/37 tests passing**
   - `app/auth/`: `passwords.py` (Argon2 + strength rules), `tokens.py`
     (secure tokens, DB stores SHA-256 hash only), `sessions.py` (create /
     validate / revoke, 12 h TTL), `csrf.py` (signed double-submit token
     bound to session, D25), `rate_limit.py` (per-process sliding window,
     D26), `dependencies.py` (`get_session_and_user` — the single auth
     entry point with the documented future Bearer-token extension, D7;
     `require_admin`; `csrf_protect`)
   - `app/errors.py` — consistent `{"error": {...}}` responses
   - Routes: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`;
     admin-only `GET/POST /admin/users` (RBAC + CSRF, with audit log)
   - `app/cli.py` — `python -m app.cli create-admin`: interactive hidden
     password entry, strength validation, no printed/logged passwords
   - Config: `session_ttl_hours`, `secure_cookies`, rate-limit knobs
   - `frontend/js/api.js` — `post()` with automatic `X-CSRF-Token` header
   - `tests/test_auth.py` — 19 tests: login/cookie flags, identical generic
     401s, me/logout/expired/revoked/inactive sessions, staff→403, admin
     list/create, CSRF reject/accept/cross-session, weak password → 400,
     duplicate → 409, rate limit → 429, Argon2 round-trip, Secure flag
   - Live smoke test passed (login → me → staff 403 → logout → 401)
4. **Inventory service (the ledger, D2/D3/D6/D14/D15) — ✅ DONE (2026-11-10), 56/56 tests passing**
   - `app/services/inventory.py`: `record_stock_entry` (both modes),
     `correct_transaction` (append-only corrections with double-correction
     prevention and own-entry/admin authorization), `verify_ledger` (cache
     vs SUM(deltas) per ingredient), `repair_ledger` (admin, audited, never
     touches the ledger; sets discrepancy flag + confidence=low)
   - Atomic write protocol: `BEGIN IMMEDIATE` for every SQLite transaction
     (engine event in `app/database.py` + DBAPI autocommit), atomic
     conditional UPDATE with rowcount check, bounded retries (3×) on lock
     errors — NO `SELECT FOR UPDATE` (D15)
   - Guards: active ingredient, Decimal(14,3) via schema, non-negative
     unless admin `allow_negative`, `confirmation_required` above
     `large_adjustment_threshold` (default 500, server-validated), validated
     reason set, idempotency pre-check + unique-constraint race handling
   - Reconciliation updates `last_counted_at`/`last_counted_by` + confidence
     → high; consumption does NOT (D3)
   - `app/services/business_time.py` — business-date derivation honoring
     timezone + configurable day boundary (D18)
   - Endpoints: `GET /inventory/stock`, `POST /inventory/entries`,
     `GET /inventory/transactions` (filters), `POST .../{id}/correct`,
     `GET /inventory/verify`, `POST /inventory/verify/repair`
   - Quantities serialized as strings in API responses (exact decimals)
   - `tests/test_inventory.py` — 19 tests incl. REAL concurrency: 8 parallel
     "used 5" submissions via ThreadPoolExecutor → exact final balance,
     8 ledger rows, no lost updates
   - README now documents production security risks (demo credentials, dev
     secret, HTTPS cookies, per-process limiter, XSS)
5. **Supplier & ingredient management (admin, D19/D22/D24) — ✅ DONE (2026-11-10), 82/82 tests passing**
   - Safeguards landed first (D27/D28/D29, `tests/test_safeguards.py`, 6 tests):
     corrections blocked after a newer reconciliation (409
     `correction_after_reconciliation`); idempotency-key reuse with a
     different payload → 409 `idempotency_conflict` (semantic payload
     comparison, both pre-check and race paths); read-only requests use
     deferred `BEGIN`, writes `BEGIN IMMEDIATE` via a thread-local
     write-intent flag (probe test proves both)
   - `app/schemas/catalog.py` + `app/services/catalog.py`: supplier CRUD,
     archive guard (409 `archive_blocked` listing blocking active
     ingredients), reactivate; ingredient CRUD with `opening_quantity` →
     `opening_balance` ledger entry atomically (D23); supplier-set validation
     (exist + active); junction sync in FK-safe order add→move-primary→remove
     (D30); `db.refresh` after commit because `expire_on_commit=False` (D31);
     audit log rows on every mutation
   - Routes: `GET/POST /suppliers`, `GET/PATCH /suppliers/{id}`,
     `POST /suppliers/{id}/archive|reactivate`, `GET/POST /ingredients`,
     `GET/PATCH /ingredients/{id}`, `POST /ingredients/{id}/archive|restore`
     (staff read-only; mutations admin-only + CSRF)
   - Ingredient PATCH schemas contain NO stock fields (D14); stray
     `current_quantity` in a request body is ignored (tested)
   - `Ingredient.unit` ORM relationship added (lazy="joined") for unit
     abbreviations in API responses — no schema change
   - `tests/test_catalog.py` — 20 tests incl. archive guard flow
     (reassign → archive → reactivate), historical junction preservation,
     opening-balance ledger verification, RBAC, audit logging
6. **Frontend — ✅ DONE (2026-11-10), 92 backend + 5 Node tests passing**
   - Hash-routed SPA, classic scripts, no build step (Capacitor-friendly, D1)
   - Design system per spec: `#F7F9FC` bg, `#102B4E` sidebar, solid colors,
     soft shadows, responsive tables → cards <720px, collapsible sidebar
     <900px, 44px touch targets, visible focus states
   - Pages: Login (loading/errors), Dashboard (4 stat cards, stock warnings,
     recent transactions, Record Stock CTA), Inventory (search/category/
     status filters, availability + SEPARATE confidence badges), Daily Stock
     Entry (Used/Remaining toggle, exact integer-millisecond preview,
     idempotency key per logical submission + reuse on retry, submit lock,
     large-adjustment confirm dialog, admin negative override), Suppliers
     (CRUD modals, detail with linked ingredients, archive guard error
     surfacing), Ingredients (CRUD with unit dropdown via new GET /units,
     primary + alternative suppliers, opening quantity at creation only),
     History (filters, previous/resulting balances, correction workflow with
     D27 warning), Settings (account, business config from /health, admin
     user management)
   - Role-aware UI (admin-only controls hidden for staff; server RBAC is the
     real enforcement); all dynamic rendering via textContent/createElement
   - New backend endpoint: `GET /api/v1/units` (read-only reference data for
     the ingredient form)
   - `tests/test_frontend.py` — 10 API-level integration tests: static asset
     serving, units auth, dashboard data flow, used→remaining entry flow,
     idempotent retry, large-adjustment confirmation, logout expiry, admin
     user creation flow. **No browser/DOM automation available — rendered-UI
     behavior is NOT covered by automated tests.**
   - `frontend/tests/ui.test.js` — 5 Node unit tests for exact quantity math
     (`toMillis`/`fromMillis`/`formatQty`, used/remaining preview math)
   - Live smoke test passed: uvicorn serving index + assets, real login,
     stock data, units endpoint
7. **Tests (pytest) — ✅ covered incrementally in steps 2–6 (92 passing)**
   - **Step 7 browser automation:** `frontend/tests/browser_smoke.mjs` —
     24 rendered-app tests via headless Chrome DevTools Protocol using only
     Node built-ins (fetch + WebSocket, zero new dependencies). Covers login/
     logout/failed login, dashboard data, inventory filters + separate
     availability/confidence badges, used & remaining entry end-to-end,
     double-click prevention, large-adjustment dialog, supplier create/
     archive-blocked/archive/reactivate, ingredient create with opening
     balance, catalog-edit balance protection, D27 correction warning,
     staff read-only UI, negative-stock error, cleared-session redirect,
     and 5 viewport widths (1440/1024/768/390/360) with overflow checks,
     screenshots, and desktop/mobile menu-button assertions.
   - **Bugs found by browser testing and fixed:**
     1. `entry.js` preview and `ingredients.js` category dropdown passed
        ARRAYS to DOM `.append()`, rendering `[object HTMLElement]` instead
        of content — fixed with spread (the stock preview never worked).
     2. `.btn { display:inline-flex }` overrode `.menu-button { display:none }`
        at equal specificity — the mobile menu button showed on desktop.
        Fixed by scoping to `.topbar .menu-button`; regression assertions added.
   - Security scans: no localStorage/sessionStorage, no innerHTML with API
     data, no credentials in frontend application code (demo passwords exist
     only in the test script and seed, both dev-only).
   - Ledger math both modes; reconciliation ≠ consumption ✅
   - **Concurrent stock entries** (parallel same-ingredient submissions →
     exact final balance) ✅
   - Idempotency, negative guard + override, large-adjustment flow ✅
   - Auth/CSRF, RBAC, supplier archive guards, junction integrity (D24),
     opening-balance reconstruction from ledger (D23) ✅
8. **Docs update** ⬜ — this file, AGENT_HANDOFF.md, README

## Known limitations / open items

- **Windows-native Python cannot run the app** on this machine: Smart App
  Control (enforce mode) blocks pip-installed native DLLs. Development runs
  in WSL; documented in README. Revisit only if the machine policy changes.
- Deprecation warning from FastAPI's TestClient about `httpx` vs the future
  `httpx2`; harmless today, watch FastAPI releases.
- PostgreSQL target is design-only until deployment phase.
- External notification channels (email, LINE, push) are provider stubs until Phase 4.
- Open-order check in the supplier archive guard becomes active in Phase 2.
- **Browser tests run on Windows Node + Chrome/Edge only** (WSL has no Node).
  They mutate the database: reseed before every full run. Command:
  `node frontend/tests/browser_smoke.mjs` against a seeded server on :8765.
- Supplier edit form: the Phase 2 contact fields (`contact_method`,
  `ordering_url`, `ordering_procedures`) support explicit clearing via null;
  the older optional text fields still treat empty as "no change" — cosmetic
  limitation, tracked for later.
- Toasts can overlap table content temporarily (cosmetic).

## Production readiness review (Step 7 — NOT deployment-ready yet)

Functionally Phase 1 is complete, but these items remain for Phase 6 before
any public deployment (none of them block Phase 2 development):

| Area | Current state | Required before production |
|------|---------------|----------------------------|
| Demo credentials | `admin`/`staff` with public dev passwords seeded | Never seed demo data in prod; create accounts via `app.cli create-admin` |
| Secret key | Public default in `app/config.py` | Set `STOCKMATE_SECRET_KEY` to a long random value |
| HTTPS / cookies | `secure_cookies` defaults off for localhost | HTTPS only + `STOCKMATE_SECURE_COOKIES=true` |
| Login rate limit | Per-process sliding window (D26) | Shared store if multi-worker |
| Sessions | 12 h TTL, SHA-256-hashed tokens, revocation works | OK; consider shorter TTL policy |
| Backups | Not implemented | Scheduled SQLite/PostgreSQL backups + restore drills |
| Error logging | Unhandled errors → generic 500 (no internals leaked); no structured request/error logging | Structured logging + alerting |
| Security headers | No CSP/HSTS etc. | Configure at reverse proxy / deployment |
| Database | SQLite single-process, WAL, FK on, tested concurrency | PostgreSQL migration via Alembic when scaling |
