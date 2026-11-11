// Purchase orders (Phase 2 Step 2). Staff and admins create draft orders and
// advance them through the status flow; cancellation is admin-only. Physical
// stock never changes here — that happens only on delivery confirmation
// (Phase 2 Step 3). Pending quantities shown are computed server-side.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.orders = {
  STATUS_LABELS: {
    draft: "Draft",
    ordered: "Ordered",
    confirmed: "Confirmed",
    partially_delivered: "Partially Delivered",
    delivered: "Delivered",
    delayed: "Delayed",
    cancelled: "Cancelled",
  },
  STATUS_KINDS: {
    draft: "neutral",
    ordered: "info",
    confirmed: "safe",
    partially_delivered: "warning",
    delivered: "safe",
    delayed: "warning",
    cancelled: "neutral",
  },

  statusBadge(status) {
    return SM.ui.badge(this.STATUS_LABELS[status] || status, this.STATUS_KINDS[status] || "neutral");
  },

  async render(view) {
    const ui = SM.ui;
    const isAdmin = SM.auth.isAdmin();
    ui.clear(view);

    const state = { status: "" };
    const statusSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by status",
      onchange: () => { state.status = statusSelect.value; load(); },
    },
      ui.el("option", { value: "", text: "All statuses" }),
      ui.el("option", { value: "ordered,confirmed,delayed,partially_delivered", text: "Open (awaiting delivery)" }),
      ...["draft", "ordered", "confirmed", "delayed", "partially_delivered", "delivered", "cancelled"]
        .map((s) => ui.el("option", { value: s, text: this.STATUS_LABELS[s] })),
    );

    const listCard = ui.el("div", { class: "card" });
    view.append(
      ui.el("div", { class: "card" },
        ui.el("div", { class: "form-grid" }, statusSelect),
        ui.el("button", {
          class: "btn", text: "New Order",
          onclick: () => this.openForm(null, load),
        }),
        ui.el("p", { class: "text-muted text-small" },
          "Ordered quantities appear as Incoming stock. Available stock changes only when a delivery is confirmed.")),
      listCard,
    );

    const load = async () => {
      ui.clear(listCard).append(ui.loading());
      try {
        const query = state.status ? "?status=" + encodeURIComponent(state.status) : "";
        const orders = await SM.api.get("/purchase-orders" + query);
        this.renderList(listCard, orders, isAdmin, load);
      } catch (error) {
        ui.clear(listCard).append(ui.alert(error.message));
      }
    };
    await load();
  },

  renderList(card, orders, isAdmin, reload) {
    const ui = SM.ui;
    ui.clear(card);
    if (orders.length === 0) {
      card.append(ui.empty("No purchase orders found."));
      return;
    }
    const table = ui.el("table", { class: "table table--responsive" },
      ui.el("thead", {}, ui.el("tr", {},
        ...["Order", "Supplier", "Expected Delivery", "Items", "Status", "Created By", "Actions"]
          .map((h) => ui.el("th", { text: h })))),
    );
    const tbody = ui.el("tbody");
    for (const order of orders) {
      const itemCount = order.items.length;
      tbody.append(ui.el("tr", {},
        ui.el("td", { "data-label": "Order" }, ui.el("strong", { text: `#${order.id}` })),
        ui.el("td", { "data-label": "Supplier", text: order.supplier_name || "—" }),
        ui.el("td", { "data-label": "Expected Delivery", text: ui.formatDateOnly(order.expected_delivery_date) }),
        ui.el("td", { "data-label": "Items", text: `${itemCount} item${itemCount === 1 ? "" : "s"}` }),
        ui.el("td", { "data-label": "Status" }, this.statusBadge(order.status)),
        ui.el("td", { "data-label": "Created By", text: order.created_by_name || "—" }),
        ui.el("td", { "data-label": "Actions" },
          ui.el("button", {
            class: "btn btn--secondary btn--small", text: "View",
            onclick: () => this.openDetail(order.id, isAdmin, reload),
          })),
      ));
    }
    table.append(tbody);
    card.append(ui.el("div", { class: "table-wrap" }, table));
  },

  async openDetail(orderId, isAdmin, reload) {
    const ui = SM.ui;
    let order;
    try {
      order = await SM.api.get(`/purchase-orders/${orderId}`);
    } catch (error) {
      ui.toast(error.message, "error");
      return;
    }
    const body = ui.el("div");
    const errorArea = ui.el("div");
    body.append(
      ui.el("p", {},
        ui.el("strong", { text: order.supplier_name || "" }), " ",
        this.statusBadge(order.status)),
      ui.el("p", { class: "text-small", text: `Expected delivery: ${ui.formatDateOnly(order.expected_delivery_date)}` }),
      ui.el("p", { class: "text-small", text: order.ordered_by_name
        ? `Marked ordered by ${order.ordered_by_name} at ${ui.formatDateTime(order.ordered_at)}`
        : "Not yet marked as ordered." }),
      order.notes ? ui.el("p", { class: "text-small text-muted", text: order.notes }) : null,
    );
    const table = ui.el("table", { class: "table table--responsive" },
      ui.el("thead", {}, ui.el("tr", {},
        ...["Ingredient", "Ordered", "Received", "Pending"].map((h) => ui.el("th", { text: h })))),
    );
    const tbody = ui.el("tbody");
    for (const item of order.items) {
      tbody.append(ui.el("tr", {},
        ui.el("td", { "data-label": "Ingredient", text: item.ingredient_name || `#${item.ingredient_id}` }),
        ui.el("td", { "data-label": "Ordered", text: ui.formatQty(item.quantity_ordered) }),
        ui.el("td", { "data-label": "Received", text: ui.formatQty(item.quantity_received) }),
        ui.el("td", { "data-label": "Pending", text: ui.formatQty(item.pending_quantity) }),
      ));
    }
    table.append(tbody);
    body.append(ui.el("div", { class: "table-wrap" }, table), errorArea);

    const actions = [];
    const close = ui.modal({
      title: `Purchase Order #${order.id}`, body, actions, wide: true,
    });
    const act = (path, label, danger = false) => {
      actions.push(ui.el("button", {
        class: danger ? "btn btn--danger" : "btn btn--secondary", text: label,
        onclick: async () => {
          ui.clear(errorArea);
          try {
            await SM.api.post(`/purchase-orders/${order.id}/${path}`);
            close();
            ui.toast(`Order #${order.id} updated.`);
            reload();
          } catch (error) {
            errorArea.append(ui.alert(error.message));
          }
        },
      }));
    };
    if (order.status === "draft") {
      actions.push(ui.el("button", {
        class: "btn", text: "Edit",
        onclick: () => { close(); this.openForm(order, reload); },
      }));
      act("mark-ordered", "Mark Ordered");
    } else if (order.status === "ordered") {
      act("confirm", "Confirm Order");
      act("delay", "Mark Delayed");
    } else if (order.status === "confirmed" || order.status === "partially_delivered") {
      act("delay", "Mark Delayed");
    } else if (order.status === "delayed") {
      act("confirm", "Confirm Order");
    }
    if (isAdmin && ["draft", "ordered", "confirmed", "delayed"].includes(order.status)) {
      actions.push(ui.el("button", {
        class: "btn btn--danger", text: "Cancel Order",
        onclick: async () => {
          const approved = await ui.confirmDialog({
            title: "Cancel Order",
            message: `Cancel purchase order #${order.id}? Its quantities will no longer count as incoming stock.`,
            confirmLabel: "Cancel Order",
            danger: true,
          });
          if (!approved) return;
          ui.clear(errorArea);
          try {
            await SM.api.post(`/purchase-orders/${order.id}/cancel`);
            close();
            ui.toast(`Order #${order.id} cancelled.`);
            reload();
          } catch (error) {
            errorArea.append(ui.alert(error.message));
          }
        },
      }));
    }
    // Actions were pushed after the modal rendered; re-render the action row.
    if (actions.length) {
      body.append(ui.el("div", { class: "button-row" }, actions));
    }
  },

  async openForm(existing, reload) {
    const ui = SM.ui;
    const editing = Boolean(existing);
    let suppliers;
    try {
      suppliers = (await SM.api.get("/suppliers?status=active"));
    } catch (error) {
      ui.toast(error.message, "error");
      return;
    }
    if (!suppliers.length) {
      ui.toast("No active suppliers available.", "error");
      return;
    }
    // Preload each supplier's linked ingredients (order items must be linked).
    const ingredientMap = {};
    try {
      await Promise.all(suppliers.map(async (supplier) => {
        const detail = await SM.api.get(`/suppliers/${supplier.id}`);
        ingredientMap[supplier.id] = (detail.ingredients || [])
          .filter((link) => link.ingredient_status !== "archived")
          .map((link) => ({ id: link.ingredient_id, name: link.ingredient_name }));
      }));
    } catch (error) {
      ui.toast(error.message, "error");
      return;
    }

    let currentSupplierId = editing ? existing.supplier_id : suppliers[0].id;
    const errorArea = ui.el("div");
    const itemsWrap = ui.el("div");
    let itemRows = [];

    const supplierSelect = ui.el("select", {
      class: "select", id: "order-supplier",
      onchange: () => {
        currentSupplierId = Number(supplierSelect.value);
        for (const row of itemRows) fillIngredientSelect(row.select);
      },
    },
      suppliers.map((s) => ui.el("option", {
        value: String(s.id), text: s.name, selected: s.id === currentSupplierId,
      })));
    const dateInput = ui.el("input", {
      class: "input", id: "order-date", type: "date",
      value: editing ? existing.expected_delivery_date : "",
    });
    const notesInput = ui.el("input", {
      class: "input", id: "order-notes", type: "text", maxlength: "500",
      value: (editing && existing.notes) || "", placeholder: "Optional notes",
    });

    const fillIngredientSelect = (select, keepValue) => {
      const previous = keepValue ? select.value : "";
      ui.clear(select);
      for (const ing of ingredientMap[currentSupplierId] || []) {
        select.append(ui.el("option", { value: String(ing.id), text: ing.name }));
      }
      if (previous) select.value = previous;
    };

    const addItemRow = (ingredientId, quantity) => {
      const select = ui.el("select", { class: "select", "aria-label": "Ingredient" });
      fillIngredientSelect(select, false);
      if (ingredientId) select.value = String(ingredientId);
      const qtyInput = ui.el("input", {
        class: "input", type: "number", min: "0.001", step: "0.001",
        value: quantity || "", placeholder: "Quantity", "aria-label": "Quantity",
      });
      const entry = { select, qtyInput };
      const removeBtn = ui.el("button", {
        class: "btn btn--secondary btn--small", text: "Remove", type: "button",
        onclick: () => {
          itemRows = itemRows.filter((row) => row !== entry);
          rowEl.remove();
        },
      });
      const rowEl = ui.el("div", { class: "form-grid" }, select, qtyInput, removeBtn);
      itemRows.push(entry);
      itemsWrap.append(rowEl);
    };

    const saveButton = ui.el("button", {
      class: "btn", type: "submit", text: editing ? "Save Changes" : "Create Order",
    });
    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        const items = [];
        const seen = new Set();
        let invalid = null;
        for (const row of itemRows) {
          const ingredientId = Number(row.select.value);
          const qty = Number(row.qtyInput.value);
          if (!ingredientId) invalid = invalid || "Every line needs an ingredient.";
          if (!(qty > 0)) invalid = invalid || "Every line needs a positive quantity.";
          if (seen.has(ingredientId)) invalid = invalid || "Duplicate ingredient lines are not allowed.";
          seen.add(ingredientId);
          items.push({ ingredient_id: ingredientId, quantity_ordered: qty });
        }
        if (!itemRows.length) invalid = "Add at least one item.";
        if (!dateInput.value) invalid = invalid || "An expected delivery date is required.";
        if (invalid) {
          errorArea.append(ui.alert(invalid));
          return;
        }
        ui.setBusy(saveButton, true, "Saving…");
        try {
          if (editing) {
            await SM.api.patch(`/purchase-orders/${existing.id}`, {
              expected_delivery_date: dateInput.value,
              notes: notesInput.value.trim() || null,
              items,
            });
          } else {
            await SM.api.post("/purchase-orders", {
              supplier_id: currentSupplierId,
              expected_delivery_date: dateInput.value,
              notes: notesInput.value.trim() || null,
              items,
            });
          }
          close();
          ui.toast(editing ? "Order updated." : "Draft order created.");
          reload();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
        } finally {
          ui.setBusy(saveButton, false);
        }
      },
    });

    form.append(
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "order-supplier", text: "Supplier" }),
        supplierSelect),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "order-date", text: "Expected Delivery Date" }),
        dateInput),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "order-notes", text: "Notes" }),
        notesInput),
      ui.el("p", { class: "text-muted text-small" },
        "Items are limited to ingredients linked to the selected supplier."),
      itemsWrap,
      ui.el("button", {
        class: "btn btn--secondary btn--small", type: "button", text: "Add Item",
        onclick: () => addItemRow(null, ""),
      }),
      errorArea,
      ui.el("div", { class: "button-row" }, saveButton),
    );

    const close = ui.modal({
      title: editing ? `Edit Purchase Order #${existing.id}` : "New Purchase Order",
      body: form, wide: true,
    });
    if (editing) {
      for (const item of existing.items) addItemRow(item.ingredient_id, ui.formatQty(item.quantity_ordered));
    } else {
      addItemRow(null, "");
    }
  },
};
