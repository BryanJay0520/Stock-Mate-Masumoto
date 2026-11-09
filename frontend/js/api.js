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

  // POST/CSRF helpers are added in Phase 1 Step 3 together with auth.
};

window.StockMateAPI = StockMateAPI;
