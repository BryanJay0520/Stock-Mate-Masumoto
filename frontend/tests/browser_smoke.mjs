// StockMate browser smoke tests — REAL rendered-application testing.
//
// Zero dependencies: drives headless Chrome/Edge through the DevTools
// Protocol using Node's built-in fetch + WebSocket (Node >= 22).
//
// Prerequisites:
//   1. Backend running with a FRESHLY seeded demo database, e.g. on port 8765:
//        STOCKMATE_DATABASE_URL=... python -m app.seed
//        STOCKMATE_DATABASE_URL=... python -m uvicorn app.main:app --port 8765
//      The suite mutates data; reseed before every full run.
//   2. Chrome or Edge installed (set CHROME_PATH to override).
//
// Run from the repo root:
//   node frontend/tests/browser_smoke.mjs
//   STOCKMATE_TEST_URL=http://127.0.0.1:8765 node frontend/tests/browser_smoke.mjs
//
// Screenshots are written to a temp directory (printed at the end) and are
// never committed. This file is intentionally NOT matched by the
// "frontend/tests/*.test.js" glob used for offline unit tests.

import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.env.STOCKMATE_TEST_URL || "http://127.0.0.1:8765";
const CHROME = process.env.CHROME_PATH
  || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const CDP_PORT = 9223;
const ARTIFACTS = mkdtempSync(join(tmpdir(), "stockmate-browser-"));
const ADMIN = { username: "admin", password: "demo-admin-password" };
const STAFF = { username: "staff", password: "demo-staff-password" };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
let chrome;
let ws;
let nextId = 1;
const pending = new Map();

// --- Tiny CDP client --------------------------------------------------------

async function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}

async function evaluate(expression) {
  const response = await send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (response.result.exceptionDetails) {
    throw new Error("Page JS error: " + JSON.stringify(response.result.exceptionDetails));
  }
  return response.result.result.value;
}

async function waitFor(expression, timeoutMs = 8000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(`Boolean(${expression})`)) return true;
    await sleep(150);
  }
  throw new Error(`Timed out waiting for: ${expression}`);
}

async function navigate(path) {
  await evaluate(`location.hash = ${JSON.stringify("#" + path)}`);
  await sleep(250);
}

async function setViewport(width, height = 900) {
  await send("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: width < 500,
  });
  await sleep(150);
}

async function screenshot(name) {
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(join(ARTIFACTS, `${name}.png`), Buffer.from(shot.result.data, "base64"));
}

async function login(credentials) {
  await evaluate(`location.hash = "#/login"; location.reload()`);
  await waitFor(`document.getElementById("login-username")`);
  await evaluate(`(() => {
    document.getElementById("login-username").value = ${JSON.stringify(credentials.username)};
    document.getElementById("login-password").value = ${JSON.stringify(credentials.password)};
    document.querySelector(".login-card button.btn").click();
    })()`);
  await waitFor(`document.getElementById("shell").classList.contains("is-visible")`, 10000);
}

async function logout() {
  await evaluate(`document.getElementById("logout-button").click()`);
  await waitFor(`document.getElementById("login-username")`);
}

async function test(name, fn) {
  try {
    await fn();
    results.push({ name, status: "PASS" });
    console.log(`PASS ${name}`);
  } catch (error) {
    results.push({ name, status: "FAIL", error: String(error.message || error) });
    console.log(`FAIL ${name}: ${error.message || error}`);
    try { await screenshot("FAIL-" + name.replace(/\W+/g, "-").slice(0, 60)); } catch { /* ignore */ }
  }
}

function assert(condition, message) {
  if (!condition) throw new Error(message || "Assertion failed");
}

async function assertText(selector, expected, message) {
  const text = await evaluate(`document.querySelector(${JSON.stringify(selector)})?.textContent || ""`);
  assert(text.includes(expected), message || `Expected "${expected}" in ${selector}, got: ${text.slice(0, 300)}`);
}

