// StockMate API client. Every backend call goes through here so the API
// contract, CSRF handling, and error shape live in exactly one place
// (mobile-ready, decision D1). Session credentials travel only in the
// HttpOnly cookie; nothing sensitive is stored in localStorage.

window.SM = window.SM || {};

SM.ApiError = class extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
};

SM.api = {
  baseUrl: "/api/v1",

  // Set by auth.js: called on any 401 so the app can return to login.
  onUnauthorized: null,

  getCookie(name) {
    const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[1]) : null;
  },

  async request(method, path, body) {
    const options = {
      method,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      // Mutating requests echo the CSRF cookie (double-submit pattern,
      // see backend/app/auth/csrf.py).
      options.headers["X-CSRF-Token"] = this.getCookie("stockmate_csrf") || "";
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(this.baseUrl + path, options);
    } catch {
      throw new SM.ApiError(
        0, "network_error",
        "Could not reach the server. Check your connection and try again."
      );
    }

    if (response.status === 401 && path !== "/auth/login") {
      if (this.onUnauthorized) this.onUnauthorized();
      throw new SM.ApiError(401, "unauthorized", "Please log in again.");
    }

    if (!response.ok) {
      let code = "api_error";
      let message = `Request failed (${response.status}).`;
      try {
        const data = await response.json();
        if (data && data.error) {
          code = data.error.code || code;
          message = data.error.message || message;
        } else if (data && data.detail) {
          message = typeof data.detail === "string"
            ? data.detail
            : "Some values are invalid. Please check the form.";
        }
      } catch { /* non-JSON error body: keep the generic message */ }
      throw new SM.ApiError(response.status, code, message);
    }
    return response.json();
  },

  get(path) {
    return this.request("GET", path);
  },

  post(path, body) {
    return this.request("POST", path, body ?? {});
  },

  patch(path, body) {
    return this.request("PATCH", path, body ?? {});
  },
};
