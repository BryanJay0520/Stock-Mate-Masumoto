# StockMate — Technical Architecture

Status: Phase 0 — **revised (consolidated review)**. Living document — update it whenever the design changes.

## 1. High-level architecture

A single **monolithic backend** (FastAPI) owns all business logic and the database.
Clients (responsive web frontend now; PWA and native mobile wrappers later) are
**thin clients** that only call the versioned REST API under `/api/v1`.

```
┌─────────────────┐   ┌──────────────────┐   ┌─────────────────┐
│  Web browser    │   │  PWA (later)     │   │ Capacitor app   │
│  (desktop/phone)│   │                  │   │ (later)         │
└────────┬────────┘   └────────┬─────────┘   └────────┬────────┘
         │  HTTPS + JSON /api/v1 (same API for all clients)
         ▼
┌────────────────────────────────────────────────────────────────┐
│ FastAPI web backend (no in-process notification scheduler)     │
│  ├─ api/v1 routers      → request/response, auth checks        │
│  ├─ services/           → ALL business logic (pure, testable)  │
│  ├─ models/             → SQLAlchemy ORM                       │
│  └─ notification API    → persisted in-app records             │
└────────┬───────────────────────────────────────────────────────┘
         ▼
   SQLite (dev) / PostgreSQL (prod) via SQLAlchemy + Alembic
```

The separate `app.notification_worker` process uses the same notification
service and database. It starts only by an explicit command, independently of
FastAPI worker count or browser activity. Email/LINE and device push delivery
are not part of this process.

Key rules:

- **No business logic in the frontend.** Stock math, schedule calculation,
  forecasting, and confidence evaluation all run on the server. This is what
  makes future native mobile apps possible without a rewrite.
- **One shared database** is the single source of truth. Browser
  `localStorage` is never authoritative.
- The web process serves the static frontend. Automatic notifications need a
  second, explicitly launched worker process; the web app remains usable if it
  stops. The API is independent of the bundled frontend.

## 2. Folder structure (target)

```
StockMate/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory, middleware, static files
│   │   ├── config.py            # env-based settings (pydantic-settings)
│   │   ├── database.py          # engine, session, Base
│   │   ├── models/              # SQLAlchemy models, one file per domain
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── api/v1/              # routers: auth, ingredients, suppliers,
│   │   │                        #   inventory, orders, forecast,
│   │   │                        #   notifications, admin, dashboard
│   │   ├── services/            # inventory.py, scheduling.py, forecasting.py,
│   │   │                        #   confidence.py, notifications.py, ordering.py
│   │   ├── auth/                # password hashing, sessions, RBAC dependencies
│   │   ├── notification_worker.py # explicit worker CLI and signal shutdown
│   │   ├── services/notification_scheduler.py # lease, heartbeat, health
│   │   └── seed.py              # fictional demo data (clearly marked)
│   ├── alembic/                 # migrations
│   ├── tests/                   # pytest, mirrors app structure
│   └── requirements.txt
├── frontend/
│   ├── index.html               # dashboard shell
│   ├── pages/                   # one HTML page per main nav section
│   ├── css/                     # base.css, components.css, mobile.css
│   └── js/
│       ├── api.js               # fetch wrapper (single place for API calls)
│       └── modules/             # one JS module per page/feature
├── docs/                        # this documentation set
├── .gitignore
└── README.md
```

Frontend pages map 1:1 to the main navigation: Dashboard, Inventory,
Daily Stock Entry, Suppliers, Purchase Orders, Delivery Calendar,
Forecast Calendar, Notifications, Analytics, Admin Settings.

## 3. API design (REST, versioned)

- Base path: `/api/v1/...` — versioning from day one protects future mobile apps.
- JSON in/out, ISO 8601 timestamps in UTC.
- Auth via **HttpOnly session cookie** (browser-first). Authentication is
  resolved through exactly one FastAPI dependency (`get_current_user`), so a
  Bearer-token path can be added later for native apps without touching
  business logic (see §7 and D7).
- CSRF: token required on mutating requests (double-submit cookie), because we
  use cookie sessions.
- Errors: consistent shape `{ "error": { "code", "message", "details?" } }`.
- Idempotency: stock mutations and delivery confirmations accept a
  client-generated idempotency key; duplicate submissions return the original
  result instead of double-applying (see §5.1).

Endpoints (Phase 1 subset marked •):

- `POST /api/v1/auth/login` •, `POST /api/v1/auth/logout` •, `GET /api/v1/auth/me` •
- `GET/POST /api/v1/ingredients` •, `GET/PATCH /api/v1/ingredients/{id}` •
  (create/edit includes `primarySupplierId` and `alternativeSupplierIds`),
  `POST /api/v1/ingredients/{id}/archive` •, `POST /api/v1/ingredients/{id}/restore` •
- `GET/POST /api/v1/suppliers` •, `GET/PATCH /api/v1/suppliers/{id}` •,
  `POST /api/v1/suppliers/{id}/archive` •, `POST /api/v1/suppliers/{id}/reactivate` •
  (archive returns warnings when supplier serves active ingredients or has open orders)