async function apiAsSession(path) {
  // Uses the browser's current session cookie, exactly like the SPA does.
  return evaluate(`fetch("${BASE}/api/v1${path}", {credentials: "same-origin"}).then(r => r.json())`);
}

// --- Boot -------------------------------------------------------------------

async function boot() {
  const profile = mkdtempSync(join(tmpdir(), "stockmate-chrome-"));
  chrome = spawn(CHROME, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--window-size=1440,900",
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: "ignore" });

  let version = null;
  for (let i = 0; i < 60 && !version; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      if (res.ok) version = await res.json();
    } catch { /* not ready */ }
    if (!version) await sleep(300);
  }
  if (!version) throw new Error("Chrome CDP did not start");
  console.log(`Browser: ${version.Browser}`);
  console.log(`Backend: ${BASE}`);

  const target = await (await fetch(
    `http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: "PUT" },
  )).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg);
    }
  };
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Network.enable");
  await setViewport(1440);
  await evaluate(`location.href = ${JSON.stringify(BASE + "/")}`);
  await waitFor(`document.getElementById("login-username")`);
}

// --- Test scenarios -----------------------------------------------------------

async function authTests() {
  await test("login page renders with form", async () => {
    await assertText(".brand", "StockMate");
    assert(await evaluate(`Boolean(document.getElementById("login-password"))`), "password field missing");
  });

  await test("wrong password shows a clear error", async () => {
    await evaluate(`(() => {
      document.getElementById("login-username").value = "admin";
      document.getElementById("login-password").value = "wrong-password";
      document.querySelector(".login-card button.btn").click();
    })()`);
    await waitFor(`document.querySelector(".login-card .alert--error")`);
    await assertText(".login-card .alert--error", "Invalid username or password");
  });

  await test("admin login loads the dashboard with real data", async () => {
    await login(ADMIN);
    await waitFor(`document.querySelectorAll(".stat-card").length === 4`);
    const ingredients = await evaluate(`(() => {
      return [...document.querySelectorAll(".stat-card")]
        .find(c => c.textContent.includes("Active Ingredients"))
        .querySelector(".stat-card__value").textContent
    })()`);
    assert(ingredients === "8", `expected 8 active ingredients, got ${ingredients}`);
    await waitFor(`document.querySelectorAll(".card .table tbody tr").length > 0`);
    await screenshot("dashboard-1440");
  });
}

async function inventoryTests() {
  await test("inventory lists 8 ingredients and filters by search", async () => {
    await navigate("/inventory");
    await waitFor(`document.querySelectorAll("#view tbody tr").length === 8`);
    await evaluate(`(() => {
      const search = document.querySelector("#view input[type=search]");
      search.value = "noodles";
      search.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await waitFor(`document.querySelectorAll("#view tbody tr").length === 2`);
    await assertText("#view", "Korean Noodles");
    await assertText("#view", "Chinese Noodles");
  });

  await test("availability and confidence badges are separate", async () => {
    await evaluate(`(() => {
      const search = document.querySelector("#view input[type=search]");
      search.value = "";
      search.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await waitFor(`document.querySelectorAll("#view tbody tr").length === 8`);
    const badges = await evaluate(`(() => {
      return [...document.querySelectorAll("#view tbody tr:first-child .badge")].map(b => b.textContent)
    })()`);
    assert(badges.some((b) => /Safe|Low stock|Critical|No data/.test(b)), "no availability badge");
    assert(badges.some((b) => /confidence/.test(b)), "no separate confidence badge");
  });

  await test("used stock entry updates balance end-to-end", async () => {
    await navigate("/entry");
    await waitFor(`document.getElementById("entry-ingredient")`);
    await evaluate(`(() => {
      const select = document.getElementById("entry-ingredient");
      select.value = [...select.options].find(o => o.text.includes("Korean Noodles")).value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      const qty = document.getElementById("entry-quantity");
      qty.value = "10";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "230");
    await evaluate(`document.querySelector("#view form button.btn[type=submit]").click()`);
    await waitFor(`document.querySelector("#view .alert--success")`);
    await assertText("#view .alert--success", "new balance 230");
    const stock = await apiAsSession("/inventory/stock");
    const noodles = stock.find((r) => r.ingredient_name === "Korean Noodles");
    assert(noodles.current_quantity === "230.000", `API balance ${noodles.current_quantity}`);
  });

  await test("remaining stock entry reconciles and verifies the count", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll(".mode-toggle__option")].find(b => b.textContent.includes("Remaining Stock")).click();
      const qty = document.getElementById("entry-quantity");
      qty.value = "200";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "-30", "preview should show -30 adjustment");
    await evaluate(`document.querySelector("#view form button.btn[type=submit]").click()`);
    await waitFor(`document.querySelector("#view .alert--success")`);
    await assertText("#view .alert--success", "new balance 200");
    const stock = await apiAsSession("/inventory/stock");
    const noodles = stock.find((r) => r.ingredient_name === "Korean Noodles");
    assert(noodles.current_quantity === "200.000", `API balance ${noodles.current_quantity}`);
    assert(noodles.confidence === "high", "physical count should set high confidence");
  });

  await test("double-click submits only one transaction", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll(".mode-toggle__option")].find(b => b.textContent.includes("Used Stock")).click();
      const qty = document.getElementById("entry-quantity");
      qty.value = "5";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await evaluate(`(() => {
      const button = document.querySelector("#view form button.btn[type=submit]");
      button.click();
      button.click(); // second click must be ignored while pending
    })()`);
    await waitFor(`document.querySelector("#view .alert--success")`);
    const history = await apiAsSession("/inventory/transactions?ingredient_id="
      + (await apiAsSession("/inventory/stock")).find((r) => r.ingredient_name === "Korean Noodles").ingredient_id
      + "&type=consumption&limit=50");
    const fiveUsed = history.filter((t) => t.quantity_delta === "-5.000");
    assert(fiveUsed.length === 1, `expected exactly one -5 entry, got ${fiveUsed.length}`);
  });

  await test("large adjustment requires the confirmation dialog", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll(".mode-toggle__option")].find(b => b.textContent.includes("Remaining Stock")).click();
      const qty = document.getElementById("entry-quantity");
      qty.value = "900";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#view form button.btn[type=submit]").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")`);
    await assertText(".modal-overlay", "unusually large");
    await evaluate(`[...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent.includes("Yes, record it")).click()`);
    await waitFor(`document.querySelector("#view .alert--success")`);
    await assertText("#view .alert--success", "new balance 900");
  });
}

