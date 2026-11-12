// Read-only views of server forecasts. No stock or consumption math in the browser.
window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.forecastUI = {
  statusInfo(status) {
    return ({enough: {label: "Enough", kind: "safe"}, low: {label: "Low", kind: "warning"},
      depleted: {label: "Depleted", kind: "critical"}, shortage: {label: "Shortage", kind: "critical"},
      unknown: {label: "Unknown", kind: "neutral"}})[status] || {label: "Unknown", kind: "neutral"};
  },
  quantity(value, unit) { return value === null || value === undefined ? "Unknown" : `${SM.ui.formatQty(value)} ${unit}`; },
  riskText(day, unit) {
    return day.pre_delivery_shortage_risk ? `Pre-delivery shortage risk: ${this.quantity(day.pre_delivery_shortage, unit)}` : null;
  },
  confidence(info) {
    return SM.ui.badge(`${info.level[0].toUpperCase()}${info.level.slice(1)} count confidence`,
      {high: "safe", medium: "warning", low: "critical"}[info.level]);
  },
  summaryCard(data) {
    const ui = SM.ui, summary = data.summary;
    const card = ui.el("div", {class: "card", id: "forecast-summary"},
      ui.el("h2", {class: "card__title", text: "Forecast outlook — today and next 7 business days"}),
      ui.el("p", {class: "field__hint", text: "Today's remaining usage defaults to the configured full-day estimate as a conservative upper bound. Today's projected close feeds the future projection; actual stock remains separate."}));
    const facts = ui.el("dl", {class: "forecast-summary-grid"});
    for (const [label, value] of [
      ["Projected to run out", summary.run_out_count], ["Below planning safety stock", summary.low_count],
      ["Stale physical counts", summary.stale_count], ["Depend on uncertain incoming", summary.uncertain_dependency_count],
      ["Pre-delivery shortage risks", summary.pre_delivery_risk_count],
      ["Missing or incomplete demand", summary.missing_demand_count],
      ["Earliest shortage", summary.earliest_shortage_date ? ui.formatDateOnly(summary.earliest_shortage_date) : "None known"],
    ]) facts.append(ui.el("div", {}, ui.el("dt", {text: label}), ui.el("dd", {text: value})));
    card.append(facts);
    if (summary.configured_ingredients === 0) card.append(ui.empty("No complete enabled forecasts yet. An administrator can configure demand in Ingredients → Forecast Settings."));
    const list = ui.el("ul", {class: "forecast-issues"});
    for (const row of summary.ingredients.filter(item => item.issues.length)) {
      list.append(ui.el("li", {}, ui.el("a", {href: `#/forecast?ingredient_id=${row.ingredient_id}`, text: row.ingredient_name}),
        ` — ${row.issues.join(" · ")}${row.first_run_out_date ? ` · Run out: ${ui.formatDateOnly(row.first_run_out_date)}` : ""}`,
        row.first_pre_delivery_risk_date ? ui.badge(`Pre-delivery shortage risk ${ui.formatDateOnly(row.first_pre_delivery_risk_date)}`, "critical") : null));
    }
    card.append(list, ui.el("a", {href: "#/forecast", text: "Open Forecast Calendar →"}));
    return card;
  },
};