- `GET /api/v1/suppliers/{id}/ingredients` • (ingredients provided by a supplier)
- `POST /api/v1/inventory/entries` •  (mode: `remaining` | `used`)
- `GET /api/v1/inventory/transactions` •  (filterable history)
- `GET /api/v1/dashboard` •
- `GET/PUT /api/v1/suppliers/{id}/schedule-rules` (Phase 2)
- `GET /api/v1/schedule/opportunities?ingredient_id=…` (Phase 2 — next cutoff + delivery)
- `GET/POST /api/v1/purchase-orders`, `POST /api/v1/purchase-orders/{id}/mark-ordered` (Phase 2)
- `POST /api/v1/purchase-orders/{id}/confirm-delivery` (Phase 2)
- `GET /api/v1/forecast/calendar?days=7&ingredient_id=…` (Phase 3)
- `GET /api/v1/recommendations?ingredient_id=…` (Phase 3 Step 3, read-only order planning)
- `GET /api/v1/notifications` (paginated `all`/`unread`/`urgent`/`history`),
  `GET /notifications/unread-count`, `POST /notifications/evaluate`,
  `POST /notifications/{id}/read|acknowledge`, `POST /notifications/mark-all-read`,
  `GET/PATCH /notifications/settings` (Phase 4 Step 1; paths under `/api/v1`)
- `GET/PUT /api/v1/admin/settings` (Phase 4; minimal subset may appear earlier)

## 4. Database schema

Conventions:

- Surrogate integer PKs; timestamps `created_at`/`updated_at` stored in **UTC**.
- All quantities are `NUMERIC(14, 3)` (exact decimal — never float).
- `business_date` (DATE, computed in the configured business timezone with the
  configured day boundary, see §5.6) is stored on operational records.
- Soft-delete via status everywhere operational (`active`/`archived`).

### 4.1 Core inventory (Phase 1)

**users** — `id`, `username` (unique), `password_hash` (Argon2),
`role` (`admin`|`staff`), `is_active`, timestamps.

**sessions** — server-side sessions: `id`, `token_hash` (unique, indexed),
`user_id` FK, `expires_at`, `created_at`, `revoked_at`. Raw tokens never stored.

**units** — `id`, `name`, `abbreviation` (e.g. "portion", "kg", "L").

**ingredients** — catalog data only (no stock numbers here):
`id`, `name`, `category`, `unit_id` FK, `min_safety_stock`,
`critical_threshold`, `usage_per_serving`, `primary_supplier_id` FK → suppliers
(nullable, see §4.2), `status` (`active`|`archived`), timestamps.

**ingredient_stock_state** — the **one canonical cached balance** location,
exactly 1:1 with `ingredients`:
- `ingredient_id` PK/FK
- `current_quantity` NUMERIC(14,3) — the only cached stock balance in the system
- `last_counted_at`, `last_counted_by` FK users (last physical verification)
- `last_transaction_at`
- `unresolved_discrepancy` bool
- `confidence` (`high`|`medium`|`low`) — derived status, see §5.5
- `version` integer — optimistic-lock counter, incremented on every update

Relationship: `ingredients` describes *what* the item is (catalog);
`ingredient_stock_state` describes *how much we have and how much we trust it*
(operational state). They are created together, never deleted (archive only),
and always joined 1:1. All reads of "current stock" go to
`ingredient_stock_state.current_quantity`; all writes happen through the
ledger protocol in §5.1.

**inventory_transactions** — the **immutable, append-only ledger**; the
authoritative transaction history:
- `id`, `ingredient_id` FK
- `type`: `opening_balance` | `consumption` | `reconciliation` | `delivery_receipt` | `correction` | `adjustment`
- `quantity_delta` NUMERIC(14,3) (signed)
- `resulting_quantity` NUMERIC(14,3) — balance after this entry (for readable history)
- `reason` (`spoilage`|`damage`|`recount`|`correction`|`daily_count`|`delivery`|…) — required for adjustments
- `mode`: `remaining` | `used` | null
- `entered_by` FK users, `business_date`, `created_at` (UTC)
- `idempotency_key` (unique, nullable) — retries/double-submits collapse to one row
- `supersedes_id` (self-FK, nullable) — links a correction to the entry it fixes
- Indexes: `(ingredient_id, id)`, `(business_date)`, unique `(idempotency_key)`

**audit_logs** — `id`, `user_id`, `action`, `entity_type`, `entity_id`,
`before_json`, `after_json`, `created_at`. Written for admin-relevant changes
(ingredient/supplier edits, settings, user management).

**app_settings** — `key` PK, `value_json`; currently holds seed markers.
Business timezone, day boundary and adjustment threshold come from runtime
`Settings`; Phase 4 daily reminder and category flags live in
`notification_settings` (§4.4).

### 4.2 Suppliers & ingredient assignment (Phase 1 CRUD, Phase 2 scheduling)

**suppliers** — `id`, `name`, `contact_name` (nullable), `phone` (nullable),
`email` (nullable), `notes`, `status` (`active`|`archived`), timestamps.
Never hard-deleted (see §5.3 archiving rules).

**supplier_ingredients** — junction, "who can supply what":
- `supplier_id` FK, `ingredient_id` FK, **unique `(supplier_id, ingredient_id)`**
- `package_size` NUMERIC(14,3) (nullable), `min_order_quantity` NUMERIC(14,3) (nullable)

Rules enforced at the database and service layers (D19, D24):

1. One supplier → many ingredients; one ingredient → many suppliers.
2. `ingredients.primary_supplier_id` designates the **single primary** supplier.
3. **Database-enforced integrity:** a composite foreign key
   `ingredients(primary_supplier_id, id)` →
   `supplier_ingredients(supplier_id, ingredient_id)` guarantees the primary
   supplier is always one of the ingredient's junction rows — an invalid
   primary assignment is impossible even via manual SQL.