async function supplierTests() {
  await test("admin can create a supplier", async () => {
    await navigate("/suppliers");
    await waitFor(`document.querySelectorAll("#view tbody tr").length >= 3`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Add Supplier").click()`);
    await waitFor(`document.getElementById("supplier-name")`);
    await evaluate(`(() => {
      document.getElementById("supplier-name").value = "Browser Test Foods";
      document.getElementById("supplier-contact_name").value = "Test Person";
      [...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Save Supplier").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Supplier created")`);
    await waitFor(`document.querySelector("#view").textContent.includes("Browser Test Foods")`);
  });

  await test("archive is blocked for a primary supplier, with the reason shown", async () => {
    // Buttons are matched by TEXT (their order changes as features grow).
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Tokyo Noodle Co."));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Archive").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")`);
    await evaluate(`[...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Archive").click()`);
    await waitFor(`document.querySelector(".modal-overlay .alert--error")`);
    await assertText(".modal-overlay .alert--error", "Korean Noodles");
    await evaluate(`document.querySelector(".modal-overlay").remove()`);
  });

  await test("archive and reactivate work for an unassigned supplier", async () => {
    const clickRowButton = (rowText, buttonText) => `
      [...document.querySelectorAll("#view tbody tr")]
        .filter(r => r.textContent.includes(${JSON.stringify(rowText)}))
        .flatMap(r => [...r.querySelectorAll("button")])
        .find(b => b.textContent === ${JSON.stringify(buttonText)}).click();
    `;
    await evaluate(clickRowButton("Browser Test Foods", "Archive"));
    await waitFor(`document.querySelector(".modal-overlay")`);
    await evaluate(`[...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Archive").click()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Supplier archived")`);
    await evaluate(clickRowButton("Browser Test Foods", "Reactivate"));
    await waitFor(`document.querySelector(".modal-overlay")`);
    await evaluate(`[...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Reactivate").click()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Supplier reactivated")`);
  });
}

