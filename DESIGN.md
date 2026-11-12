---
version: alpha
name: StockMate
colors:
  background: "#f7f9fc"
  surface: "#ffffff"
  navigation: "#102b4e"
  primary: "#2563eb"
  text: "#14243a"
  muted: "#5b6b80"
  border: "#e2e8f0"
  danger: "#dc2626"
typography:
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.9375rem"
rounded:
  card: "12px"
  control: "8px"
spacing:
  small: "8px"
  control: "12px"
  section: "16px"
  card: "24px"
components:
  button:
    height: "44px"
    backgroundColor: "{colors.primary}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
  modal:
    width: "760px"
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.card}"
---

## Overview

StockMate is a restaurant operations tool: a staff stock notebook with clear
quantities and an administrator's planning sheet. Preserve the existing blue
sidebar and white work surfaces. Avoid marketing layouts, ornamental charts,
gradients, and a new visual identity for each feature.

Japan is the initial business market; the maintained product contract specifies
English first (ARCHITECTURE §9). Device usage includes desktop administration
and narrow phone screens in the restaurant. Quantities display the ingredient's
canonical unit; schedules retain their configured business timezone. This change
does not introduce Japanese language or regulated identity/payment flows.

## Colors

Runtime tokens in `frontend/css/base.css` are authoritative. The names above map
to `--color-bg`, `--color-surface`, `--color-sidebar`, `--color-primary`,
`--color-text`, `--color-text-muted`, `--color-border`, and `--color-critical`.
Semantic status is always accompanied by text. There is one light theme.

## Typography

Use the system font stack and existing size tokens. Field labels are compact
and semibold; numerical results use clear unit labels. Avoid decorative display
fonts. Notes and errors wrap naturally.

## Layout

Follow `frontend/css/app.css`: cards, form grids, responsive tables below 720px,
and sidebar collapse below 900px. Forecast settings reuse the wide modal. The
seven-day grid flows into narrow columns; the weekly total remains a distinct
preview below the inputs. The document and modal retain visible native scrollbars.
Forecast Calendar follows the existing table-to-card pattern rather than adding
charts. Keep the actual-stock and assumption card above dated projected values;
arrival totals and their uncertainty explanations form one responsive cell.
Place forecast-only Today's Remaining Usage controls beside the selectors and
a distinct current-day preview before future dates. Label the full-day default
as an upper bound and distinguish blank from explicit zero. Use the existing
critical red badge for pre-delivery shortage risk, independently of a positive
closing-stock badge; keep both labels readable on mobile cards.
The dashboard uses a concise forecast summary with ingredient links and keeps
its existing four actual-inventory stat cards.
Order Planner uses the same cards, quantity typography, status badges, native
selectors, and responsive daily tables. Lead with review status and calculated
or provisional purchasing units, then the dated cutoff/coverage facts. Keep
urgent pre-delivery risk visible beside pending-order and count checks. Native
disclosures hold calculation and order contributions; contacts remain plain,
readable operational instructions. No new palette or dashboard theme is added.

## Elevation & Depth

Use `--shadow-card` and `--shadow-modal`; no extra feature-specific elevation.

## Shapes

Cards use `--radius-card`; controls use `--radius-control`. Keep 44px controls.

## Components

Canonical DOM, modal, alert, busy, and toast helpers live in `frontend/js/ui.js`.
Planning forms use the same inputs, buttons, and feedback as Ingredient
Management. Preserve input and button geometry during save. Read-only settings
show disabled fields and an explicit staff access explanation.

## Do's and Don'ts

- Show unknown demand as blank / not configured; show an intentional zero as 0.
- Keep actual inventory, counting packages, purchasing packages, and demand
  assumptions distinct in wording and data.
- Put validation at the affected field and preserve entered values on failure.
- Do not use native confirmation dialogs for application workflows.
- Keep motion restrained and honor reduced-motion preferences.
