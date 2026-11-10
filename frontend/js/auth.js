// Session state. The session itself lives in the HttpOnly cookie managed by
// the backend; this module only tracks WHO is logged in (id, username, role)
// in memory — never in localStorage.

window.SM = window.SM || {};

SM.auth = {
  user: null,

  isAdmin() {
    return Boolean(this.user && this.user.role === "admin");
  },

  async fetchUser() {
    try {
      const data = await SM.api.get("/auth/me");
      this.user = data.user;
    } catch {
      this.user = null;
    }
    return this.user;
  },

  async login(username, password) {
    const data = await SM.api.post("/auth/login", { username, password });
    this.user = data.user;
    return this.user;
  },

  async logout() {
    try {
      await SM.api.post("/auth/logout");
    } catch {
      // Even if the request fails, drop local state and return to login.
    } finally {
      this.user = null;
    }
  },
};

// Any 401 from the API means the session expired or was revoked:
// clear local state and send the user back to the login screen.
SM.api.onUnauthorized = () => {
  SM.auth.user = null;
  SM.app.showLogin("Your session has expired. Please log in again.");
};
