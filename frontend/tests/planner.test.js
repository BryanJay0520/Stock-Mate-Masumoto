const test = require("node:test");
const assert = require("node:assert/strict");
globalThis.window = globalThis;
require("../js/ui.js");
require("../js/pages/forecast.js");
require("../js/pages/planner.js");

test("planner urgency and pending verification keep distinct warning labels", () => {
  assert.deepEqual(SM.plannerUI.statusInfo("urgent_shortage_risk"), {label: "Urgent Shortage Risk", kind: "critical"});
  assert.equal(SM.plannerUI.statusInfo("verify_pending_order").label, "Verify Pending Order");
  assert.equal(SM.plannerUI.statusInfo("no_additional_order_needed").kind, "safe");
});

test("potential package advice requires review rather than unconditional ordering", () => {
  const text = SM.plannerUI.purchaseText({definitive: false, purchasing: {
    recommended_quantity: "180.000", recommended_package_count: "3.000", package_name: "Box"}}, "pc");
  assert.equal(text, "Potential requirement — review first: 3 × Box · 180 pc");
});

test("explicit zero advice is displayed separately from unknown quantity", () => {
  assert.equal(SM.plannerUI.purchaseText({definitive: true, purchasing: {
    recommended_quantity: "0.000", recommended_package_count: "0.000", package_name: "Box"}}, "pc"), "Calculated advice: 0 × Box · 0 pc");
  assert.match(SM.plannerUI.purchaseText({purchasing: {recommended_quantity: null}}, "pc"), /awaiting configuration or manual review/);
});

test("fractional base-unit purchasing displays exact stock units without inventing packages", () => {
  assert.equal(SM.plannerUI.purchaseText({definitive: true, purchasing: {
    recommended_quantity: "1.125", recommended_package_count: null}}, "pack"), "Calculated advice: 1.125 pack");
});
