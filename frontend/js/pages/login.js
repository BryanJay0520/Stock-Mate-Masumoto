// Login screen. Credentials go straight to the backend over fetch; they are
// never persisted anywhere in the browser.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.pages.login = {
  render(root, message) {
    const ui = SM.ui;
    const errorArea = ui.el("div");
    if (message) errorArea.append(ui.alert(message, "info"));

    const username = ui.el("input", {
      class: "input", id: "login-username", name: "username", type: "text",
      autocomplete: "username", required: true, maxlength: 50,
    });
    const password = ui.el("input", {
      class: "input", id: "login-password", name: "password", type: "password",
      autocomplete: "current-password", required: true, maxlength: 200,
    });
    const submit = ui.el("button", { class: "btn", type: "submit", text: "Sign in" });

    const form = ui.el("form", {
      onsubmit: async (event) => {
        event.preventDefault();
        ui.clear(errorArea);
        ui.setBusy(submit, true, "Signing in…");
        try {
          await SM.auth.login(username.value.trim(), password.value);
          SM.app.enterApp();
        } catch (error) {
          errorArea.append(ui.alert(error.message));
          password.value = "";
          password.focus();
        } finally {
          ui.setBusy(submit, false);
        }
      },
    },
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "login-username", text: "Username" }),
        username),
      ui.el("div", { class: "field" },
        ui.el("label", { class: "field__label", for: "login-password", text: "Password" }),
        password),
      submit,
    );

    ui.clear(root).append(
      ui.el("div", { class: "login-page" },
        ui.el("div", { class: "card login-card" },
          ui.el("h1", { class: "brand", text: "StockMate" }),
          ui.el("p", { class: "tagline", text: "Smart restaurant inventory & supply planning" }),
          errorArea,
          form,
        )
      )
    );
    username.focus();
  },
};