SM.pages.forecast = {
  async render(view) {
    const ui = SM.ui;
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    let data = null, selected = params.get("ingredient_id"), requestId = 0;
    let overrideId = null, overrideUsage = null, overrideDate = null;
    const form = ui.el("div", {class: "form-grid"});
    const ingredient = ui.el("select", {id: "forecast-ingredient", class: "select", disabled: true});
    const horizon = ui.el("select", {id: "forecast-days", class: "select"}, [7, 14, 30].map(days =>
      ui.el("option", {value: days, text: `${days} days`, selected: String(days) === params.get("days")})));
    const scenario = ui.el("select", {id: "forecast-scenario", class: "select"},
      ui.el("option", {value: "conservative", text: "Conservative — confirmed eligible deliveries"}),
      ui.el("option", {value: "expected", text: "Expected — includes uncertain eligible deliveries", selected: params.get("scenario") === "expected"}));
    for (const [label, control] of [["Ingredient", ingredient], ["Forecast range", horizon], ["Delivery scenario", scenario]]) {
      form.append(ui.el("div", {class: "field"}, ui.el("label", {class: "field__label", for: control.id, text: label}), control));
    }
    const refresh = ui.el("button", {class: "btn btn--secondary", type: "button", text: "Refresh forecast"});
    const feedback = ui.el("div"), content = ui.el("div", {id: "forecast-content"});
    const usageError = ui.el("span", {class: "field-error", id: "forecast-today-error"});
    const usageInput = ui.el("input", {id: "forecast-today-usage", class: "input", type: "text", inputmode: "decimal", maxlength: 20,
      "aria-describedby": "forecast-today-hint forecast-today-error", disabled: true,
      oninput: () => { usageInput.removeAttribute("aria-invalid"); usageError.textContent = ""; }});
    const usageLabel = ui.el("label", {class: "field__label", for: "forecast-today-usage", text: "Today's Remaining Usage"});
    const usageHint = ui.el("p", {id: "forecast-today-hint", class: "field__hint"});
    const applyUsage = ui.el("button", {class: "btn btn--secondary", type: "button", text: "Apply remaining usage", disabled: true});
    const resetUsage = ui.el("button", {class: "btn btn--secondary", type: "button", text: "Use configured full-day estimate", disabled: true});
    const usageControls = ui.el("div", {class: "forecast-today-controls"},
      ui.el("div", {class: "field"}, usageLabel, usageInput, usageError),
      ui.el("div", {class: "button-row"}, applyUsage, resetUsage), usageHint);
    view.append(ui.el("div", {class: "card"}, form, usageControls, refresh, feedback), content);
    const updateUrl = () => {
      if (!location.hash.startsWith("#/forecast")) return;
      const next = new URLSearchParams({days: horizon.value, scenario: scenario.value});
      if (selected) next.set("ingredient_id", selected);
      history.replaceState(null, "", `#/forecast?${next}`);
    };
    const draw = () => {
      if (!data) return;
      ui.clear(content);
      const item = data.ingredients.find(row => String(row.ingredient_id) === selected);
      if (!item) { content.append(ui.empty("No active ingredients are available for forecasting.")); return; }
      usageLabel.textContent = `Today's Remaining Usage (${item.unit}) — ${item.ingredient_name}`;
      usageInput.disabled = false; applyUsage.disabled = false;
      usageInput.value = item.today_usage_source === "override" ? ui.formatQty(item.today_remaining_usage) : "";
      usageInput.placeholder = item.today_configured_usage === null ? "Not configured" : ui.formatQty(item.today_configured_usage);
      resetUsage.disabled = item.today_usage_source !== "override";
      usageHint.textContent = `Default full-day upper bound: ${SM.forecastUI.quantity(item.today_configured_usage, item.unit)}. Blank uses that default; enter zero after operations if no usage remains. Overrides affect this forecast only and are not saved. The dashboard uses the default policy.`;
      updateUrl();
      content.append(this.renderIngredient(data, item, scenario.value));
    };
    const load = async () => {
      const token = ++requestId;
      ui.clear(feedback); content.setAttribute("aria-busy", "true");
      ui.clear(content).append(ui.loading("Calculating read-only forecast…"));
      refresh.disabled = true;
      applyUsage.disabled = true; resetUsage.disabled = true;
      try {
        // One batch contains both scenarios and all ingredients. Selecting a
        // different ingredient/scenario reuses it; range/refresh reload once.
        const query = new URLSearchParams({days: horizon.value});
        if (overrideUsage !== null) {
          query.set("today_usage_ingredient_id", overrideId);
          query.set("today_remaining_usage", overrideUsage);
          if (overrideDate) query.set("today_usage_date", overrideDate);
        }
        const result = await SM.api.get(`/forecast/calendar?${query}`);
        if (token !== requestId) return;
        data = result;
        if (!data.ingredients.some(row => String(row.ingredient_id) === selected)) {
          selected = String((data.ingredients.find(row => row.configuration_state === "enabled") || data.ingredients[0])?.ingredient_id || "");
        }
        ui.clear(ingredient).append(...data.ingredients.map(row => ui.el("option", {
          value: row.ingredient_id, text: `${row.ingredient_name} (${row.unit})`, selected: String(row.ingredient_id) === selected,
        })));
        ingredient.disabled = !data.ingredients.length;
        draw();
      } catch (error) {
        if (token === requestId && error.code === "today_override_expired") {
          overrideId = null; overrideUsage = null; overrideDate = null;
          await load();
          feedback.append(ui.alert("The previous business day's temporary usage override expired. The current full-day default is now shown.", "info"));
          return;
        }
        if (token === requestId) { data = null; ui.clear(content); feedback.append(ui.alert(error.message), ui.el("p", {text: "Use Refresh forecast to try again."})); }
      } finally {
        if (token === requestId) { refresh.disabled = false; applyUsage.disabled = !selected;
          resetUsage.disabled = overrideUsage === null; content.setAttribute("aria-busy", "false"); }
      }
    };
    ingredient.addEventListener("change", () => { selected = ingredient.value; draw(); });
    scenario.addEventListener("change", draw);
    horizon.addEventListener("change", load);
    refresh.addEventListener("click", load);
    applyUsage.addEventListener("click", () => {
      try {
        const value = SM.demandUI.quantity(usageInput.value);
        usageInput.removeAttribute("aria-invalid"); usageError.textContent = "";
        if (value === null) { overrideId = null; overrideUsage = null; overrideDate = null; }
        else { overrideId = selected; overrideUsage = value; overrideDate = data?.business_date || null; }
        load();
      } catch (error) { usageError.textContent = error.message; usageInput.setAttribute("aria-invalid", "true"); usageInput.focus(); }
    });
    resetUsage.addEventListener("click", () => { overrideId = null; overrideUsage = null; overrideDate = null; load(); });
    await load();
  },

  renderIngredient(data, item, scenarioName) {
    const ui = SM.ui, helper = SM.forecastUI, projection = item[scenarioName];
    const root = ui.el("div");
    const card = ui.el("div", {class: "card"},
      ui.el("h2", {class: "card__title", text: `${item.ingredient_name} — ${scenarioName === "expected" ? "Expected" : "Conservative"} projection`}),
      ui.el("p", {text: `As of ${ui.formatInTz(data.as_of, data.business_timezone)}. Future forecast dates: ${ui.formatDateOnly(data.start_date)} to ${ui.formatDateOnly(data.end_date)}. Today's preview feeds tomorrow's opening.`}));
    const facts = ui.el("dl", {class: "forecast-summary-grid"});
    for (const [label, value] of [["Actual available stock now", helper.quantity(item.actual_available, item.unit)],
      ["Planning safety stock", helper.quantity(item.safety_stock, item.unit)],
      ["Outstanding placed orders (all dates)", helper.quantity(item.open_outstanding_quantity, item.unit)],
      ["Excluded from both scenarios in this range", helper.quantity(item.excluded_open_quantity, item.unit)],
      ["First projected shortage", projection.first_shortage_date ? ui.formatDateOnly(projection.first_shortage_date) : "None known"],
      ["Last verified physical count", item.confidence.last_verified_at ? ui.formatInTz(item.confidence.last_verified_at, data.business_timezone) : "Not recorded"]]) {
      facts.append(ui.el("div", {}, ui.el("dt", {text: label}), ui.el("dd", {text: value})));
    }
    card.append(facts, helper.confidence(item.confidence), ui.el("p", {class: "field__hint", text: "This physical-count confidence applies to all projected days and is independent of availability."}));
    if (item.depends_on_uncertain) card.append(ui.alert("Maintaining projected stock depends on uncertain incoming orders. Compare both scenarios.", "info"));
    if (item.configuration_state !== "enabled") {
      card.append(ui.alert(`Demand configuration: ${item.configuration_state}. Unknown values are not zero.`, "info"));
      card.append(ui.el("a", {href: "#/ingredients", text: SM.auth.isAdmin() ? "Edit Forecast Settings in Ingredients →" : "View Forecast Settings in Ingredients →"}));
    }
    const warnings = ui.el("ul", {class: "forecast-issues"});
    item.warnings.forEach(warning => warnings.append(ui.el("li", {text: warning})));
    card.append(warnings);
    const assumptions = ui.el("details", {class: "forecast-explanation"}, ui.el("summary", {text: "Forecast assumptions and delivery timing"}));
    assumptions.append(ui.el("ul", {}, data.assumptions.map(text => ui.el("li", {text}))));
    // The baseline caveat stays visible even when the remaining details collapse.
    card.append(ui.el("p", {class: "field__hint", text: data.assumptions[0]}), assumptions);
    root.append(card);
    const today = projection.today;
    const todayCard = ui.el("div", {class: "card", id: "forecast-today-preview"},
      ui.el("h2", {class: "card__title", text: `Today — current-day preview (${ui.formatDateOnly(today.date)})`}),
      ui.el("p", {class: "field__hint", text: item.today_usage_source === "override"
        ? "Using an explicit forecast-only remaining usage override. Actual stock and permanent demand settings are unchanged."
        : item.today_usage_source === "full_day_upper_bound"
          ? "Using the configured full-day estimate as a conservative upper bound for remaining usage. Adjust it if some usage is already accounted for."
          : "Today's remaining usage is unknown or forecasting is disabled; unknown is not zero."}));
    const todayFacts = ui.el("dl", {class: "forecast-summary-grid"});
    for (const [label, value] of [["Actual stock at as-of", item.actual_available], ["Today's remaining usage", today.estimated_consumption],
      ["Eligible outstanding arrivals today", today.eligible_incoming], ["Before-delivery balance", today.pre_delivery_balance],
      ["Today's projected close / tomorrow's opening", today.closing_balance]]) {
      todayFacts.append(ui.el("div", {}, ui.el("dt", {text: label}), ui.el("dd", {text: helper.quantity(value, item.unit)})));
    }
    todayCard.append(todayFacts, ui.badge(helper.statusInfo(today.status).label, helper.statusInfo(today.status).kind));
    if (helper.riskText(today, item.unit)) todayCard.append(ui.badge(helper.riskText(today, item.unit), "critical"));
    today.warnings.forEach(warning => todayCard.append(ui.el("p", {class: "field__hint", text: warning})));
    root.append(todayCard);
    const calendar = ui.el("div", {class: "card"}, ui.el("h2", {class: "card__title", text: "Daily availability calendar"}),
      ui.el("p", {class: "field__hint", text: "These are future dates, starting from today's projected close. Delivery times are unknown: the before-delivery calculation deducts usage before incoming and flags possible shortage, while closing balances include eligible arrivals. Negative projections carry cumulative unmet demand forward."}));
    const table = ui.el("table", {class: "table table--responsive", id: "forecast-calendar"},
      ui.el("thead", {}, ui.el("tr", {}, ["Business day", "Opening projected", "Eligible arrivals", "Estimated usage", "Closing projected", "Planning safety", "Availability", "Warnings"].map(text => ui.el("th", {scope: "col", text})))));
    const tbody = ui.el("tbody");
    for (const day of projection.days) {
      const status = helper.statusInfo(day.status);
      const availability = day.status === "shortage" ? `${status.label}: ${helper.quantity(day.unmet_demand, item.unit)} unmet` : status.label;
      tbody.append(ui.el("tr", {"data-date": day.date, "data-status": day.status},
        ui.el("td", {"data-label": "Business day", text: ui.formatDateOnly(day.date)}),
        ui.el("td", {"data-label": "Opening projected", text: helper.quantity(day.opening_balance, item.unit)}),
        ui.el("td", {"data-label": "Eligible arrivals"}, ui.el("div", {class: "forecast-arrivals"},
          ui.el("strong", {text: helper.quantity(day.eligible_incoming, item.unit)}),
          ui.el("div", {class: "field__hint", text: `Confirmed: ${helper.quantity(day.confirmed_incoming, item.unit)} · Uncertain: ${helper.quantity(day.uncertain_incoming, item.unit)} (${scenarioName === "expected" ? "included" : "excluded"})` }))),
        ui.el("td", {"data-label": "Estimated usage", text: helper.quantity(day.estimated_consumption, item.unit)}),
        ui.el("td", {"data-label": "Closing projected", text: helper.quantity(day.closing_balance, item.unit)}),
        ui.el("td", {"data-label": "Planning safety", text: helper.quantity(day.safety_stock, item.unit)}),
        ui.el("td", {"data-label": "Availability"}, ui.badge(availability, status.kind)),
        ui.el("td", {"data-label": "Warnings"}, ui.el("div", {class: "forecast-risk-details"},
          helper.riskText(day, item.unit) ? ui.badge(helper.riskText(day, item.unit), "critical") : null,
          ui.el("span", {text: day.warnings.join(" ") || "—"})))));
    }
    table.append(tbody); calendar.append(ui.el("div", {class: "table-wrap"}, table)); root.append(calendar);
    const incoming = ui.el("div", {class: "card"}, ui.el("h2", {class: "card__title", text: "Incoming orders and eligibility"}));
    if (!item.incoming.length) incoming.append(ui.empty("No outstanding order lines. Received goods are already part of actual stock."));
    for (const delivery of item.incoming) {
      incoming.append(ui.el("div", {class: "forecast-delivery"},
        ui.el("strong", {text: `Order #${delivery.order_id} · ${delivery.supplier_name} · ${delivery.status.replaceAll("_", " ")}`}),
        ui.el("p", {text: `${delivery.expected_date ? ui.formatDateOnly(delivery.expected_date) : "No expected date"} · Outstanding ${helper.quantity(delivery.outstanding_quantity, item.unit)} · Already accepted ${helper.quantity(delivery.accepted_received_quantity, item.unit)}`}),
        ui.el("p", {class: "field__hint", text: `Conservative: ${delivery.conservative_eligible ? "included" : "excluded"}. Expected: ${delivery.expected_eligible ? "included" : "excluded"}. ${delivery.reasons.join(" ")}`})));
    }
    root.append(incoming);
    const cutoffs = ui.el("div", {class: "card"}, ui.el("h2", {class: "card__title", text: "Upcoming supplier cutoffs"}));
    if (!item.upcoming_cutoffs.length) cutoffs.append(ui.empty("No applicable upcoming cutoff is configured in the scheduling horizon."));
    for (const cutoff of item.upcoming_cutoffs) cutoffs.append(ui.el("p", {
      text: `${cutoff.supplier_name}${cutoff.is_primary ? " (primary)" : " (alternative)"}: ${ui.formatInTz(cutoff.cutoff_at, cutoff.timezone)} · delivery ${ui.formatDateOnly(cutoff.delivery_date)} · ${cutoff.status}${cutoff.exception_type ? ` · ${cutoff.exception_type.replaceAll("_", " ")}` : ""}`,
    }));
    cutoffs.append(ui.el("p", {class: "field__hint", text: "Cutoffs are scheduling information only. No new orders are created or recommended by this view."}));
    root.append(cutoffs);
    return root;
  },
};