4. **Status validation:** the service layer rejects assignments and new orders
   referencing a non-`active` supplier (archived suppliers stay readable for
   history but accept nothing new).
5. Alternative suppliers = all other `supplier_ingredients` rows for that ingredient.
6. Changing the primary supplier never touches historical purchase orders
   (orders store their own `supplier_id` permanently).
7. No redundant assignment paths: the junction is the only place that records
   "supplier X provides ingredient Y"; `primary_supplier_id` only *points at*
   one of those rows.

**purchase_orders** (Phase 2) — `id`, `supplier_id` FK (retained forever, even
if the supplier is later archived),
`status` (`draft`|`ordered`|`confirmed`|`partially_delivered`|`delivered`|`delayed`|`cancelled`),
`expected_delivery_date`, `created_by` FK, `ordered_by` FK (nullable),
`ordered_at` (nullable), `notes`, timestamps.

**purchase_order_items** (Phase 2) — `id`, `order_id` FK, `ingredient_id` FK,
`quantity_ordered`, `quantity_received` (default 0).

**Counting packages** (inventory enhancement, migration `0007`):
`ingredients.count_package_enabled`, `count_package_name`,
`count_units_per_package`, and `count_package_version` configure a staff
counting aid in the ingredient's canonical stock unit. The balance remains
solely in `ingredient_stock_state.current_quantity`; package counts never
form another balance. `inventory_transactions.counting_json` preserves the
original full-package/loose-unit breakdown, conversion factor, stock unit,
and configuration version on each packaged stock entry. Plain stock-unit
entries have null breakdowns. A configuration change increments the version
without touching stock or earlier ledger records; stale open forms are
rejected before writing. An idempotent retry of an already written count can
still return the original record after configuration changes.
The development demo seed now creates Korean Noodles in `pc` with a 60-piece
Box and 180-piece opening balance. It also uses `pc` for Chinese Noodles,
Eggs, and Chashu and `pack` for Soup Base. Changing an ingredient's canonical
unit after stock history exists remains blocked; the guarded development-only
reset replaces the old fictional rows without relabeling historical entries.

**Purchasing packages** stay supplier-specific on `supplier_ingredients`:
`package_size` is canonical stock units per purchase package,
`purchase_package_name` is the supplier's label, and `whole_packages_only`
controls future whole-package recommendations. These fields are independent
of the ingredient's counting package. Phase 3 can calculate whole purchase
packages with `ceil(required_units / supplier.package_size)` for each chosen
supplier; no forecasting calculation is implemented in this enhancement.

**delivery_receipts** (Phase 2, D37) — `id`, `purchase_order_id` FK,
`received_by` FK (authenticated account), `received_by_staff_name` (required,
self-reported employee name), `received_at` (UTC), `receipt_date` (business
date), `notes`, unique `idempotency_key`, `reversal_of_id` self-FK, `created_at`.
Legacy receipts migrated from before staff-name collection retain an empty
name; the UI labels it "Not recorded" rather than inventing an employee.

**delivery_receipt_items** — receipt/order-item/ingredient FKs, `quantity`
(accepted), `quantity_rejected`, nullable unique `inventory_transaction_id`.
Only accepted quantities increase stock and fulfill orders; rejected goods
remain outstanding. Fully rejected lines create no stock ledger entry.
Reversals append negative accepted/rejected quantities and compensating ledger
rows. History derives outstanding after each receipt from immutable accepted
quantities in receipt-ID order; later receipts never rewrite earlier history.
Migrations: `0004_delivery_receipts` → `0005_receipt_rejected_qty` →
`0006_receipt_staff_name`.

**supplier_schedule_rules** (Phase 2) — repeatable weekly rules, multiple rows
per supplier (optionally per ingredient):
- `id`, `supplier_id` FK, `ingredient_id` FK (null = supplier-wide rule)
- `delivery_weekday` (0–6)
- `order_cutoff_days_before` (smallint), `order_cutoff_time` (local time)
- `order_open_days_before`/`order_open_time` (nullable — ordering window start)
- `reminder_days_before` (smallint), `reminder_time` (local time)
- `is_active`

**calendar_exceptions** (Phase 2) — `id`, `supplier_id` FK (null = global),
`date`, `type` (`holiday`|`closed`|`special`), `blocks_delivery` bool,
`blocks_ordering` bool, `note`. Per-supplier holidays override weekly rules.

### 4.3 Forecasting (Phase 3)

**ingredient_demand_settings** (Step 1, migration `0008`, D39) —
`ingredient_id` PK/FK, `enabled`, seven nullable `NUMERIC(14,3)` columns
`monday` through `sunday`, nullable `safety_stock`, optional `notes`,
`version`, `created_at`, and `updated_at`. Each quantity has a nonnegative
database check. No row is inserted by migration or demo seeding. Null is an
unknown estimate, distinct from zero. This replaces the earlier planned
global weekday serving defaults for ingredient forecasting.

Read/update API: `GET/PATCH /api/v1/ingredients/{id}/demand-settings`.
Reads synthesize an unconfigured view without inserting a row. PATCH updates
only supplied fields; null clears a quantity. `expected_version=0` creates a
first row, subsequent updates require the current version. Admin + CSRF writes
reuse the existing atomic transaction and audit conventions. Staff can read.
The response exposes configured day count, complete/forecast-ready flags, and
an exact weekly sum only if all seven days are known. Enabled partial rows
remain incomplete. The planning safety quantity does not change the existing
ingredient inventory warning threshold.

