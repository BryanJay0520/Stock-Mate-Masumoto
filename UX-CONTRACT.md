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
