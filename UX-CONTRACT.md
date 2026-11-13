# StockMate UI contracts

The maintained business rules in `docs/DECISIONS.md` and the active user request
take precedence. Existing runtime tokens remain authoritative (see DESIGN.md).

| Capability | Canonical owner | Variants | Verification |
|---|---|---|---|
| Form | `SM.ui.el`, shared `.field` / `.input` styles; feature API schemas | Ingredient CRUD; demand form with explicit validation and blank/null values | backend demand tests, frontend quantity tests, browser suite |
| Select/Listbox | Native `<select>` in existing catalog/schedule forms | OS owns popup geometry and keyboard behavior | browser suite |
| Date | Native date fields in existing order forms | Browser owns popup; display helpers in `SM.ui` | existing order browser tests |
| Scrollbar | Global document and `.modal` overflow in app.css | Native visible scrollbars; modal max-height 90vh | narrow browser tests |
| Toast | `SM.ui.toast`, `#toasts` | success/error acknowledgment; inline errors carry corrections | browser suite |
| CRUD | Versioned API services, `SM.api`, Ingredient Management | Routine save closes modal and acknowledges; demand conflict retains draft | backend and browser demand tests |
| Modal | `SM.ui.modal` | normal/wide; optional beforeClose/onClose guard | focus trap, Escape, dirty discard and focus restoration browser tests |

Forecast Settings is available to administrators and staff on each ingredient.
Staff have read-only fields. Administrators may save partial settings, which
remain incomplete until all weekdays and safety stock are set. Disabled or
incomplete settings are never forecast-ready. The preview is an exact decimal
sum of all seven daily values and excludes planning safety stock.

The Monday–Friday shortcut copies one explicit value into those five fields;
weekend values remain unchanged. Unsaved modal changes prompt within the app
before discard. A failed save preserves input; stale saves require reloading
current settings. Data is held in memory, never browser storage.

Baseline legacy gaps outside the touched workflow include native validation
in older forms and inconsistent unsaved guards. This step extends the existing
shared modal for focus containment/restoration and the new demand guard; it
does not redesign unrelated screens.

Forecast Calendar is a read-only workflow available to both roles. Native
ingredient, horizon, and scenario selectors accept OS-owned popup geometry and
keyboard behavior. A range loads one batch for every ingredient and both
scenarios; local ingredient/scenario changes reuse it. Query parameters retain
selection and allow dashboard ingredient links. An explicit refresh reloads
the current snapshot. Superseded requests cannot replace a newer response.

The calendar uses the existing responsive table-to-card pattern, with one
wrapper for arrival total and details so they stay together on narrow screens.
Unknown quantities display as Unknown, explicit zero as 0. Availability labels
and physical-count confidence use separate badges. Current actual stock,
projected opening/closing, uncertain arrivals, and excluded outstanding orders
retain distinct labels. Timing and cumulative unmet-demand explanations remain
visible beside the calendar. Network failure retains selectors, clears stale
projection values, and offers Refresh forecast for retry.

Today's Remaining Usage is a forecast-only field for either role. Blank means
the configured full-day upper bound; explicit zero means no remaining usage.
Validation is inline and server-enforced. Apply reloads the batch with an
ingredient/date-scoped temporary override; reset restores the configured default.
An expired business-date override is cleared with an explanation. The dashboard
always uses the default policy. Today's preview is a separate card above future
dates, showing today's close as tomorrow's opening. No override is persisted.

Delivery-day risk uses a red text badge independent of the closing-status badge:
positive closing stock can coexist with a possible pre-delivery shortage. Unknown
arrival times remain unknown. Partial-receipt status alone never implies that
the outstanding remainder is confirmed; eligibility explanations show the evidence
policy. These warnings wrap with their responsive table cell.

Order Planner follows D42 and reuses `SM.ui`, `SM.forecastUI` quantity/confidence
formatting and `SM.demandUI.quantity` validation. Native ingredient/scenario
selectors reuse the loaded batch. Remaining Usage is temporary and supports zero;
blank restores the labeled full-day upper bound. A dated override expires on
business-day rollover. Offline failure clears stale advice, retains selectors,
and offers Refresh recommendations. No stock or order action is exposed here.

Urgency and count confidence remain separate. Pending-order advice includes
visible order links and a verification warning before potential quantities;
earlier shortages require physical/manual review and withhold numeric purchase
advice. A positive closing balance or zero requirement never hides a red
pre-delivery warning. Native disclosure controls expose daily arithmetic,
assumptions, and each existing line's contribution phase. Supplier and order
links open their established detail dialogs; Forecast Settings reuses its
administrator-editable/staff-read-only modal.

The page displays one selected ingredient with bounded detail tables (30-day
search horizon); dashboard priority and cutoff lists show at most six/five rows
and link to the full ingredient selector. This is a deterministic planning batch,
not an unbounded history table. Existing document scrolling and table-to-card
behavior own narrow layouts. Both roles may inspect recommendations, and all
existing write permissions remain enforced by their owning APIs.

The Notification Center uses explicit evaluation. `Refresh tasks` is a
CSRF-protected POST; opening the bell, dashboard, or full page only reads the
last persisted snapshot. The last evaluation time is always visible; older
than one hour is labeled stale and the dashboard withholds its task list until
refresh. The bell displays the unread count and four recent titles. Its
disclosure is keyboard accessible, and the page works at 390px without horizontal
overflow. Read, acknowledge and business resolution have different labels and
effects. A shared login shares read/ack state, which the page says plainly.

`All`, `Unread`, `Urgent`, and `Resolved / history` are route-backed filters.
All includes active and historical notices; the bell and dashboard show active
work only. History isolates resolved, expired, and superseded notices.
The API provides deterministic priority/task/time ordering and ten-item page links;
filter changes start at the first page. Cards use text for priority and type,
show business/action timestamps, link to the relevant existing workflow, and
expose Mark as read and Acknowledge separately. Mark all as read affects only
active notices. Empty states are specific to each filter. Network failures
clear stale content and leave a retry path. Admin-only Settings controls
category flags, count time and count ingredient applicability; a save never
silently evaluates conditions or contacts a supplier.

Phase 4 Step 2 adds automatic evaluation through a separately launched worker.
The Settings control only enables it and selects a 60–900-second interval;
Enabled never claims the process is Running. Administrator status reads an
unexpired lease and recent heartbeat, shows attempts, success, failure and
next expected check, and has an explicit read-only Refresh worker status
button. Staff retain the same operational inbox without health details or
settings edits. Manual Refresh tasks remains available. While visible, the app
polls the bell and current dashboard/inbox once a minute using GET requests;
hidden tabs do not poll, and no browser tab evaluates business conditions.
An unavailable worker leaves existing notices readable with their last
evaluation time and stale warning. No push or external message is sent.
