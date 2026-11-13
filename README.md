# StockMate

Smart restaurant inventory management and supply planning system.
Web-based, mobile-ready, designed initially for a ramen restaurant.

🚧 **Work in Progress — Phase 4 Step 2 automatic in-app evaluation complete**

The responsive web UI includes login, dashboard, inventory, package-aware
daily stock entry, supplier and ingredient management, purchase orders and
delivery receiving, transaction history with corrections, and account
settings, editable ingredient demand estimates, and a read-only Forecast Calendar.
Order Planner provides advisory purchasing quantities and supplier deadlines.
The in-app Notification Center stores automatically or manually evaluated tasks
and risks. Automatic ordering, app/device push, email and LINE are future work.

## What it does (planned)

- Track ingredient stock with two entry modes: **remaining stock** (reconciliation) and **used stock** (consumption)
- Supplier-specific ordering schedules with cutoff/deadline detection
- Purchase orders with delivery confirmation (incoming vs. actual stock)
- Demand forecasting and a 7/14/30-day stock forecast calendar (green/yellow/red/gray)
- Stock Confidence Indicator (how trustworthy the current numbers are)
- Explicit in-app reminders and alerts (daily counts, order deadlines, supplier
  contact, forecast shortages, uncertain orders and deliveries)
- Separately launched automatic in-app evaluation; future external delivery
  (browser push, email, LINE)
- Role-based access: **Administrator** and **Staff**

## Counting full packages and loose stock

In Ingredient Management, an administrator can enable a counting package,
enter its name (such as Box or Tray), and set its size in the ingredient's
existing stock unit. Stock Entry then lets staff enter full packages plus
loose units, or a direct total in stock units. Count everything in opened or
partially filled packages as loose units; do not include the contents of full
packages twice. The preview shows the converted total before submission.
Remaining Stock reconciles the physical count, while Used Stock deducts the
converted amount. Both use the same canonical inventory ledger and retain
the original count breakdown in history. Supplier purchasing package sizes
are configured separately through the admin API and drive Order Planner advice.

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Pydantic, Alembic, SQLite (dev) → PostgreSQL (prod)
- **Frontend:** HTML5, CSS3, vanilla JavaScript, responsive mobile-first (served by FastAPI)
- **Scheduler:** separately launched, database-coordinated notification worker
- **Tests:** pytest
- **Mobile readiness:** versioned REST API (`/api/v1`), all business logic server-side; PWA and Capacitor packaging planned for later phases

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture, database schema, business logic
- [docs/DECISIONS.md](docs/DECISIONS.md) — architectural and business-rule decisions
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — current phase, completed and pending work
- [docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md) — handoff notes for coding agents

## Setup

**Development runs in WSL (Windows Subsystem for Linux).** Windows Smart App
Control on this machine blocks pip-installed native Python packages, so the
backend virtual environment lives in WSL while the code stays in the Windows
workspace (VS Code edits it normally; use the "WSL" extension for terminals).

Prerequisites: WSL2 with Ubuntu, Python 3.12 (already installed).

One-time environment setup (in a WSL terminal):

```bash
python3 -m venv --without-pip ~/.venvs/stockmate   # python3.12-venv workaround
curl -sS https://bootstrap.pypa.io/get-pip.py | ~/.venvs/stockmate/bin/python -
~/.venvs/stockmate/bin/python -m pip install -r /mnt/c/Users/bryan/Projects/StockMate/backend/requirements.txt
```

