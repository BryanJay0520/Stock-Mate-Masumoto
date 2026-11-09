# StockMate

Smart restaurant inventory management and supply planning system.
Web-based, mobile-ready, designed initially for a ramen restaurant.

🚧 **Work in Progress — Phase 0 (Planning & Architecture)**

## What it does (planned)

- Track ingredient stock with two entry modes: **remaining stock** (reconciliation) and **used stock** (consumption)
- Supplier-specific ordering schedules with cutoff/deadline detection
- Purchase orders with delivery confirmation (incoming vs. actual stock)
- Demand forecasting and a 7/14/30-day stock forecast calendar (green/yellow/red/gray)
- Stock Confidence Indicator (how trustworthy the current numbers are)
- Scheduled reminders (daily stock count, order deadlines, deliveries, shortages)
- Multi-channel notifications (in-app first; browser push, email, LINE later)
- Role-based access: **Administrator** and **Staff**

## Tech stack

- **Backend:** Python, FastAPI, SQLAlchemy, Pydantic, Alembic, SQLite (dev) → PostgreSQL (prod)
- **Frontend:** HTML5, CSS3, vanilla JavaScript, responsive mobile-first (served by FastAPI)
- **Scheduler:** APScheduler (in-process, DB-persisted jobs)
- **Tests:** pytest
- **Mobile readiness:** versioned REST API (`/api/v1`), all business logic server-side; PWA and Capacitor packaging planned for later phases

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture, database schema, business logic
- [docs/DECISIONS.md](docs/DECISIONS.md) — architectural and business-rule decisions
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — current phase, completed and pending work
- [docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md) — handoff notes for coding agents

## Setup

Setup instructions will be added in Phase 1 once the application skeleton exists.
Target: easy to open and run in VS Code on Windows.

## Data disclaimer

All seed/demo data is explicitly fictional and must never be treated as real restaurant inventory.
