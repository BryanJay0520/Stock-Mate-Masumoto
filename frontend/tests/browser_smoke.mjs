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
let chromeStderr = "";
let ws;
let nextId = 1;
const pending = new Map();
let forecastRequests = 0;

// --- Tiny CDP client --------------------------------------------------------

async function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP ${method} timed out (Chrome exit: ${chrome.exitCode ?? "running"}): ${chromeStderr}`));
    }, 10000);
    pending.set(id, {
      resolve: (value) => { clearTimeout(timer); resolve(value); },
      reject: (error) => { clearTimeout(timer); reject(error); },
    });
    try {
      ws.send(JSON.stringify({ id, method, params }));
    } catch (error) {
      pending.delete(id);
      clearTimeout(timer);
      reject(error);
    }
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
    "--headless=new", "--no-sandbox", "--disable-gpu", "--in-process-gpu", "--disable-gpu-sandbox",
    "--disable-features=Vulkan,SkiaGraphite", "--no-first-run", "--window-size=1440,900",
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  chrome.stderr.on("data", (chunk) => {
    chromeStderr = (chromeStderr + chunk.toString()).slice(-2000);
  });

  let version = null;
  for (let i = 0; i < 60 && !version; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`);
      if (res.ok) version = await res.json();
    } catch { /* not ready */ }
    if (!version) await sleep(300);
  }
  if (!version) throw new Error(
    `Chrome CDP did not start (exit: ${chrome.exitCode ?? "running"}): ${chromeStderr}`,
  );
  console.log(`Browser: ${version.Browser}`);
  console.log(`Backend: ${BASE}`);

  const target = await (await fetch(
    `http://127.0.0.1:${CDP_PORT}/json/new?about:blank`, { method: "PUT" },
  )).json();
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(
      `Chrome CDP WebSocket timed out (Chrome exit: ${chrome.exitCode ?? "running"})`,
    )), 10000);
    ws.onopen = () => { clearTimeout(timer); resolve(); };
    ws.onerror = (error) => { clearTimeout(timer); reject(error); };
    ws.onclose = () => { clearTimeout(timer); reject(new Error("Chrome CDP WebSocket closed")); };
    if (chrome.exitCode !== null) {
      clearTimeout(timer);
      reject(new Error(`Chrome exited before CDP connected: ${chrome.exitCode}`));
    }
  });
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.method === "Network.requestWillBeSent" && msg.params.request.url.includes("/api/v1/forecast/calendar")) forecastRequests++;
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
      const method = document.getElementById("entry-count-method");
      method.value = "units";
      method.dispatchEvent(new Event("change", {bubbles: true}));
      const qty = document.getElementById("entry-quantity");
      qty.value = "10";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "170");
    await evaluate(`document.querySelector("#view form button.btn[type=submit]").click()`);
    await waitFor(`document.querySelector("#view .alert--success")?.textContent.includes("Usage recorded")`);
    await assertText("#view .alert--success", "new balance 170");
    const stock = await apiAsSession("/inventory/stock");
    const noodles = stock.find((r) => r.ingredient_name === "Korean Noodles");
    assert(noodles.current_quantity === "170.000", `API balance ${noodles.current_quantity}`);
  });

  await test("remaining stock entry reconciles and verifies the count", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll(".mode-toggle__option")].find(b => b.textContent.includes("Remaining Stock")).click();
      const qty = document.getElementById("entry-quantity");
      qty.value = "200";
      qty.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "+30", "preview should show +30 adjustment");
    await evaluate(`document.querySelector("#view form button.btn[type=submit]").click()`);
    await waitFor(`document.querySelector("#view .alert--success")?.textContent.includes("Physical count saved")`);
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`); // close the Tokyo panel
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`); // close form
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`); // close panel
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
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

async function receivingTests() {
  const overlay = `[...document.querySelectorAll(".modal-overlay")].pop()`;

  await test("partial receipt posts stock, keeps outstanding, updates order", async () => {
    await navigate("/orders");
    await waitFor(`document.querySelector("#view").textContent.includes("Masumoto Provisions")`);
    const before = (await apiAsSession("/inventory/stock"))
      .find((r) => r.ingredient_name === "Chashu");
    // Open the Masumoto confirmed order.
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Masumoto Provisions") && r.textContent.includes("Confirmed"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "View").click();
    })()`);
    await waitFor(`(() => {
      const o = ${overlay};
      return o && [...o.querySelectorAll("button")].some(b => b.textContent === "Receive Delivery");
    })()`);
    await evaluate(`(() => {
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Receive Delivery").click();
    })()`);
    await waitFor(`document.getElementById("receipt-date")`);
    await evaluate(`(() => {
      document.getElementById("receipt-staff-name").value = "   ";
      document.querySelector('input[aria-label="Received quantity for Chashu"]').value = "50";
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Confirm Receipt").click();
    })()`);
    await waitFor(`${overlay}.textContent.includes("Received By (Staff Name) is required.")`);
    await evaluate(`(() => {
      const overlay = ${overlay};
      const input = overlay.querySelector('input[aria-label="Received quantity for Chashu"]');
      input.value = "50";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      const rejected = overlay.querySelector('input[aria-label="Rejected quantity for Chashu"]');
      rejected.value = "2";
      rejected.dispatchEvent(new Event("input", {bubbles: true}));
      document.getElementById("receipt-notes").value = "Browser test partial delivery";
      document.getElementById("receipt-staff-name").value = "Kenji Browser";
      [...overlay.querySelectorAll("button")].find(b => b.textContent === "Confirm Receipt").click();
    })()`);
    // The safety confirmation dialog must appear and be confirmed.
    await waitFor(`(() => {
      const o = ${overlay};
      return o && o.textContent.includes("Post received stock?");
    })()`);
    await evaluate(`(() => {
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Confirm Receipt").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Delivery received")`);

    const after = (await apiAsSession("/inventory/stock"))
      .find((r) => r.ingredient_name === "Chashu");
    // Only the ACCEPTED 50 entered stock; the 2 rejected did not.
    assert(parseFloat(after.current_quantity) === parseFloat(before.current_quantity) + 50,
      `Chashu ${before.current_quantity} -> ${after.current_quantity}`);
    assert(after.incoming_quantity === "150.000", `outstanding incoming ${after.incoming_quantity}`);
    // Confidence is NOT refreshed by a receipt.
    assert(after.last_counted_at === before.last_counted_at, "receipt refreshed count confidence");

    // The rejected quantity is visible in the receipt record.
    const order = (await apiAsSession("/purchase-orders?status=partially_delivered"))[0];
    const receipts = await apiAsSession(`/purchase-orders/${order.id}/receipts`);
    assert(receipts[0].items[0].quantity_rejected === "2.000",
      `rejected qty not recorded: ${JSON.stringify(receipts[0].items[0])}`);

    await waitFor(`document.querySelector("#view").textContent.includes("Partially Delivered")`);
    await screenshot("order-partially-delivered");
  });

  await test("receipt history is shown with the responsible user", async () => {
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Masumoto Provisions") && r.textContent.includes("Partially Delivered"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "View").click();
    })()`);
    await waitFor(`(() => {
      const o = ${overlay};
      return o && o.textContent.includes("Delivery Receipts") && o.textContent.includes("Chashu: 50");
    })()`);
    await assertText(".modal-overlay", "Browser test partial delivery");
    await assertText(".modal-overlay", "Received By (self-reported): Kenji Browser");
    await assertText(".modal-overlay", "2 rejected");
    await assertText(".modal-overlay", "150 outstanding after this receipt");
    await assertText(".modal-overlay", "Posted");
    await setViewport(390);
    assert(await evaluate(`document.documentElement.scrollWidth <= window.innerWidth + 1`),
      "receipt history overflows on mobile");
    await screenshot("receipt-history-390");
    await setViewport(1440);
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
  });

  await test("over-receiving through the form is blocked server-side", async () => {
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Masumoto Provisions") && r.textContent.includes("Partially Delivered"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "View").click();
    })()`);
    await waitFor(`(() => {
      const o = ${overlay};
      return o && [...o.querySelectorAll("button")].some(b => b.textContent === "Receive Delivery");
    })()`);
    await evaluate(`(() => {
      [...${overlay}.querySelectorAll("button")].find(b => b.textContent === "Receive Delivery").click();
    })()`);
    await waitFor(`document.getElementById("receipt-date")`);
    // The client pre-validates: 999 exceeds the 150 outstanding.
    await evaluate(`(() => {
      const overlay = ${overlay};
      const input = overlay.querySelector('input[aria-label="Received quantity for Chashu"]');
      input.value = "999";
      document.getElementById("receipt-staff-name").value = "Kenji Browser";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      [...overlay.querySelectorAll("button")].find(b => b.textContent === "Confirm Receipt").click();
    })()`);
    await waitFor(`(() => {
      const o = ${overlay};
      return o && o.querySelector(".alert--error") !== null;
    })()`);
    const errorText = await evaluate(`(() => {
      const o = ${overlay};
      const alert = o ? o.querySelector(".alert--error") : null;
      return alert ? alert.textContent : "";
    })()`);
    assert(errorText.includes("outstanding"), `expected outstanding error, got: ${errorText}`);
    // And the API itself enforces the same rule.
    const order = (await apiAsSession("/purchase-orders?status=partially_delivered"))[0];
    const rejected = await evaluate(`(() => {
      const raw = document.cookie.split("; ").find(c => c.startsWith("stockmate_csrf=")).split("=")[1];
      return fetch("${BASE}/api/v1/purchase-orders/${order.id}/receive", {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(raw)},
        body: JSON.stringify({
          lines: [{ingredient_id: ${order.items[0].ingredient_id}, quantity: 999}],
          idempotency_key: "browser-over-receive",
          received_by_staff_name: "Kenji Browser",
        }),
      }).then(r => r.status);
    })()`);
    assert(rejected === 409, `expected 409 over_receive, got ${rejected}`);
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
  });
}

