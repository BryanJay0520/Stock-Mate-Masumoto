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