async function scheduleTests() {
  // The schedules panel is a modal; forms open a SECOND modal on top, so
  // form interactions always target the last overlay.
  const overlay = `[...document.querySelectorAll(".modal-overlay")].pop()`;

  await test("admin sees seeded rules, instructions, and real opportunities", async () => {
    await navigate("/suppliers");
    await waitFor(`document.querySelectorAll("#view tbody tr").length >= 3`);
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Tokyo Noodle Co."));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Schedules").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")?.textContent.includes("Delivery Schedule Rules")`);
    // Three seeded rules: Mon + Fri supplier-wide, Wed for Korean Noodles.
    await assertText(".modal-overlay", "Korean Noodles only");
    await assertText(".modal-overlay", "Monday");
    await assertText(".modal-overlay", "Friday");
    // General instructions and per-schedule special instructions.
    await assertText(".modal-overlay", "Preferred: Phone Call");
    await assertText(".modal-overlay", "Sunday morning");
    // Upcoming opportunities carry real calendar dates and UTC-derived cutoffs.
    const oppText = await evaluate(`(() => {
      const heading = [...document.querySelectorAll(".modal-overlay h3")]
        .find(h => h.textContent.includes("Upcoming Deliveries"));
      return heading ? heading.parentElement.textContent : "";
    })()`);
    assert(oppText.includes("order before"), "no cutoff shown in opportunities");
    assert(/\d{4}/.test(oppText), `no real calendar date in opportunities: ${oppText.slice(0, 200)}`);
    await screenshot("schedules-panel");
  });

  await test("admin can add a delivery schedule through the panel", async () => {
    await evaluate(`${overlay}.remove()`); // close the Tokyo panel
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Browser Test Foods"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Schedules").click();
    })()`);
    // The modal title matches immediately; wait until the BODY has loaded
    // (the Add button only exists after the async panel render).
    await waitFor(`(() => {
      const o = [...document.querySelectorAll(".modal-overlay")].pop();
      return o && [...o.querySelectorAll("button")].some(b => b.textContent === "Add Delivery Schedule");
    })()`);
    await evaluate(`(() => {
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Add Delivery Schedule").click();
    })()`);
    await waitFor(`document.getElementById("rule-weekday")`);
    await evaluate(`(() => {
      document.getElementById("rule-weekday").value = "6"; // Sunday
      document.getElementById("rule-cutoffDays").value = "1";
      document.getElementById("rule-cutoffTime").value = "09:00";
      document.getElementById("rule-reminderDays").value = "2";
      document.getElementById("rule-reminderTime").value = "18:30";
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Add Schedule").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Schedule added")`);
    // The panel reloaded: the new Sunday rule and a matching opportunity appear.
    await waitFor(`(() => {
      const panel = document.querySelector(".modal-overlay");
      return panel && panel.textContent.includes("Sunday");
    })()`);
    const oppText = await evaluate(`(() => {
      const heading = [...document.querySelectorAll(".modal-overlay h3")]
        .find(h => h.textContent.includes("Upcoming Deliveries"));
      return heading ? heading.parentElement.textContent : "";
    })()`);
    assert(oppText.includes("Sunday"), `new rule missing from opportunities: ${oppText.slice(0, 200)}`);
    await evaluate(`${overlay}.remove()`);
  });

  await test("schedule validation errors are shown in the form", async () => {
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Browser Test Foods"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Schedules").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")?.textContent.includes("Add Delivery Schedule")`);
    await evaluate(`(() => {
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Add Delivery Schedule").click();
    })()`);
    await waitFor(`document.getElementById("rule-weekday")`);
    // Sunday already exists for this supplier -> the server must reject it.
    await evaluate(`(() => {
      document.getElementById("rule-weekday").value = "6";
      document.getElementById("rule-cutoffDays").value = "2";
      document.getElementById("rule-cutoffTime").value = "10:00";
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Add Schedule").click();
    })()`);
    await waitFor(`(() => {
      const form = ${overlay};
      return form.querySelector(".alert--error") !== null;
    })()`);
    const errorText = await evaluate(`(() => {
      return ${overlay}.querySelector(".alert--error")?.textContent || "";
    })()`);
    assert(errorText.includes("already exists"), `expected conflict error, got: ${errorText}`);
    await evaluate(`${overlay}.remove()`); // close form
    await evaluate(`${overlay}.remove()`); // close panel
  });
}

