# StockMate — Decisions Log

Numbered decisions. New agents must read this before changing design.
Append new decisions; do not silently change old ones — supersede them instead.

## D1 — Monolith with thin clients

Single FastAPI process owns all business logic; frontend and future mobile
apps are thin clients over `/api/v1`. Rejected: frontend-heavy SPA frameworks,
microservices. Rationale: learning-friendly, one deployable, and the only way
to guarantee identical behavior across web and future native apps.

## D2 — Append-only inventory ledger

`inventory_transactions` is immutable and authoritative. Corrections create new
rows (`supersedes_id`) rather than editing history. Rationale: auditability,
double-count prevention, re-derivable balances.

## D3 — Two entry modes are distinct transaction types

`mode = "remaining"` creates a **reconciliation** (delta vs. current stock);
`mode = "used"` creates a **consumption**. They are never interchangeable, and
a reconciliation — not a "used" entry — is the only thing that counts as a
physical stock verification for the Confidence Indicator. Confirmed deliveries
do not count as physical verification either.

## D4 — Exact decimal quantities

`NUMERIC(14, 3)` everywhere; floats are forbidden for quantities.

## D5 — UTC storage, business timezone at the edges

All timestamps in UTC; `business_date` and all schedule math use the
configured timezone (`app_settings.business_timezone`, default `Asia/Tokyo`).
Timezone is admin-configurable, not hardcoded.

## D6 — Idempotency keys on all stock mutations

Client generates a key per user action; unique constraint collapses retries
and double-submits. Applies to stock entries and delivery confirmations.

## D7 — Session cookies now, tokens later

HttpOnly session cookies + CSRF for the browser. All auth resolution flows
through one FastAPI dependency (`get_current_user`), so a Bearer-token path
(access + refresh) can be added for native apps without touching business
logic. No JWT until there is a concrete need.

## D8 — Draft orders never count as supply

Only `confirmed`/`partially_delivered` purchase orders feed "confirmed
incoming" and the conservative projection. `ordered` (unconfirmed) quantities
are tracked and displayed separately as uncertain. Drafts are planning
artifacts. Forecasts stay conservative when deliveries are delayed or
unconfirmed.

## D9 — Confidence is derived, not stored opinions

Confidence status is computed from ledger facts (last reconciliation age,
unresolved discrepancy flag) against per-ingredient admin thresholds.
Descriptive levels only (high/medium/low) — no fabricated percentages.
Displayed independently from supply-risk status.

## D10 — Forecasts are deterministic and explainable

Weekday servings (with date overrides) × usage per serving, walked day by day.
Every projected number must be reproducible from displayed inputs. No external
AI/ML services in MVP; simple statistics may be added in Phase 5.

## D11 — Soft delete everywhere operational

Ingredients and suppliers are archived, never deleted. Historical
transactions, orders, and reports must survive catalog changes. Archived
suppliers stay visible in historical purchase orders and admin review.

## D12 — No secrets in the repository

All credentials via environment variables (`.env`, gitignored). Notification
providers must disable safely when unconfigured.

## D13 — PWA/Capacitor deferred but unblocked

MVP is a responsive web app. PWA in a later phase; Capacitor evaluation is a
future milestone. No browser-only business logic that would block mobile
packaging. Offline stock entry is deferred; when built it must use idempotent
queued sync with conflict detection.

## D14 — One canonical cached balance (revised in Phase 0 review)

The only cached stock balance is `ingredient_stock_state.current_quantity`
(1:1 with `ingredients`). `ingredients` holds catalog data only. Every stock
write updates the ledger row and the cached balance in one DB transaction,
serialized per ingredient via a row lock (`SELECT … FOR UPDATE`); a `version`
column supports optimistic checks. Verify/repair procedures replay the ledger
to detect and fix drift (see ARCHITECTURE.md §5.1).

## D15 — Concurrency strategy (revised for SQLite safety)