async function packageCountingTests() {
  await test("admin configures a counting package in Ingredient Management", async () => {
    await navigate("/ingredients");
    await waitFor(`document.querySelector("#view").textContent.includes("Korean Noodles")`);
    await evaluate(`(() => {
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Korean Noodles"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Edit").click();
    })()`);
    await waitFor(`document.getElementById("ingredient-count_package_enabled")`);
    await evaluate(`(() => {
      const enabled = document.getElementById("ingredient-count_package_enabled");
      enabled.checked = true;
      enabled.dispatchEvent(new Event("change", {bubbles: true}));
      document.getElementById("ingredient-count_package_name").value = "Box";
      document.getElementById("ingredient-count_units_per_package").value = "60";
      [...document.querySelectorAll(".modal-overlay button")]
        .find(b => b.textContent === "Save Ingredient").click();
    })()`);
    await waitFor(`document.getElementById("toasts").textContent.includes("Ingredient updated")`);
    const item = (await apiAsSession("/ingredients")).find(i => i.name === "Korean Noodles");
    assert(item.count_package_enabled && item.count_units_per_package === "60.000",
      `count packaging was not saved: ${JSON.stringify(item)}`);
  });

  await test("two full boxes and 15 loose units reconcile to 135", async () => {
    await navigate("/entry");
    await waitFor(`document.getElementById("entry-ingredient")`);
    await evaluate(`(() => {
      const select = document.getElementById("entry-ingredient");
      select.value = [...select.options].find(o => o.text.includes("Korean Noodles")).value;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      [...document.querySelectorAll(".mode-toggle button")]
        .find(b => b.textContent.includes("Remaining Stock")).click();
      const boxes = document.getElementById("entry-full-packages");
      const loose = document.getElementById("entry-loose-units");
      boxes.value = "2";
      boxes.dispatchEvent(new Event("input", {bubbles: true}));
      loose.value = "15";
      loose.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "135");
    await assertText("#view", "Do not include pieces already counted in full packages");
    assert(await evaluate(`getComputedStyle(document.getElementById("entry-quantity").closest(".field")).display === "none"`),
      "single-unit field must be hidden while counting packages");
    await evaluate(`document.querySelector("#view form button[type=submit]").click()`);
    await waitFor(`document.querySelector(".modal-overlay")?.textContent.includes("Confirm large adjustment")`);
    await evaluate(`[...document.querySelectorAll(".modal-overlay button")]
      .find(b => b.textContent === "Yes, record it").click()`);
    await waitFor(`document.querySelector("#view .alert--success")?.textContent.includes("Physical count saved")`);
    const item = (await apiAsSession("/inventory/stock")).find(i => i.ingredient_name === "Korean Noodles");
    assert(item.current_quantity === "135.000" && item.confidence === "high",
      `package reconciliation failed: ${JSON.stringify(item)}`);
    const history = await apiAsSession(`/inventory/transactions?ingredient_id=${item.ingredient_id}`);
    assert(history[0].counting_breakdown?.full_packages === 2,
      `count breakdown missing: ${JSON.stringify(history[0])}`);
    await screenshot("package-count-entry");
  });

  await test("used packages deduct canonical units and total-unit mode remains available", async () => {
    await evaluate(`(() => {
      [...document.querySelectorAll(".mode-toggle button")]
        .find(b => b.textContent.includes("Used Stock")).click();
      document.getElementById("entry-full-packages").value = "1";
      const loose = document.getElementById("entry-loose-units");
      loose.value = "5";
      loose.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText(".preview-box", "65");
    await evaluate(`document.querySelector("#view form button[type=submit]").click()`);
    await waitFor(`document.querySelector("#view .alert--success")?.textContent.includes("Usage recorded")`);
    const item = (await apiAsSession("/inventory/stock")).find(i => i.ingredient_name === "Korean Noodles");
    assert(item.current_quantity === "70.000", `used package conversion failed: ${item.current_quantity}`);
    await evaluate(`(() => {
      const method = document.getElementById("entry-count-method");
      method.value = "units";
      method.dispatchEvent(new Event("change", {bubbles: true}));
    })()`);
    assert(await evaluate(`getComputedStyle(document.getElementById("entry-quantity").closest(".field")).display !== "none"`),
      "total-unit input was not shown");
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
      const row = [...document.querySelectorAll("#view tbody tr")]
        .find(r => r.textContent.includes("Browser Spice"));
      [...row.querySelectorAll("button")].find(b => b.textContent === "Edit").click();
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

async function openDemand(name = "Korean Noodles") {
  await navigate("/ingredients");
  await waitFor(`document.querySelector("#view").textContent.includes(${JSON.stringify(name)})`);
  await evaluate(`(() => {
    const row = [...document.querySelectorAll("#view tbody tr")].find(r => r.textContent.includes(${JSON.stringify(name)}));
    const button = [...row.querySelectorAll("button")].find(b => b.textContent === "Forecast Settings");
    button.focus(); button.click();
  })()`);
  await waitFor(`document.getElementById("demand-form")`);
}

async function demandTests() {
  let before;
  await test("forecast settings start unconfigured and reject invalid input", async () => {
    before = (await apiAsSession("/inventory/stock")).find(i => i.ingredient_name === "Korean Noodles");
    await openDemand();
    await assertText("#demand-preview", "0 of 7 days configured");
    assert(await evaluate(`document.getElementById("demand-monday").value === ""`), "unknown value became zero");
    await evaluate(`(() => {
      const input = document.getElementById("demand-monday"); input.value = "-1";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#demand-form button[type=submit]").click();
    })()`);
    await waitFor(`document.getElementById("demand-monday").getAttribute("aria-invalid") === "true"`);
    assert(await evaluate(`document.activeElement.id === "demand-monday"`), "invalid field did not receive focus");
  });

  await test("weekday shortcut and editable weekends save the 320-piece Korean example", async () => {
    await evaluate(`(() => {
      const set = (id, value) => { const node = document.getElementById(id); node.value = value; node.dispatchEvent(new Event("input", {bubbles:true})); };
      set("demand-weekdays", "40");
      [...document.querySelectorAll("#demand-form button")].find(b => b.textContent === "Apply to Monday–Friday").click();
      set("demand-saturday", "60"); set("demand-sunday", "60"); set("demand-safety_stock", "60");
      set("demand-notes", "Restaurant planning example");
      document.getElementById("demand-enabled").click();
    })()`);
    await assertText("#demand-preview", "320 pc");
    await screenshot("forecast-settings-desktop");
    await evaluate(`document.querySelector("#demand-form button[type=submit]").click()`);
    await waitFor(`!document.getElementById("demand-form")`);
    const after = (await apiAsSession("/inventory/stock")).find(i => i.ingredient_name === "Korean Noodles");
    assert(JSON.stringify(after) === JSON.stringify(before), "demand settings changed stock/package data");
    const config = await apiAsSession(`/ingredients/${before.ingredient_id}/demand-settings`);
    assert(config.weekly_estimated_demand === "320.000" && config.forecast_ready && config.safety_stock === "60.000", JSON.stringify(config));
  });

  await test("individual weekdays remain editable and stale edits retain the draft", async () => {
    await openDemand();
    await evaluate(`(() => {
      const input = document.getElementById("demand-tuesday"); input.value = "41";
      input.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    await assertText("#demand-preview", "321 pc");
    await evaluate(`(async () => {
      const path = "/ingredients/${before.ingredient_id}/demand-settings";
      const current = await SM.api.get(path);
      await SM.api.patch(path, {expected_version: current.version, tuesday: "45"});
      document.querySelector("#demand-form button[type=submit]").click();
    })()`);
    await waitFor(`document.getElementById("demand-feedback").textContent.includes("changed since")`);
    assert(await evaluate(`document.getElementById("demand-tuesday").value === "41"`), "draft lost after conflict");
    await evaluate(`[...document.querySelectorAll("#demand-feedback button")].find(b => b.textContent === "Reload latest settings").click()`);
    await waitFor(`document.getElementById("demand-tuesday")?.value === "45"`);
    await evaluate(`(() => {
      const input = document.getElementById("demand-tuesday"); input.value = "40";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#demand-form button[type=submit]").click();
    })()`);
    await waitFor(`!document.getElementById("demand-form")`);
  });

  await test("failed forecast save preserves quantities and can be retried", async () => {
    await openDemand();
    await evaluate(`(() => {
      const input = document.getElementById("demand-monday"); input.value = "40.125";
      input.dispatchEvent(new Event("input", {bubbles: true}));
    })()`);
    try {
      await send("Network.emulateNetworkConditions", {offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1});
      await evaluate(`document.querySelector("#demand-form button[type=submit]").click()`);
      await waitFor(`document.getElementById("demand-feedback").textContent.includes("Could not reach")`);
      assert(await evaluate(`document.getElementById("demand-monday").value === "40.125"`), "offline failure lost input");
    } finally {
      await send("Network.emulateNetworkConditions", {offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1});
    }
    await evaluate(`(() => {
      const input = document.getElementById("demand-monday"); input.value = "40";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#demand-form button[type=submit]").click();
    })()`);
    await waitFor(`!document.getElementById("demand-form")`);
  });

  await test("a different ingredient keeps unknown days distinct from zero", async () => {
    await openDemand("Chinese Noodles");
    await evaluate(`(() => {
      const input = document.getElementById("demand-monday"); input.value = "0";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      document.querySelector("#demand-form button[type=submit]").click();
    })()`);
    await waitFor(`!document.getElementById("demand-form")`);
    const chinese = (await apiAsSession("/ingredients")).find(i => i.name === "Chinese Noodles");
    const config = await apiAsSession(`/ingredients/${chinese.id}/demand-settings`);
    assert(config.monday === "0.000" && config.tuesday === null && !config.enabled, JSON.stringify(config));
    const korean = await apiAsSession(`/ingredients/${before.ingredient_id}/demand-settings`);
    assert(korean.monday === "40.000", "settings leaked between ingredients");
  });

  await test("mobile forecast dialog traps focus and confirms discarding unsaved settings", async () => {
    await setViewport(390);
    await openDemand();
    const overflow = await evaluate(`(() => { const modal = document.querySelector(".modal"); return modal.scrollWidth - modal.clientWidth; })()`);
    assert(overflow <= 1, `forecast modal overflows by ${overflow}`);
    await screenshot("forecast-settings-390");
    await evaluate(`document.getElementById("demand-preview").scrollIntoView({block:"center"})`);
    await screenshot("forecast-settings-390-preview");
    await evaluate(`[...document.querySelectorAll(".modal button")].find(b => b.textContent === "Close").focus()`);
    await send("Input.dispatchKeyEvent", {type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
    await send("Input.dispatchKeyEvent", {type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9});
    assert(await evaluate(`document.activeElement.id === "demand-enabled"`), "focus escaped modal");
    await evaluate(`(() => {
      const input = document.getElementById("demand-monday"); input.value = "99";
      input.dispatchEvent(new Event("input", {bubbles: true}));
      document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}));
    })()`);
    await assertText("#demand-feedback", "unsaved forecast settings");
    await evaluate(`[...document.querySelectorAll("#demand-feedback button")].find(b => b.textContent === "Discard changes").click()`);
    await waitFor(`!document.getElementById("demand-form")`);
    assert(await evaluate(`document.activeElement.textContent === "Forecast Settings"`), "focus not restored");
    await setViewport(1440);
  });
}

async function forecastTests() {
  let beforeStock, beforeHistory, beforeOrders, korean, incomingDate, forecastOrderId;
  await test("forecast calendar distinguishes today's upper-bound preview from future dates", async () => {
    beforeStock = await apiAsSession("/inventory/stock");
    korean = beforeStock.find(row => row.ingredient_name === "Korean Noodles");
    await navigate(`/forecast?ingredient_id=${korean.ingredient_id}`);
    await waitFor(`document.querySelectorAll("#forecast-calendar tbody tr").length === 7`);
    await assertText("#forecast-content", "Future forecast dates");
    await assertText("#forecast-content", "Actual available stock now");
    await assertText("#forecast-today-preview", "conservative upper bound");
    const data = await apiAsSession(`/forecast/calendar?days=7&ingredient_id=${korean.ingredient_id}`);
    assert(data.ingredients[0].actual_available === korean.current_quantity, "forecast changed the stock baseline");
    await assertText("#forecast-calendar tbody tr:first-child", SMQuantity(data.ingredients[0].conservative.days[0].closing_balance));
    await screenshot("forecast-calendar-desktop");
  });

  await test("today's remaining usage accepts explicit zero and resets without saving demand or stock", async () => {
    const demandBefore = await apiAsSession(`/ingredients/${korean.ingredient_id}/demand-settings`);
    const stockBefore = await apiAsSession("/inventory/stock");
    const defaults = (await apiAsSession(`/forecast/calendar?ingredient_id=${korean.ingredient_id}`)).ingredients[0];
    await evaluate(`document.getElementById("forecast-today-usage").value = "-1"`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Apply remaining usage").click()`);
    await waitFor(`document.getElementById("forecast-today-usage").getAttribute("aria-invalid") === "true"`);
    await evaluate(`document.getElementById("forecast-today-usage").value = "0"`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Apply remaining usage").click()`);
    await waitFor(`document.getElementById("forecast-today-preview")?.textContent.includes("explicit forecast-only")`);
    assert(await evaluate(`document.getElementById("forecast-today-usage").value === "0"`), "zero was discarded");
    const zero = (await apiAsSession(`/forecast/calendar?ingredient_id=${korean.ingredient_id}&today_remaining_usage=0`)).ingredients[0];
    assert(zero.conservative.today.closing_balance !== defaults.conservative.today.closing_balance, "zero did not change today's forecast");
    assert(zero.conservative.days[0].opening_balance === zero.conservative.today.closing_balance, "today did not feed tomorrow");
    await assertText("#forecast-calendar tbody tr:first-child", `${SMQuantity(zero.conservative.days[0].closing_balance)} pc`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Use configured full-day estimate").click()`);
    await waitFor(`document.getElementById("forecast-today-preview")?.textContent.includes("conservative upper bound")`);
    assert(await evaluate(`document.getElementById("forecast-today-usage").value === ""`), "blank default was replaced with zero");
    assert(JSON.stringify(await apiAsSession(`/ingredients/${korean.ingredient_id}/demand-settings`)) === JSON.stringify(demandBefore), "override saved permanent demand");
    assert(JSON.stringify(await apiAsSession("/inventory/stock")) === JSON.stringify(stockBefore), "override changed stock");
  });

  await test("forecast scenarios distinguish uncertain orders without repeated calculations", async () => {
    const data = await apiAsSession(`/forecast/calendar?days=7&ingredient_id=${korean.ingredient_id}`);
    const ingredient = (await apiAsSession("/ingredients")).find(row => row.id === korean.ingredient_id);
    const cutoff = data.ingredients[0].upcoming_cutoffs.find(row => row.supplier_id === ingredient.primary_supplier_id && row.delivery_date <= data.end_date);
    assert(cutoff, "test needs an applicable upcoming primary supplier delivery");
    incomingDate = cutoff.delivery_date;
    const order = await evaluate(`SM.api.post("/purchase-orders", {
      supplier_id: ${ingredient.primary_supplier_id}, expected_delivery_date: ${JSON.stringify(incomingDate)},
      notes: "Isolated browser forecast example", items: [{ingredient_id: ${korean.ingredient_id}, quantity_ordered: "240"}]
    })`);
    forecastOrderId = order.id;
    await evaluate(`SM.api.post("/purchase-orders/${order.id}/mark-ordered")`);
    beforeStock = await apiAsSession("/inventory/stock");
    beforeHistory = await apiAsSession("/inventory/transactions?limit=200");
    beforeOrders = await apiAsSession("/purchase-orders?limit=200");
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Refresh forecast").click()`);
    await waitFor(`document.getElementById("forecast-content").textContent.includes("Order #${order.id}")`);
    const forecastData = await apiAsSession(`/forecast/calendar?days=7&ingredient_id=${korean.ingredient_id}`);
    const expectedDay = forecastData.ingredients[0].expected.days.find(day => day.date === incomingDate);
    const calls = forecastRequests;
    await evaluate(`(() => { const select = document.getElementById("forecast-scenario"); select.value = "expected"; select.dispatchEvent(new Event("change", {bubbles:true})); })()`);
    await assertText("#forecast-content", "Expected projection");
    await assertText("#forecast-content", "not supplier-confirmed");
    await assertText(`#forecast-calendar tr[data-date="${incomingDate}"]`, `${SMQuantity(expectedDay.eligible_incoming)} pc`);
    assert(expectedDay.pre_delivery_shortage_risk && Number(expectedDay.closing_balance) > 0, "fixture must cover a shortage before a positive delivery-day close");
    await assertText(`#forecast-calendar tr[data-date="${incomingDate}"] .badge--critical`, "Pre-delivery shortage risk");
    await screenshot("forecast-pre-delivery-risk");
    await evaluate(`(() => { const select = document.getElementById("forecast-scenario"); select.value = "conservative"; select.dispatchEvent(new Event("change", {bubbles:true})); })()`);
    await assertText("#forecast-content", "Conservative projection");
    assert(forecastRequests === calls, "scenario switches recalculated the batch");
  });

  await test("a partial receipt does not confirm the outstanding shipment in the forecast", async () => {
    await evaluate(`SM.api.post("/purchase-orders/${forecastOrderId}/receive", {
      received_by_staff_name: "Isolated Forecast Check", idempotency_key: "browser-forecast-partial",
      lines: [{ingredient_id: ${korean.ingredient_id}, quantity: "10", quantity_rejected: "0"}]
    })`);
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Refresh forecast").click()`);
    await waitFor(`document.getElementById("forecast-content").textContent.includes("Confirmation of the outstanding remainder is not established")`);
    const item = (await apiAsSession(`/forecast/calendar?ingredient_id=${korean.ingredient_id}`)).ingredients[0];
    const delivery = item.incoming.find(row => row.order_id === forecastOrderId);
    assert(delivery.status === "partially_delivered" && delivery.outstanding_quantity === "230.000", "partial receipt quantity was wrong");
    assert(!delivery.conservative_eligible && delivery.expected_eligible && delivery.uncertain, "partial receipt was mistaken for supplier confirmation");
    beforeStock = await apiAsSession("/inventory/stock");
    beforeHistory = await apiAsSession("/inventory/transactions?limit=200");
    beforeOrders = await apiAsSession("/purchase-orders?limit=200");
  });

  await test("forecast ranges load 14 and 30 days and ingredient switching reuses the batch", async () => {
    for (const days of [14, 30]) {
      await evaluate(`(() => { const select = document.getElementById("forecast-days"); select.value = "${days}"; select.dispatchEvent(new Event("change", {bubbles:true})); })()`);
      await waitFor(`document.querySelectorAll("#forecast-calendar tbody tr").length === ${days}`);
    }
    const calls = forecastRequests;
    await evaluate(`(() => { const select = document.getElementById("forecast-ingredient"); select.value = [...select.options].find(o => o.text.includes("Nori")).value; select.dispatchEvent(new Event("change", {bubbles:true})); })()`);
    await assertText("#forecast-content", "Demand configuration: missing");
    await assertText("#forecast-calendar tbody tr:first-child", "Unknown");
    assert(forecastRequests === calls, "ingredient switching recalculated the batch");
  });

  await test("forecast refresh handles network failure and keeps selections for retry", async () => {
    const selected = await evaluate(`document.getElementById("forecast-ingredient").value`);
    try {
      await send("Network.emulateNetworkConditions", {offline:true, latency:0, downloadThroughput:-1, uploadThroughput:-1});
      await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Refresh forecast").click()`);
      await waitFor(`document.querySelector("#view .alert--error")?.textContent.includes("Could not reach")`);
    } finally {
      await send("Network.emulateNetworkConditions", {offline:false, latency:0, downloadThroughput:-1, uploadThroughput:-1});
    }
    assert(await evaluate(`document.getElementById("forecast-ingredient").value === ${JSON.stringify(selected)}`), "selection was lost");
    await evaluate(`[...document.querySelectorAll("#view button")].find(b => b.textContent === "Refresh forecast").click()`);
    await waitFor(`document.querySelectorAll("#forecast-calendar tbody tr").length === 30`);
  });

  await test("forecast calendar remains usable on a phone with native selectors", async () => {
    await setViewport(390);
    await evaluate(`(() => { const select = document.getElementById("forecast-ingredient"); select.value = "${korean.ingredient_id}"; select.dispatchEvent(new Event("change", {bubbles:true})); })()`);
    assert(await evaluate(`document.documentElement.scrollWidth - innerWidth <= 1`), "forecast page overflows phone width");
    await screenshot("forecast-calendar-390");
    await evaluate(`document.querySelector("#forecast-calendar").scrollIntoView({block:"start"})`);
    await screenshot("forecast-calendar-390-days");
    await send("Input.dispatchKeyEvent", {type:"keyDown", key:"Tab", code:"Tab", windowsVirtualKeyCode:9});
    await send("Input.dispatchKeyEvent", {type:"keyUp", key:"Tab", code:"Tab", windowsVirtualKeyCode:9});
    await setViewport(1440);
  });

  await test("dashboard forecast summary links to the selected ingredient and views never write stock", async () => {
    await navigate("/dashboard");
    await waitFor(`document.getElementById("forecast-summary")`);
    await assertText("#forecast-summary", "Projected to run out");
    await assertText("#forecast-summary", "Missing or incomplete demand");
    await assertText("#forecast-summary", "full-day estimate as a conservative upper bound");
    await evaluate(`(() => { const link = [...document.querySelectorAll("#forecast-summary a")].find(a => a.textContent === "Korean Noodles"); link.click(); })()`);
    await waitFor(`document.querySelectorAll("#forecast-calendar tbody tr").length === 7`);
    assert(await evaluate(`document.getElementById("forecast-ingredient").value === "${korean.ingredient_id}"`), "summary linked to a different ingredient");
    assert(JSON.stringify(await apiAsSession("/inventory/stock")) === JSON.stringify(beforeStock), "forecast viewing changed stock");
    assert(JSON.stringify(await apiAsSession("/inventory/transactions?limit=200")) === JSON.stringify(beforeHistory), "forecast viewing wrote ledger rows");
    assert(JSON.stringify(await apiAsSession("/purchase-orders?limit=200")) === JSON.stringify(beforeOrders), "forecast viewing changed orders");
  });
}

function SMQuantity(value) {
  return value === null ? "Unknown" : String(value).replace(/\.0+$/, "");
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
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
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
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

  await test("staff can read forecast settings without editing controls", async () => {
    await openDemand();
    await assertText("#demand-form", "Read-only forecast settings");
    await assertText("#demand-preview", "320 pc");
    assert(await evaluate(`document.getElementById("demand-monday").disabled && !document.querySelector("#demand-form button[type=submit]")`), "staff has forecast editing controls");
    await evaluate(`document.dispatchEvent(new KeyboardEvent("keydown", {key:"Escape"}))`);
  });

  await test("staff can view the read-only Forecast Calendar", async () => {
    await navigate("/forecast");
    await waitFor(`document.querySelectorAll("#forecast-calendar tbody tr").length === 7`);
    await assertText("#forecast-content", "Actual available stock now");
    assert(!(await evaluate(`document.querySelector("#view").textContent.includes("Save Forecast Settings")`)), "forecast view exposes editing");
  });

  await test("cleared session redirects protected pages to login", async () => {
    await openDemand();
    await send("Network.clearBrowserCookies");
    await evaluate(`SM.api.get("/ingredients").catch(() => {})`);
    await waitFor(`document.getElementById("login-username")`);
    assert(await evaluate(`!document.querySelector(".modal-overlay") && !document.getElementById("app").inert`), "expired session left a blocking dialog");
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
  await receivingTests();
  await packageCountingTests();
  await demandTests();
  await forecastTests();
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
