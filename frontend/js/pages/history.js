// Transaction history: the immutable ledger, read-only except for the
// correction workflow. Corrections APPEND new ledger rows via the backend;
// nothing here ever edits or deletes a historical transaction (D2/D27).

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.history = {
  TYPES: [
    "opening_balance", "consumption", "reconciliation", "correction",
    "delivery_receipt", "adjustment",
  ],

  async render(view) {
    const ui = SM.ui;
    ui.clear(view);

    const state = { ingredientId: "", type: "", transactions: [], names: {} };

    const ingredients = await SM.api.get("/ingredients");
    state.names = Object.fromEntries(ingredients.map((i) => [i.id, i.name]));

    // Staff names: only admins may list users; others see "You" / "User #id".
    this.userNames = {};
    if (SM.auth.isAdmin()) {
      try {
        const users = (await SM.api.get("/admin/users")).users;
        this.userNames = Object.fromEntries(users.map((u) => [u.id, u.username]));
      } catch { /* fall back to generic labels */ }
    }

    const ingredientSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by ingredient",
      onchange: () => { state.ingredientId = ingredientSelect.value; load(); },
    },
      ui.el("option", { value: "", text: "All ingredients" }),
      ingredients.map((i) => ui.el("option", { value: i.id, text: i.name })),
    );
    const typeSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by transaction type",
      onchange: () => { state.type = typeSelect.value; load(); },
    },
      ui.el("option", { value: "", text: "All types" }),
      this.TYPES.map((t) => ui.el("option", { value: t, text: t.replaceAll("_", " ") })),
    );

    const listCard = ui.el("div", { class: "card" });
    view.append(
      ui.el("div", { class: "card" }, ui.el("div", { class: "form-grid" }, ingredientSelect, typeSelect)),
      listCard,
    );

    const load = async () => {
      ui.clear(listCard).append(ui.loading());
      try {
        const params = new URLSearchParams({ limit: "200" });
        if (state.ingredientId) params.set("ingredient_id", state.ingredientId);
        if (state.type) params.set("type", state.type);
        state.transactions = await SM.api.get("/inventory/transactions?" + params.toString());
        this.renderList(listCard, state);
      } catch (error) {
        ui.clear(listCard).append(ui.alert(error.message));
      }
    };
    await load();
  },

  actorLabel(userId) {
    if (SM.auth.user && userId === SM.auth.user.id) return `${SM.auth.user.username} (you)`;
    return this.userNames[userId] || `User #${userId}`;
  },

  renderList(card, state) {
    const ui = SM.ui;
    ui.clear(card);
    if (state.transactions.length === 0) {
      card.append(ui.empty("No transactions match the current filters."));
      return;
    }
    const supersededIds = new Set(
      state.transactions.filter((t) => t.supersedes_id !== null).map((t) => t.supersedes_id),
    );
    const table = ui.el("table", { class: "table table--responsive" },
      ui.el("thead", {}, ui.el("tr", {},
        ...["Time", "Ingredient", "Type", "Change", "Previous", "Resulting", "Reason", "Staff", ""]
          .map((h) => ui.el("th", { text: h })))),
    );
    const tbody = ui.el("tbody");
    for (const tx of state.transactions) {
      const canCorrect = !supersededIds.has(tx.id)
        && (tx.mode === "used" || tx.mode === "remaining")
        && (SM.auth.isAdmin() || tx.entered_by === SM.auth.user.id);
      const actions = [];
      if (supersededIds.has(tx.id)) actions.push(ui.badge("Corrected", "neutral"));
      if (canCorrect) {
        actions.push(ui.el("button", {
          class: "btn btn--secondary btn--small", text: "Correct",
          onclick: () => this.openCorrection(tx, state),
        }));
      }
      tbody.append(ui.el("tr", {},
        ui.el("td", { "data-label": "Time", text: ui.formatDateTime(tx.created_at) }),
        ui.el("td", { "data-label": "Ingredient", text: state.names[tx.ingredient_id] || `#${tx.ingredient_id}` }),
        ui.el("td", { "data-label": "Type" },
          tx.type.replaceAll("_", " "),
          tx.mode ? ui.el("span", { class: "text-muted text-small", text: ` (${tx.mode})` }) : null),
        ui.el("td", { "data-label": "Change" }, ui.delta(tx.quantity_delta)),
        ui.el("td", { "data-label": "Previous", text: ui.formatQty(tx.previous_quantity) }),
        ui.el("td", { "data-label": "Resulting", text: ui.formatQty(tx.resulting_quantity) }),
        ui.el("td", { "data-label": "Reason", text: tx.reason || "—" }),
        ui.el("td", { "data-label": "Staff", text: this.actorLabel(tx.entered_by) }),
        ui.el("td", { "data-label": "" }, actions),
      ));
    }
    table.append(tbody);
    card.append(ui.el("div", { class: "table-wrap" }, table));
    card.append(ui.el("p", { class: "text-muted text-small" },
      "History is immutable. Corrections add a new correcting transaction — the original record is never changed or deleted."));
  },

  openCorrection(tx, state) {
    const ui = SM.ui;
    const quantityLabel = tx.mode === "used"
      ? "Correct USED quantity (the amount that should have been consumed)"
      : "Correct PHYSICAL COUNT (the quantity that should have been counted)";
    const quantityInput = ui.el("input", {
      class: "input", type: "number", min: "0", step: "0.001", inputmode: "decimal",
      id: "correction-quantity", required: true,
    });
    const reasonInput = ui.el("input", {
      class: "input", type: "text", id: "correction-reason", required: true,
      maxlength: 40, value: "correction",
    });
    const errorArea = ui.el("div");
    const key = SM.pages.entry.newKey();
    const saveButton = ui.el("button", { class: "btn", type: "submit", text: "Submit Correction" });

    const form = ui.el("form", {},
      ui.el("p", { class: "text-small text-muted" },
        `Original entry: ${state.names[tx.ingredient_id] || tx.ingredient_id}, ` +
        `${tx.type} ${ui.formatQty(tx.quantity_delta)} on ${ui.formatDateTime(tx.created_at)}. ` +
        "A new correcting transaction will be appended; the original stays untouched."),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "correction-quantity", text: quantityLabel }),
        quantityInput),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "correction-reason", text: "Correction reason" }),
        reasonInput),
      errorArea,
      ui.el("div", { class: "button-row" }, saveButton),
    );
    const close = ui.modal({ title: "Correct Transaction", body: form });

    const submit = async (confirmed) => {
      ui.clear(errorArea);
      if (quantityInput.value.trim() === "" || ui.toMillis(quantityInput.value) === null) {
        errorArea.append(ui.alert("Enter a valid non-negative quantity (up to 3 decimals)."));
        return;
      }
      ui.setBusy(saveButton, true, "Correcting…");
      try {
        await SM.api.post(`/inventory/transactions/${tx.id}/correct`, {
          corrected_quantity: quantityInput.value,
          reason: reasonInput.value.trim(),
          idempotency_key: key,
          confirmed,
        });
        close();
        ui.toast("Correction recorded. The balance has been updated.");
        SM.app.navigate(); // reload history
      } catch (error) {
        if (error.code === "confirmation_required") {
          const approved = await ui.confirmDialog({
            title: "Confirm large correction", message: error.message,
            confirmLabel: "Yes, correct it", danger: true,
          });
          if (approved) return submit(true);
          errorArea.append(ui.alert("Correction cancelled.", "info"));
        } else if (error.code === "correction_after_reconciliation") {
          // D27: a newer physical count is the source of truth.
          errorArea.append(ui.alert(
            `${error.message} A newer physical stock count exists, so this entry can no longer be corrected. ` +
            "If stock is wrong, record a new Remaining Stock count instead."));
        } else {
          errorArea.append(ui.alert(error.message));
        }
      } finally {
        ui.setBusy(saveButton, false);
      }
    };
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit(false);
    });
  },
};
