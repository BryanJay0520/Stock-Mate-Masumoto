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
