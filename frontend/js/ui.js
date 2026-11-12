// UI helpers. Dynamic values are rendered ONLY through textContent /
// createElement — never innerHTML with API data — so stored XSS cannot
// execute even if a name or note contains markup.

window.SM = window.SM || {};

SM.ui = {
  activeModals: new Set(),

  dismissModals() {
    [...this.activeModals].reverse().forEach(destroy => destroy());
  },
  // el("td", { class: "x", "data-label": "Qty", text: "5" }, child1, ...)
  el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined) continue;
      if (key === "text") {
        node.textContent = String(value);
      } else if (key === "class") {
        node.className = value;
      } else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === "checked" || key === "disabled" || key === "selected" || key === "required") {
        node[key] = Boolean(value);
      } else {
        node.setAttribute(key, String(value));
      }
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  },

  clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  },

  badge(text, kind, title) {
    return this.el("span", { class: `badge badge--${kind}`, text, title: title || null });
  },

  loading(message = "Loading…") {
    return this.el("div", { class: "loading", text: message });
  },

  empty(message) {
    return this.el("div", { class: "empty-state", text: message });
  },

  alert(message, kind = "error") {
    return this.el("div", { class: `alert alert--${kind}`, role: "alert", text: message });
  },

  toast(message, kind = "success") {
    const container = document.getElementById("toasts");
    [...container.children].filter(node => node.textContent === message).forEach(node => node.remove());
    while (container.children.length >= 2) container.firstElementChild.remove();
    const toast = this.el("div", { class: `toast toast--${kind}`, text: message });
    container.append(toast);
    setTimeout(() => toast.remove(), 5000);
  },

  setBusy(button, busy, busyLabel = "Working…") {
    if (busy) {
      button.dataset.label = button.textContent;
      button.textContent = busyLabel;
      button.disabled = true;
    } else {
      button.textContent = button.dataset.label || button.textContent;
      button.disabled = false;
    }
  },

  // Decimal strings arrive like "240.000" — trim zeros with string math,
  // never parseFloat, so displayed quantities stay exact (decision D4).
  formatQty(value) {
    if (value === null || value === undefined) return "—";
    let text = String(value);
    if (text.includes(".")) {
      text = text.replace(/0+$/, "").replace(/\.$/, "");
    }
    return text === "" || text === "-" ? "0" : text;
  },

  // Signed, color-coded quantity delta used by dashboard and history.
  delta(quantityDelta) {
    const millis = this.toMillis(quantityDelta);
    return this.el("span", {
      class: millis !== null && millis < 0 ? "qty-negative" : "qty-positive",
      text: (millis !== null && millis > 0 ? "+" : "") + this.formatQty(quantityDelta),
    });
  },

  formatDateTime(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  },

  formatDate(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  },

  // Format a UTC instant in a specific IANA timezone (schedule rules store
  // their own timezone, so display must not depend on the browser's).
  formatInTz(iso, timeZone) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const options = {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    };
    if (timeZone) {
      options.timeZone = timeZone;
      options.timeZoneName = "short";
    }
    try {
      return date.toLocaleString(undefined, options);
    } catch (_error) {
      return date.toLocaleString(undefined); // unknown timezone: fall back
    }
  },

  // Format a local calendar date ("2026-11-16") without timezone shifting.
  formatDateOnly(isoDate, weekday) {
    if (!isoDate) return "—";
    const [year, month, day] = isoDate.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    const text = date.toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
    });
    return weekday ? `${weekday}, ${text}` : text;
  },

  // Client-side preview math uses integer thousandths to avoid float errors.
  // The backend remains authoritative; this is informational only (D4).
  toMillis(value) {
    const text = String(value ?? "0").trim();
    if (!/^-?\d+(\.\d{1,3})?$/.test(text)) return null;
    const negative = text.startsWith("-");
    const [intPart, fracPart = ""] = text.replace("-", "").split(".");
    const millis = Number(intPart) * 1000 + Number(fracPart.padEnd(3, "0"));
    return negative ? -millis : millis;
  },

  fromMillis(millis) {
    const negative = millis < 0;
    const abs = Math.abs(millis);
    const whole = Math.floor(abs / 1000);
    const frac = String(abs % 1000).padStart(3, "0").replace(/0+$/, "");
    return (negative ? "-" : "") + whole + (frac ? "." + frac : "");
  },

  // Modal dialog. Returns a close function. Focus is moved into the dialog.
  modal({ title, body, actions = [], wide = false, beforeClose = () => true, onClose = () => {} }) {
    this.clear(document.getElementById("toasts"));
    const previousFocus = document.activeElement;
    const overlay = this.el("div", { class: "modal-overlay" });
    const dialog = this.el("div", {
      class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title,
    });
    if (wide) dialog.style.maxWidth = "760px";
    dialog.append(this.el("h2", { class: "modal__title", text: title }));
    dialog.append(body);
    let removed = false;
    const destroy = () => {
      if (removed) return;
      removed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      this.activeModals.delete(destroy);
      document.getElementById("app").inert = Boolean(document.querySelector(".modal-overlay"));
      onClose();
      if (previousFocus?.isConnected) previousFocus.focus();
    };
    const close = () => { if (beforeClose()) destroy(); };
    this.activeModals.add(destroy);
    if (actions.length) {
      dialog.append(this.el("div", { class: "button-row" }, actions));
    }
    overlay.append(dialog);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });
    const onKey = (event) => {
      if (overlay !== document.querySelector("#modals .modal-overlay:last-child")) return;
      if (event.key === "Escape") close();
      if (event.key === "Tab") {
        const focusable = [...dialog.querySelectorAll("input, select, textarea, button, [tabindex]")]
          .filter(node => !node.disabled && node.tabIndex >= 0 && node.getClientRects().length);
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    document.getElementById("modals").append(overlay);
    document.getElementById("app").inert = true;
    const firstInput = dialog.querySelector("input, select, textarea, button");
    if (firstInput) firstInput.focus();
    return close;
  },

  confirmDialog({ title, message, confirmLabel = "Confirm", danger = false }) {
    return new Promise((resolve) => {
      let close;
      let approved = false;
      const cancelButton = this.el("button", {
        class: "btn btn--secondary", text: "Cancel",
        onclick: () => close(),
      });
      const confirmButton = this.el("button", {
        class: danger ? "btn btn--danger" : "btn", text: confirmLabel,
        onclick: () => { approved = true; close(); },
      });
      close = this.modal({
        title,
        body: this.el("p", { text: message }),
        actions: [cancelButton, confirmButton],
        onClose: () => resolve(approved),
      });
    });
  },
};
