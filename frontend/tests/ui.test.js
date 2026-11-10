// Unit tests for the pure quantity/formatting helpers in frontend/js/ui.js.
// Run from the repository root with: node --test frontend/tests/
// No dependencies — uses the built-in Node test runner.

const test = require("node:test");
const assert = require("node:assert/strict");

// ui.js attaches to window.SM; shim just enough browser globals to load it.
globalThis.window = globalThis;
require("../js/ui.js");
const ui = globalThis.SM.ui;

test("toMillis parses decimal strings exactly", () => {
  assert.equal(ui.toMillis("240"), 240000);
  assert.equal(ui.toMillis("240.000"), 240000);
  assert.equal(ui.toMillis("5.5"), 5500);
  assert.equal(ui.toMillis("0.001"), 1);
  assert.equal(ui.toMillis("0.02"), 20);
  assert.equal(ui.toMillis("-35"), -35000);
  assert.equal(ui.toMillis(null), 0); // nullish coalesces to "0"
});

test("toMillis rejects invalid or over-precise input", () => {
  assert.equal(ui.toMillis(""), null); // empty is invalid, not zero
  assert.equal(ui.toMillis("abc"), null);
  assert.equal(ui.toMillis("1.2345"), null); // max 3 decimals (Decimal(14,3))
  assert.equal(ui.toMillis("1.2.3"), null);
  assert.equal(ui.toMillis("1,5"), null);
});

test("fromMillis is the exact inverse of toMillis", () => {
  for (const value of ["240", "5.5", "0.001", "0.02", "-35", "0"]) {
    assert.equal(ui.fromMillis(ui.toMillis(value)), value);
  }
});

test("preview math: used stock deducts, remaining stock reconciles", () => {
  const current = ui.toMillis("100.000");
  const used = ui.toMillis("35");
  assert.equal(ui.fromMillis(current - used), "65");
  const counted = ui.toMillis("45");
  assert.equal(ui.fromMillis(counted - current), "-55"); // adjustment
  assert.equal(ui.fromMillis(counted), "45"); // new balance
});

test("formatQty trims trailing zeros without float conversion", () => {
  assert.equal(ui.formatQty("240.000"), "240");
  assert.equal(ui.formatQty("5.500"), "5.5");
  assert.equal(ui.formatQty("0.020"), "0.02");
  assert.equal(ui.formatQty("8"), "8");
  assert.equal(ui.formatQty(null), "—");
  assert.equal(ui.formatQty("0.000"), "0");
});

