// StockMate API client wrapper.
// All frontend code talks to the backend exclusively through these helpers,
// so the API contract stays in one place (mobile-ready, decision D1).

const StockMateAPI = {
  baseUrl: "/api/v1",

  async get(path) {
    const response = await fetch(this.baseUrl + path, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new Error(`API error ${response.status}: ${await response.text()}`);
    }
    return response.json();
  },

  getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  },

  // Mutating requests must echo the CSRF cookie in this header
  // (double-submit pattern, see backend/app/auth/csrf.py).
  async post(path, body) {
    const response = await fetch(this.baseUrl + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRF-Token": this.getCookie("stockmate_csrf") || "",
      },
      credentials: "same-origin",
      body: JSON.stringify(body ?? {}),
    });
    if (!response.ok) {
      throw new Error(`API error ${response.status}: ${await response.text()}`);
    }
    return response.json();
  },
};

window.StockMateAPI = StockMateAPI;
