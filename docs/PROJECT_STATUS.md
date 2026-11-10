# StockMate — Project Status

## Current phase

**Phase 1 — Inventory MVP: IN PROGRESS**

- **Step 1 (skeleton): ✅ COMPLETE** (commit `a9de3bc`)
- **Step 2 (database models + migrations + seed): ✅ COMPLETE** (commit `c9e72ae`)
- **Step 3 (auth & RBAC): ✅ COMPLETE** — awaiting user approval to start Step 4.
- Steps 4–8: not started (see checklist below).

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
| 1 | Inventory MVP: DB, auth, ingredients, **basic supplier CRUD + assignment**, dual stock entry, ledger, dashboard, confidence timestamps | 🔶 Step 1 of 8 done |
| 2 | Supplier schedule rules, delivery calendars, cutoff engine, purchase orders, delivery confirmation, supplier reminders (engine part), alternative-supplier ordering | ⬜ Not started |
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
4. **Inventory service (the ledger, D2/D3/D6/D14/D15)** ⬜
   - `POST /api/v1/inventory/entries`: idempotency check → `BEGIN IMMEDIATE`
     → delta → atomic conditional UPDATE (rowcount = 1) → negative guard →
     large-adjustment confirmation → ledger row + stock-state update in one
     transaction → bounded retries on busy
   - Transaction history endpoint with filters
   - Correction entries (`supersedes_id`)
   - Ledger replay verify/repair service function (admin/debug)
5. **Supplier & ingredient management (admin, D19/D22/D24)** ⬜
   - CRUD + archive/reactivate endpoints with archive-guard warnings
   - `GET /api/v1/suppliers/{id}/ingredients`
   - Ingredient create/edit accepts `primarySupplierId` +
     `alternativeSupplierIds`; service enforces active-supplier status
   - Audit logging on all admin mutations
6. **Frontend** ⬜
   - Pages: Login, Dashboard, Inventory list, Daily Stock Entry (mode
     toggle), Ingredient admin (primary-supplier dropdown), Supplier admin,
     Transaction history
7. **Tests (pytest)** ⬜
   - Ledger math both modes; reconciliation ≠ consumption
   - **Concurrent stock entries** (parallel same-ingredient submissions →
     exact final balance)
   - Idempotency, negative guard + override, large-adjustment flow
   - Auth/CSRF, RBAC, supplier archive guards, junction integrity (D24),
     opening-balance reconstruction from ledger (D23)
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
