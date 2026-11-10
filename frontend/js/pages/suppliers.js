// Supplier management. Staff get read-only access; create/edit/archive/
// reactivate controls render only for admins (the backend enforces RBAC
// regardless — hidden buttons are convenience, not security).

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.suppliers = {
  async render(view) {
    const ui = SM.ui;
    const isAdmin = SM.auth.isAdmin();
    ui.clear(view);

    const state = { search: "", status: "", suppliers: [] };

    const search = ui.el("input", {
      class: "input", type: "search", placeholder: "Search suppliers…",
      "aria-label": "Search suppliers",
      oninput: () => { state.search = search.value.trim(); load(); },
    });
    const statusSelect = ui.el("select", {
      class: "select", "aria-label": "Filter by status",
      onchange: () => { state.status = statusSelect.value; load(); },
    },
      ui.el("option", { value: "", text: "All statuses" }),
      ui.el("option", { value: "active", text: "Active" }),
      ui.el("option", { value: "archived", text: "Archived" }),
    );
    const listCard = ui.el("div", { class: "card" });

    view.append(
      ui.el("div", { class: "card" },
        ui.el("div", { class: "form-grid" }, search, statusSelect),
        isAdmin ? ui.el("button", {
          class: "btn", text: "Add Supplier",
          onclick: () => this.openForm(null, load),
        }) : ui.el("p", { class: "text-muted text-small", text: "Read-only access. Contact an administrator for changes." })),
      listCard,
    );

    const load = async () => {
      ui.clear(listCard).append(ui.loading());
      try {
        const params = new URLSearchParams();
        if (state.search) params.set("search", state.search);
        if (state.status) params.set("status", state.status);
        const query = params.toString();
        state.suppliers = await SM.api.get("/suppliers" + (query ? "?" + query : ""));
        this.renderList(listCard, state.suppliers, isAdmin, load);
      } catch (error) {
        ui.clear(listCard).append(ui.alert(error.message));
      }
    };
    await load();
  },

  renderList(card, suppliers, isAdmin, reload) {
    const ui = SM.ui;
    ui.clear(card);
    if (suppliers.length === 0) {
      card.append(ui.empty("No suppliers found."));
      return;
    }
    const table = ui.el("table", { class: "table table--responsive" },
      ui.el("thead", {}, ui.el("tr", {},
        ...["Name", "Contact", "Phone", "Email", "Status", "Actions"].map((h) => ui.el("th", { text: h })))),
    );
    const tbody = ui.el("tbody");
    for (const supplier of suppliers) {
      const actions = ui.el("div", { class: "button-row" },
        ui.el("button", {
          class: "btn btn--secondary btn--small", text: "View",
          onclick: () => this.openDetail(supplier.id),
        }));
      if (isAdmin) {
        actions.append(ui.el("button", {
          class: "btn btn--secondary btn--small", text: "Edit",
          onclick: () => this.openForm(supplier, reload),
        }));
        actions.append(ui.el("button", {
          class: "btn btn--secondary btn--small",
          text: supplier.status === "active" ? "Archive" : "Reactivate",
          onclick: () => this.toggleStatus(supplier, reload),
        }));
      }
      tbody.append(ui.el("tr", {},
        ui.el("td", { "data-label": "Name" }, ui.el("strong", { text: supplier.name })),
        ui.el("td", { "data-label": "Contact", text: supplier.contact_name || "—" }),
        ui.el("td", { "data-label": "Phone", text: supplier.phone || "—" }),
        ui.el("td", { "data-label": "Email", text: supplier.email || "—" }),
        ui.el("td", { "data-label": "Status" },
          ui.badge(supplier.status, supplier.status === "active" ? "safe" : "neutral")),
        ui.el("td", { "data-label": "Actions" }, actions),
      ));
    }
    table.append(tbody);
    card.append(ui.el("div", { class: "table-wrap" }, table));
  },

  async openDetail(supplierId) {
    const ui = SM.ui;
    try {
      const supplier = await SM.api.get(`/suppliers/${supplierId}`);
      const body = ui.el("div");
      body.append(ui.el("dl", { class: "preview-box" },
        ui.el("p", {}, ui.el("strong", { text: supplier.name })),
        ui.el("p", { class: "text-small", text: `Contact: ${supplier.contact_name || "—"} · ${supplier.phone || "—"} · ${supplier.email || "—"}` }),
        supplier.notes ? ui.el("p", { class: "text-small", text: supplier.notes }) : null,
      ));
      body.append(ui.el("h3", { text: "Linked Ingredients" }));
      if (supplier.ingredients.length === 0) {
        body.append(ui.empty("No ingredients are assigned to this supplier."));
      } else {
        const list = ui.el("ul");
        for (const link of supplier.ingredients) {
          list.append(ui.el("li", {},
            `${link.ingredient_name} `,
            link.is_primary ? ui.badge("Primary", "info") : ui.badge("Alternative", "neutral"),
            link.ingredient_status === "archived" ? [" ", ui.badge("Archived", "neutral")] : null,
          ));
        }
        body.append(list);
      }
      SM.ui.modal({ title: "Supplier Details", body });
    } catch (error) {
      SM.ui.toast(error.message, "error");
    }
  },

  openForm(supplier, reload) {
    const ui = SM.ui;
    const editing = Boolean(supplier);
    const fields = {};
    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        ui.setBusy(saveButton, true, "Saving…");
        const payload = {};
        for (const [key, input] of Object.entries(fields)) {
          const value = input.value.trim();
          if (editing) {
            if (value !== "") payload[key] = value;
          } else {
            payload[key] = value === "" ? null : value;
          }
        }
        if (!editing && !payload.name) {
          errorArea.append(ui.alert("Supplier name is required."));
          ui.setBusy(saveButton, false);
          return;
        }
        try {
          if (editing) {
            await SM.api.patch(`/suppliers/${supplier.id}`, payload);
          } else {
            await SM.api.post("/suppliers", payload);
          }
          close();
          ui.toast(editing ? "Supplier updated." : "Supplier created.");
          reload();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
        } finally {
          ui.setBusy(saveButton, false);
        }
      },
    });
    const errorArea = ui.el("div");
    const definitions = [
      ["name", "Supplier Name", true],
      ["contact_name", "Contact Person", false],
      ["phone", "Phone", false],
      ["email", "Email", false],
    ];
    for (const [key, label, required] of definitions) {
      fields[key] = ui.el("input", {
        class: "input", id: `supplier-${key}`, type: "text",
        value: supplier?.[key] || "", required: !editing && required,
        maxlength: key === "name" || key === "contact_name" ? 120 : key === "phone" ? 40 : 200,
      });
      form.append(ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: `supplier-${key}`, text: label }),
        fields[key]));
    }
    fields.notes = ui.el("textarea", { class: "textarea", id: "supplier-notes", rows: 3 });
    fields.notes.value = supplier?.notes || "";
    form.append(ui.el("div", { class: "field" },
      ui.el("label", { class: "field__label", for: "supplier-notes", text: "Notes" }),
      fields.notes));
    form.append(errorArea);
    const saveButton = ui.el("button", { class: "btn", type: "submit", text: "Save Supplier" });
    form.append(ui.el("div", { class: "button-row" }, saveButton));

    const close = ui.modal({
      title: editing ? `Edit ${supplier.name}` : "Add Supplier",
      body: form,
    });
  },

  async toggleStatus(supplier, reload) {
    const ui = SM.ui;
    const archiving = supplier.status === "active";
    const approved = await ui.confirmDialog({
      title: archiving ? "Archive Supplier" : "Reactivate Supplier",
      message: archiving
        ? `Archive ${supplier.name}? Archived suppliers stay visible in history but cannot be assigned to new orders. This is blocked while the supplier is the primary supplier of active ingredients.`
        : `Reactivate ${supplier.name}? It will become available for assignments again.`,
      confirmLabel: archiving ? "Archive" : "Reactivate",
      danger: archiving,
    });
    if (!approved) return;
    try {
      await SM.api.post(`/suppliers/${supplier.id}/${archiving ? "archive" : "reactivate"}`);
      ui.toast(archiving ? "Supplier archived." : "Supplier reactivated.");
      reload();
    } catch (error) {
      // archive_blocked: the server message lists the blocking ingredients.
      ui.modal({
        title: "Cannot archive supplier",
        body: ui.alert(error.message),
      });
    }
  },
};
