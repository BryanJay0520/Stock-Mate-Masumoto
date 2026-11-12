const test = require("node:test");
const assert = require("node:assert/strict");
globalThis.window = globalThis;
require("../js/ui.js");
require("../js/pages/notifications.js");

test("notification center keeps all six operational categories distinct", () => {
  assert.deepEqual(Object.keys(SM.notificationUI.labels).sort(), [
    "daily_count", "forecast_shortage", "order_cutoff", "pending_delivery", "supplier_contact", "verify_pending_order",
  ]);
});

test("unevaluated and old notification snapshots are labeled stale", () => {
  assert.match(SM.notificationUI.evaluatedLabel(null), /not been evaluated yet/);
  assert.equal(SM.notificationUI.stale(null), true);
  assert.equal(SM.notificationUI.stale(new Date(Date.now() - 61 * 60 * 1000).toISOString()), true);
  assert.equal(SM.notificationUI.stale(new Date().toISOString()), false);
});

test("notification navigation accepts only internal workflow routes", () => {
  assert.equal(SM.notificationUI.safeTarget("#/orders?order_id=42"), "#/orders?order_id=42");
  assert.equal(SM.notificationUI.safeTarget("#/entry"), "#/entry");
  assert.equal(SM.notificationUI.safeTarget("javascript:alert(1)"), "#/notifications");
  assert.equal(SM.notificationUI.safeTarget("https://example.com"), "#/notifications");
});
