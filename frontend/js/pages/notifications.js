// Persistent in-app notices. Reads never evaluate conditions or resolve work.
window.SM = window.SM || {};
SM.pages = SM.pages || {};

SM.notificationUI = {
  labels: {
    daily_count: "Inventory count", order_cutoff: "Order deadline", supplier_contact: "Supplier contact",
    forecast_shortage: "Forecast shortage", verify_pending_order: "Verify pending order",
    pending_delivery: "Pending delivery",
  },
  priority(item) {
    return SM.ui.badge(item.priority[0].toUpperCase() + item.priority.slice(1),
      item.priority === "critical" ? "critical" : item.priority === "high" ? "warning" : "neutral");
  },
  evaluatedLabel(iso) {
    if (!iso) return "Notifications have not been evaluated yet. Select Refresh tasks to calculate current conditions.";
    return `Last evaluated ${SM.ui.formatDateTime(iso)}. Notifications are snapshots; select Refresh tasks for current conditions.`;
  },
  stale(iso) {
    return !iso || Date.now() - new Date(iso).getTime() > 60 * 60 * 1000;
  },
  safeTarget(value) {
    return typeof value === "string" && /^#\/(entry|planner|forecast|orders|suppliers|notifications)(\?[a-z_]+=[0-9]+)?$/.test(value)
      ? value : "#/notifications";
  },
  async refreshBell() {
    const count = document.getElementById("notification-count");
    const recent = document.getElementById("notification-recent");
    if (!count || !recent || !SM.auth.user) return;
    try {
      const [summary, page] = await Promise.all([
        SM.api.get("/notifications/unread-count"), SM.api.get("/notifications?limit=4")]);
      count.textContent = String(summary.unread_count);
      count.setAttribute("aria-label", `${summary.unread_count} unread notifications`);
      document.querySelector("#notification-bell summary")?.setAttribute("aria-label", `Notifications, ${summary.unread_count} unread`);
      SM.ui.clear(recent);
      if (!page.items.length) recent.append(SM.ui.empty("No active notifications. Open Notifications to refresh tasks."));
      for (const item of page.items) recent.append(SM.ui.el("a", {
        class: "notification-recent__link", href: `#/notifications?view=all`, text: `${item.priority}: ${item.title}`,
      }));
      recent.append(SM.ui.el("a", {href: "#/notifications", text: "View all notifications →"}));
    } catch (error) {
      count.textContent = "–";
      SM.ui.clear(recent).append(SM.ui.alert(error.message));
    }
  },
  card(item, reload) {
    const ui = SM.ui;
    const card = ui.el("article", {class: `card notification-card${item.read_at ? " is-read" : " is-unread"}`});
    const header = ui.el("div", {class: "notification-card__header"},
      ui.el("h3", {text: item.title}),
      this.priority(item),
      ui.badge(this.labels[item.type] || item.type, "neutral"),
      ui.badge(item.status === "active" ? item.read_at ? "Read" : "Unread" : item.status, "neutral"));
    card.append(header, ui.el("p", {text: item.message}));
    const when = [item.business_date ? `Business date ${ui.formatDateOnly(item.business_date)}` : null,
      item.action_at ? `Action time ${ui.formatDateTime(item.action_at)}` : null,
      `Created ${ui.formatDateTime(item.created_at)}`].filter(Boolean).join(" · ");
    card.append(ui.el("p", {class: "text-muted text-small", text: when}));
    const actions = ui.el("div", {class: "button-row"});
    actions.append(ui.el("a", {href: this.safeTarget(item.navigation_target), text: "Open related page →"}));
    if (!item.read_at) actions.append(ui.el("button", {type: "button", class: "btn btn--secondary btn--small",
      text: "Mark as read", onclick: async () => {
        try { await SM.api.post(`/notifications/${item.id}/read`); await reload(); }
        catch (error) { ui.toast(error.message, "error"); }
      }}));
    if (item.status === "active" && !item.acknowledged_at) actions.append(ui.el("button", {
      type: "button", class: "btn btn--secondary btn--small", text: "Acknowledge",
      title: "Acknowledging does not complete the inventory, order, contact, or delivery task.",
      onclick: async () => {
        try { await SM.api.post(`/notifications/${item.id}/acknowledge`); await reload(); }
        catch (error) { ui.toast(error.message, "error"); }
      },
    }));
    if (item.acknowledged_at) actions.append(ui.el("span", {class: "text-muted text-small",
      text: `Acknowledged ${ui.formatDateTime(item.acknowledged_at)}; task completion is tracked separately.`}));
    card.append(actions);
    return card;
  },
  async dashboardCard() {
    const ui = SM.ui;
    const card = ui.el("section", {class: "card", "data-notification-dashboard": ""},
      ui.el("h2", {class: "card__title", text: "Notifications / Action Required"}));
    try {
      const page = await SM.api.get("/notifications?limit=6");
      card.append(ui.el("p", {class: "field__hint", text: this.evaluatedLabel(page.evaluated_at)}));
      if (this.stale(page.evaluated_at)) {
        card.append(ui.alert("This notification snapshot may be stale. Refresh tasks before acting on it.", "info"));
      } else if (!page.items.length) {
        card.append(ui.empty("No active notifications at the last evaluation."));
      } else {
        const list = ui.el("ul", {class: "notification-summary"});
        for (const item of page.items) list.append(ui.el("li", {}, this.priority(item),
          ui.el("a", {href: `#/notifications?view=all`, text: item.title})));
        card.append(list);
      }
      card.append(ui.el("a", {href: "#/notifications", text: "Open Notifications →"}));
    } catch (error) {
      card.append(ui.alert(error.message), ui.el("a", {href: "#/notifications", text: "Open Notifications to retry →"}));
    }
    return card;
  },
  async refreshDashboard() {
    const current = document.querySelector("[data-notification-dashboard]");
    if (current) current.replaceWith(await this.dashboardCard());
  },
};