**Future date overrides** will be keyed by ingredient and business date and
override that ingredient's weekday estimate. They are not implemented in Step 1.

**confidence_config** — `ingredient_id` PK/FK,
`high_freshness_hours` (default 24), `medium_freshness_hours` (default 72),
`requires_daily_verification` bool. Per-ingredient, admin-tunable.

### 4.4 Notifications (Phase 4 Step 1, migration `0009`)

**notification_events** records six types: `daily_count`, `order_cutoff`,
`supplier_contact`, `forecast_shortage`, `verify_pending_order`, and
`pending_delivery`. Each has an explicit priority, current status
(`active`/`resolved`/`expired`/`superseded`), title/message, plain structured
snapshot, optional source FKs and delivery-cycle identity, business date, UTC
action/creation/update/closed/resolved timestamps, and a safe internal route.
`condition_key` identifies the business condition; unique `dedupe_key` is that
key plus an episode suffix if a resolved condition genuinely reappears. Source
records may be archived; snapshot text keeps old notices understandable.

**notification_user_states** has unique `(notification_id, user_id)` and separate
`read_at` and `acknowledged_at`. Neither represents business completion. A shared
operational login therefore shares its read/ack state; separate users remain
independent. The one-business-scope database and existing RBAC remain intact.

**notification_settings** has a singleton row, created by an explicit evaluation
or admin update, for six category enable flags, a daily count reminder time
(default 21:30 in the configured business timezone), and last evaluation time.
Migration `0010_notification_scheduler` adds `automatic_evaluation_enabled`
(default false) and `evaluation_interval_seconds` (default 60, accepted 60–900).
**notification_count_rules** opts individual active ingredients into daily
physical-count reminders. None are opted in by the migration or demo seed.
Used Stock and receipts never satisfy a physical count.

**notification_worker_state** is a singleton execution record (migration 0010)
with an owner token, expiring lease, heartbeat, last attempt/success/failure,
sanitized error type and latest result counts. It stores no account credentials.

No delivery-attempt or external-channel table is created in Step 1. Future
email/LINE/push delivery must track attempts separately from event and read
state, without treating provider success as acknowledgement or resolution.

## 5. Business logic

### 5.1 Inventory ledger, balance consistency, and concurrency

**Source of truth hierarchy:**

1. `inventory_transactions` (append-only) — authoritative history.
2. `ingredient_stock_state.current_quantity` — the **single** cached balance,
   always derivable by replaying the ledger. No other table caches stock.

**Opening balances (D23):** every initial quantity — including all fictional
demo seed stock — is recorded as an `opening_balance` ledger transaction
before any other entry for that ingredient. The ledger alone therefore always
reconstructs the cached balance exactly; there is no "magic" starting number
that exists only in the cache.

**Atomic write protocol (every stock mutation):**

1. Receive `{ingredientId, mode: "remaining"|"used", quantity, reason?, idempotencyKey}`.
2. Begin the transaction **as a write transaction from the start**:
   `BEGIN IMMEDIATE` on SQLite (acquires the reserved write lock immediately,
   so a transaction never starts as a reader and deadlocks upgrading to a
   writer); a normal transaction on PostgreSQL. This is the SQLite-safe
   strategy — we do **not** rely on `SELECT … FOR UPDATE`, which SQLite does
   not support (D15).
3. **Idempotency check:** if `idempotency_key` already exists in the ledger,
   return the original result — no state change. The unique constraint is the
   final guard: a racing duplicate insert raises `IntegrityError`, which we
   catch and translate into returning the original result.
4. Read the current balance from `ingredient_stock_state` (safe under the
   write transaction).
5. Compute delta: `used` → `delta = -quantity`;
   `remaining` → `delta = counted - current` (reconciliation, not consumption).
6. Guards: reject negative results unless caller is admin **and**
   `allowNegative=true`; flag `requiresConfirmation` when `|delta|` exceeds the
   admin-configured large-adjustment threshold (client must re-submit with
   `confirmed=true`).
7. **Atomic conditional UPDATE** — the balance changes in a single statement
   whose `WHERE` clause re-checks the guard:

   ```sql
   UPDATE ingredient_stock_state
   SET current_quantity   = current_quantity + :delta,
       last_transaction_at = :now,
       last_counted_at     = CASE WHEN :is_reconciliation THEN :now ELSE last_counted_at END,
       last_counted_by     = CASE WHEN :is_reconciliation THEN :user ELSE last_counted_by END,
       version             = version + 1
   WHERE ingredient_id = :id
     AND (:allow_negative OR current_quantity + :delta >= 0)
   ```

   Exactly one row must be updated. A rowcount of 0 means a concurrent change
   made the result negative → roll back and return a conflict error. This
   statement is atomic on **every** database, including SQLite.
8. Insert the ledger row with `resulting_quantity` = the new balance.
9. Commit. Any failure rolls back the ledger row and balance update together.
10. **Bounded retry:** `SQLITE_BUSY` / deadlock errors retry the whole block
    up to 3 times with short backoff, then return a "please retry" error.
    Because the idempotency key is checked first, client retries are always
    safe.

**Concurrency notes by database:**

