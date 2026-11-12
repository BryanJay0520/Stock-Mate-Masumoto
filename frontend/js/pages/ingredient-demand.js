// Demand inputs are planning assumptions in canonical stock units.
window.SM = window.SM || {};
SM.demandUI = {
  days: ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],

  quantity(text) {
    if (text.trim() === "") return null;
    const value = SM.ui.toMillis(text);
    if (value === null || value < 0 || !Number.isSafeInteger(value) || value > 99999999999999) {
      throw new Error("Enter zero or a positive quantity with up to 3 decimal places (maximum 99999999999.999).");
    }
    return SM.ui.fromMillis(value);
  },

  weekly(values) {
    if (values.some(value => value === null)) return null;
    return SM.ui.fromMillis(values.reduce((total, value) => total + SM.ui.toMillis(value), 0));
  },

  async open(ingredient) {
    const ui = SM.ui;
    const admin = SM.auth.isAdmin();
    const body = ui.el("div", { "aria-busy": "true" }, ui.loading("Loading forecast settings…"));
    let dirty = false, saving = false, closed = false;
    const feedback = ui.el("div", { id: "demand-feedback" });
    const unload = event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", unload);
    let close;
    const cancel = ui.el("button", {
      type: "button", class: "btn btn--secondary", text: "Close",
      onclick: () => close(),
    });
    close = ui.modal({
      title: `Forecast Settings — ${ingredient.name}`, body, actions: [cancel], wide: true,
      beforeClose: () => {
        if (saving) return false;
        if (!dirty) return true;
        ui.clear(feedback).append(ui.alert("You have unsaved forecast settings. Keep editing or discard your changes.", "info"),
          ui.el("button", {
            type: "button", class: "btn btn--danger", text: "Discard changes",
            onclick: () => { dirty = false; close(); },
          }));
        feedback.scrollIntoView({ block: "nearest" });
        feedback.querySelector("button").focus();
        return false;
      },
      onClose: () => { closed = true; window.removeEventListener("beforeunload", unload); },
    });
    const load = async () => {
      ui.clear(body).append(ui.loading("Loading forecast settings…"));
      body.setAttribute("aria-busy", "true");
      try {
        const config = await SM.api.get(`/ingredients/${ingredient.id}/demand-settings`);
        if (!closed) render(config);
      } catch (error) {
        if (!closed) ui.clear(body).append(ui.alert(error.message), ui.el("button", {
          class: "btn btn--secondary", text: "Retry loading", onclick: load,
        }));
      } finally { body.setAttribute("aria-busy", "false"); }
    };

    function render(config) {
      ui.clear(body);
      ui.clear(feedback);
      const form = ui.el("form", { novalidate: true, id: "demand-form" });
      const inputs = {}, errors = {};
      const addQuantity = (key, label, parent, initial = null) => {
        const id = `demand-${key}`;
        const error = ui.el("span", { id: `${id}-error`, class: "field-error" });
        const input = ui.el("input", {
          id, class: "input", type: "text", inputmode: "decimal", maxlength: 20,
          value: initial === null ? "" : ui.formatQty(initial), disabled: !admin,
          "aria-describedby": `${id}-error`, placeholder: "Not configured",
          oninput: () => { dirty = true; clearError(key); updatePreview(); },
        });
        inputs[key] = input; errors[key] = error;
        parent.append(ui.el("div", { class: "field" },
          ui.el("label", { class: "field__label", for: id, text: `${label} (${config.unit})` }), input, error));
        return input;
      };
      const clearError = key => {
        inputs[key].removeAttribute("aria-invalid"); errors[key].textContent = "";
      };
      const readQuantity = key => {
        try { clearError(key); return SM.demandUI.quantity(inputs[key].value); }
        catch (error) {
          inputs[key].setAttribute("aria-invalid", "true"); errors[key].textContent = error.message;
          throw error;
        }
      };
      const enabled = ui.el("input", {
        id: "demand-enabled", type: "checkbox", checked: config.enabled, disabled: !admin,
        onchange: () => { dirty = true; updatePreview(); },
      });
      form.append(ui.el("p", { class: "text-muted", text: admin
        ? "Set estimated maximum daily consumption. Leave an unknown value blank; enter 0 only when no consumption is expected."
        : "Read-only forecast settings. Ask an administrator to make changes." }),
      ui.el("label", { class: "field__label", for: "demand-enabled" }, enabled, " Forecast enabled"));
      const packageText = ingredient.count_package_enabled
        ? `Counting package: 1 ${ingredient.count_package_name} = ${ui.formatQty(ingredient.count_units_per_package)} ${config.unit}.`
        : `Stock unit: ${config.unit}. No counting package configured.`;
      form.append(ui.el("p", { class: "field__hint", text: `${packageText} Supplier purchasing packages are managed separately.` }));
      if (admin) {
        const shortcut = ui.el("div", { class: "demand-shortcut" });
        addQuantity("weekdays", "Monday–Friday shortcut", shortcut);
        shortcut.append(ui.el("button", {
          type: "button", class: "btn btn--secondary", text: "Apply to Monday–Friday",
          onclick: () => {
            try {
              const value = readQuantity("weekdays");
              if (value === null) throw new Error("Enter a quantity to apply to Monday–Friday.");
              SM.demandUI.days.slice(0, 5).forEach(day => {
                inputs[day].value = value; clearError(day);
              });
              dirty = true; updatePreview();
            } catch (error) {
              errors.weekdays.textContent = error.message;
              inputs.weekdays.setAttribute("aria-invalid", "true"); inputs.weekdays.focus();
            }
          },
        }));
        form.append(shortcut);
      }
      const grid = ui.el("div", { class: "form-grid demand-week" });
      SM.demandUI.days.forEach(day => addQuantity(day, day[0].toUpperCase() + day.slice(1), grid, config[day]));
      form.append(grid);
      addQuantity("safety_stock", "Planning safety stock", form, config.safety_stock);
      form.append(ui.el("p", { class: "field__hint", text: "Planning buffer for future forecasts. The inventory warning threshold is edited separately in Ingredient Management." }));
      const notes = ui.el("textarea", {
        id: "demand-notes", class: "textarea demand-notes", rows: 3, maxlength: 1000,
        disabled: !admin, value: config.notes || "",
        oninput: () => {
          dirty = true; notes.style.height = "auto"; notes.style.height = `${notes.scrollHeight}px`;
        },
      });
      // Textareas take their initial content from the value property.
      notes.value = config.notes || "";
      form.append(ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "demand-notes", text: "Estimation notes (optional)" }), notes));
      const preview = ui.el("div", { class: "preview-box", id: "demand-preview", role: "status", "aria-live": "polite" });
      function updatePreview() {
        let values;
        try { values = SM.demandUI.days.map(day => SM.demandUI.quantity(inputs[day].value)); }
        catch (_) { preview.textContent = "Weekly estimated demand: correct the invalid daily quantities."; return; }
        const weekly = SM.demandUI.weekly(values);
        const count = values.filter(value => value !== null).length;
        preview.textContent = weekly === null
          ? `Weekly estimated demand: incomplete — ${count} of 7 days configured.`
          : `Weekly estimated demand: ${weekly} ${config.unit}.`;
        let safety = null;
        try { safety = SM.demandUI.quantity(inputs.safety_stock.value); } catch (_) { /* field validation handles it */ }
        preview.append(ui.el("p", { class: "field__hint", text: !enabled.checked
          ? "Forecast disabled. These settings will be excluded from future forecasts."
          : weekly === null || safety === null
            ? "Incomplete settings: configure all seven days and planning safety stock before forecasting."
            : "All planning inputs configured. Stock projections are not part of this step." }));
      }
      form.append(preview, ui.el("p", { class: "field__hint", text: config.updated_at
        ? `Last updated: ${ui.formatDateTime(config.updated_at)}` : "No demand configuration has been saved." }), feedback);
      if (admin) {
        const save = ui.el("button", { type: "submit", class: "btn", text: "Save Forecast Settings" });
        form.append(save);
        form.addEventListener("submit", async event => {
          event.preventDefault(); if (saving || event.isComposing) return;
          ui.clear(feedback);
          const payload = { expected_version: config.version, enabled: enabled.checked, notes: notes.value.trim() || null };
          let firstInvalid;
          [...SM.demandUI.days, "safety_stock"].forEach(key => {
            try { payload[key] = readQuantity(key); } catch (_) { firstInvalid ||= inputs[key]; }
          });
          if (firstInvalid) { firstInvalid.focus(); return; }
          saving = true; cancel.disabled = true;
          save.style.minWidth = `${save.getBoundingClientRect().width}px`;
          ui.setBusy(save, true, "Saving…");
          try {
            await SM.api.patch(`/ingredients/${ingredient.id}/demand-settings`, payload);
            dirty = false; saving = false; close(); ui.toast("Forecast settings saved.");
          } catch (error) {
            feedback.append(ui.alert(error.message)); feedback.scrollIntoView({ block: "nearest" });
            if (error.code === "demand_settings_changed") feedback.append(ui.el("button", {
              type: "button", class: "btn btn--secondary", text: "Reload latest settings",
              onclick: () => { dirty = false; load(); },
            }));
          } finally { saving = false; cancel.disabled = false; ui.setBusy(save, false); }
        });
      }
      body.append(form); updatePreview();
      if (admin) enabled.focus();
    }
    await load();
  },
};