Do **not** rely on `SELECT … FOR UPDATE` (unsupported by SQLite). The portable
strategy is: `BEGIN IMMEDIATE` write transactions on SQLite, an **atomic
conditional UPDATE** (`UPDATE … SET qty = qty + :delta WHERE …
AND qty + :delta >= 0`, requiring rowcount = 1) for the balance change, a
unique-constraint-backed idempotency check, and bounded retries (3 attempts,
short backoff) on `SQLITE_BUSY`/deadlock. SQLite runs in WAL mode with a busy
timeout; concurrent tests assert final correctness, not interleaving.
`FOR UPDATE` may be added on PostgreSQL later as an optimization only.

## D16 — Cutoff time is inclusive

An order placed at exactly the cutoff instant counts as on time
(`placed_at <= cutoff` compared as UTC instants). Deadlines are classified as
`upcoming` / `covered` / `missed` / `expired` — computed, never stored.

## D17 — APScheduler 3.x, single process

Pin `apscheduler>=3.10,<4` with a SQLAlchemy job store. The scheduler runs
inside the single FastAPI process for MVP (`coalesce=True`, `max_instances=1`,
`misfire_grace_time` set). Startup catch-up dispatches overdue pending events;
unique `dedupe_key`s make replays harmless. Scale-out path: web workers
without scheduler + exactly one dedicated scheduler process
(`SCHEDULER_ENABLED=true`); DB leader-election only if ever needed.

## D18 — Configurable business-day boundary

`app_settings.business_day_start_time` (default `00:00`) defines when the
business date rolls over, in the business timezone. A 04:00 boundary assigns
the post-midnight hours to the previous business date. All daily grouping uses
the stored `business_date`.

## D19 — Ingredient–supplier model

`supplier_ingredients` (unique `(supplier_id, ingredient_id)`) is the only
record of "supplier X provides ingredient Y". `ingredients.primary_supplier_id`
points at one of those rows (service-enforced: a primary must also exist in
the junction). Alternative suppliers = remaining junction rows. Changing the
primary never modifies historical purchase orders — orders permanently store
their own `supplier_id`. Similar-but-different items (e.g. Korean Noodles vs
Chinese Noodles) are separate ingredients with independent stock, never merged.

## D20 — Reminders are keyed per supplier + delivery date

A multi-ingredient order produces exactly one deadline reminder (payload lists
the ingredients). No per-ingredient duplicate reminders for the same delivery
cycle.

## D21 — Intraday delivery availability

Expected deliveries count as available from the start of their delivery
business day (morning-delivery default). Admin setting
`delivery_availability` (`same_day`|`next_day`) switches to the conservative
rule where arrivals count only from the next business day.

## D22 — Phase 1 includes basic supplier management

Supplier CRUD, archive/reactivate with guards, ingredient↔supplier assignment
(primary + alternatives) are Phase 1 scope. Schedule rules, delivery
calendars, purchase orders, and supplier reminders are Phase 2 scope.

## D23 — Opening balances are ledger transactions

All starting quantities — including fictional demonstration seed stock — are
recorded as `opening_balance` transactions in the ledger before any other
entry. The append-only ledger can therefore always reconstruct the cached
balance from zero; no quantity ever exists only in the cache.

## D24 — Supplier relationship integrity is DB-enforced

A composite foreign key `ingredients(primary_supplier_id, id)` →
`supplier_ingredients(supplier_id, ingredient_id)` makes an invalid primary
assignment impossible at the database level (a primary must be a junction
row). The service layer additionally validates that the supplier is `active`
when assigning or ordering; archived suppliers remain readable for history.
Foreign key enforcement is enabled explicitly on SQLite
(`PRAGMA foreign_keys=ON`).

## D25 — CSRF via signed double-submit token

Because we use cookie sessions, every mutating endpoint (except login/logout)
requires the `X-CSRF-Token` header to match the `stockmate_csrf` cookie. The
token is the session token's hash signed with the server secret
(itsdangerous), so it is bound to the session, unforgeable for other
sessions, and requires no extra database columns. The CSRF cookie is
non-HttpOnly by design (our JS must read it); the session cookie stays
HttpOnly.

## D26 — Login rate limiting is per-process for the MVP