- *SQLite (dev, MVP):* WAL mode + busy timeout + `BEGIN IMMEDIATE` serialize
  writers safely; concurrent writes queue instead of failing. Concurrent-entry
  tests assert *final correctness* (balance = sum of accepted deltas, one
  ledger row per accepted entry), never a specific interleaving.
- *PostgreSQL (prod):* the same conditional UPDATE is sufficient; a
  `SELECT … FOR UPDATE` may be added as an extra optimization but is never
  required for correctness. The `version` column supports optimistic checks
  for read-modify-write API flows.

**Corrections** never edit history: a new `correction` row with `supersedes_id`
points at the mistaken entry and applies its own delta through the same protocol.

**Recovery & reconciliation procedures:**

- **Verify:** admin/debug operation that replays
  `SUM(quantity_delta)` per ingredient and compares it to
  `ingredient_stock_state.current_quantity`. Any drift is reported.
- **Repair:** if drift is found (should be impossible given the protocol, but
  covers manual DB edits or restored backups), the repair operation rewrites
  the cached balance from the ledger inside a transaction, writes an
  `audit_logs` entry, and sets `unresolved_discrepancy` on the state row until
  an admin confirms. Confidence drops to LOW automatically.
- **Backup:** SQLite file copies (or `pg_dump` in prod) on a schedule defined
  in Phase 6; restore + verify is part of deployment readiness testing.

### 5.2 Supplier management & archiving (Phase 1)

- Supplier form fields: name (required), contact person, phone, email, notes,
  status. Admin-only.
- **Archive, never delete.** Archiving a supplier:
  - Hides it from new-order and ingredient-form selection (only `active`
    suppliers appear in dropdowns).
  - Keeps it fully visible in historical purchase orders and admin review views.
  - Preserves all related records untouched.
- **Archive guard:** before archiving, the server checks (a) ingredients whose
  *primary* supplier this is, and (b) open purchase orders (`draft`, `ordered`,
  `confirmed`, `partially_delivered`, `delayed`). The response includes these
  warnings; the admin must confirm, and every affected primary assignment must
  be reassigned to another active supplier of that ingredient (or cleared)
  before archiving completes.
- Reactivation restores availability for new orders.
- Ingredient form: name, category, unit, current quantity (initial balance —
  afterwards only via stock entries), safety threshold, **primary supplier
  dropdown** (active suppliers only) and optional alternative suppliers.

### 5.3 Ordering schedule engine (Phase 2)

Input: `supplier_schedule_rules` (+ per-ingredient override rows) and
`calendar_exceptions` (global or per-supplier). Rules are **per supplier, per
delivery cycle** — Korean Noodles (Supplier 1) and Chinese Noodles
(Supplier 2) share nothing unless configured to.

**Deadline states** (computed, never stored):

- `upcoming` — cutoff is in the future and no covering order exists; reminder
  schedule applies.
- `covered` — a non-draft, non-cancelled order exists for that supplier and
  delivery date (reminders suppressed).
- `missed` — cutoff has passed and no covering order exists; clearly marked,
  escalated per config.
- `expired` — the delivery date itself has passed; the opportunity is closed
  and only relevant historically.

**Boundary case — order exactly at cutoff:** the cutoff is **inclusive**
(decision D16). An order placed at exactly `cutoff_time` counts as on time
(`placed_at <= cutoff` in UTC after timezone conversion). The engine compares
instants, never local wall-clock strings.

**Multi-ingredient orders:** reminders are keyed by
`supplier + delivery_date`, not by ingredient (D20). One purchase order
covering many ingredients produces exactly one deadline reminder event; the
payload lists the affected ingredients. No duplicate per-ingredient reminders.

**Next-opportunity algorithm:** scan forward from "now" over candidate delivery
dates (rule weekdays, skipping exception dates that block delivery); for each,
compute `cutoff = delivery_date - cutoff_days_before at cutoff_time`
(business tz → UTC) and the reminder instant. The first cutoff still in the
future (and inside the optional ordering window) is the active opportunity.
Exceptions that block ordering move the cutoff evaluation to the next
candidate.

Example — noodles Monday delivery: `delivery_weekday=0`,
`order_cutoff_days_before=1`, `order_cutoff_time=14:00`,
`reminder_days_before=2`, `reminder_time=21:30` → Saturday 21:30 JST reminder,
Sunday 14:00 JST cutoff (inclusive). Wednesday/Friday rules are separate rows
with their own values — nothing is inferred from the Monday rule.

### 5.4 Forecast & the four stock numbers (Phase 3)

**Step 2 implementation (D40, corrected by D41):** `app/services/forecast.py` supplies the
authenticated, read-only `GET /api/v1/forecast/calendar` API. `days` accepts
7/14/30; optional `ingredient_id` restricts the response to one active ingredient.
Without that filter one batch contains all active ingredients, both scenarios,
supplier cutoff information, and a dashboard summary. All quantity fields are
decimal strings. No schema migration or persistent forecast table is required;
head remains `0008_ingredient_demand_settings`.

The service accepts an injected current clock for tests; HTTP clients cannot
request historical forecasts using today's balances. It verifies ledger sums
against cached states before projecting, uses a consistent read transaction,
and never calls ledger repair, inventory mutation, or order mutation services.
The response includes UTC and business-local as-of timestamps, the business
date, projection start/end dates, and explicit baseline/delivery assumptions.

