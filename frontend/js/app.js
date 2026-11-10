// Application shell + hash router. Pages register themselves in SM.pages;
// this module owns navigation, role-aware menus, and session gating.

window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.app = {
  routes: {
    dashboard: { title: "Dashboard", label: "Dashboard" },
    inventory: { title: "Inventory", label: "Inventory" },
    entry: { title: "Daily Stock Entry", label: "Daily Stock Entry" },
    suppliers: { title: "Suppliers", label: "Suppliers" },
    ingredients: { title: "Ingredients", label: "Ingredients" },
    history: { title: "Transaction History", label: "Transaction History" },
    settings: { title: "Settings / Account", label: "Settings / Account" },
  },
  defaultRoute: "dashboard",
  shellReady: false,

  boot() {
    document.getElementById("logout-button").addEventListener("click", async () => {
      await SM.auth.logout();
      this.showLogin();
    });
    document.getElementById("menu-button").addEventListener("click", () => {
      document.getElementById("sidebar").classList.toggle("is-open");
      document.getElementById("sidebar-overlay").classList.toggle("is-visible");
    });
    document.getElementById("sidebar-overlay").addEventListener("click", () => {
      this.closeSidebar();
    });
    window.addEventListener("hashchange", () => this.navigate());
    SM.auth.fetchUser().then(() => this.navigate());
  },

  closeSidebar() {
    document.getElementById("sidebar").classList.remove("is-open");
    document.getElementById("sidebar-overlay").classList.remove("is-visible");
  },

  showLogin(message) {
    this.shellReady = false;
    document.getElementById("shell").classList.remove("is-visible");
    let loginRoot = document.getElementById("login-root");
    if (!loginRoot) {
      loginRoot = SM.ui.el("div", { id: "login-root" });
      document.getElementById("app").append(loginRoot);
    }
    SM.pages.login.render(loginRoot, message);
  },

  enterApp() {
    const loginRoot = document.getElementById("login-root");
    if (loginRoot) loginRoot.remove();
    this.shellReady = false; // rebuild nav for the new user's role
    if (!location.hash || location.hash === "#/") {
      // replaceState does not fire hashchange; we navigate explicitly below.
      history.replaceState(null, "", "#/" + this.defaultRoute);
    }
    this.navigate();
  },

  renderShell() {
    const nav = SM.ui.clear(document.getElementById("nav"));
    for (const [key, route] of Object.entries(this.routes)) {
      nav.append(SM.ui.el("a", {
        class: "nav-link",
        href: "#/" + key,
        "data-route": key,
        text: route.label,
      }));
    }
    const user = SM.auth.user;
    const roleLabel = user.role === "admin" ? "Administrator" : "Staff";
    SM.ui.clear(document.getElementById("topbar-user")).append(
      `${user.username} · ${roleLabel}`
    );
    SM.ui.clear(document.getElementById("sidebar-user")).append(
      `Signed in as ${user.username} (${roleLabel})`
    );
    document.getElementById("shell").classList.add("is-visible");
    this.shellReady = true;
  },

  async navigate() {
    if (!SM.auth.user) {
      this.showLogin();
      return;
    }
    if (!this.shellReady) this.renderShell();

    const key = location.hash.replace(/^#\//, "") || this.defaultRoute;
    const routeKey = this.routes[key] ? key : this.defaultRoute;
    const route = this.routes[routeKey];
    const page = SM.pages[routeKey];

    document.getElementById("page-title").textContent = route.title;
    document.title = `${route.title} · StockMate`;
    for (const link of document.querySelectorAll(".nav-link")) {
      link.classList.toggle("is-active", link.dataset.route === routeKey);
    }
    this.closeSidebar();

    const view = SM.ui.clear(document.getElementById("view"));
    view.append(SM.ui.loading());
    try {
      await page.render(view);
    } catch (error) {
      SM.ui.clear(view).append(
        SM.ui.alert(error.message || "This page could not be loaded. Please try again."),
        SM.ui.el("button", {
          class: "btn btn--secondary", text: "Retry",
          onclick: () => this.navigate(),
        })
      );
    }
  },
};

document.addEventListener("DOMContentLoaded", () => SM.app.boot());
