// Advisory order planning. All quantities, deadlines and priorities come from the server.
window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.plannerUI = {
  statusInfo(status) {
    return ({order_needed: {label: "Order Needed", kind: "warning"},
      no_additional_order_needed: {label: "No Additional Order Needed", kind: "safe"},
      verify_pending_order: {label: "Verify Pending Order", kind: "warning"},
      urgent_shortage_risk: {label: "Urgent Shortage Risk", kind: "critical"},
      check_physical_stock: {label: "Check Physical Stock", kind: "warning"},
      configuration_required: {label: "Configuration Required", kind: "neutral"}})[status] || {label: "Configuration Required", kind: "neutral"};
  },
  purchaseText(calculation, unit) {
    const purchasing = calculation.purchasing, qty = SM.forecastUI.quantity;
    if (purchasing.recommended_quantity === null) return "Quantity awaiting configuration or manual review";
    const prefix = calculation.definitive ? "Calculated advice" : "Potential requirement — review first";
    return `${prefix}: ${purchasing.recommended_package_count === null ? "" : `${SM.ui.formatQty(purchasing.recommended_package_count)} × ${purchasing.package_name || "purchasing package"} · `}${qty(purchasing.recommended_quantity, unit)}`;
  },
  summaryCard(data) {
    const ui = SM.ui, summary = data.summary;
    const card = ui.el("div", {class: "card", id: "planner-summary"},
      ui.el("h2", {class: "card__title", text: "Smart ordering — review priorities"}),
      ui.el("p", {class: "field__hint", text: "Advisory only. Verify pending orders and physical stock before acting; no purchase orders are created."}));
    const facts = ui.el("dl", {class: "forecast-summary-grid"});
    for (const [label, value] of [["Potential additional orders", summary.order_needed_count],
      ["Urgent shortages / delivery risks", summary.urgent_shortage_count], ["Pending orders to verify", summary.verify_pending_count],
      ["Physical counts / reviews needed", summary.physical_count_required_count], ["Configuration needed", summary.configuration_required_count]]) {
      facts.append(ui.el("div", {}, ui.el("dt", {text: label}), ui.el("dd", {text: value})));
    }
    card.append(facts);
    const issues = ui.el("ul", {class: "forecast-issues"});
    for (const item of data.recommendations.filter(row => row.status !== "no_additional_order_needed").slice(0, 6)) {
      const status = this.statusInfo(item.status);
      issues.append(ui.el("li", {}, ui.el("a", {href: `#/planner?ingredient_id=${item.ingredient_id}`, text: item.ingredient_name}),
        " ", ui.badge(status.label, status.kind)));
    }
    card.append(issues);
    const cutoffs = ui.el("ul", {class: "forecast-issues"});
    for (const row of summary.upcoming_cutoffs.slice(0, 5)) cutoffs.append(ui.el("li", {},
      ui.el("a", {href: `#/planner?ingredient_id=${row.ingredient_id}`, text: row.ingredient_name}),
      ` — cutoff ${ui.formatInTz(row.cutoff_at, data.business_timezone)} · ${row.ordering_status === "upcoming" ? "window not yet open" : "window open"}`));
    card.append(ui.el("h3", {text: "Upcoming ordering cutoffs"}), cutoffs,
      ui.el("a", {href: "#/planner", text: "Open Order Planner →"}));
    return card;
  },
  supplierDetails(supplier, opportunity) {
    const ui = SM.ui, body = ui.el("div", {class: "planner-contact"});
    if (!supplier) return ui.empty("Assign an active primary supplier in Ingredients.");
    body.append(ui.el("p", {text: `${supplier.name} · ${supplier.contact_name || "Contact name not configured"}`}),
      ui.el("p", {text: `Phone: ${supplier.phone || "Not configured"} · Email: ${supplier.email || "Not configured"}`}),
      ui.el("p", {text: `Preferred contact: ${supplier.contact_method || "Not configured"}`}),
      supplier.ordering_url ? ui.el("p", {text: `Ordering website: ${supplier.ordering_url}`}) : null,
      supplier.ordering_procedures ? ui.el("p", {text: supplier.ordering_procedures}) : null);
    if (opportunity?.special_instructions) body.append(ui.el("p", {text: opportunity.special_instructions}));
    if (opportunity?.contact) body.append(ui.el("p", {text: `${opportunity.contact.required ? "Required" : "Suggested"} ${opportunity.contact.action}: ${ui.formatInTz(opportunity.contact.deadline_at, opportunity.timezone)}`}));
    body.append(ui.el("a", {href: `#/suppliers?supplier_id=${supplier.id}`, text: "Supplier Details →"}));
    return body;
  },
};