The response separates `scenario.today` from 7/14/30 future `scenario.days`.
Today's opening is actual stock at the as-of instant; remaining usage defaults
to today's full-day estimate as a conservative upper bound. An optional decimal
`today_remaining_usage` override accepts zero, scoped by `ingredient_id` or
`today_usage_ingredient_id` for a batch. The UI supplies `today_usage_date`;
the server rejects a stale date with 409 `today_override_expired`. Blank/absent
uses the default; a missing estimate remains unknown. No elapsed-hours usage is
inferred, and no transactions are interpreted as demand. Today's eligible
outstanding arrivals are included; today's projected close feeds tomorrow's
opening. The dashboard uses this same default policy. Overrides are not stored.

The system keeps four distinct numbers per ingredient, never conflated:

| Number | Definition |
|---|---|
| **Actual stock** | `ingredient_stock_state.current_quantity` — physically available, from confirmed receipts, consumption, reconciliations |
| **Ordered (unconfirmed) incoming** | Quantity on POs in `ordered` status — expected but not supplier-confirmed; shown as uncertain |
| **Confirmed incoming** | Eligible confirmed POs, plus partial remainders with matching supplier-confirmation audit evidence (D41) |
| **Projected stock** | Day-by-day estimate: actual + reliable arrivals − forecast consumption |

Draft POs are **never** supply (D9). Unconfirmed ordered quantities are
displayed separately and excluded from the conservative projection (or shown
as an optimistic range, labeled).

**Intraday delivery timing (D41 supersedes the original D21 timing assumption):**
arrival times are unknown. Closing includes eligible deliveries on their date,
while a separate before-delivery balance deducts demand first. Negative
before-delivery stock with eligible incoming produces a red shortage-risk badge,
even if the closing-stock status is Enough. No arrival time or timing setting
is invented.

**Projection walk** (per ingredient, server-side):

Step 2 uses configured weekday estimates for future dates. Today's temporary
remaining-usage override is independent of permanent demand settings; future
date overrides are not implemented.

```
dailyUse(d) = today's explicit remaining usage if supplied (including zero)
              otherwise ingredientWeekdayEstimate[weekday(d)]

for today, then each future business date in horizon (7/14/30):
  incoming(d) = Σ scenario-eligible outstanding PO items with expected_delivery_date = d
  opening     = previous closing (starts from actual stock)
  beforeDelivery = opening − dailyUse(d)
  preDeliveryRisk = incoming(d) > 0 and beforeDelivery < 0
  closing     = opening + incoming(d) − dailyUse(d)
  status      = unknown  if required inputs are missing
                shortage if closing < 0
                depleted if closing == 0
                low      if 0 < closing < demand.safety_stock
                enough   otherwise (positive and >= demand.safety_stock)
```

**Future missing-input behavior (D39):** disabled configurations are excluded.
Missing weekday consumption is not zero: the first missing day and all following
balances become null/Unknown, even if a later delivery is known. A prefix may
remain calculable when its estimates and planning safety are available. A missing
safety buffer or absent/disabled settings leave projections unknown. Explicit
zero remains valid. Count freshness is a separate confidence indicator; stale
counts warn without changing valid arithmetic. Negative balances carry unmet
demand into subsequent days until projected supply covers the backlog.

**Delivery scenarios and eligibility:** conservative uses eligible confirmed
orders and partial remainders with matching supplier-confirmation audit evidence;
expected additionally uses eligible unconfirmed orders/remainders. Partial status
alone is insufficient. The read-only evidence scan matches supplier, delivery
date, ingredient, and ordered quantity; invalidating transitions discard old
confirmation (D41). Outstanding uses accepted
received quantities only, preserving rejected replacements as owed. Drafts,
cancelled and fully received orders never add supply. Delayed, overdue,
missing-date, supplier-cancelled, and out-of-range
arrivals are excluded without moving their dates, and are exposed separately.
The current order schema requires a date; an explicit defensive guard also
rejects undated legacy/malformed inputs without inventing a date.

Applicable weekly schedule conflicts are expected-only; extra-delivery
exceptions permit their dates. Ordering suspensions do not cancel already
placed deliveries. Supplier delivery cancellations exclude them from both
scenarios. Cutoff changes remain scheduling information and never alter a PO.
Each supplier's opportunity calendar is computed once per batch and filtered
to the ingredient's general/additive rules, including alternative suppliers.
Accepted goods already in actual stock are never added to incoming twice.

Eligible due-today deliveries participate in today's preview. Accepted quantities
already in actual stock are excluded from outstanding incoming. Each daily
response includes `pre_delivery_balance`, `pre_delivery_shortage`, and
`pre_delivery_shortage_risk`, beside the signed closing balance and its status.
Scheduled quantities remain projected until actual receiving. Summary risk dates
include the separate current-day preview and future dates.

**Two independent indicators:** every forecast card shows **Supply risk**
(green/yellow/red/gray, from the walk above) and **Data confidence**
(high/medium/low, §5.5) side by side. A projection can be supply-SAFE and
confidence-LOW at once, with an explanatory warning.

**Order quantity recommendation:** cover demand from the delivery date until
the *subsequent* delivery opportunity, plus safety buffer, minus actual stock
and already-confirmed incoming; round up to `package_size`, respect
`min_order_quantity`. The API returns all calculation inputs with the result
(explainable), respects outstanding orders (no duplicate replenishment), and
never auto-submits.

**Step 3 implementation (D42):** `services/recommendations.py` reuses one 30-day
`generate_forecast` batch and the existing per-supplier opportunity engine. Its
structured response is in `schemas/recommendations.py`; the authenticated
`GET /recommendations` route supports the same ingredient-scoped temporary
remaining-usage override and optional business-date guard as Forecast Calendar.
No table or migration is needed; head remains 0008. Both staff and administrators
may read advice. All operational mutations continue through existing services.

