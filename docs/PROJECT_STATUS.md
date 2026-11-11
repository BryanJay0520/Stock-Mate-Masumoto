# StockMate — Project Status

## Current phase

**Phase 2 — Supplier Scheduling & Ordering: IN PROGRESS** (Phase 1 complete)

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
- **Step 2 (purchase orders + incoming stock): ✅ COMPLETE, awaiting approval
  to commit** — purchase orders with line items (statuses draft → ordered →
  confirmed → partially delivered / delayed → delivered, cancelled),
  staff create/advance orders, admin-only cancel; incoming-stock indicators
  on Inventory (Available vs Incoming vs clearly-labeled Projected, D36) and
  an Upcoming Deliveries card on the dashboard; drafts/cancelled never count
  as incoming; physical stock untouched until Step 3 receipts; supplier
  archive blocked while open orders exist; migration `0003_purchase_orders`.
  **149 backend + 5 Node unit + 32/32 real browser tests passing.**

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
| 2 | Supplier schedule rules, delivery calendars, cutoff engine, purchase orders, delivery confirmation, supplier reminders (engine part), alternative-supplier ordering | 🔶 Step 1 (scheduling, `2a34b14`) done; Step 2 (purchase orders + incoming stock) done, awaiting commit approval |
| 3 | Demand forecasting, forecast calendar, status colors, order suggestions, confidence indicators | ⬜ Not started |
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