SM.pages.planner = {
  async render(view) {
    const ui = SM.ui, params = new URLSearchParams(location.hash.split("?")[1] || "");
    let selected = params.get("ingredient_id"), data = null, request = 0, override = null;
    const ingredient = ui.el("select", {id: "planner-ingredient", class: "select", disabled: true});
    const scenario = ui.el("select", {id: "planner-scenario", class: "select"},
      ui.el("option", {value: "conservative", text: "Conservative — confirmed supply"}),
      ui.el("option", {value: "expected", text: "Expected — includes uncertain supply", selected: params.get("scenario") === "expected"}));
    const usage = ui.el("input", {id: "planner-today-usage", type: "text", inputmode: "decimal", maxlength: 20, class: "input",
      disabled: true, "aria-describedby": "planner-usage-hint planner-usage-error"});
    const error = ui.el("span", {id: "planner-usage-error", class: "field-error"});
    const hint = ui.el("p", {id: "planner-usage-hint", class: "field__hint"});
    const apply = ui.el("button", {type: "button", class: "btn btn--secondary", text: "Apply remaining usage", disabled: true});
    const reset = ui.el("button", {type: "button", class: "btn btn--secondary", text: "Use configured full-day estimate", disabled: true});
    const refresh = ui.el("button", {type: "button", class: "btn btn--secondary", text: "Refresh recommendations"});
    const fields = ui.el("div", {class: "form-grid"});
    for (const [label, control] of [["Ingredient", ingredient], ["Scenario", scenario], ["Today's Remaining Usage (forecast only)", usage]]) {
      fields.append(ui.el("div", {class: "field"}, ui.el("label", {class: "field__label", for: control.id, text: label}), control,
        control === usage ? error : null));
    }
    const feedback = ui.el("div", {"aria-live": "polite"}), content = ui.el("div", {id: "planner-content"});
    view.append(ui.el("div", {class: "card"}, fields,
      ui.el("div", {class: "button-row"}, apply, reset, refresh), hint, feedback), content);
    const draw = () => {
      if (!data) return;
      const item = data.recommendations.find(row => String(row.ingredient_id) === selected);
      ui.clear(content);
      if (!item) { content.append(ui.empty("No active ingredients are available for planning.")); return; }
      usage.value = item.today_usage_source === "override" ? ui.formatQty(item.today_remaining_usage) : "";
      usage.placeholder = item.today_configured_usage === null ? "Not configured" : ui.formatQty(item.today_configured_usage);
      hint.textContent = `Remaining usage in ${item.unit}. Blank uses today's configured full-day estimate (${SM.forecastUI.quantity(item.today_configured_usage, item.unit)}) as a conservative upper bound; explicit zero means none remains. This temporary estimate is not saved. Dashboard advice uses the default.`;
      history.replaceState(null, "", `#/planner?${new URLSearchParams({ingredient_id: selected, scenario: scenario.value})}`);
      content.append(this.renderRecommendation(data, item, scenario.value));
    };
    const load = async () => {
      const token = ++request;
      refresh.disabled = true; apply.disabled = true; reset.disabled = true;
      ui.clear(feedback); ui.clear(content).append(ui.loading("Calculating advisory recommendations…"));
      content.setAttribute("aria-busy", "true");
      try {
        const query = new URLSearchParams();
        if (override) { query.set("today_usage_ingredient_id", override.id); query.set("today_remaining_usage", override.value); query.set("today_usage_date", override.date); }
        const result = await SM.api.get(`/recommendations?${query}`);
        if (token !== request) return;
        data = result;
        if (!data.recommendations.some(row => String(row.ingredient_id) === selected)) selected = String(data.recommendations[0]?.ingredient_id || "");
        ui.clear(ingredient).append(...data.recommendations.map(row => ui.el("option", {value: row.ingredient_id,
          selected: String(row.ingredient_id) === selected, text: `${row.ingredient_name} (${row.unit})`})));
        draw();
      } catch (failure) {
        if (token !== request) return;
        if (failure.code === "today_override_expired") {
          override = null; await load();
          feedback.append(ui.alert("The previous business day's temporary override expired. The current full-day default is now shown.", "info"));
          return;
        }
        data = null; ui.clear(content); feedback.append(ui.alert(failure.message), ui.el("p", {text: "Use Refresh recommendations to try again."}));
      } finally {
        if (token === request) {
          refresh.disabled = false; ingredient.disabled = !data?.recommendations.length;
          apply.disabled = !data?.recommendations.length; usage.disabled = !data?.recommendations.length;
          reset.disabled = !override; content.setAttribute("aria-busy", "false");
        }
      }
    };
    ingredient.addEventListener("change", () => { selected = ingredient.value; draw(); });
    scenario.addEventListener("change", draw);
    refresh.addEventListener("click", load);
    usage.addEventListener("input", () => { usage.removeAttribute("aria-invalid"); error.textContent = ""; });
    apply.addEventListener("click", () => {
      try {
        const value = SM.demandUI.quantity(usage.value);
        override = value === null ? null : {id: selected, value, date: data.business_date};
        usage.removeAttribute("aria-invalid"); error.textContent = ""; load();
      } catch (failure) { error.textContent = failure.message; usage.setAttribute("aria-invalid", "true"); usage.focus(); }
    });
    reset.addEventListener("click", () => { override = null; load(); });
    await load();
  },

  renderRecommendation(data, item, scenario) {
    const ui = SM.ui, qty = value => SM.forecastUI.quantity(value, item.unit), calc = item[scenario];
    const card = ui.el("div", {class: "card planner-card", "data-ingredient-id": item.ingredient_id},
      ui.el("h2", {class: "card__title", text: `${item.ingredient_name} — ${scenario === "expected" ? "Expected" : "Conservative"} advice`}),
      ui.el("p", {class: "field__hint", text: `As of ${ui.formatInTz(data.as_of, data.business_timezone)}. Advisory snapshot; no orders are created or sent.`}));
    const statuses = ui.el("div", {class: "button-row"});
    for (const flag of item.status_flags.filter(flag => !item.verify_pending_order || flag !== "order_needed")) {
      const status = SM.plannerUI.statusInfo(flag); statuses.append(ui.badge(status.label, status.kind));
    }
    card.append(statuses, SM.forecastUI.confidence(item.confidence));
    if (item.verify_pending_order) {
      card.append(ui.alert("An existing unconfirmed order may already cover this requirement. Confirm the pending order before ordering additional stock.", "info"));
      const pending = ui.el("p", {class: "button-row"}, "Review pending shipments:");
      for (const order of item.existing_orders.filter(row => row.uncertain && ["starting_projection", "delivery_cycle"].includes(row.projection_phase))) {
        pending.append(ui.el("a", {href: `#/orders?order_id=${order.order_id}`, text: `Order #${order.order_id} — ${order.supplier_name}`}));
      }
      card.append(pending);
    }
    card.append(ui.el("p", {class: "planner-purchase", id: "planner-purchase", text: SM.plannerUI.purchaseText(calc, item.unit)}));
    for (const risk of calc.pre_delivery_risks) card.append(ui.el("p", {class: "planner-risk"},
      ui.badge(`Pre-delivery shortage risk: ${qty(risk.quantity)} · ${ui.formatDateOnly(risk.date)}`, "critical")));
    const facts = ui.el("dl", {class: "forecast-summary-grid"});
    for (const [label, value] of [["Actual available stock", qty(item.actual_available)], ["Primary supplier", item.primary_supplier?.name || "Not assigned"],
      ["Ordering status", item.opportunity ? item.opportunity.status === "open" ? "Window open — inclusive cutoff" : "Window not yet open" : "No valid opportunity"],
      ["Ordering window opens", item.opportunity?.order_open_at ? ui.formatInTz(item.opportunity.order_open_at, item.opportunity.timezone) : "No opening restriction configured"],
      ["Next actionable cutoff", item.opportunity ? ui.formatInTz(item.opportunity.cutoff_at, item.opportunity.timezone) : "Unknown"],
      ["Expected delivery", ui.formatDateOnly(item.coverage_start)], ["Coverage through", ui.formatDateOnly(item.coverage_end)],
      ["Following actionable delivery", ui.formatDateOnly(item.following_opportunity?.delivery_date)],
      ["Coverage demand", qty(calc.coverage_demand)], ["Planning safety stock", qty(calc.safety_stock)], ["Demand + safety target", qty(calc.target_stock)],
      ["Projected before proposed delivery", qty(calc.projected_before_delivery)],
      ["Confirmed incoming already in starting stock", qty(calc.confirmed_incoming_before_delivery)],
      ["Uncertain incoming before delivery (Expected only)", qty(calc.uncertain_incoming_before_delivery)],
      ["Existing confirmed incoming in cycle", qty(calc.confirmed_incoming_in_cycle)],
      ["Existing uncertain incoming in cycle (Expected only)", qty(calc.uncertain_incoming_in_cycle)],
      ["Additional base units required", qty(calc.required_additional_quantity)],
      ["Purchasing package", calc.purchasing.package_size === null ? "Not configured" : `${qty(calc.purchasing.package_size)} per ${calc.purchasing.package_name || "package"}`],
      ["Whole-package purchasing", calc.purchasing.whole_packages_only ? "Required — rounded up" : "Base-unit purchasing allowed"],
      ["Supplier minimum order", qty(calc.purchasing.minimum_order_quantity)]]) facts.append(ui.el("div", {}, ui.el("dt", {text: label}), ui.el("dd", {text: value})));
    card.append(facts, ui.el("p", {text: calc.message}));
    const warnings = ui.el("ul", {class: "forecast-issues", id: "planner-warnings"});
    [...new Set([...item.warnings, ...calc.warnings])].forEach(warning => warnings.append(ui.el("li", {text: warning})));
    card.append(warnings);
    if (item.manual_actions.length) card.append(ui.el("h3", {text: "Manual actions before relying on a quantity"}),
      ui.el("ul", {class: "forecast-issues"}, item.manual_actions.map(text => ui.el("li", {text}))));
    const explanation = ui.el("details", {class: "forecast-explanation", id: "planner-calculation"}, ui.el("summary", {text: "Inspect daily calculation and assumptions"}));
    explanation.append(ui.el("p", {text: "Incoming is used once, on its dated day. Additional stock covers the largest daily deficit against safety; a later shipment cannot cover earlier demand. Cycle sizing includes arrivals before usage, while unknown arrival times retain separate pre-delivery warnings."}));
    if (calc.daily_calculation.length) {
      const table = ui.el("table", {class: "table table--responsive"}, ui.el("thead", {}, ui.el("tr", {},
        ["Date", "Demand", "Eligible arrivals", "Close without new order", "Illustrative close with calculated quantity"].map(text => ui.el("th", {scope: "col", text})))));
      const rows = ui.el("tbody");
      for (const day of calc.daily_calculation) rows.append(ui.el("tr", {},
        [ui.formatDateOnly(day.date), qty(day.estimated_consumption), qty(day.eligible_incoming), qty(day.closing_without_new_order), qty(day.closing_with_recommendation)]
          .map((text, i) => ui.el("td", {"data-label": ["Date", "Demand", "Eligible arrivals", "Close without new order", "Illustrative close"][i], text}))));
      table.append(rows); explanation.append(ui.el("div", {class: "table-wrap"}, table));
    } else explanation.append(ui.empty("Complete the required settings or physical review to calculate a reliable quantity."));
    explanation.append(ui.el("ul", {class: "forecast-issues"}, data.assumptions.map(text => ui.el("li", {text}))));
    card.append(explanation, ui.el("h3", {text: "Supplier contact and instructions"}), SM.plannerUI.supplierDetails(item.primary_supplier, item.opportunity));
    const orders = ui.el("details", {class: "forecast-explanation", id: "planner-existing-orders"}, ui.el("summary", {text: `Existing orders and contributions (${item.existing_orders.length})`}));
    if (!item.existing_orders.length) orders.append(ui.empty("No outstanding order lines or drafts."));
    for (const order of item.existing_orders) orders.append(ui.el("div", {class: "forecast-delivery"},
      ui.el("a", {href: `#/orders?order_id=${order.order_id}`, text: `Order #${order.order_id} · ${order.supplier_name}`}),
      ui.el("p", {text: `${order.status.replaceAll("_", " ")} · ${ui.formatDateOnly(order.expected_date)} · outstanding ${qty(order.outstanding_quantity)} · accepted ${qty(order.accepted_received_quantity)}`}),
      ui.el("p", {text: `Contribution: ${order.projection_phase.replaceAll("_", " ")}. Conservative: ${order.conservative_eligible ? "included" : "excluded"}; Expected: ${order.expected_eligible ? "included" : "excluded"}. ${order.reasons.join(" ")}`})));
    card.append(orders);
    if (item.alternative_opportunities.length) {
      card.append(ui.el("h3", {text: "Approved alternatives — manual review"}));
      for (const alt of item.alternative_opportunities) card.append(ui.el("div", {class: "forecast-delivery"},
        ui.el("strong", {text: alt.supplier.name}),
        ui.el("p", {text: `Earliest delivery: ${ui.formatDateOnly(alt.opportunity.delivery_date)} · cutoff ${ui.formatInTz(alt.opportunity.cutoff_at, alt.opportunity.timezone)} · purchasing package ${qty(alt.supplier.purchasing.package_size)}`}),
        ui.el("p", {text: alt.warning}), SM.plannerUI.supplierDetails(alt.supplier, alt.opportunity)));
    }
    card.append(ui.el("div", {class: "button-row planner-links"},
      ui.el("button", {type: "button", class: "btn btn--secondary", text: "Ingredient Forecast Settings", onclick: () => SM.demandUI.open({id: item.ingredient_id, name: item.ingredient_name})}),
      ui.el("a", {class: "btn btn--secondary", href: `#/forecast?ingredient_id=${item.ingredient_id}`, text: "Forecast Calendar"}),
      ui.el("a", {class: "btn btn--secondary", href: "#/orders", text: "Existing Purchase Orders"})));
    return card;
  },
};