Failed logins are limited (default 5 per 10 minutes per username+client)
using an in-memory sliding window. Acceptable because the MVP runs a single
process; if we ever scale to multiple workers, the limiter moves to a shared
store (e.g. the database or Redis) — same interface.

## D27 — Corrections may not override a newer physical count

A correction targets a specific past transaction. If a NEWER `reconciliation`
(a real physical count, Mode A) already exists for that ingredient, the
physical count is the best-known truth and rewriting history underneath it
would silently invalidate it. `correct_transaction` therefore rejects such
corrections with HTTP 409 (`correction_after_reconciliation`). To fix stock
in that situation, staff record a new reconciliation instead. Corrections of
transactions newer than the last physical count remain allowed.

## D28 — Idempotency-key reuse with a different payload is a conflict

Replaying an idempotency key with the SAME request returns the original
result (safe retries). Replaying it with a DIFFERENT payload returns HTTP 409
(`idempotency_conflict`) rather than silently returning an unrelated original
transaction. Payloads are compared semantically (parsed decimals, not raw
strings). The check runs both on the pre-check path and on the unique-
constraint race path, for stock entries and corrections alike. No schema
change was needed.

## D29 — BEGIN IMMEDIATE only for write-intent transactions (SQLite)

SQLite has no row locks / `SELECT FOR UPDATE`, so stock writes use
`BEGIN IMMEDIATE` to take the write lock up front (D15). To keep ordinary
read-only requests from needlessly blocking other writers, the engine's
`begin` event emits `BEGIN IMMEDIATE` only when a thread-local write-intent
flag is set (by the ledger/catalog services at the start of a write
transaction); otherwise a plain deferred `BEGIN` is used. Sessions are
created with `isolation_level=None` (autocommit at the DBAPI level) so the
listener controls the BEGIN mode. A probe test asserts reads stay deferred
and writes use IMMEDIATE, alongside the concurrency tests.

## D30 — Junction sync order: add links, move primary, remove links

Because of the composite FK (D24), reassigning suppliers must happen in this
exact order inside one transaction: (1) insert missing junction rows,
(2) update `ingredients.primary_supplier_id`, (3) delete junction rows no
longer assigned. Any other order either points the primary at a nonexistent
junction row or deletes a row the primary still references. Implemented in
`app/services/catalog.py::_sync_junction`.

## D31 — Sessions keep loaded objects after commit; services refresh

The session factory uses `expire_on_commit=False` (predictable state in
tests, no surprise lazy loads). Services that modify relationship collections
inside an atomic block (e.g. supplier junction rows) call `db.refresh(obj)`
after the commit so API responses serialize fresh data.

## D32 — Ingredient schedule overrides are additive, not replacements