async function staffScheduleTests() {
  await test("staff sees schedules read-only", async () => {
    await login(STAFF); // staffTests ended with a cleared session
    await navigate("/suppliers");
    await waitFor(`document.querySelector("#view").textContent.includes("Read-only access")`);
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Tokyo Noodle Co."));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Schedules").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")?.textContent.includes("Delivery Schedule Rules")`);
    await assertText(".modal-overlay", "Korean Noodles only");
    const adminControls = await evaluate(`(() => {
      const panel = document.querySelector(".modal-overlay");
      const labels = [...panel.querySelectorAll("button")].map(b => b.textContent);
      return labels.some(l => ["Add Delivery Schedule", "Edit", "Deactivate", "Add Exception"].includes(l));
    })()`);
    assert(!adminControls, "staff should not see schedule mutation controls");
    await evaluate(`document.querySelector(".modal-overlay").remove()`);
  });
}

async function orderTests() {
  await test("inventory shows seeded incoming stock (orders never touch available)", async () => {
    await navigate("/inventory");
    await waitFor(`document.querySelectorAll("#view tbody tr").length === 8`);
    const rowText = await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Korean Noodles"));
      return row ? row.textContent : "";
    })()`);
    assert(rowText.includes("100"), `no incoming qty for Korean Noodles: ${rowText.slice(0, 250)}`);
    assert(rowText.includes("Ordered — Not Received"), `no incoming badge: ${rowText.slice(0, 250)}`);
    assert(rowText.includes("1000"), `no projected qty (900 + 100): ${rowText.slice(0, 250)}`);
    // Server-side truth: available stays physical, incoming is separate.
    const stock = await apiAsSession("/inventory/stock");
    const noodles = stock.find((r) => r.ingredient_name === "Korean Noodles");
    assert(noodles.current_quantity === "900.000", `available ${noodles.current_quantity}`);
    assert(noodles.incoming_quantity === "100.000", `incoming ${noodles.incoming_quantity}`);
    assert(noodles.projected_quantity === "1000.000", `projected ${noodles.projected_quantity}`);
    // Draft orders (Dragon Noodle Works) must NOT count as incoming.
    const chinese = stock.find((r) => r.ingredient_name === "Chinese Noodles");
    assert(chinese.incoming_quantity === "0.000", `draft counted as incoming: ${chinese.incoming_quantity}`);
    await screenshot("inventory-incoming");
  });

  await test("dashboard lists pending deliveries, excluding drafts", async () => {
    await navigate("/dashboard");
    await waitFor(`document.querySelectorAll(".stat-card").length === 4`);
    await waitFor(`(() => {
      return [...document.querySelectorAll("#view h2")]
        .some(h => h.textContent === "Upcoming Deliveries");
    })()`);
    const cardText = await evaluate(`(() => {
      const heading = [...document.querySelectorAll("#view h2")]
        .find(h => h.textContent === "Upcoming Deliveries");
      return heading.parentElement.textContent;
    })()`);
    assert(cardText.includes("Tokyo Noodle Co."), `Tokyo order missing: ${cardText.slice(0, 300)}`);
    assert(cardText.includes("Masumoto Provisions"), `Masumoto order missing: ${cardText.slice(0, 300)}`);
    assert(!cardText.includes("Dragon Noodle Works"), "draft order leaked into deliveries");
    await screenshot("dashboard-deliveries");
  });

  await test("admin creates an order, marks it ordered, incoming updates", async () => {
    await navigate("/orders");
    await waitFor(`document.querySelector("#view").textContent.includes("Tokyo Noodle Co.")`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "New Order").click()`);
    await waitFor(`document.getElementById("order-supplier")`);
    await evaluate(`(() => {
      const supplier = document.getElementById("order-supplier");
      supplier.value = [...supplier.options].find(o => o.textContent.includes("Masumoto")).value;
      supplier.dispatchEvent(new Event("change", {bubbles: true}));
      const nextWeek = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
      document.getElementById("order-date").value = nextWeek;
    })()`);
    await sleep(300); // let the ingredient select refill for Masumoto
    await evaluate(`(() => {
      const overlay = document.querySelector(".modal-overlay");
      const ingredient = overlay.querySelector("select.select[aria-label=Ingredient]");
      ingredient.value = [...ingredient.options].find(o => o.textContent.includes("Eggs")).value;
      const qty = overlay.querySelector("input[aria-label=Quantity]");
      qty.value = "25";
      [...overlay.querySelectorAll("button")].find(b => b.textContent === "Create Order").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Draft order created")`);
    // Open the new draft (the Masumoto row with a Draft badge) and order it.
    await waitFor(`(() => {
      return [...document.querySelectorAll("#view tbody tr")]
        .some(r => r.textContent.includes("Masumoto Provisions") && r.textContent.includes("Draft"));
    })()`);
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Masumoto Provisions") && r.textContent.includes("Draft"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "View").click();
    })()`);
    await waitFor(`(() => {
      const o = [...document.querySelectorAll(".modal-overlay")].pop();
      return o && [...o.querySelectorAll("button")].some(b => b.textContent === "Mark Ordered");
    })()`);
    await evaluate(`(() => {
      const overlay = [...document.querySelectorAll(".modal-overlay")].pop();
      [...overlay.querySelectorAll("button")].find(b => b.textContent === "Mark Ordered").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("updated")`);
    // Eggs: 120 confirmed (seeded) + 25 newly ordered = 145 incoming.
    await navigate("/inventory");
    await waitFor(`document.querySelectorAll("#view tbody tr").length === 8`);
    const stock = await apiAsSession("/inventory/stock");
    const eggs = stock.find((r) => r.ingredient_name === "Eggs");
    assert(eggs.incoming_quantity === "145.000", `Eggs incoming ${eggs.incoming_quantity}`);
    const rowText = await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Eggs"));
      return row ? row.textContent : "";
    })()`);
    assert(rowText.includes("145"), `Eggs row missing incoming 145: ${rowText.slice(0, 250)}`);
    assert(rowText.includes("Confirmed"), `Eggs row missing Confirmed badge: ${rowText.slice(0, 250)}`);
  });
}

