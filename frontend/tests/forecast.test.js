const test = require("node:test");
const assert = require("node:assert/strict");
globalThis.window = globalThis;
require("../js/ui.js");
require("../js/pages/forecast.js");

test("forecast status labels preserve depletion, shortage and unknown distinctions", () => {
  const ui = SM.forecastUI;
  assert.deepEqual(ui.statusInfo("depleted"), {label: "Depleted", kind: "critical"});
  assert.deepEqual(ui.statusInfo("shortage"), {label: "Shortage", kind: "critical"});
  assert.deepEqual(ui.statusInfo("unknown"), {label: "Unknown", kind: "neutral"});
  assert.equal(ui.statusInfo("low").kind, "warning");
  assert.equal(ui.statusInfo("enough").kind, "safe");
});

test("forecast quantities show unknown separately from zero and negative projections", () => {
  const quantity = SM.forecastUI.quantity;
  assert.equal(quantity(null, "pc"), "Unknown");
  assert.equal(quantity("0.000", "pc"), "0 pc");
  assert.equal(quantity("-20.000", "pc"), "-20 pc");
  assert.equal(quantity("0.125", "pack"), "0.125 pack");
});

test("positive closing stock still displays a pre-delivery shortage warning", () => {
  assert.equal(SM.forecastUI.riskText({closing_balance: "100.000", pre_delivery_shortage_risk: true,
    pre_delivery_shortage: "20.000"}, "pc"), "Pre-delivery shortage risk: 20 pc");
});

test("no pre-delivery warning is invented for zero or unknown risk", () => {
  assert.equal(SM.forecastUI.riskText({pre_delivery_shortage_risk: false, pre_delivery_shortage: "0.000"}, "pc"), null);
  assert.equal(SM.forecastUI.riskText({pre_delivery_shortage_risk: false, pre_delivery_shortage: null}, "pc"), null);
});
