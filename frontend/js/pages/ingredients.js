// Ingredient management. Balances are NEVER edited here — the form has no
// stock field except an opening quantity at creation time, which the backend
// records as an opening_balance ledger transaction (decisions D14/D23).

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.ingredients = {
  async render(view) {
    const ui = SM.ui;
    const isAdmin = SM.auth.isAdmin();
    ui.clear(view);

    const state = { search: "", category: "", status: "active", ingredients: [], suppliers: [] };

    const search = ui.el("input", {
      class: "input", type: "search", placeholder: "Search ingredients…",
      "aria-label": "Search ingredients",
      oninput: () => { state.search = search.value.trim().toLowerCase(); refresh(); },
    });
    const categorySelect = ui.el("select", {
      class: "select", "aria-label": "Filter by category",
      onchange: () => { state.category = categorySelect.value; refresh(); },
    });
    const statusSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by status",
      onchange: () => { state.status = statusSelect.value; refresh(); },
    },
      ui.el("option", { value: "active", text: "Active", selected: true }),
      ui.el("option", { value: "archived", text: "Archived" }),
      ui.el("option", { value: "", text: "All statuses" }),
    );

    const listCard = ui.el("div", { class: "card" });
    view.append(
      ui.el("div", { class: "card" },
        ui.el("div", { class: "form-grid" }, search, categorySelect, statusSelect),
        isAdmin ? ui.el("button", {
          class: "btn", text: "Add Ingredient",
          onclick: () => this.openForm(null, state.suppliers, reload),
        }) : ui.el("p", { class: "text-muted text-small", text: "Read-only access. Contact an administrator for changes." })),
      listCard,
    );

    const refresh = () => {
      const filtered = state.ingredients.filter((item) => {
        if (state.status && item.status !== state.status) return false;
        if (state.category && item.category !== state.category) return false;
        if (state.search && !item.name.toLowerCase().includes(state.search)) return false;
        return true;
      });
      this.renderList(listCard, filtered, state.suppliers, isAdmin, reload);
    };

    const reload = async () => {
      ui.clear(listCard).append(ui.loading());
      try {
        state.ingredients = await SM.api.get("/ingredients");
        const categories = [...new Set(state.ingredients.map((i) => i.category))].sort();
        const selected = categorySelect.value;
        SM.ui.clear(categorySelect).append(
          ui.el("option", { value: "", text: "All categories" }),
          categories.map((c) => ui.el("option", { value: c, text: c, selected: c === selected })),
        );
        refresh();
      } catch (error) {
        ui.clear(listCard).append(ui.alert(error.message));
      }
    };

    state.suppliers = await SM.api.get("/suppliers");
    await reload();
  },

  renderList(card, ingredients, suppliers, isAdmin, reload) {
    const ui = SM.ui;
    ui.clear(card);
    if (ingredients.length === 0) {
      card.append(ui.empty("No ingredients match the current filters."));
      return;
    }
    const supplierById = Object.fromEntries(suppliers.map((s) => [s.id, s]));
    const table = ui.el("table", { class: "table table--responsive" },
      ui.el("thead", {}, ui.el("tr", {},
        ...["Ingredient", "Category", "Unit", "Safety", "Critical", "Primary Supplier", "Status", "Actions"]
          .map((h) => ui.el("th", { text: h })))),
    );
    const tbody = ui.el("tbody");
    for (const item of ingredients) {
      const actions = ui.el("div", { class: "button-row" });
      if (isAdmin) {
        actions.append(
          ui.el("button", {
            class: "btn btn--secondary btn--small", text: "Edit",
            onclick: () => this.openForm(item, suppliers, reload),
          }),
          ui.el("button", {
            class: "btn btn--secondary btn--small",
            text: item.status === "active" ? "Archive" : "Restore",
            onclick: () => this.toggleStatus(item, reload),
          }),
        );
      }
      tbody.append(ui.el("tr", {},
        ui.el("td", { "data-label": "Ingredient" }, ui.el("strong", { text: item.name })),
        ui.el("td", { "data-label": "Category", text: item.category }),
        ui.el("td", { "data-label": "Unit", text: item.unit }),
        ui.el("td", { "data-label": "Safety", text: ui.formatQty(item.min_safety_stock) }),
        ui.el("td", { "data-label": "Critical", text: ui.formatQty(item.critical_threshold) }),
        ui.el("td", {
          "data-label": "Primary Supplier",
          text: supplierById[item.primary_supplier_id]?.name || "—",
        }),
        ui.el("td", { "data-label": "Status" },
          ui.badge(item.status, item.status === "active" ? "safe" : "neutral")),
        ui.el("td", { "data-label": "Actions" }, actions),
      ));
    }
    table.append(tbody);
    card.append(ui.el("div", { class: "table-wrap" }, table));
  },

  async openForm(ingredient, suppliers, reload) {
    const ui = SM.ui;
    const editing = Boolean(ingredient);
    let units;
    try {
      units = await SM.api.get("/units");
    } catch (error) {
      ui.toast(error.message, "error");
      return;
    }
    const activeSuppliers = suppliers.filter((s) => s.status === "active");
    const errorArea = ui.el("div");
    const fields = {};

    const form = ui.el("form");
    const addField = (key, label, node, hint) => {
      fields[key] = node;
      form.append(ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: `ingredient-${key}`, text: label }),
        node,
        hint ? ui.el("span", { class: "field__hint", text: hint }) : null));
    };

    addField("name", "Ingredient Name", ui.el("input", {
      class: "input", id: "ingredient-name", type: "text", required: !editing,
      maxlength: 120, value: ingredient?.name || "",
    }));
    addField("category", "Category", ui.el("input", {
      class: "input", id: "ingredient-category", type: "text", required: !editing,
      maxlength: 80, value: ingredient?.category || "", placeholder: "e.g. Noodles, Soup, Toppings",
    }));
    addField("unit_id", "Measurement Unit", ui.el("select", {
      class: "select", id: "ingredient-unit_id",
    }, units.map((u) => ui.el("option", {
      value: u.id, text: `${u.name} (${u.abbreviation})`,
      selected: ingredient?.unit_id === u.id,
    }))));

    const decimalInput = (key, value) => ui.el("input", {
      class: "input", id: `ingredient-${key}`, type: "number",
      min: "0", step: "0.001", inputmode: "decimal", value: value ?? "0",
    });
    addField("min_safety_stock", "Safety Stock Threshold",
      decimalInput("min_safety_stock", ingredient ? ui.formatQty(ingredient.min_safety_stock) : "0"));
    addField("critical_threshold", "Critical Stock Threshold",
      decimalInput("critical_threshold", ingredient ? ui.formatQty(ingredient.critical_threshold) : "0"));
    addField("usage_per_serving", "Usage per Ramen Serving",
      decimalInput("usage_per_serving", ingredient ? ui.formatQty(ingredient.usage_per_serving) : "1"),
      "How much stock one serving consumes (in the stock unit).");

    addField("primary_supplier_id", "Primary Supplier", ui.el("select", {
      class: "select", id: "ingredient-primary_supplier_id",
    },
      ui.el("option", { value: "", text: "— None —" }),
      activeSuppliers.map((s) => ui.el("option", {
        value: s.id, text: s.name, selected: ingredient?.primary_supplier_id === s.id,
      })),
    ));

    const alternativeBoxes = activeSuppliers.map((s) => {
      const checked = ingredient?.supplier_ids.includes(s.id)
        && ingredient.primary_supplier_id !== s.id;
      const box = ui.el("input", { type: "checkbox", value: s.id, checked });
      return { supplierId: s.id, box, label: ui.el("label", {}, box, ` ${s.name}`) };
    });
    form.append(ui.el("div", { class: "field" },
      ui.el("span", { class: "field__label", text: "Alternative Suppliers" }),
      ui.el("div", { class: "checkbox-list" },
        alternativeBoxes.length
          ? alternativeBoxes.map((a) => a.label)
          : ui.el("span", { class: "text-muted", text: "No active suppliers available." }))));

    if (!editing) {
      addField("opening_quantity", "Opening Stock Quantity (optional)",
        decimalInput("opening_quantity", ""),
        "Recorded as an opening-balance ledger transaction. Leave empty to start at zero.");
    }

    form.append(errorArea);
    const saveButton = ui.el("button", { class: "btn", type: "submit", text: "Save Ingredient" });
    form.append(ui.el("div", { class: "button-row" }, saveButton));

    const close = ui.modal({ title: editing ? `Edit ${ingredient.name}` : "Add Ingredient", body: form, wide: true });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      ui.clear(errorArea);
      ui.setBusy(saveButton, true, "Saving…");
      try {
        const payload = {};
        const text = (key) => fields[key].value.trim();
        if (!editing || text("name")) payload.name = text("name");
        if (!editing || text("category")) payload.category = text("category");
        if (!editing) {
          payload.name = text("name");
          payload.category = text("category");
          if (!payload.name || !payload.category) {
            throw new SM.ApiError(0, "validation", "Name and category are required.");
          }
          payload.unit_id = Number(fields.unit_id.value);
          payload.usage_per_serving = fields.usage_per_serving.value || "1";
        } else {
          payload.unit_id = Number(fields.unit_id.value);
          if (text("usage_per_serving") !== "") payload.usage_per_serving = text("usage_per_serving");
        }
        payload.min_safety_stock = text("min_safety_stock") || "0";
        payload.critical_threshold = text("critical_threshold") || "0";

        const primary = fields.primary_supplier_id.value;
        payload.primary_supplier_id = primary === "" ? null : Number(primary);
        payload.alternative_supplier_ids = alternativeBoxes
          .filter((a) => a.box.checked && String(a.supplierId) !== primary)
          .map((a) => a.supplierId);

        if (!editing && text("opening_quantity") !== "") {
          payload.opening_quantity = text("opening_quantity");
        }
        // The backend treats primary_supplier_id=null as "no change" on
        // update, so only include it when a supplier is actually selected.
        if (editing && payload.primary_supplier_id === null) {
          delete payload.primary_supplier_id;
        }

        if (editing) {
          await SM.api.patch(`/ingredients/${ingredient.id}`, payload);
        } else {
          await SM.api.post("/ingredients", payload);
        }
        close();
        ui.toast(editing ? "Ingredient updated." : "Ingredient created.");
        reload();
      } catch (error) {
        errorArea.append(ui.alert(error.message));
      } finally {
        ui.setBusy(saveButton, false);
      }
    });
  },

  async toggleStatus(ingredient, reload) {
    const ui = SM.ui;
    const archiving = ingredient.status === "active";
    const approved = await ui.confirmDialog({
      title: archiving ? "Archive Ingredient" : "Restore Ingredient",
      message: archiving
        ? `Archive ${ingredient.name}? Stock entries will be blocked and it disappears from daily workflows. Its full history is preserved.`
        : `Restore ${ingredient.name}? It will become available for stock entries again.`,
      confirmLabel: archiving ? "Archive" : "Restore",
      danger: archiving,
    });
    if (!approved) return;
    try {
      await SM.api.post(`/ingredients/${ingredient.id}/${archiving ? "archive" : "restore"}`);
      ui.toast(archiving ? "Ingredient archived." : "Ingredient restored.");
      reload();
    } catch (error) {
      ui.toast(error.message, "error");
    }
  },
};
