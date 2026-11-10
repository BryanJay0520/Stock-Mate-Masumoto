// Settings / Account: session info for everyone, user management for admins
// (server-enforced RBAC; the admin section simply isn't rendered for staff).

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.settings = {
  async render(view) {
    const ui = SM.ui;
    ui.clear(view);
    const user = SM.auth.user;

    const accountCard = ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Your Account" }),
      ui.el("p", {}, ui.el("strong", { text: user.username })),
      ui.el("p", { class: "text-muted", text: `Role: ${user.role === "admin" ? "Administrator" : "Staff"}` }),
      ui.el("p", { class: "text-muted text-small" },
        "Your session is kept in a secure, HttpOnly cookie. Sessions expire automatically; log in again when asked."),
      ui.el("button", {
        class: "btn btn--secondary", text: "Log out",
        onclick: async () => {
          await SM.auth.logout();
          SM.app.showLogin();
        },
      }),
    );
    view.append(accountCard);

    // Business configuration is read from the health endpoint (read-only).
    try {
      const health = await SM.api.get("/health");
      const configCard = ui.el("div", { class: "card" },
        ui.el("h2", { class: "card__title", text: "Business Configuration" }),
        ui.el("p", { class: "text-small", text: `Business timezone: ${health.businessTimezone}` }),
        ui.el("p", { class: "text-small", text: `Business day starts at: ${health.businessDayStartTime}` }),
        ui.el("p", { class: "text-muted text-small" },
          "Timezone and counting schedules are administrator settings. Editable configuration arrives in a later phase."),
      );
      view.append(configCard);
    } catch { /* health info is optional on this page */ }

    if (SM.auth.isAdmin()) {
      await this.renderAdminUsers(view);
    }
  },

  async renderAdminUsers(view) {
    const ui = SM.ui;
    const card = ui.el("div", { class: "card" },
      ui.el("h2", { class: "card__title", text: "Staff Accounts (Administrator)" }));
    view.append(card);

    const load = async () => {
      try {
        const users = (await SM.api.get("/admin/users")).users;
        const table = ui.el("table", { class: "table table--responsive" },
          ui.el("thead", {}, ui.el("tr", {},
            ui.el("th", { text: "Username" }),
            ui.el("th", { text: "Role" }),
            ui.el("th", { text: "Status" }),
          )),
        );
        const tbody = ui.el("tbody");
        for (const account of users) {
          tbody.append(ui.el("tr", {},
            ui.el("td", { "data-label": "Username", text: account.username }),
            ui.el("td", { "data-label": "Role", text: account.role }),
            ui.el("td", { "data-label": "Status" },
              ui.badge(account.is_active ? "active" : "inactive", account.is_active ? "safe" : "neutral")),
          ));
        }
        table.append(tbody);
        ui.clear(card).append(
          ui.el("h2", { class: "card__title", text: "Staff Accounts (Administrator)" }),
          ui.el("div", { class: "table-wrap" }, table),
        );
        card.append(ui.el("button", {
          class: "btn", text: "Create Staff Account",
          onclick: () => this.openCreateUser(load),
        }));
      } catch (error) {
        card.append(ui.alert(error.message));
      }
    };
    await load();
  },

  openCreateUser(reload) {
    const ui = SM.ui;
    const username = ui.el("input", {
      class: "input", id: "new-username", type: "text", required: true,
      maxlength: 50, autocomplete: "off",
    });
    const password = ui.el("input", {
      class: "input", id: "new-password", type: "password", required: true,
      maxlength: 200, autocomplete: "new-password",
    });
    const role = ui.el("select", { class: "select", id: "new-role" },
      ui.el("option", { value: "staff", text: "Staff" }),
      ui.el("option", { value: "admin", text: "Administrator" }));
    const errorArea = ui.el("div");
    const saveButton = ui.el("button", { class: "btn", type: "submit", text: "Create Account" });

    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        ui.setBusy(saveButton, true, "Creating…");
        try {
          await SM.api.post("/admin/users", {
            username: username.value.trim(),
            password: password.value,
            role: role.value,
          });
          close();
          ui.toast("Account created. Share the password securely — it is not stored in the browser.");
          reload();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
        } finally {
          ui.setBusy(saveButton, false);
        }
      },
    },
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "new-username", text: "Username" }), username),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "new-password", text: "Password" }),
        password,
        ui.el("span", { class: "field__hint", text: "Must meet the server's password strength rules." })),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "new-role", text: "Role" }), role),
      errorArea,
      ui.el("div", { class: "button-row" }, saveButton),
    );
    const close = ui.modal({ title: "Create Staff Account", body: form });
  },
};
