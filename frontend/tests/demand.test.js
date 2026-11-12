const test = require("node:test");
const assert = require("node:assert/strict");
globalThis.window = globalThis;
require("../js/ui.js");
require("../js/pages/ingredient-demand.js");
const demand = SM.demandUI;

test("demand quantities distinguish unknown from explicit zero", () => {
  assert.equal(demand.quantity(""), null);
  assert.equal(demand.quantity("0"), "0");
  assert.equal(demand.weekly(["0", "0", "0", "0", "0", "0", null]), null);
  assert.equal(demand.weekly(Array(7).fill("0")), "0");
});

test("weekly preview is decimal safe and the Korean example totals 320", () => {
  assert.equal(demand.weekly(["40", "40", "40", "40", "40", "60", "60"]), "320");
  assert.equal(demand.weekly(["0.125", "0.125", "0.125", "0.125", "0.125", "0.001", "0.001"]), "0.627");
});

test("invalid and excessive demand quantities are rejected", () => {
  for (const value of ["-1", "abc", "NaN", "Infinity", "1.0001", "100000000000"]) {
    assert.throws(() => demand.quantity(value));
  }
});
