# StockMate — Project Status

## Current phase

**Phase 1 — Inventory MVP: IN PROGRESS**

- **Step 1 (skeleton): ✅ COMPLETE** — awaiting user approval to start Step 2.
- Steps 2–8: not started (see checklist below).

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
2. **Database foundation** ⬜
   - Models: `users`, `sessions`, `units`, `suppliers`,
     `supplier_ingredients`, `ingredients` (with `primary_supplier_id` +
     composite FK per D24), `ingredient_stock_state` (canonical cached
     balance, D14), `inventory_transactions` (incl. `opening_balance`, D23),
     `audit_logs`, `app_settings`
   - Alembic init + first migration
   - Seed script (clearly fictional): admin/staff demo users; units;
     demo suppliers (Supplier 1/2/3); demo ingredients — Korean Noodles →
     Supplier 1, Chinese Noodles → Supplier 2, Soup Base → Supplier 1,
     Chashu → Supplier 3, plus Eggs, Nori, Green Onions, Menma — with
     `opening_balance` transactions for every starting quantity (D23)
3. **Auth & RBAC** ⬜
   - Login/logout/me; server-side sessions; HttpOnly cookies; CSRF token
   - `require_staff` / `require_admin` dependencies on every endpoint
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
