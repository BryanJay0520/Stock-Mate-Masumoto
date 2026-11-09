# StockMate — Agent Handoff

> Read this first if you are a coding agent (Kimi Code, Codex, or other)
> joining the project. Do not assume any prior conversation context.

## Where the project is

- **Current development phase:** Phase 0 complete — **revised** after the
  architecture review and the new ingredient–supplier requirements.
  **Awaiting user approval before Phase 1.**
- **Current state:** Documentation only. There is **no application code yet**.
- **Do not write application code** until the user approves Phase 1.

## Read these before doing anything

1. `docs/ARCHITECTURE.md` — full architecture, database schema, business logic.
2. `docs/DECISIONS.md` — binding decisions D1–D22. Follow them; supersede,
   never silently change. Note: decisions were renumbered during the Phase 0
   revision — always cite the current text, not an old number you remember.
3. `docs/PROJECT_STATUS.md` — phase roadmap and the ordered Phase 1 checklist.

## Completed so far

- Workspace inspected: greenfield (only `.gitignore` + `README.md` existed).
- Fixed `README.md` (it contained unresolved Git merge-conflict markers from
  commit `52acef3`); rewrote it as a proper project overview.
- Created `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PROJECT_STATUS.md`,
  and this file.
- **Phase 0 revision (latest):**
  - Canonical cached balance moved to `ingredient_stock_state` (1:1 with
    `ingredients`); ledger stays authoritative; atomic lock-then-update write
    protocol; verify/repair procedures (D14/D15).
  - Concurrency design: per-ingredient row locking, WAL SQLite, idempotency
    keys, unique constraints (D15).
  - Supplier scheduling refined: deadline states
    (upcoming/covered/missed/expired), inclusive cutoff boundary (D16),
    per-supplier holidays/exceptions, reminders deduped per
    supplier+delivery date for multi-ingredient orders (D20).
  - Forecasting refined: four distinct stock numbers (actual / ordered-
    unconfirmed / confirmed incoming / projected), intraday delivery
    availability setting (D21), GRAY on missing inputs, supply risk and data
    confidence kept independent.
  - Notifications refined: APScheduler pinned to 3.x, single-process MVP,
    documented dedicated-scheduler scale-out, startup catch-up + dedupe keys
    (D17).
  - Business dates: configurable day boundary (`business_day_start_time`,
    default 00:00 Asia/Tokyo), UTC storage (D18).
  - Auth: session cookies + CSRF in Phase 1; Bearer-token path preserved for
    future native apps via the single `get_current_user` dependency (D7).
  - **New: ingredient–supplier management (D19/D22).** Junction table is the
    only "who supplies what" record; one primary supplier per ingredient;
    archive-never-delete suppliers with reassignment guards; Korean Noodles
    and Chinese Noodles are separate demo ingredients with independent stock
    and different suppliers. Basic supplier CRUD + assignment moved into
    **Phase 1**; scheduling/orders stay in **Phase 2**.

## Key rules for all agents

- One phase at a time; wait for user approval between phases.
- All business logic lives server-side (`backend/app/services/`); frontend is
  a thin client; the API must stay client-agnostic.
- Stock changes only via the immutable ledger (D2) through the atomic write
  protocol (ARCHITECTURE.md §5.1); both entry modes are distinct (D3).
- The only cached balance is `ingredient_stock_state.current_quantity` (D14).
- Decimal quantities only (D4); UTC storage + configurable business tz/day
  boundary (D5/D18).
- Idempotency keys on stock mutations and delivery confirmations (D6).
- Session-cookie auth + CSRF now; auth isolated behind one dependency (D7).
- Never commit secrets; demo data must be labeled fictional.
- Work on Windows + VS Code; use Git Bash-compatible commands.
- After each completed task: update `docs/PROJECT_STATUS.md` and this file.

## Pending decisions (waiting on user)

- Approval to begin Phase 1. All Phase 0 assumptions were confirmed in review.

## Recommended next task

Start **Phase 1** (after approval), following the ordered checklist in
`docs/PROJECT_STATUS.md`: skeleton → DB models/migrations → auth → inventory
ledger service → supplier + ingredient admin → frontend pages → tests.

## Git

- Recent history: `52acef3` merge of local project with GitHub repo
  (left conflict markers in README.md, fixed in Phase 0).
- Use descriptive English commit messages; keep changes focused.
- **Do not push or merge without explicit user approval.**
- Phase 0 docs are staged for commit; commit on user approval
  (suggested message: "docs: add Phase 0 architecture and planning documents").
