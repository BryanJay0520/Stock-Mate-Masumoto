// Dashboard: live inventory overview built only from data the backend
// actually provides — no invented analytics or forecasts.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.dashboard = {
  async render(view) {
    const ui = SM.ui;
    const forecastTask = SM.api.get("/forecast/calendar?days=7")
      .then(data => ({data}), error => ({error}));
    const { rows, ingredients, suppliers } = await SM.data.catalog();
    const transactions = await SM.api.get("/inventory/transactions?limit=8");
    const deliveries = await SM.api.get(
      "/purchase-orders?status=ordered,confirmed,delayed,partially_delivered&limit=10");

    const activeRows = rows.filter((r) => r.status === "active");
    const attention = activeRows.filter((r) => {
      const kind = SM.data.stockStatus(r).kind;
      return kind === "warning" || kind === "critical";
    });
    const critical = attention.filter((r) => SM.data.stockStatus(r).kind === "critical");

    ui.clear(view);

    // Stat cards
    const stats = ui.el("div", { class: "stat-grid" },
      this.statCard("Active Ingredients", ingredients.filter((i) => i.status === "active").length),
      this.statCard("Active Suppliers", suppliers.filter((s) => s.status === "active").length),
      this.statCard("Below Safety Stock", attention.length, "warning"),
      this.statCard("At Critical Stock", critical.length, "critical"),
    );
    view.append(stats);

    view.append(ui.el("div", { class: "button-row", style: "margin-bottom: 24px" },
      ui.el("a", { class: "btn", href: "#/entry", text: "Record Stock" }),
      ui.el("a", { class: "btn btn--secondary", href: "#/inventory", text: "View Inventory" }),
    ));

    const forecast = await forecastTask;
    view.append(forecast.data ? SM.forecastUI.summaryCard(forecast.data) : ui.el("div", {class: "card"},
      ui.el("h2", {class: "card__title", text: "Forecast outlook"}), ui.alert(forecast.error.message),
      ui.el("a", {href: "#/forecast", text: "Open Forecast Calendar to retry →"})));

    // Upcoming deliveries — ordered but not yet received (Phase 2 Step 2).
    const deliveriesCard = ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Upcoming Deliveries" }));
    if (deliveries.length === 0) {
      deliveriesCard.append(ui.empty("No pending deliveries."));
    } else {
      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ui.el("th", { text: "Supplier" }),
          ui.el("th", { text: "Order" }),
          ui.el("th", { text: "Expected" }),
          ui.el("th", { text: "Items" }),
          ui.el("th", { text: "Status" }),
        )),
      );
      const tbody = ui.el("tbody");
      for (const order of deliveries) {
        tbody.append(ui.el("tr", {},
          ui.el("td", { "data-label": "Supplier", text: order.supplier_name || "—" }),
          ui.el("td", { "data-label": "Order", text: `#${order.id}` }),
          ui.el("td", { "data-label": "Expected", text: ui.formatDateOnly(order.expected_delivery_date) }),
          ui.el("td", { "data-label": "Items", text: `${order.items.length} item${order.items.length === 1 ? "" : "s"}` }),
          ui.el("td", { "data-label": "Status" }, SM.pages.orders.statusBadge(order.status)),
        ));
      }
      table.append(tbody);
      deliveriesCard.append(ui.el("div", { class: "table-wrap" }, table));
      deliveriesCard.append(ui.el("p", {},
        ui.el("a", { href: "#/orders", text: "View all purchase orders →" })));
    }
    view.append(deliveriesCard);

    // Items needing attention
    const attentionCard = ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Stock Warnings" }));
    if (attention.length === 0) {
      attentionCard.append(ui.empty("All active ingredients are above their safety stock levels."));
    } else {
      attention.sort((a, b) => {
        const order = { critical: 0, warning: 1 };
        return order[SM.data.stockStatus(a).kind] - order[SM.data.stockStatus(b).kind];
      });
      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ui.el("th", { text: "Ingredient" }),
          ui.el("th", { text: "Available" }),
          ui.el("th", { text: "Safety Stock" }),
          ui.el("th", { text: "Status" }),
          ui.el("th", { text: "Confidence" }),
        )),
      );
      const tbody = ui.el("tbody");
      for (const item of attention) {
        tbody.append(ui.el("tr", {},
          ui.el("td", { "data-label": "Ingredient", text: item.ingredient_name }),
          ui.el("td", { "data-label": "Available", text: `${ui.formatQty(item.current_quantity)} ${item.unit}` }),
          ui.el("td", { "data-label": "Safety Stock", text: ui.formatQty(item.ingredient.min_safety_stock) }),
          ui.el("td", { "data-label": "Status" }, SM.data.stockStatusBadge(item)),
          ui.el("td", { "data-label": "Confidence" }, SM.data.confidenceBadge(item)),
        ));
      }
      table.append(tbody);
      attentionCard.append(ui.el("div", { class: "table-wrap" }, table));
    }
    view.append(attentionCard);

    // Recent activity
    const recentCard = ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Recent Inventory Transactions" }));
    if (transactions.length === 0) {
      recentCard.append(ui.empty("No inventory transactions recorded yet."));
    } else {
      const table = ui.el("table", { class: "table table--responsive" },
        ui.el("thead", {}, ui.el("tr", {},
          ui.el("th", { text: "Time" }),
          ui.el("th", { text: "Ingredient" }),
          ui.el("th", { text: "Type" }),
          ui.el("th", { text: "Change" }),
          ui.el("th", { text: "Balance" }),
        )),
      );
      const tbody = ui.el("tbody");
      for (const tx of transactions) {
        tbody.append(ui.el("tr", {},
          ui.el("td", { "data-label": "Time", text: ui.formatDateTime(tx.created_at) }),
          ui.el("td", {
            "data-label": "Ingredient",
            text: rows.find((r) => r.ingredient_id === tx.ingredient_id)?.ingredient_name || `#${tx.ingredient_id}`,
          }),
          ui.el("td", { "data-label": "Type", text: tx.type.replaceAll("_", " ") }),
          ui.el("td", { "data-label": "Change" }, ui.delta(tx.quantity_delta)),
          ui.el("td", { "data-label": "Balance", text: ui.formatQty(tx.resulting_quantity) }),
        ));
      }
      table.append(tbody);
      recentCard.append(ui.el("div", { class: "table-wrap" }, table));
      recentCard.append(ui.el("p", {},
        ui.el("a", { href: "#/history", text: "View full transaction history →" })));
    }
    view.append(recentCard);
  },

  statCard(label, value, kind) {
    return SM.ui.el("div", { class: `stat-card${kind ? " stat-card--" + kind : ""}` },
      SM.ui.el("div", { class: "stat-card__label", text: label }),
      SM.ui.el("div", { class: "stat-card__value", text: String(value) }),
    );
  },

};
