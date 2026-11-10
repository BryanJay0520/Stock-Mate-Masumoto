// Daily Stock Entry — the primary staff workflow.
//
// Two clearly separated modes (decision D3):
//   USED      — staff records consumption; the server deducts it.
//   REMAINING — staff records a physical count; the server reconciles.
// The preview is informational only; the backend computes the authoritative
// balance. One idempotency key per LOGICAL submission: retries reuse it,
// a new entry gets a fresh key, and the button locks while submitting.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.entry = {
  REASONS: [
    ["daily_count", "Daily count / routine"],
    ["spoilage", "Spoilage"],
    ["damage", "Damage"],
    ["recount", "Recount"],
  ],

  newKey() {
    const uuid = window.crypto && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
    return `web-${uuid}`;
  },

  async render(view) {
    const ui = SM.ui;
    const { rows } = await SM.data.catalog();
    const active = rows.filter((r) => r.status === "active");
    ui.clear(view);

    if (active.length === 0) {
      view.append(ui.el("div", { class: "card" },
        ui.empty("No active ingredients available. An administrator must add ingredients first.")));
      return;
    }

    const state = {
      mode: "used",
      submitting: false,
      key: this.newKey(),
    };
    const byId = Object.fromEntries(active.map((r) => [r.ingredient_id, r]));

    // --- Controls -----------------------------------------------------------
    const usedButton = ui.el("button", {
      type: "button", class: "mode-toggle__option",
      onclick: () => setMode("used"),
    },
      ui.el("strong", { text: "Used Stock" }),
      ui.el("span", { class: "text-small", text: "Record how much was consumed" }));
    const remainingButton = ui.el("button", {
      type: "button", class: "mode-toggle__option",
      onclick: () => setMode("remaining"),
    },
      ui.el("strong", { text: "Remaining Stock" }),
      ui.el("span", { class: "text-small", text: "Record the actual physical count" }));

    const ingredientSelect = ui.el("select", {
      class: "select", id: "entry-ingredient",
      onchange: () => { resetSubmission(); updatePreview(); },
    }, active.map((r) => ui.el("option", {
      value: r.ingredient_id,
      text: `${r.ingredient_name} (${r.unit})`,
    })));

    const quantityInput = ui.el("input", {
      class: "input", id: "entry-quantity", type: "number",
      min: "0", step: "0.001", inputmode: "decimal", required: true,
      oninput: () => updatePreview(),
    });
    const reasonSelect = ui.el("select", { class: "select", id: "entry-reason" },
      this.REASONS.map(([value, label]) => ui.el("option", { value, text: label })));

    const negativeField = ui.el("label", { class: "field__hint" },
      ui.el("input", { type: "checkbox", id: "entry-allow-negative" }),
      " Allow negative stock (administrator override)");

    const preview = ui.el("dl");
    const previewBox = ui.el("div", { class: "preview-box" }, preview);
    const feedback = ui.el("div");
    const submitButton = ui.el("button", { class: "btn", type: "submit", text: "Record Stock" });

    const selectedItem = () => byId[Number(ingredientSelect.value)];

    function setMode(mode) {
      state.mode = mode;
      usedButton.classList.toggle("is-selected", mode === "used");
      remainingButton.classList.toggle("is-selected", mode === "remaining");
      resetSubmission();
      updatePreview();
    }

    function resetSubmission() {
      // Changing WHAT is being submitted starts a NEW logical submission.
      state.key = SM.pages.entry.newKey();
    }

    function updatePreview() {
      const item = selectedItem();
      const current = ui.toMillis(item.current_quantity);
      const quantity = ui.toMillis(quantityInput.value);
      const rowsOut = [
        ["Current system stock", `${ui.formatQty(item.current_quantity)} ${item.unit}`],
      ];
      if (quantityInput.value.trim() === "") {
        rowsOut.push([state.mode === "used" ? "Quantity used" : "Physical count", "—"]);
      } else if (quantity === null || quantity < 0) {
        rowsOut.push(["Quantity", "Enter a valid non-negative number (max 3 decimals)"]);
      } else if (state.mode === "used") {
        rowsOut.push(["Quantity used", `${ui.fromMillis(quantity)} ${item.unit}`]);
        rowsOut.push(["Expected remaining stock", `${ui.fromMillis(current - quantity)} ${item.unit}`]);
      } else {
        rowsOut.push(["Physical count", `${ui.fromMillis(quantity)} ${item.unit}`]);
        rowsOut.push(["Adjustment", `${quantity - current >= 0 ? "+" : ""}${ui.fromMillis(quantity - current)} ${item.unit}`]);
        rowsOut.push(["New stock balance", `${ui.fromMillis(quantity)} ${item.unit}`]);
      }
      ui.clear(preview).append(
        rowsOut.flatMap(([label, value]) => [
          ui.el("dt", { text: label }), ui.el("dd", { text: value }),
        ]),
      );
      if (state.mode === "used" && quantity !== null && current - quantity < 0) {
        previewBox.append(ui.alert(
          "This would make stock negative. The server will reject it unless an administrator overrides.",
          "info"));
      } else {
        previewBox.querySelector(".alert")?.remove();
      }
    }

    async function submit(confirmed) {
      const item = selectedItem();
      const quantity = ui.toMillis(quantityInput.value);
      if (quantity === null || quantity < 0 || quantityInput.value.trim() === "") {
        ui.clear(feedback).append(ui.alert("Enter a valid non-negative quantity (up to 3 decimal places)."));
        return;
      }
      const payload = {
        ingredient_id: item.ingredient_id,
        mode: state.mode,
        quantity: ui.fromMillis(quantity),
        reason: reasonSelect.value,
        idempotency_key: state.key,
        confirmed,
        allow_negative: SM.auth.isAdmin()
          ? document.getElementById("entry-allow-negative").checked
          : false,
      };

      state.submitting = true;
      ui.setBusy(submitButton, true, "Recording…");
      ui.clear(feedback);
      try {
        const result = await SM.api.post("/inventory/entries", payload);
        const tx = result.transaction;
        item.current_quantity = tx.resulting_quantity;
        item.last_transaction_at = tx.created_at;
        if (state.mode === "remaining") {
          item.last_counted_at = tx.created_at;
          item.confidence = "high";
        }
        const verb = state.mode === "remaining" ? "Physical count saved" : "Usage recorded";
        feedback.append(ui.alert(
          `${verb}. ${item.ingredient_name}: new balance ${ui.formatQty(tx.resulting_quantity)} ${item.unit}.`,
          "success"));
        quantityInput.value = "";
        resetSubmission(); // next entry = new logical submission = fresh key
        updatePreview();
      } catch (error) {
        if (error.code === "confirmation_required") {
          const approved = await ui.confirmDialog({
            title: "Confirm large adjustment",
            message: error.message,
            confirmLabel: "Yes, record it",
            danger: true,
          });
          if (approved) {
            state.submitting = false;
            // Same logical submission: SAME idempotency key, now confirmed.
            return submit(true);
          }
          feedback.append(ui.alert("Entry cancelled — nothing was recorded.", "info"));
        } else {
          // Keep state.key so a retry of THIS submission stays idempotent.
          feedback.append(ui.alert(error.message));
        }
      } finally {
        state.submitting = false;
        ui.setBusy(submitButton, false);
      }
    }

    const form = ui.el("form", {
      onsubmit: (event) => {
        event.preventDefault();
        if (!state.submitting) submit(false);
      },
    },
      ui.el("div", { class: "mode-toggle" }, usedButton, remainingButton),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "entry-ingredient", text: "Ingredient" }),
        ingredientSelect),
      ui.el("div", { class: "field" },
        ui.el("label", {
          class: "field__label", for: "entry-quantity", id: "quantity-label",
          text: "Quantity",
        }),
        quantityInput,
        ui.el("span", { class: "field__hint", text: "Up to 3 decimal places, in the ingredient's stock unit." })),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "entry-reason", text: "Reason" }),
        reasonSelect),
      SM.auth.isAdmin() ? negativeField : null,
      previewBox,
      feedback,
      submitButton,
    );

    view.append(ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Record Stock" }),
      ui.el("p", { class: "text-muted text-small" },
        "Used Stock deducts consumption. Remaining Stock reconciles to your physical count and marks it as verified. The server calculates the final balance."),
      form,
    ));

    setMode("used");
    updatePreview();
  },
};