The service chooses the earliest primary-supplier delivery whose opportunity is
open or upcoming, then the next distinct valid delivery date within 30 days.
Ingredient-specific schedule rows remain additive. Opening windows are displayed
and upcoming windows are explicitly not open yet. Inclusive cutoff and supplier
timezone calculations are owned by `scheduling.py`; expired or blocked cycles
are skipped without changing an order's expected date. A one-time extra delivery
obeys cancellations, suspensions, and effective cutoff overrides too.

Coverage starts on the proposed delivery and ends the day before the following
actionable delivery. Starting stock is that date's forecast opening, including
only already-dated eligible incoming before it. Each existing order line exposes
its item ID, outstanding/accepted quantities, eligibility, and contribution phase.
Within the cycle, daily incoming is applied once on its date. Additional quantity
is the largest daily deficit against safety across the coverage period, clamped
to zero. This protects earlier dates from being falsely covered by later stock.
The displayed demand-plus-safety target is separate from the time-phased result.

Purchasing rounds with supplier-junction size/name/whole-package/minimum fields.
Counting packages never participate. Zero need is not raised to a minimum.
Missing purchasing configuration leaves raw required units visible while package
and recommended quantity remain unknown. Non-whole purchasing shows exact base
units; rounded quantities respect NUMERIC(14,3) limits.

Conservative and Expected calculations keep D41's eligible incoming split. If a
pending shipment changes the result, Verify Pending Order appears with order and
contact references; potential conservative quantities are not unconditional
ordering instructions. Drafts are visible without counting as supply. Earlier
negative closings suppress quantities until physical/manual review; signed unmet
demand is never silently converted into a customer backlog. Same-day risk remains
separate from quantity and closing status. Alternatives are manual, unconfirmed
opportunities and never duplicate ingredient requirements or allocate quantities.

The dashboard obtains one recommendation batch, reuses its forecast summary
limited to today plus seven future dates, and renders ordering priorities and
upcoming cutoffs. Order Planner uses native ingredient/scenario selectors over
one batch, temporary remaining-usage controls, expandable daily calculations,
supplier contacts, existing-order contributions, and links into canonical detail
dialogs. No purchasing or supplier-contact side effect exists in the planner.

### 5.5 Stock Confidence Indicator

Step 2 uses `services/confidence.py` as a read-only view of the existing count
timestamp, stored confidence, and discrepancy flag. Freshness caps are Settings
fields `count_high_freshness_hours` and `count_medium_freshness_hours` (defaults
24/72, positive and ordered). No `confidence_config` table or new admin screen
is introduced. The stored level is never upgraded by this read. Future count
timestamps, missing counts, and discrepancy flags produce Low confidence.
All projected days carry this as-of confidence; no future count is assumed.

Derived from ledger facts, per ingredient, against `confidence_config`
thresholds (D10):

- **high** — latest `reconciliation` entry within `high_freshness_hours`,
  no unresolved discrepancy.
- **medium** — count older than high but within `medium_freshness_hours`;
  consumption being recorded without recent verification.
- **low** — no count on record, count older than medium threshold, or
  `unresolved_discrepancy` set.

Only `reconciliation` (mode `remaining`) entries count as physical
verification (D3). Descriptive levels only — no fabricated percentages.

### 5.6 Business dates & timezone

- All timestamps stored in UTC (D5).
- Business timezone from `app_settings.business_timezone`, default `Asia/Tokyo`.
- **Business-day boundary is configurable** (D18):
  `app_settings.business_day_start_time`, default `00:00`. A restaurant open
  past midnight can set e.g. `04:00`; then 02:30 on Tuesday wall-clock still
  belongs to *Monday's* business date. All operational records store the
  derived `business_date`, and all daily grouping/reporting uses it.
- Conversion happens only at the edges: API input/output and schedule
  computation convert UTC ⇄ business tz in the service layer.

### 5.7 Explicit notification evaluation (Phase 4 Step 1)

Authenticated staff or administrators call CSRF-protected
`POST /api/v1/notifications/evaluate`. Ordinary GETs, application startup,
and the dashboard never evaluate or write. The injectable UTC clock and
business-date boundary support deterministic tests. Evaluation first derives
conditions through existing scheduling, forecast, recommendation, ledger and
order services, then persists only notification rows/settings. A SQLite
`BEGIN IMMEDIATE` write-intent transaction serializes concurrent evaluations;
unique keys and bounded transaction retries protect replays. Inventory, POs,
receipts and their ledgers are never mutated by evaluation.

Daily count uses only `reconciliation`/`remaining` entries on the applicable
business date. It aggregates missing configured ingredients after the selected
local reminder time. Order deadlines use supplier-specific reminder/cutoff
instants and one notice per supplier/date, with confirmed incoming already
subtracted by D42. Earlier shortages withhold numeric order instructions;
uncertain supply triggers separate order-verification notices. Required supplier
contact starts 24 hours before its own deadline and never claims contact
completion. Forecast alerts distinguish negative closes from possible
pre-delivery shortages, actual stock from projected stock, and stale/uncertain
inputs. Pending deliveries use ordered minus accepted received, retain their
original expected date and do not infer a new arrival.

