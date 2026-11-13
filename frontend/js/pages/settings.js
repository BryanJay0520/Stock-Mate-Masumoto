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
      await this.renderNotificationSettings(view);
      await this.renderAdminUsers(view);
    }
  },

  async renderNotificationSettings(view) {
    const ui = SM.ui;
    const card = ui.el("section", {class: "card"},
      ui.el("h2", {class: "card__title", text: "Notification Settings (Administrator)"}),
      ui.el("p", {class: "field__hint", text: "Manual Refresh tasks remains available. Automatic checks require this setting and a separately running worker. No external messages are sent."}));
    view.append(card);
    try {
      const [settings, catalog] = await Promise.all([SM.api.get("/notifications/settings"), SM.data.catalog()]);
      const form = ui.el("form", {novalidate: "novalidate"});
      const flags = [
        ["daily_count_enabled", "Daily inventory count reminders"],
        ["order_cutoff_enabled", "Supplier ordering reminders"],
        ["supplier_contact_enabled", "Required supplier contact reminders"],
        ["forecast_shortage_enabled", "Forecast shortage alerts"],
        ["verify_pending_order_enabled", "Pending order verification alerts"],
        ["pending_delivery_enabled", "Pending or delayed delivery reminders"],
      ];
      const controls = {};
      const options = ui.el("div", {class: "notification-settings-grid"});
      for (const [key, label] of flags) {
        const input = ui.el("input", {type: "checkbox", id: `notification-${key}`, checked: settings[key]});
        controls[key] = input;
        options.append(ui.el("label", {class: "notification-check", for: input.id}, input, ui.el("span", {text: label})));
      }
      const dailyTime = ui.el("input", {class: "input", id: "notification-daily-time", type: "time",
        value: settings.daily_count_time.slice(0, 5)});
      const automatic = ui.el("input", {type: "checkbox", id: "notification-automatic",
        checked: settings.automatic_evaluation_enabled});
      const interval = ui.el("select", {class: "select", id: "notification-interval"},
        ...[[60, "Every minute"], [300, "Every 5 minutes"], [600, "Every 10 minutes"],
          [900, "Every 15 minutes"]].map(([seconds, label]) =>
          ui.el("option", {value: String(seconds), text: label})));
      if (![60, 300, 600, 900].includes(settings.evaluation_interval_seconds)) {
        interval.append(ui.el("option", {value: String(settings.evaluation_interval_seconds),
          text: `Current: every ${settings.evaluation_interval_seconds} seconds`}));
      }
      interval.value = String(settings.evaluation_interval_seconds);
      const workerStatus = ui.el("div", {role: "status", "aria-live": "polite"});
      const statusButton = ui.el("button", {type: "button", class: "btn btn--secondary btn--small",
        text: "Refresh worker status"});
      const showStatus = async () => {
        try {
          const health = await SM.api.get("/notifications/scheduler-health");
          const fmt = value => value ? ui.formatDateTime(value) : "Never";
          const latest = health.last_result;
          ui.clear(workerStatus).append(
            ui.el("p", {text: `Worker status: ${health.status}. Enabled: ${health.automatic_evaluation_enabled ? "Yes" : "No"}. Interval: ${health.evaluation_interval_seconds / 60} minute(s).`}),
            ui.el("p", {class: "text-muted text-small", text: `Heartbeat: ${fmt(health.heartbeat_at)}. Last attempt: ${fmt(health.last_attempt_at)}. Last success: ${fmt(health.last_success_at)}. Next eligible check (if running): ${fmt(health.next_expected_evaluation_at)}.`}),
            health.last_failure_at ? ui.el("p", {class: "text-muted text-small", text: `Last failure: ${fmt(health.last_failure_at)} (${health.last_error_code || "unknown error"}).`}) : "",
            latest ? ui.el("p", {class: "text-muted text-small", text: `Latest result: ${latest.created} new, ${latest.updated} updated, ${latest.resolved} resolved, ${latest.expired} expired.`}) : "",
          );
        } catch (error) { ui.clear(workerStatus).append(ui.alert(error.message)); }
      };
      statusButton.addEventListener("click", showStatus);
      const scheduler = ui.el("fieldset", {class: "notification-count-scope"},
        ui.el("legend", {text: "Automatic notification checks"}),
        ui.el("label", {class: "notification-check", for: automatic.id}, automatic,
          ui.el("span", {text: "Enable automatic evaluation when the worker is running"})),
        ui.el("div", {class: "field"},
          ui.el("label", {class: "field__label", for: interval.id, text: "Check interval"}), interval,
          ui.el("span", {class: "field__hint", text: "The worker checks at this interval. A 21:30 reminder appears on the first successful check at or after 21:30, not exactly to the second."})),
        ui.el("p", {class: "field__hint", text: "Enabled does not mean running. The worker must be launched separately; staff can still use manual Refresh tasks if it is stopped."}),
        statusButton, workerStatus);
      const ingredients = ui.el("fieldset", {class: "notification-count-scope"},
        ui.el("legend", {text: "Ingredients requiring daily physical counts"}),
        ui.el("p", {class: "field__hint", text: "Select only ingredients whose daily count is required. Used Stock entries and receipts do not complete a physical count."}));
      const ingredientChecks = new Map();
      for (const ingredient of catalog.ingredients.filter(row => row.status === "active")) {
        const input = ui.el("input", {type: "checkbox", checked: settings.daily_count_ingredient_ids.includes(ingredient.id)});
        ingredientChecks.set(ingredient.id, input);
        ingredients.append(ui.el("label", {class: "notification-check"}, input, ui.el("span", {text: ingredient.name})));
      }
      if (!ingredientChecks.size) ingredients.append(ui.empty("No active ingredients are available."));
      const feedback = ui.el("div", {role: "status", "aria-live": "polite"});
      const save = ui.el("button", {class: "btn", type: "submit", text: "Save notification settings"});
      form.append(options, ui.el("div", {class: "field"},
        ui.el("label", {class: "field__label", for: "notification-daily-time", text: "Daily inventory reminder time"}),
        dailyTime, ui.el("span", {class: "field__hint", text: "Business timezone: Asia/Tokyo by default. The configured business timezone applies."})),
        ingredients, scheduler, feedback, save);
      form.addEventListener("submit", async event => {
        event.preventDefault();
        ui.clear(feedback);
        if (!/^\d{2}:\d{2}$/.test(dailyTime.value)) {
          dailyTime.setAttribute("aria-invalid", "true");
          feedback.append(ui.alert("Enter a valid daily reminder time.")); dailyTime.focus(); return;
        }
        dailyTime.removeAttribute("aria-invalid");
        save.disabled = true;
        try {
          const payload = Object.fromEntries(flags.map(([key]) => [key, controls[key].checked]));
          payload.daily_count_time = dailyTime.value;
          payload.daily_count_ingredient_ids = [...ingredientChecks].filter(([, input]) => input.checked).map(([id]) => id);
          payload.automatic_evaluation_enabled = automatic.checked;
          payload.evaluation_interval_seconds = Number(interval.value);
          await SM.api.patch("/notifications/settings", payload);
          feedback.append(ui.alert("Notification settings saved. A running worker checks automatically; Refresh tasks is available now.", "info"));
          await showStatus();
        } catch (error) { feedback.append(ui.alert(error.message)); }
        finally { save.disabled = false; }
      });
      card.append(form);
      await showStatus();
    } catch (error) {
      card.append(ui.alert(error.message));
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