Run the application (from a WSL terminal, e.g. VS Code's WSL terminal):

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.seed                # only for a fresh fictional demo database
~/.venvs/stockmate/bin/python -m uvicorn app.main:app --reload
```

For an existing database, apply migrations without reseeding it:

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m alembic upgrade head
```

Automatic notifications require a **second WSL terminal** after migration.
The web server and worker must use the same `STOCKMATE_DATABASE_URL` and
`STOCKMATE_ENVIRONMENT` values (or the same default development database when
both are unset). Check both terminals before starting the worker so it never
evaluates a different database.
In Settings / Account, an administrator enables automatic evaluation and
selects the interval. Then run:

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.notification_worker
```

Stop the worker with **Ctrl+C**; SIGTERM also releases its lease. If it crashes,
the database lease expires after 180 seconds so another worker can take over.
The web app stays usable while it is stopped. Starting Uvicorn or opening a
browser never starts a worker. The default automatic setting is off. A worker
may be launched while disabled; it then heartbeats without evaluating until an
administrator enables it. The worker has no stored browser login or password.

Then open:

- App (frontend shell): http://127.0.0.1:8000/
- Health check: http://127.0.0.1:8000/api/v1/health
- Interactive API docs: http://127.0.0.1:8000/docs

### Reset fictional development data

Stop the local StockMate server and notification worker first, and disable
automatic evaluation in Settings. This command accepts only the exact
`backend/stockmate.db` path in the default `development` environment, with no
database URL override or `.env` file. It verifies the fictional seed markers,
demo catalog, accounts, migration head, and foreign keys, then creates a
timestamped checked backup in `backend/backups/`. It replaces operational
demo records in one transaction while preserving the schema, migrations,
users, login sessions, units, and unrelated application settings. It will
refuse an unknown database or unexpected catalog record.

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.reset_demo \
  --database /mnt/c/Users/bryan/Projects/StockMate/backend/stockmate.db \
  --confirm RESET_FICTIONAL_STOCKMATE_DATA
```

The reseeded Korean Noodles use `pc` with 60 pieces per Box and 180 pieces
of opening stock. Chinese Noodles use 50 pieces per Box, Eggs 30 pieces per
Tray, Soup Base 10 packs per Case, and Chashu 20 pieces per Pack. These sizes
are editable. Supplier purchase packages are set separately on each demo
supplier link. Opening stock is recorded through `opening_balance` ledger rows.

Run the tests:

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m pytest tests/ -v
```

Frontend unit tests (quantity math helpers, no dependencies — uses the
built-in Node.js test runner, run from the repo root in Windows or WSL):

```bash
node --test "frontend/tests/*.test.js"
```

Real browser tests (headless Chrome/Edge via the DevTools Protocol, zero
dependencies, Windows Node >= 22). Requires a FRESHLY seeded backend on
port 8765 — the suite mutates data, so reseed before every run:

```bash
# In WSL, use a fresh isolated temporary database:
cd /mnt/c/Users/bryan/Projects/StockMate/backend
test_dir=$(mktemp -d /tmp/stockmate-browsertest-XXXXXX)
STOCKMATE_DATABASE_URL="sqlite:///$test_dir/demo.db" ~/.venvs/stockmate/bin/python -m app.seed
STOCKMATE_DATABASE_URL="sqlite:///$test_dir/demo.db" ~/.venvs/stockmate/bin/python -m uvicorn app.main:app --port 8765

# In a Windows PowerShell terminal (repo root), while the server runs:
$env:STOCKMATE_TEST_DATABASE_URL = "sqlite:////tmp/stockmate-browsertest-XXXXXX/demo.db" # copy the actual test_dir
node frontend/tests/browser_smoke.mjs
```

Screenshots are saved to a temp directory whose path is printed at the end.

## In-app notifications (Phase 4 Steps 1–2)

Open **Notifications** from the sidebar or the bell. **Refresh tasks** remains
an explicit manual evaluation action. When automatic evaluation is enabled and
the separate worker is running, it checks conditions without a browser session.
Opening the page, bell or dashboard does not generate records. The app reads
updated notices about once a minute while visible and warns when the last
evaluation snapshot is over an hour old. The full
page has All, Unread, Urgent and Resolved/history filters, pagination, read and
acknowledgement controls, and links to the existing workflows. Reading or
acknowledging a notice does not complete its restaurant task. A shared login
also shares read and acknowledgement state.

Administrators configure category switches, daily count reminder time and the
specific ingredients that require a daily physical count under **Settings /
Account**. The default time is 21:30 in the configured business timezone;
no ingredients are selected automatically. Used Stock entries and delivery
receipts do not satisfy a physical-count reminder. Staff can see and refresh
notifications but cannot change settings. Automatic evaluation defaults off;
its interval defaults to 60 seconds and may be set from 60 to 900 seconds.
The interval is a check frequency, not a change to the reminder time: a 21:30
reminder appears on the first successful check at or after 21:30. Settings
shows a separate worker status, heartbeat, attempt, success, failure and last
result. A recent heartbeat and valid lease are needed for Running status;
Enabled alone does not prove the worker is running. This step sends no
external messages. Migration head is `0010_notification_scheduler`.

## Ingredient forecast settings

Open **Ingredients → Forecast Settings** for an ingredient. Administrators can
set its Monday–Sunday estimated maximum consumption, enable or disable future
forecast use, set a planning safety buffer, and record estimation notes.
**Apply to Monday–Friday** copies the shortcut quantity into those five days;
Saturday and Sunday stay independent. Every quantity is in the displayed stock
unit. Blank means not configured; zero means intentionally no consumption.
Staff can open the same settings in read-only mode.

The weekly preview totals the seven daily estimates and excludes safety stock.
For Korean Noodles, 40 pc on each weekday and 60 pc on Saturday/Sunday gives
320 pc per week; a 60 pc planning buffer is separate. These values are editable
and are demonstrated only in isolated tests, not automatically saved to the
development database. The existing Box × 60 counting configuration is reused.
The Forecast Calendar uses these inputs; ordering recommendations remain a later step.

## Forecast Calendar

Open **Forecast Calendar** and select an ingredient, 7/14/30 days, and a scenario.
Actual stock is shown separately from projected daily openings/closings. The
dashboard summarizes conservative shortages, low stock, stale counts, uncertain
supply dependencies, and missing settings, with links to individual ingredients.

The separate **Today — current-day preview** starts from actual stock at the
displayed timestamp. **Today's Remaining Usage** defaults to today's configured
full-day estimate as a conservative upper bound. Enter an optional non-negative
forecast-only override, including **0** after operations; blank restores the
default. Missing estimates remain unknown. Overrides are temporary, scoped to
one ingredient/business date, and never save demand settings or alter stock.
The dashboard uses the same full-day default. Today's projected close, including
eligible outstanding arrivals due today, becomes tomorrow's opening. Refresh
after recording actual usage, receipts, or a physical count.
Each future day subtracts that weekday's configured usage once. Negative projections
carry cumulative unmet demand forward; unknown usage makes downstream balances
unknown. Explicit zero is valid. Planning safety stock is the configured buffer.

**Conservative** uses eligible confirmed orders and partially delivered orders
whose remainder retains matching supplier-confirmation audit evidence. A partial
receipt alone establishes no confirmation. **Expected** also includes eligible
unconfirmed remainders/orders. Neither scenario
uses drafts, cancellations, accepted quantities already in stock, delayed or
overdue arrivals, or dates invented for missing delivery information. Supplier
delivery cancellations exclude shipments; weekly schedule conflicts are flagged
and expected-only. The view lists original outstanding quantities and exclusions.
It never reschedules an order. Arrival times are unknown. The before-delivery
balance deducts demand before incoming; a negative result displays a **red
pre-delivery shortage risk**, even if closing is positive. Closing remains
opening + eligible incoming − demand. For example, 20 opening − 40 usage gives
a possible 20-pc shortage before a 120-pc delivery, then a 100-pc closing balance.

Count confidence is separate from availability and is capped by count age
(24/72 hours by default). These read-only freshness thresholds use
`STOCKMATE_COUNT_HIGH_FRESHNESS_HOURS` and `STOCKMATE_COUNT_MEDIUM_FRESHNESS_HOURS`.
Forecast views do not change stock, write ledger entries, create purchase orders,
or recommend purchase quantities. Forecast Calendar itself uses migration 0008;
the newer notification center adds migration 0009 independently.

## Order Planner

Open **Order Planner** to review an ingredient's next valid supplier deadline,
delivery, coverage period, and conservative/expected additional requirement.
Expand the daily calculation to see how dated incoming and usage affect stock.
Today's remaining usage uses the same full-day upper bound and temporary-zero
override policy as Forecast Calendar. The dashboard uses the default policy.

Advice uses the active primary supplier's next two valid delivery opportunities
within a 30-day search horizon. Holidays, opening windows, inclusive cutoffs,
supplier timezones, and ingredient-specific rules come from Supplier Scheduling.
Coverage ends the day before the following actionable delivery. Missing demand,
an unknown following delivery, or missing purchasing settings shows what needs
configuration rather than inventing a quantity.

The additional requirement covers the largest daily shortfall against planning
safety stock. A shipment later in the cycle cannot cover an earlier day. Whole
packages round upward using that supplier's purchasing package and minimum order;
counting packages remain independent. For example, Friday–Sunday demand of
160 pc, safety 60 pc, and 100 pc before Friday requires 120 pc: two 60-pc boxes.
A confirmed 120-pc Friday order covers it; an unconfirmed order instead requires
**Verify Pending Order** before considering any additional purchase.

Earlier shortages withhold numeric purchase advice until a physical count or
manual review. Same-day pre-delivery risks remain visible even if no additional
order is needed. Stale counts require verification. Supplier contacts, existing
orders/drafts, and approved alternative opportunities are available for review.
The planner never creates orders, sends messages, changes suppliers, or writes
stock. No operational demand settings are populated automatically.

## Authentication (added Step 3)

Login flow: `POST /api/v1/auth/login` with username + password → the server
sets an **HttpOnly session cookie** and a readable **CSRF cookie**, and
returns the user object. All mutating API calls must send the CSRF cookie's
value back in the `X-CSRF-Token` header (the `SM.api` helper in
`frontend/js/api.js` does this automatically). `POST /api/v1/auth/logout`
revokes the session server-side.

Demo accounts (dev only): `admin` / `demo-admin-password`,
`staff` / `demo-staff-password`.

Create a real administrator (no default credentials exist):

```bash
cd /mnt/c/Users/bryan/Projects/StockMate/backend
~/.venvs/stockmate/bin/python -m app.cli create-admin --username yourname
```

The password is typed hidden, strength-checked, and never printed or logged.

## Production security checklist

The development setup is intentionally convenient and **must never reach
production unchanged**. Before any public deployment (tracked in Phase 6):

- **Demo accounts** — `admin`/`demo-admin-password` and `staff`/`demo-staff-password`
  exist ONLY for local development. Never seed them into a production database;
  create real accounts via `python -m app.cli create-admin`.
- **Development secret key** — the default `secret_key` in `app/config.py` is
  publicly known. Production must set `STOCKMATE_SECRET_KEY` to a long random value.
- **HTTPS** — set `STOCKMATE_SECURE_COOKIES=true` so session cookies carry the
  `Secure` flag, and serve the app behind HTTPS only.
- **Login rate limiter** — currently per-process (decision D26). It must move
  to a shared store if more than one backend worker ever runs.
- **XSS surface** — the frontend must keep rendering API data via
  `textContent`/escaping only (never `innerHTML`); CSP headers must be
  configured at deployment.

## Data disclaimer

All seed/demo data is explicitly fictional and must never be treated as real restaurant inventory.
