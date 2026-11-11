// Supplier delivery schedule management (Phase 2 Step 1). Rendered inside a
// modal from the Suppliers page. Staff see everything read-only; create/edit/
// deactivate controls render only for admins (RBAC is enforced server-side).
//
// Rule times are local wall-clock in the rule's timezone; computed instants
// (cutoffs, reminders, contact deadlines) come back from the API as UTC and
// are formatted in the rule's timezone for display.

window.SM = window.SM || {};

SM.schedulesUI = {
  WEEKDAYS: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],

  CONTACT_METHODS: [
    ["phone", "Phone Call"], ["line", "LINE"], ["email", "Email"],
    ["website", "Website"], ["other", "Other"],
  ],
  CONTACT_ACTIONS: [
    ["", "None"], ["call", "Call"], ["email", "Email"], ["line", "LINE Message"],
    ["website_order", "Website Order"], ["other", "Other"],
  ],
  EXCEPTION_TYPES: [
    ["delivery_cancelled", "Delivery cancelled"],
    ["ordering_suspended", "Ordering suspended"],
    ["cutoff_override", "Cutoff moved (one-time)"],
    ["extra_delivery", "Extra one-time delivery"],
  ],
  STATUS_BADGES: {
    open: ["Open", "safe"],
    upcoming: ["Upcoming", "info"],
    cutoff_reached: ["Cutoff reached", "warning"],
    missed: ["Missed", "neutral"],
    unavailable_holiday: ["Unavailable (holiday)", "critical"],
  },

  async open(supplier, isAdmin) {
    const ui = SM.ui;
    const body = ui.el("div");
    body.append(ui.loading());
    const close = ui.modal({
      title: `Delivery Schedules — ${supplier.name}`, body, wide: true,
    });

    const load = async () => {
      try {
        const [detail, rules, exceptions, opportunities] = await Promise.all([
          SM.api.get(`/suppliers/${supplier.id}`),
          SM.api.get(`/suppliers/${supplier.id}/schedules`),
          SM.api.get(`/suppliers/${supplier.id}/exceptions`),
          SM.api.get(`/suppliers/${supplier.id}/opportunities?days=21`),
        ]);
        ui.clear(body);
        this.renderGeneral(body, detail);
        this.renderOpportunities(body, opportunities);
        this.renderRules(body, detail, rules, isAdmin, load);
        this.renderExceptions(body, detail, rules, exceptions, isAdmin, load);
      } catch (error) {
        ui.clear(body).append(ui.alert(error.message));
      }
    };
    await load();
    return close;
  },

  // --- General instructions ------------------------------------------------

  renderGeneral(body, supplier) {
    const ui = SM.ui;
    const method = this.CONTACT_METHODS.find(([value]) => value === supplier.contact_method);
    const details = [
      supplier.contact_name ? `Contact: ${supplier.contact_name}` : null,
      supplier.phone, supplier.email,
      method ? `Preferred: ${method[1]}` : null,
    ].filter(Boolean).join(" · ");
    body.append(ui.el("div", { class: "card" },
      ui.el("h3", { text: "General Instructions" }),
      details ? ui.el("p", { class: "text-small", text: details }) : null,
      supplier.ordering_url
        ? ui.el("p", { class: "text-small" }, "Ordering site: ",
            ui.el("a", { href: supplier.ordering_url, target: "_blank", rel: "noopener noreferrer", text: supplier.ordering_url }))
        : null,
      supplier.ordering_procedures
        ? ui.el("p", { class: "text-small", text: supplier.ordering_procedures })
        : null,
      supplier.notes ? ui.el("p", { class: "text-small text-muted", text: supplier.notes }) : null,
      !details && !supplier.ordering_procedures
        ? ui.el("p", { class: "text-small text-muted", text: "No general instructions recorded yet." })
        : null,
    ));
  },

  // --- Upcoming opportunities ------------------------------------------------

  renderOpportunities(body, opportunities) {
    const ui = SM.ui;
    const section = ui.el("div", { class: "card" },
      ui.el("h3", { text: "Upcoming Deliveries & Cutoffs" }));
    const visible = opportunities.filter((o) => o.status !== "missed").slice(0, 8);
    if (visible.length === 0) {
      section.append(ui.empty("No upcoming delivery windows in the next 3 weeks."));
    } else {
      const list = ui.el("ul", { class: "opportunity-list" });
      for (const opp of visible) {
        const [label, kind] = this.STATUS_BADGES[opp.status] || [opp.status, "neutral"];
        const lines = [
          ui.el("strong", {
            text: ui.formatDateOnly(opp.delivery_date, this.WEEKDAYS[opp.delivery_weekday]),
          }),
          opp.ingredient_name ? ` (${opp.ingredient_name})` : "",
          " — order before ",
          ui.el("strong", { text: ui.formatInTz(opp.cutoff_at, opp.timezone) }),
          " ",
          ui.badge(label, kind),
        ];
        if (opp.contact) {
          lines.push(ui.el("div", { class: "text-small" },
            `${opp.contact.required ? "Required action" : "Optional action"}: ${opp.contact.action} before `,
            ui.el("strong", { text: ui.formatInTz(opp.contact.deadline_at, opp.timezone) })));
        }
        if (opp.special_instructions) {
          lines.push(ui.el("div", { class: "text-small text-muted", text: opp.special_instructions }));
        }
        if (opp.exception) {
          lines.push(ui.el("div", { class: "text-small text-muted" },
            `Exception: ${opp.exception.type.replaceAll("_", " ")}`,
            opp.exception.note ? ` — ${opp.exception.note}` : ""));
        }
        list.append(ui.el("li", {}, lines));
      }
      section.append(list);
    }
    body.append(section);
  },

  // --- Schedule rules ----------------------------------------------------------

  renderRules(body, supplier, rules, isAdmin, reload) {
    const ui = SM.ui;
    const section = ui.el("div", { class: "card" },
      ui.el("div", { class: "button-row" },
        ui.el("h3", { text: "Delivery Schedule Rules", style: "margin-right:auto" }),
        isAdmin ? ui.el("button", {
          class: "btn btn--secondary btn--small", text: "Add Delivery Schedule",
          onclick: () => this.ruleForm(supplier, null, reload),
        }) : null));
    if (rules.length === 0) {
      section.append(ui.empty("No delivery schedules configured yet."));
    } else {
      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ...["Delivery", "Order Cutoff", "Reminder", "Contact Action", "Instructions", "Status"]
            .concat(isAdmin ? ["Actions"] : [])
            .map((h) => ui.el("th", { text: h })))));
      const tbody = ui.el("tbody");
      for (const rule of rules) {
        tbody.append(this.ruleRow(supplier, rule, isAdmin, reload));
      }
      table.append(tbody);
      section.append(ui.el("div", { class: "table-wrap" }, table));
    }
    body.append(section);
  },

  ruleRow(supplier, rule, isAdmin, reload) {
    const ui = SM.ui;
    const cutoff = `${this.offsetLabel(rule.order_cutoff_days_before)} ${this.timeLabel(rule.order_cutoff_time)}`;
    const reminder = rule.reminder_days_before !== null
      ? `${this.offsetLabel(rule.reminder_days_before)} ${this.timeLabel(rule.reminder_time)}`
      : "—";
    const contact = rule.contact_action
      ? `${rule.contact_action.replace("_", " ")} · ${this.offsetLabel(rule.contact_deadline_days_before)} ${this.timeLabel(rule.contact_deadline_time)}${rule.contact_required ? " (required)" : ""}`
      : "—";
    const delivery = this.WEEKDAYS[rule.delivery_weekday]
      + (rule.ingredient_name ? ` (${rule.ingredient_name} only)` : "");
    const cells = [
      ui.el("td", { "data-label": "Delivery", text: delivery }),
      ui.el("td", { "data-label": "Order Cutoff", text: cutoff }),
      ui.el("td", { "data-label": "Reminder", text: reminder }),
      ui.el("td", { "data-label": "Contact Action", text: contact }),
      ui.el("td", { "data-label": "Instructions", text: rule.special_instructions || "—" }),
      ui.el("td", { "data-label": "Status" },
        ui.badge(rule.is_active ? "Active" : "Inactive", rule.is_active ? "safe" : "neutral")),
    ];
    if (isAdmin) {
      cells.push(ui.el("td", { "data-label": "Actions" },
        ui.el("div", { class: "button-row" },
          ui.el("button", {
            class: "btn btn--secondary btn--small", text: "Edit",
            onclick: () => this.ruleForm(supplier, rule, reload),
          }),
          ui.el("button", {
            class: "btn btn--secondary btn--small",
            text: rule.is_active ? "Deactivate" : "Reactivate",
            onclick: () => this.toggleRule(rule, reload),
          }))));
    }
    return ui.el("tr", {}, cells);
  },

  offsetLabel(daysBefore) {
    if (daysBefore === 1) return "Day before";
    return `${daysBefore} days before`;
  },

  timeLabel(value) {
    return value ? value.slice(0, 5) : "";
  },

  async toggleRule(rule, reload) {
    const ui = SM.ui;
    const action = rule.is_active ? "deactivate" : "reactivate";
    const approved = await ui.confirmDialog({
      title: `${rule.is_active ? "Deactivate" : "Reactivate"} schedule`,
      message: rule.is_active
        ? "Deactivate this delivery schedule? Historical data is preserved; the rule simply stops generating upcoming opportunities. This is the safe alternative to deletion."
        : "Reactivate this schedule rule? This fails if a newer active rule now covers the same weekday.",
      confirmLabel: rule.is_active ? "Deactivate" : "Reactivate",
      danger: rule.is_active,
    });
    if (!approved) return;
    try {
      await SM.api.post(`/schedules/${rule.id}/${action}`);
      ui.toast(`Schedule ${action}d.`);
      reload();
    } catch (error) {
      ui.modal({ title: "Cannot update schedule", body: ui.alert(error.message) });
    }
  },

  // --- Rule form --------------------------------------------------------------

  ruleForm(supplier, rule, reload) {
    const ui = SM.ui;
    const editing = Boolean(rule);
    const fields = {};
    const errorArea = ui.el("div");

    const field = (key, labelText, input) => {
      fields[key] = input;
      input.id = `rule-${key}`;
      return ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: `rule-${key}`, text: labelText }),
        input);
    };
    const daysInput = (key, min, max, value) => ui.el("input", {
      class: "input", type: "number", min: String(min), max: String(max), step: "1",
      value: value === null || value === undefined ? "" : String(value),
    });
    const timeInput = (key, value) => ui.el("input", {
      class: "input", type: "time", value: value ? value.slice(0, 5) : "",
    });

    const weekdaySelect = ui.el("select", { class: "select" },
      this.WEEKDAYS.map((name, index) => ui.el("option", {
        value: String(index), text: name,
        selected: rule ? rule.delivery_weekday === index : false,
      })));
    const ingredientSelect = ui.el("select", { class: "select" },
      ui.el("option", { value: "", text: "All ingredients (supplier-wide)" }),
      (supplier.ingredients || []).map((link) => ui.el("option", {
        value: String(link.ingredient_id),
        text: `${link.ingredient_name} only (override)`,
        selected: rule ? rule.ingredient_id === link.ingredient_id : false,
      })));
    const contactSelect = ui.el("select", { class: "select" },
      this.CONTACT_ACTIONS.map(([value, label]) => ui.el("option", {
        value, text: label, selected: rule ? (rule.contact_action || "") === value : false,
      })));
    const contactRequired = ui.el("input", { type: "checkbox", checked: rule?.contact_required });
    const timezoneInput = ui.el("input", {
      class: "input", type: "text", maxlength: "50",
      value: rule?.timezone || "Asia/Tokyo",
    });
    const instructions = ui.el("textarea", { class: "textarea", rows: 2 });
    instructions.value = rule?.special_instructions || "";

    const saveButton = ui.el("button", { class: "btn", type: "submit", text: editing ? "Save Schedule" : "Add Schedule" });
    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        ui.setBusy(saveButton, true, "Saving…");
        const number = (input) => input.value === "" ? null : Number(input.value);
        const time = (input) => input.value === "" ? null : input.value;
        const payload = {
          ingredient_id: ingredientSelect.value === "" ? null : Number(ingredientSelect.value),
          delivery_weekday: Number(weekdaySelect.value),
          order_cutoff_days_before: number(fields.cutoffDays),
          order_cutoff_time: time(fields.cutoffTime),
          order_open_days_before: number(fields.openDays),
          order_open_time: time(fields.openTime),
          reminder_days_before: number(fields.reminderDays),
          reminder_time: time(fields.reminderTime),
          timezone: timezoneInput.value.trim() || "Asia/Tokyo",
          contact_action: contactSelect.value || null,
          contact_deadline_days_before: number(fields.contactDays),
          contact_deadline_time: time(fields.contactTime),
          contact_required: contactRequired.checked,
          special_instructions: instructions.value.trim() || null,
        };
        if (payload.order_cutoff_days_before === null || !payload.order_cutoff_time) {
          errorArea.append(ui.alert("A cutoff day offset (1–6) and cutoff time are required."));
          ui.setBusy(saveButton, false);
          return;
        }
        try {
          if (editing) {
            await SM.api.patch(`/schedules/${rule.id}`, payload);
          } else {
            await SM.api.post(`/suppliers/${supplier.id}/schedules`, payload);
          }
          close();
          ui.toast(editing ? "Schedule updated." : "Schedule added.");
          reload();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
        } finally {
          ui.setBusy(saveButton, false);
        }
      },
    });

    form.append(
      field("weekday", "Delivery Day", weekdaySelect),
      field("ingredient", "Applies To", ingredientSelect),
      field("cutoffDays", "Order cutoff — days before delivery (1–6)",
        daysInput("cutoffDays", 1, 6, rule?.order_cutoff_days_before)),
      field("cutoffTime", "Order cutoff time", timeInput("cutoffTime", rule?.order_cutoff_time)),
      field("openDays", "Ordering opens — days before (optional)",
        daysInput("openDays", 1, 13, rule?.order_open_days_before)),
      field("openTime", "Ordering opens at (optional)", timeInput("openTime", rule?.order_open_time)),
      field("reminderDays", "Reminder — days before (optional)",
        daysInput("reminderDays", 0, 13, rule?.reminder_days_before)),
      field("reminderTime", "Reminder at (optional)", timeInput("reminderTime", rule?.reminder_time)),
      field("contactAction", "Required contact action (optional)", contactSelect),
      field("contactDays", "Contact deadline — days before", daysInput("contactDays", 0, 13, rule?.contact_deadline_days_before)),
      field("contactTime", "Contact deadline at", timeInput("contactTime", rule?.contact_deadline_time)),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label" },
          ui.el("span", { text: "Contact action is required " }), contactRequired)),
      field("timezone", "Timezone (IANA name)", timezoneInput),
      (() => {
        const wrapper = ui.el("div", { class: "field" },
          ui.el("label", { class: "field__label", for: "rule-instructions", text: "Special instructions" }),
          instructions);
        instructions.id = "rule-instructions";
        return wrapper;
      })(),
      errorArea,
      ui.el("div", { class: "button-row" }, saveButton),
    );

    const close = ui.modal({
      title: editing
        ? `Edit ${this.WEEKDAYS[rule.delivery_weekday]} schedule — ${supplier.name}`
        : `Add Delivery Schedule — ${supplier.name}`,
      body: form, wide: true,
    });
  },

  // --- Exceptions ---------------------------------------------------------------

  renderExceptions(body, supplier, rules, exceptions, isAdmin, reload) {
    const ui = SM.ui;
    const section = ui.el("div", { class: "card" },
      ui.el("div", { class: "button-row" },
        ui.el("h3", { text: "Holidays & Exceptions", style: "margin-right:auto" }),
        isAdmin ? ui.el("button", {
          class: "btn btn--secondary btn--small", text: "Add Exception",
          onclick: () => this.exceptionForm(supplier, rules, null, reload),
        }) : null));
    if (exceptions.length === 0) {
      section.append(ui.empty("No exceptions configured. Weekly rules apply on every date."));
    } else {
      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ...["Date", "Type", "Cutoff Override", "Note", "Status"]
            .concat(isAdmin ? ["Actions"] : [])
            .map((h) => ui.el("th", { text: h })))));
      const tbody = ui.el("tbody");
      for (const exception of exceptions) {
        const cells = [
          ui.el("td", { "data-label": "Date", text: ui.formatDateOnly(exception.date) }),
          ui.el("td", { "data-label": "Type", text: exception.type.replaceAll("_", " ") }),
          ui.el("td", { "data-label": "Cutoff Override", text: ui.formatDateTime(exception.override_cutoff_at) }),
          ui.el("td", { "data-label": "Note", text: exception.note || "—" }),
          ui.el("td", { "data-label": "Status" },
            ui.badge(exception.is_active ? "Active" : "Inactive", exception.is_active ? "warning" : "neutral")),
        ];
        if (isAdmin) {
          cells.push(ui.el("td", { "data-label": "Actions" },
            ui.el("div", { class: "button-row" },
              ui.el("button", {
                class: "btn btn--secondary btn--small", text: "Edit",
                onclick: () => this.exceptionForm(supplier, rules, exception, reload),
              }),
              ui.el("button", {
                class: "btn btn--secondary btn--small",
                text: exception.is_active ? "Deactivate" : "Reactivate",
                onclick: () => this.toggleException(exception, reload),
              }))));
        }
        tbody.append(ui.el("tr", {}, cells));
      }
      table.append(tbody);
      section.append(ui.el("div", { class: "table-wrap" }, table));
    }
    body.append(section);
  },

  async toggleException(exception, reload) {
    const ui = SM.ui;
    const action = exception.is_active ? "deactivate" : "reactivate";
    const approved = await ui.confirmDialog({
      title: `${exception.is_active ? "Deactivate" : "Reactivate"} exception`,
      message: exception.is_active
        ? "Deactivate this exception? The normal weekly schedule will apply on that date again."
        : "Reactivate this exception?",
      confirmLabel: exception.is_active ? "Deactivate" : "Reactivate",
    });
    if (!approved) return;
    try {
      await SM.api.post(`/exceptions/${exception.id}/${action}`);
      ui.toast(`Exception ${action}d.`);
      reload();
    } catch (error) {
      ui.modal({ title: "Cannot update exception", body: ui.alert(error.message) });
    }
  },

  exceptionForm(supplier, rules, exception, reload) {
    const ui = SM.ui;
    const editing = Boolean(exception);
    const errorArea = ui.el("div");

    const dateInput = ui.el("input", {
      class: "input", type: "date", id: "exception-date",
      value: exception?.date || "",
    });
    const typeSelect = ui.el("select", { class: "select", id: "exception-type" },
      this.EXCEPTION_TYPES.map(([value, label]) => ui.el("option", {
        value, text: label, selected: exception ? exception.type === value : false,
        disabled: editing, // type is fixed once created
      })));
    if (editing) typeSelect.disabled = true;
    const ruleSelect = ui.el("select", { class: "select", id: "exception-rule" },
      ui.el("option", { value: "", text: "Whole supplier" }),
      (rules || []).map((rule) => ui.el("option", {
        value: String(rule.id),
        text: `${this.WEEKDAYS[rule.delivery_weekday]}${rule.ingredient_name ? ` (${rule.ingredient_name})` : ""}`,
        selected: exception ? exception.schedule_rule_id === rule.id : false,
      })));
    const overrideInput = ui.el("input", {
      class: "input", type: "datetime-local", id: "exception-override",
      value: exception?.override_cutoff_at ? this.toLocalInputValue(exception.override_cutoff_at) : "",
    });
    const noteInput = ui.el("textarea", { class: "textarea", rows: 2, id: "exception-note" });
    noteInput.value = exception?.note || "";

    const saveButton = ui.el("button", { class: "btn", type: "submit", text: editing ? "Save Exception" : "Add Exception" });
    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        ui.setBusy(saveButton, true, "Saving…");
        if (!dateInput.value) {
          errorArea.append(ui.alert("A date is required."));
          ui.setBusy(saveButton, false);
          return;
        }
        // datetime-local is browser-local; send an explicit UTC instant.
        const override = overrideInput.value
          ? new Date(overrideInput.value).toISOString()
          : null;
        try {
          if (editing) {
            await SM.api.patch(`/exceptions/${exception.id}`, {
              date: dateInput.value,
              schedule_rule_id: ruleSelect.value === "" ? null : Number(ruleSelect.value),
              override_cutoff_at: override,
              note: noteInput.value.trim() || null,
            });
          } else {
            await SM.api.post(`/suppliers/${supplier.id}/exceptions`, {
              date: dateInput.value,
              type: typeSelect.value,
              schedule_rule_id: ruleSelect.value === "" ? null : Number(ruleSelect.value),
              override_cutoff_at: override,
              note: noteInput.value.trim() || null,
            });
          }
          close();
          ui.toast(editing ? "Exception updated." : "Exception added.");
          reload();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
        } finally {
          ui.setBusy(saveButton, false);
        }
      },
    });

    const labelled = (id, labelText, input) => ui.el("div", { class: "field" },
      ui.el("label", { class: "field__label", for: id, text: labelText }), input);
    form.append(
      labelled("exception-date", "Date", dateInput),
      labelled("exception-type", "Exception type", typeSelect),
      labelled("exception-rule", "Applies to (optional)", ruleSelect),
      labelled("exception-override", "Replacement cutoff (required for cutoff moves and extra deliveries)", overrideInput),
      labelled("exception-note", "Note", noteInput),
      errorArea,
      ui.el("div", { class: "button-row" }, saveButton),
    );

    const close = ui.modal({
      title: `${editing ? "Edit" : "Add"} Exception — ${supplier.name}`, body: form,
    });
  },

  toLocalInputValue(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const pad = (value) => String(value).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
  },
};