async function ingredientTests() {
  await test("admin can create an ingredient with an opening balance", async () => {
    await navigate("/ingredients");
    await waitFor(`document.querySelectorAll("#view tbody tr").length >= 8`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Add Ingredient").click()`);
    await waitFor(`document.getElementById("ingredient-name")`);
    await evaluate(`(() => {
      document.getElementById("ingredient-name").value = "Browser Spice";
      document.getElementById("ingredient-category").value = "Test";
      document.getElementById("ingredient-unit_id").value = "2"; // kg
      document.getElementById("ingredient-primary_supplier_id").value =
        [...document.getElementById("ingredient-primary_supplier_id").options]
          .find(o => o.text.includes("Browser Test Foods")).value;
      document.getElementById("ingredient-opening_quantity").value = "12.5";
      [...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Save Ingredient").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Ingredient created")`);
    const stock = await apiAsSession("/inventory/stock");
    const spice = stock.find((r) => r.ingredient_name === "Browser Spice");
    assert(spice && spice.current_quantity === "12.500", `opening balance wrong: ${JSON.stringify(spice)}`);
  });

  await test("ingredient catalog edit does not change the stock balance", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Browser Spice"))
        .querySelectorAll("button")[0].click(); // Edit
    })()`);
    await waitFor(`document.getElementById("ingredient-category")`);
    await evaluate(`(() => {
      document.getElementById("ingredient-category").value = "Spices";
      [...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Save Ingredient").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Ingredient updated")`);
    const stock = await apiAsSession("/inventory/stock");
    const spice = stock.find((r) => r.ingredient_name === "Browser Spice");
    assert(spice.current_quantity === "12.500", `balance changed by catalog edit: ${spice.current_quantity}`);
  });
}

async function correctionTests() {
  await test("correction is blocked after a newer physical count (D27 warning)", async () => {
    await navigate("/history");
    await waitFor(`document.getElementById("page-title").textContent === "Transaction History"
      && document.querySelectorAll("#view tbody tr").length > 0`);
    // The "used 10" entry is older than the "remaining 200/900" counts.
    const opened = await evaluate(`(() => {
      return (() => {
        const rows = [...document.querySelectorAll("#view tbody tr")];
        const row = rows.find(r => r.textContent.includes("Korean Noodles") && r.textContent.includes("-10"));
        if (!row) return "row not found";
        const button = [...row.querySelectorAll("button")].find(b => b.textContent === "Correct");
        if (!button) return "no correct button";
        button.click();
        return "opened";
      })()
    })()`);
    assert(opened === "opened", `could not open correction: ${opened}`);
    await waitFor(`document.getElementById("correction-quantity")`);
    await evaluate(`(() => {
      document.getElementById("correction-quantity").value = "5";
      [...document.querySelectorAll(".modal-overlay button")].find(b => b.textContent === "Submit Correction").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay .alert--error")`);
    await assertText(".modal-overlay .alert--error", "newer physical stock count");
    await evaluate(`document.querySelector(".modal-overlay").remove()`);
  });
}

async function staffOrderTests() {
  await test("staff sees purchase orders read-mostly, without cancel", async () => {
    // Already logged in as staff (staffScheduleTests ends on a staff session).
    assert(await evaluate(`Boolean(document.querySelector('.nav-link[data-route="orders"]'))`),
      "staff nav should include Purchase Orders");
    await navigate("/orders");
    await waitFor(`document.querySelector("#view").textContent.includes("Tokyo Noodle Co.")`);
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Tokyo Noodle Co."));
      [...row.querySelectorAll("button")].find(b => b.textContent === "View").click();
    })()`);
    await waitFor(`document.querySelector(".modal-overlay")?.textContent.includes("Purchase Order #")`);
    const labels = await evaluate(`(() => {
      const overlay = [...document.querySelectorAll(".modal-overlay")].pop();
      return [...overlay.querySelectorAll("button")].map(b => b.textContent);
    })()`);
    assert(!labels.includes("Cancel Order"), `staff should not see Cancel Order: ${labels}`);
    // Staff CAN advance the workflow (Mark Ordered / Confirm), per requirements.
    await evaluate(`document.querySelector(".modal-overlay").remove()`);
  });
}

async function responsiveTests() {
  for (const width of [1440, 1024, 768, 390, 360]) {
    await test(`responsive: no horizontal overflow at ${width}px`, async () => {
      await setViewport(width);
      for (const page of ["/dashboard", "/inventory", "/entry", "/suppliers"]) {
        await navigate(page);
        await waitFor(`document.querySelector("#view .card")`);
        await sleep(200);
        const overflow = await evaluate(`document.documentElement.scrollWidth - window.innerWidth`);
        assert(overflow <= 1, `${page} overflows by ${overflow}px at ${width}px`);
      }
      await screenshot(`inventory-${width}`);
      await navigate("/dashboard");
      await waitFor(`document.querySelector(".stat-grid")`);
      await screenshot(`dashboard-${width}`);
      // Menu button is mobile-only (regression: .btn overrode display:none).
      const menuDisplay = await evaluate(
        `getComputedStyle(document.getElementById("menu-button")).display`,
      );
      if (width > 900) {
        assert(menuDisplay === "none", `menu button should be hidden at ${width}px, got ${menuDisplay}`);
      } else {
        assert(menuDisplay !== "none", `menu button should be visible at ${width}px`);
      }
    });
  }
  await setViewport(1440);
}

async function staffTests() {
  await test("logout returns to the login page", async () => {
    await logout();
    await assertText(".brand", "StockMate");
  });

  await test("staff login hides admin-only controls", async () => {
    await login(STAFF);
    await navigate("/suppliers");
    // The staff read-only note only exists on the rendered suppliers page —
    // unlike the page title, it cannot match stale dashboard content.
    await waitFor(`document.querySelector("#view").textContent.includes("Read-only access")`);
    const hasAdd = await evaluate(`(() => {
      return [...document.querySelectorAll("#view button")].some(b => b.textContent === "Add Supplier")
    })()`);
    assert(!hasAdd, "staff should not see Add Supplier");
    await assertText("#view", "Read-only access");
    await navigate("/ingredients");
    await waitFor(`document.querySelector("#view").textContent.includes("Read-only access")`);
    const hasAddIngredient = await evaluate(`(() => {
      return [...document.querySelectorAll("#view button")].some(b => b.textContent === "Add Ingredient")
    })()`);
    assert(!hasAddIngredient, "staff should not see Add Ingredient");
  });

  await test("staff cannot record negative stock; the server error is shown", async () => {
    await navigate("/entry");
    await waitFor(`document.getElementById("entry-ingredient")`);
    await evaluate(`(() => {
      const select = document.getElementById("entry-ingredient");
      select.value = [...select.options].find(o => o.text.includes("Nori")).value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      const qty = document.getElementById("entry-quantity");
      qty.value = "99999";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#view form button.btn[type=submit]").click();
    })()`);
    await waitFor(`document.querySelector("#view .alert--error")`);
    await assertText("#view .alert--error", "negative");
  });

  await test("cleared session redirects protected pages to login", async () => {
    await send("Network.clearBrowserCookies");
    await evaluate(`location.reload()`);
    await waitFor(`document.getElementById("login-username")`);
    await assertText(".brand", "StockMate");
  });
}

// --- Main -------------------------------------------------------------------

try {
  await boot();
  await authTests();
  await inventoryTests();
  await supplierTests();
  await scheduleTests();
  await orderTests();
  await ingredientTests();
  await correctionTests();
  await responsiveTests();
  await staffTests();
  await staffScheduleTests();
  await staffOrderTests();
} catch (error) {
  console.error("HARNESS ERROR:", error);
  results.push({ name: "harness", status: "FAIL", error: String(error) });
} finally {
  if (ws) ws.close();
  if (chrome) chrome.kill();
}

const failed = results.filter((r) => r.status === "FAIL");
console.log(`\nScreenshots: ${ARTIFACTS}`);
console.log(`RESULT: ${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