A schedule rule with `ingredient_id NULL` applies to every ingredient the
supplier provides. A rule WITH `ingredient_id` is an ADDITIVE override that
applies only to that ingredient. Exclusion ("Soup Base gets Mon/Fri only
while Korean Noodles gets Mon/Wed/Fri") is modeled by keeping the shared
days on supplier-wide rules and putting the extra day on an ingredient
rule — never by subtracting days from a shared rule. This keeps every rule
self-contained: no priority/override resolution order is needed at read
time. Ingredient rules require an existing `supplier_ingredients` junction
row (validated server-side).

## D33 — Opportunity statuses exclude "covered" until purchase orders exist

Computed statuses are `upcoming` (order window not open yet), `open`
(window open, inclusive cutoff not passed), `cutoff_reached`, `missed`
(delivery date in the past), and `unavailable_holiday` (blocked by an
exception). A `covered` status is deliberately NOT implemented in Phase 2
Step 1: nothing may claim a deadline is covered by an order until the
purchase order system (Phase 2 Step 2) can prove it. Statuses are computed
on read, never stored.

## D34 — Calendar exceptions are date-specific rows; weekly rules are never rewritten

Holidays, one-off cancellations, moved cutoffs, and extra deliveries are
rows in `supplier_calendar_exceptions` keyed by concrete date, applied by
the scheduling engine at read time. Exceptions never edit the weekly rules,
so removing (deactivating) an exception always restores the normal schedule
exactly. Types: `delivery_cancelled`, `ordering_suspended` (both block the
cycle), `cutoff_override` (replaces one cutoff with `override_cutoff_at`),
`extra_delivery` (adds a one-time delivery with its own cutoff). One
exception per (supplier, date, type); deactivation preferred over deletion.

## D35 — Cutoffs use a days-before offset (1–6), never same-day

Ordering cutoffs are stored as `order_cutoff_days_before` (1–6 days) plus a
local time in the rule's timezone. Offset 0 (same-day cutoff) is rejected as
ambiguous with respect to the delivery-day boundary, and 7+ would blur the
"which week" question the data model must answer unambiguously. The engine
converts (delivery date − offset, local time, rule timezone) into a UTC
instant; the inclusive-cutoff rule (D16) applies to that instant. Contact
deadlines and reminders use the same representation (0–13 days; a reminder
must land strictly before the cutoff, a contact deadline strictly before
the delivery day).

## D36 — Available / incoming / projected are three separate stock numbers

Physical availability (`current_quantity`, the ledger cache per D14) is never
mixed with on-order quantities. The stock API additionally exposes per
ingredient: `incoming_quantity` split into `incoming_ordered_quantity`
(statuses `ordered`/`delayed` — uncertain) and `incoming_confirmed_quantity`
(statuses `confirmed`/`partially_delivered` — reliable), plus
`expected_delivery` (earliest pending delivery date) and `incoming_status`
(worst case across pending orders: delayed > confirmed > ordered).
`projected_quantity` = available + incoming and must always be labeled as a
projection in the UI — it is never physical stock.

Rules: (1) `draft` and `cancelled` orders NEVER contribute to incoming;
(2) pending = `quantity_ordered − quantity_received` per order item,
aggregated server-side in SQL (GROUP BY ingredient/status) — never summed on
the client; (3) purchase orders never write to the ledger — physical stock
changes only through delivery receipts (Phase 2 Step 3) via the existing
ledger service; (4) `quantity_received ≤ quantity_ordered` is enforced by a
database CHECK so Step 3 receipts cannot over-receive silently; (5) supplier
archiving is blocked (409 `archive_blocked`) while open purchase orders
exist — open = any status except `delivered`/`cancelled`.

## D37 — Receipts are the only path from "ordered" to "in stock"; reversals are receipts too

Physical stock increases from a purchase order ONLY through confirmed
delivery receipts. One atomic transaction per receipt writes, for every
line: the immutable `delivery_receipt` ledger row, the cached balance
update, the order item's `quantity_received` increment, and the
`delivery_receipts`/`delivery_receipt_items` rows (each line links 1:1 to
its ledger transaction). Rules:

- Receivable statuses: `ordered`, `confirmed`, `delayed`,
  `partially_delivered`. Draft/cancelled/delivered are rejected (409
  `not_receivable`); over-receiving is rejected (409 `over_receive`) and
  also DB-enforced (`quantity_received ≤ quantity_ordered`).
- `partially_delivered` and `delivered` are derived ONLY from received
  quantities inside the receipt flow — no generic status endpoint may set
  them. A reversal that removes all received goods returns the order to
  `ordered` (always factual: receiving requires a placed order).
- Receipts are immutable. Mistakes are undone by an admin-only reversal
  receipt (`reversal_of_id`) with negative item quantities and compensating
  ledger rows (`supersedes_id` points at the original stock-in row). A
  reversal cannot drive stock negative — if the goods were consumed, the
  reversal is refused (409 `insufficient_stock`) and a stock correction is
  the right tool instead.
- Receipt-level idempotency keys guard the whole receipt (D6); a replayed
  key with a different payload is a 409 conflict (D28).
  Staff name and notes are part of that comparison, including on reversals.
  A retry with an omitted receipt date reuses the original business date,
  including across midnight.
- Damaged/refused goods are recorded per line as `quantity_rejected`:
  never stocked, never reduce the outstanding obligation, and mirrored
  negatively on reversals. A fully-rejected line (accepted 0) is valid and
  posts no ledger row.
- Receiving is NOT a physical count: `counted_by=None`, so
  `last_counted_at`/confidence are untouched (D3).
- Every new receipt and reversal requires a nonblank, trimmed, self-reported
  employee name; the authenticated account FK and audit actor remain separate.
- Accepted plus rejected quantities in one arriving line cannot exceed its
  current outstanding quantity. Rejected replacements may arrive later;
  rejection itself never discharges the supplier obligation.

## D38 — Counting packages convert into canonical stock units

An ingredient can optionally define a named counting package and a positive
number of its canonical stock units per full package. A stock entry accepts
either a direct canonical quantity or `(full_packages, loose_units,
package_version)`, never both. The server validates nonnegative values,
converts using exact decimals, and sends the result through the unchanged
remaining/used ledger protocol. Full packages are whole numbers; loose units
include contents of opened packages and may exceed one package. Staff must
not count the contents of a full package twice. Remaining counts verify
stock confidence; used entries do not.

The conversion configuration has a version. Editing it leaves historical
stock untouched and invalidates unposted forms using an old version.
Packaged ledger entries retain the original breakdown and conversion factor,
so history stays interpretable after configuration changes. Idempotent
retries match the original breakdown, not only the converted total. Changing
an ingredient's canonical unit is refused once stock history exists.

Purchasing packages are supplier-specific `supplier_ingredients` fields:
`package_size`, `purchase_package_name`, and `whole_packages_only`. Counting
and buying package sizes may differ. These fields are ready for Phase 3
recommendations, which must round up whole-package orders from the selected
supplier's package size; no Phase 3 forecast behavior starts here.

## D39 — Ingredient demand inputs supersede global serving assumptions

For Phase 3, each ingredient has its own optional one-to-one demand settings
row: seven nullable daily maximum consumption estimates, nullable planning
safety stock, enabled state, notes, update time, and revision. Quantities use
the ingredient's existing stock unit and NUMERIC(14,3). This supersedes the
global weekday servings × usage-per-serving input described in D10 for future
forecasting; existing usage-per-serving catalog data remains intact.

No row and a null weekday mean unconfigured, never zero. Explicit zero is a
valid estimate. Partial settings may be saved, but forecast-ready requires
enabled, all seven days, safety stock, and an active ingredient. Disabled and
incomplete settings cannot become usable forecast inputs silently. The weekly
preview is the sum of the seven estimates only; planning safety stock is a
separate future buffer, distinct from the existing inventory warning threshold.

Only administrators may mutate settings (CSRF protected); staff may read.
Updates are audited and require the revision read by the client; stale writes
return 409 without overwriting newer settings. These writes never touch the
stock state, ledger, counting packages, purchasing packages, or orders. No
demand values are seeded automatically. Date overrides, stock projections,
shortage calculations, cutoff integration, and smart ordering remain future
steps; purchasing rounding must continue to use supplier package sizes (D38).

## D40 — Read-only forecast snapshots, explicit timing, and separate scenarios

The original current-day, delivery-timing, and partial-confirmation policies below
are superseded by D41. Other snapshot, ledger, scheduling, and confidence rules
remain in force.

Step 2 adds no persistent balance or schema. Forecasts read the existing
ledger/cache verification, count facts, ingredient demand, accepted receipt
totals on order lines, and supplier schedules in one request transaction.
Ledger drift or a snapshot newer than the injected as-of clock produces unknown
projections, never an automatic repair. This is a current snapshot API, not a
historical reconstruction API.

The baseline is the next business day after the as-of timestamp, using the
configured business timezone (default Asia/Tokyo) and day boundary. Today's
remaining usage and unreceived deliveries are excluded explicitly. No full-day
usage is deducted again from today's recorded stock, no hourly usage is inferred,
and physical-count adjustments are never interpreted as consumption.

Daily closing = prior closing + eligible arrivals − configured weekday usage.
Eligible arrivals precede modeled consumption; if the opening projection cannot
cover that day's usage without the arrival, display an arrival-time warning.
Negative projections carry cumulative unmet demand; they are not clamped.
Statuses: positive and at/above planning safety = Enough; positive below it =
Low; zero = Depleted; negative = Shortage; missing required inputs = Unknown.
This refines D39: an incomplete configuration may show a known prefix when
safety and those daily estimates exist, but its first missing day and every
downstream closing remain unknown. Explicit zero is valid. Disabled settings
are excluded, and no future stock values are persisted.

Conservative includes only eligible confirmed/partially delivered outstanding
quantities. Expected additionally includes eligible ordered-but-unconfirmed
quantities. Pending = ordered − accepted received; rejected goods remain owed.
Draft/cancelled orders are not committed supply. Fully accepted quantities are
already in stock and never added twice. Both scenarios exclude delayed, overdue,
due-today-unreceived, undated, holiday-cancelled, and out-of-horizon quantities,
while keeping the original dates and outstanding quantities visible.

An active weekly schedule conflict makes a dated delivery expected-only until
reconciled; an explicit extra-delivery exception permits its date. With no
applicable weekly rule, an otherwise valid confirmed date remains usable.
Delivery-cancelled exceptions exclude arrivals. Ordering-suspended exceptions
block new ordering opportunities but do not cancel an existing placed shipment.
Cutoff overrides alter cutoff information only. Existing POs are never rescheduled
or rejected merely because their ordering cutoff has since passed. Historical
order suppliers retain their own calendar even after ingredient reassignment.

Physical-count confidence remains independent: the stored level is capped by
read-only count age (24 hours high, 72 hours medium by default), missing/future
counts, and unresolved discrepancy flags. Thresholds are configurable through
Settings; no confidence fields are written by forecasting. Stale counts alone
do not fabricate missing demand or erase otherwise calculable projections.
Daily confidence describes the as-of snapshot, not future physical verification.

One forecast batch supplies all active ingredients, both scenarios, and a
conservative dashboard summary. Ingredient/scenario changes reuse it; range
changes and explicit refresh request a new batch. No purchasing recommendation,
rounding, order creation, contact action, or notification occurs here.

## D41 — Current-day preview, pre-delivery risk, and evidence for remaining supply

Today's preview starts from current actual stock and deducts remaining usage.
Without an override, today's configured full-day demand is the conservative upper
bound, independent of elapsed hours or recorded transactions. Staff may provide
an explicit non-negative decimal estimate, including zero after operations.
Blank/absent uses the configured default; missing remains unknown. No demand,
inventory, order, receipt, or audit rows are written by this forecast request.
Today's eligible outstanding deliveries are included using ordered minus accepted
received quantity, so already-received stock is never counted twice. Today's
projected close becomes tomorrow's opening; the 7/14/30 future dates remain
separate from the current-day preview. The dashboard uses the same default.
Overrides are ingredient-scoped and held in page memory, not permanent settings
or browser storage. The UI includes the business date; the server rejects a
stale-date override (409) and the UI reloads the new default with an explanation.

No arrival time is inferred. Before-delivery balance = opening − demand. Where
eligible incoming exists and that balance is negative, show a red pre-delivery
shortage-risk badge for its magnitude, independently of closing availability.
Signed closing remains opening + incoming − demand, carrying unmet demand forward.
Thus 20 opening, 40 demand, and 120 incoming produce risk 20 and closing 100.
The Korean weekly example uses Sunday 2026-11-15 as-of with explicit zero remaining
usage (operations finished), then Monday–Sunday closings 140,100,60,20,100,40,-20.
Friday warns of a possible 20-pc shortage before delivery despite closing at 100.

A partial receipt proves accepted quantity, not supplier commitment to its
remaining shipment. Conservative eligibility requires an existing order.confirm
audit snapshot matching supplier, delivery date, ingredient, and ordered quantity.
Receipt transitions may preserve that matching evidence; delay, cancellation,
reordering/editing, malformed evidence, or receipt reversal invalidate it until
reconfirmation. Missing legacy evidence yields Expected-only uncertain supply.
This is a read-only interpretation; historical orders and receipts are untouched.
Existing calendar exclusions and expected-only schedule conflicts still apply.
