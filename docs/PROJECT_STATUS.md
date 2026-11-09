# StockMate — Project Status

## Current phase

**Phase 0 — Planning & Architecture: COMPLETE (revised after architecture review + ingredient/supplier requirements). Awaiting user approval to start Phase 1.**

No application code exists yet. Do not start Phase 1 without explicit approval.

## Phase roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Planning, architecture, docs (incl. review revision) | ✅ Done |
| 1 | Inventory MVP: DB, auth, ingredients, **basic supplier CRUD + assignment**, dual stock entry, ledger, dashboard, confidence timestamps | ⬜ Not started |
| 2 | Supplier schedule rules, delivery calendars, cutoff engine, purchase orders, delivery confirmation, supplier reminders (engine part), alternative-supplier ordering | ⬜ Not started |
| 3 | Demand forecasting, forecast calendar, status colors, order suggestions, confidence indicators | ⬜ Not started |
| 4 | Scheduled reminders, in-app notifications, preferences, provider routing | ⬜ Not started |
| 5 | Analytics, reports, CSV export, simple statistics | ⬜ Not started |
| 6 | Deployment readiness: security, regression, backups | ⬜ Not started |
| — | PWA milestone (manifest, service worker) | ⬜ Future |
| — | Capacitor evaluation (Android, then iOS) | ⬜ Future |

## Phase 1 implementation checklist (ordered)

1. **Project skeleton**
   - `backend/` layout per docs/ARCHITECTURE.md §2; `requirements.txt`:
     fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings,
     argon2-cffi, itsdangerous, `apscheduler>=3.10,<4` (D17), pytest, httpx
   - `config.py` (env-based settings), `database.py` (engine/session/Base,
     SQLite WAL mode + busy timeout per D15)
   - FastAPI app factory serving `/api/v1` + static frontend
2. **Database foundation**
   - Models: `users`, `sessions`, `units`, `suppliers`,
     `supplier_ingredients`, `ingredients` (with `primary_supplier_id`),
     `ingredient_stock_state` (canonical cached balance, D14),
     `inventory_transactions`, `audit_logs`, `app_settings`
   - Alembic init + first migration
   - Seed script (clearly fictional): admin/staff demo users; units;
     demo suppliers (Supplier 1/2/3 with realistic fictional names); demo
     ingredients — Korean Noodles → Supplier 1, Chinese Noodles → Supplier 2,
     Soup Base → Supplier 1, Chashu → Supplier 3, plus Eggs, Nori,
     Green Onions, Menma — each with independent stock
3. **Auth & RBAC**
   - Login/logout/me; server-side sessions; HttpOnly cookies; CSRF token
   - `require_staff` / `require_admin` dependencies on every endpoint
4. **Inventory service (the ledger, D2/D3/D6/D14/D15)**
   - `POST /api/v1/inventory/entries`: idempotency check → row lock → delta →
     negative guard → large-adjustment confirmation → ledger row +
     `ingredient_stock_state` update in one transaction
   - Transaction history endpoint with filters
   - Correction entries (`supersedes_id`)
   - Ledger replay verify/repair service function (admin/debug)
5. **Supplier management (admin, D19/D22)**
   - CRUD + archive/reactivate endpoints with archive-guard warnings
     (primary-supplier assignments, open orders — open orders are Phase 2
     objects, so the Phase 1 guard checks assignments only)
   - `GET /api/v1/suppliers/{id}/ingredients`
   - Ingredient create/edit accepts `primarySupplierId` +
     `alternativeSupplierIds`; service enforces primary ∈ junction (D19)
   - Audit logging on all admin mutations
6. **Frontend**
   - Base layout (sidebar desktop / bottom nav mobile), design tokens
     (light background, deep navy sidebar, blue primary, green/yellow/red
     indicators)
   - Pages: Login, Dashboard (counts + recent activity), Inventory list,
     Daily Stock Entry (mode toggle, touch-friendly), Ingredient admin
     (with primary-supplier dropdown of active suppliers), Supplier admin,
     Transaction history
   - `js/api.js` fetch wrapper with CSRF handling
7. **Tests (pytest)**
   - Ledger math both modes; reconciliation ≠ consumption
   - **Concurrent stock entries:** parallel same-ingredient submissions →
     exact final balance, no lost updates, one ledger row each
   - Idempotency: duplicate key → single effect, original response returned
   - Negative-stock guard + admin override; large-adjustment confirmation
   - Auth: login/logout, expired session, CSRF rejection
   - RBAC: staff blocked from all admin endpoints (ingredients, suppliers,
     settings, users)
   - Supplier archive: warnings, reassignment requirement, reactivation;
     ingredient↔supplier integrity (primary must be a junction row)
   - Ingredient archive preserves transaction history
8. **Docs update** — this file, AGENT_HANDOFF.md, README setup instructions

## Known limitations / open items

- PostgreSQL target is design-only until deployment phase.
- External notification channels (email, LINE, push) are provider stubs until Phase 4.
- Open-order check in the supplier archive guard becomes active in Phase 2
  (purchase orders do not exist yet in Phase 1).
- All prior assumptions confirmed by the Phase 0 review; see
  docs/ARCHITECTURE.md §9.