SM.pages.notifications = {
  async render(view) {
    const ui = SM.ui;
    const params = new URLSearchParams(location.hash.split("?")[1] || "");
    const filter = ["all", "unread", "urgent", "history"].includes(params.get("view")) ? params.get("view") : "all";
    const offset = Math.max(0, Number.parseInt(params.get("offset") || "0", 10) || 0);
    ui.clear(view);
    const intro = ui.el("div", {class: "card"},
      ui.el("h2", {class: "card__title", text: "Notification Center"}),
      ui.el("p", {text: "These are evaluated business conditions. Reading or acknowledging a notice does not complete an inventory count, supplier contact, order, or delivery."}),
      ui.el("p", {class: "field__hint", text: "A shared operational account also shares its read and acknowledgement state."}));
    const evaluation = ui.el("p", {class: "field__hint"});
    const feedback = ui.el("div", {role: "status", "aria-live": "polite"});
    const refresh = ui.el("button", {type: "button", class: "btn", text: "Refresh tasks"});
    const markAll = ui.el("button", {type: "button", class: "btn btn--secondary", text: "Mark all as read"});
    intro.append(evaluation, ui.el("div", {class: "button-row"}, refresh, markAll), feedback);
    const filters = ui.el("nav", {class: "notification-filters", "aria-label": "Notification filters"});
    for (const [value, label] of [["all", "All"], ["unread", "Unread"], ["urgent", "Urgent"], ["history", "Resolved / history"]])
      filters.append(ui.el("a", {class: `btn btn--secondary${filter === value ? " is-active" : ""}`,
        href: `#/notifications?view=${value}`, "aria-current": filter === value ? "page" : null, text: label}));
    const content = ui.el("div", {class: "notification-list", "aria-live": "polite"});
    view.append(intro, filters, content);
    let serial = 0;
    const load = async () => {
      const token = ++serial;
      content.setAttribute("aria-busy", "true");
      try {
        const page = await SM.api.get(`/notifications?view=${filter}&limit=10&offset=${offset}`);
        if (token !== serial) return;
        evaluation.textContent = SM.notificationUI.evaluatedLabel(page.evaluated_at);
        ui.clear(content);
        if (SM.notificationUI.stale(page.evaluated_at)) content.append(ui.alert("This snapshot may be stale. Refresh tasks before acting.", "info"));
        if (!page.items.length) content.append(ui.empty(filter === "history" ? "No resolved or expired notices yet." :
          filter === "unread" ? "No unread notifications." : filter === "urgent" ? "No urgent notifications." : "No notifications yet."));
        for (const item of page.items) content.append(SM.notificationUI.card(item, load));
        const paging = ui.el("div", {class: "notification-paging"},
          ui.el("span", {text: page.total ? `Showing ${page.offset + 1}–${page.offset + page.items.length} of ${page.total}` : "0 notifications"}));
        if (offset > 0) paging.append(ui.el("a", {class: "btn btn--secondary btn--small",
          href: `#/notifications?view=${filter}&offset=${Math.max(0, offset - 10)}`, text: "Previous"}));
        if (offset + page.items.length < page.total) paging.append(ui.el("a", {class: "btn btn--secondary btn--small",
          href: `#/notifications?view=${filter}&offset=${offset + 10}`, text: "Next"}));
        content.append(paging);
        await SM.notificationUI.refreshBell();
      } catch (error) {
        if (token === serial) ui.clear(content).append(ui.alert(error.message), ui.el("p", {text: "Select Refresh tasks or reopen this page to retry."}));
      } finally {
        if (token === serial) content.setAttribute("aria-busy", "false");
      }
    };
    refresh.addEventListener("click", async () => {
      refresh.disabled = true;
      ui.clear(feedback);
      try {
        const result = await SM.api.post("/notifications/evaluate");
        feedback.append(ui.alert(`Evaluated ${ui.formatDateTime(result.evaluated_at)}. ${result.created} new, ${result.updated} updated, ${result.transitioned} transitioned.`, "info"));
        if (offset) { location.hash = `#/notifications?view=${filter}`; return; }
        await load();
      } catch (error) { feedback.append(ui.alert(error.message)); }
      finally { refresh.disabled = false; }
    });
    markAll.addEventListener("click", async () => {
      markAll.disabled = true;
      try { await SM.api.post("/notifications/mark-all-read"); await load(); }
      catch (error) { ui.clear(feedback).append(ui.alert(error.message)); }
      finally { markAll.disabled = false; }
    });
    this.refreshCurrent = load;
    await load();
  },
};
