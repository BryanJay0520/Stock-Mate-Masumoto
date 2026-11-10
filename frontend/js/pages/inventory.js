// Inventory: searchable live stock table. Stock availability (safe/low/
// critical) and data confidence are shown as separate indicators.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.inventory = {
  async render(view) {
    const ui = SM.ui;
    const { rows } = await SM.data.catalog();
    ui.clear(view);

    const state = { search: "", category: "", status: "active" };

    const search = ui.el("input", {
      class: "input", type: "search", placeholder: "Search ingredients…",
      "aria-label": "Search ingredients",
      oninput: () => { state.search = search.value.trim().toLowerCase(); refresh(); },
    });
    const categories = [...new Set(rows.map((r) => r.category))].sort();
    const categorySelect = ui.el("select", {
      class: "select", "aria-label": "Filter by category",
      onchange: () => { state.category = categorySelect.value; refresh(); },
    },
      ui.el("option", { value: "", text: "All categories" }),
      categories.map((c) => ui.el("option", { value: c, text: c })),
    );
    const statusSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by status",
      onchange: () => { state.status = statusSelect.value; refresh(); },
    },
      ui.el("option", { value: "active", text: "Active", selected: true }),
      ui.el("option", { value: "archived", text: "Archived" }),
      ui.el("option", { value: "", text: "All statuses" }),
    );

    const tableCard = ui.el("div", { class: "card" });
    view.append(
      ui.el("div", { class: "card" },
        ui.el("div", { class: "form-grid" }, search, categorySelect, statusSelect)),
      tableCard,
    );

    const refresh = () => {
      const filtered = rows.filter((r) => {
        if (state.status && r.status !== state.status) return false;
        if (state.category && r.category !== state.category) return false;
        if (state.search && !r.ingredient_name.toLowerCase().includes(state.search)) return false;
        return true;
      });

      ui.clear(tableCard);
      if (filtered.length === 0) {
        tableCard.append(ui.empty("No ingredients match the current filters."));
        return;
      }

      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ...["Ingredient", "Category", "Available", "Safety Stock", "Status",
            "Confidence", "Primary Supplier", "Last Counted"].map((label) => ui.el("th", { text: label })),
        )),
      );
      const tbody = ui.el("tbody");
      for (const item of filtered) {
        const confidence = SM.data.confidenceInfo(item);
        tbody.append(ui.el("tr", {},
          ui.el("td", { "data-label": "Ingredient" },
            ui.el("strong", { text: item.ingredient_name }),
            item.status === "archived" ? [" ", ui.badge("Archived", "neutral")] : null),
          ui.el("td", { "data-label": "Category", text: item.category }),
          ui.el("td", { "data-label": "Available" },
            ui.el("strong", { text: ui.formatQty(item.current_quantity) }), ` ${item.unit}`),
          ui.el("td", {
            "data-label": "Safety Stock",
            text: item.ingredient ? ui.formatQty(item.ingredient.min_safety_stock) : "—",
          }),
          ui.el("td", { "data-label": "Status" }, SM.data.stockStatusBadge(item)),
          ui.el("td", { "data-label": "Confidence" },
            ui.badge(confidence.label, confidence.kind, confidence.detail)),
          ui.el("td", { "data-label": "Primary Supplier", text: item.supplier?.name || "—" }),
          ui.el("td", {
            "data-label": "Last Counted",
            text: item.last_counted_at ? ui.formatDateTime(item.last_counted_at) : "Never",
          }),
        ));
      }
      table.append(tbody);
      tableCard.append(ui.el("div", { class: "table-wrap" }, table));
      tableCard.append(ui.el("p", { class: "text-muted text-small" },
        "Confidence describes how fresh the physical stock count is — it is separate from stock availability."));
    };
    refresh();
  },
};