Priority rules: shortage within two days is critical, later shortage high;
order cutoff within 24 hours high, earlier normal; required contact within 12
hours high, earlier normal; delivery two or more days overdue or explicitly
delayed high, otherwise normal; count and pending-order verification normal.
These are documented Step 1 thresholds, not inferred urgency for every event.
Expired cutoffs/contact deadlines become expired, changed cycles superseded,
and business conditions that are demonstrably satisfied resolved. Unknown
forecast/demand inputs preserve prior unresolved risk. Reading and acknowledging
do not close an event. A material active-message update resets account read/ack
so new instructions are seen. Resolved history is retained; reappearance gets
a new episode key. Last evaluation time is exposed; the UI flags snapshots
older than one hour as needing refresh before action.

Step 2's explicit worker calls this same evaluator. It takes a 180-second
database lease using a short SQLite `BEGIN IMMEDIATE` transaction and checks
ownership again inside the evaluation transaction. A second worker cannot
run a simultaneous valid evaluation; manual refresh serializes through the
same database write intent and event keys. The worker polls every 30 seconds,
evaluating on the first enabled check and then at the configured 60–900-second
interval. It logs recoverable errors and backs off to at most 300 seconds.
Crash recovery waits for lease expiry, then evaluates current conditions once;
it does not replay each missed minute or invent historical stock snapshots.
Current-day counts can catch up after their reminder time. Past supplier
cutoffs remain expired; the schedule engine supplies later valid cycles.
`GET /notifications/scheduler-health` is administrator-only and read-only.
The bell and open notification/dashboard views poll read-only data once a
minute while visible. No browser tab generates notification records.
Push, email and LINE delivery remain separate future work.

### 5.8 Authentication & mobile readiness

- **Phase 1 (web):** Argon2 password hashing; server-side sessions
  (HttpOnly + Secure + SameSite cookies, idle/absolute expiry, revocation on
  logout/password change); CSRF token on mutations; RBAC enforced by FastAPI
  dependencies on every endpoint (`require_staff`, `require_admin`) — never
  only in the UI.
- **Future native auth:** all auth resolution flows through the single
  `get_current_user` dependency. A Bearer-token path (short-lived access token
  + refresh token) will be added alongside cookie auth when Capacitor work
  begins — no business-logic changes required (D7).
- **API independence:** the backend never serves or assumes frontend-specific
  behavior; all clients use the same documented `/api/v1` contract.

## 6. Security

- Argon2 hashing; server-side sessions with expiry and revocation.
- RBAC server-side on every endpoint; admin APIs are unreachable for staff.
- Pydantic validation on all input; parameterized ORM queries only.
- CSRF tokens; XSS-safe frontend (no `innerHTML` with API data — `textContent`
  or an escaping helper); security headers (CSP, X-Content-Type-Options,
  Referrer-Policy).
- Audit logs for admin actions; secrets via environment variables only (D13).
- SQLite/PostgreSQL backups scheduled in Phase 6; restore procedure tested.

## 7. Mobile readiness

- All clients share `/api/v1`; auth abstraction allows adding Bearer tokens later.
- Mobile-first CSS (bottom nav on small screens, touch targets ≥ 44px, no
  horizontal overflow).
- PWA milestone (later phase): manifest, icons, service worker for static
  assets, connection-status indicator. Offline stock entry is explicitly
  **deferred**; when added it will use a queued, idempotent sync with conflict
  detection — newer server records are never silently overwritten.
- Capacitor evaluation is a future milestone; nothing in the architecture
  (no browser-only business logic, static frontend build) blocks it.

## 8. Testing strategy

- pytest + FastAPI `TestClient` against a temporary SQLite database
  (WAL mode, same pragmas as dev).
- Services receive an injected clock — time-dependent logic never calls
  `now()` directly deep in the code.
- **Phase 1 must-have scenarios** (details in docs/PROJECT_STATUS.md):
  - Ledger math for both entry modes; reconciliation ≠ consumption (D3).
  - **Concurrent stock entries:** parallel requests against the same
    ingredient must yield the exact final balance (sum of all deltas), no lost
    updates, and a ledger row per accepted entry.
  - Idempotency: duplicate submission (same key) → single effect, original
    response returned.
  - Negative-stock prevention + explicit admin override path.
  - Large-adjustment confirmation flow.
  - Auth: login/logout, session expiry, CSRF rejection.
  - RBAC: staff blocked from every admin endpoint (ingredients, suppliers,
    settings, users).
  - Supplier archiving: guard warnings, reassignment requirement, historical
    visibility retained.
  - Ingredient archive preserves transaction history.
- Frontend: manual responsive checklist in early phases.

## 9. Risks & assumptions (confirmed unless noted)

1. **Single restaurant, single tenant.**
2. **Dedicated worker process** for automatic evaluation; SQLite lease
   coordinates accidental duplicate workers (§5.7).
3. **UI language: English** first; strings centralized for later Japanese localization.
4. **Session-cookie auth now**, Bearer tokens added for native apps later.
5. **Decimal quantities** ("portions" and convertible units); full unit
   conversion deferred, schema leaves room via `units` + `package_size`.
6. Demo data is visibly labeled fictional in the UI. Demo catalog now includes
   **Korean Noodles** and **Chinese Noodles** as separate ingredients with
   independent stock, assigned to different suppliers (per requirements).
7. **Open item:** none blocking — prior assumptions A1–A7 were confirmed by
   the Phase 0 review; remaining open choices are defaults only.
