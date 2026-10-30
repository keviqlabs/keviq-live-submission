import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole } from "./console_harness.mjs";

// Approve/reject from the approval queue: every tap is one POST to the seller decision route of
// the app, with the body the route defines. Nothing is decided locally.
function setup() {
  const nodes = new Map(), requests = [], listeners = new Map();
  const document = {
    activeElement: null,
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    querySelector() { return null; },
    addEventListener(kind, listener) { listeners.set(kind, listener); },
  };
  const context = vm.createContext({
    document, location: { pathname: "/console/decision ui" }, CSS: { escape: (s) => s },
    EventSource: class { addEventListener() {} }, setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch(url, options) {
      if (url === "/api/cost") return { json: async () => ({ today_usd: 0, daily_cap_usd: 30 }) };
      requests.push({ url, method: options?.method ?? "GET", body: options?.body ? JSON.parse(options.body) : undefined });
      return { ok: true, json: async () => ({}) };
    },
  });
  const run = loadConsole(context);
  run(`handlers.order({ type: "order", event_id: "o1-e", order_id: "o1", state: "NEEDS_SELLER", prev_state: "DRAFT", ts_ms: 10,
    actor: "assistant", reason: "price question", amount: 150000, customer_ref: "buyer-0900000002", caused_by: [],
    lines: [{ sku: "SYN-2", card_id: "c", card_version: 2, qty: 1, unit_price: 150000 }] });`);
  return { run, nodes, requests, async click(dataset) {
    const button = { dataset, disabled: false };
    await listeners.get("click")({ target: { closest: () => button } });
    return button;
  }, async input(dataset, value) { listeners.get("input")({ target: { dataset, value } }); } };
}

const route = "/api/sessions/decision%20ui/orders/o1/decision";

test("approving with a typed unit price posts approve + unit_price", async () => {
  const ui = setup();
  assert.match(ui.nodes.get("queue").innerHTML, /data-act="price" data-oid="o1"/);
  await ui.input({ price: "o1" }, "120000");
  const button = await ui.click({ act: "price", oid: "o1" });
  assert.deepEqual(ui.requests, [{ url: route, method: "POST", body: { decision: "approve", unit_price: 120000 } }]);
  assert.equal(button.disabled, false);
});

test("keeping the card price posts a bare approve; reject posts reject", async () => {
  const ui = setup();
  await ui.click({ act: "card", oid: "o1" });
  await ui.click({ act: "reject", oid: "o1" });
  assert.deepEqual(ui.requests.map((r) => r.body), [{ decision: "approve" }, { decision: "reject" }]);
  assert.ok(ui.requests.every((r) => r.url === route));
});

test("an invalid unit price is refused locally with a banner and no request", async () => {
  const ui = setup();
  await ui.input({ price: "o1" }, "12k");
  await ui.click({ act: "price", oid: "o1" });
  assert.deepEqual(ui.requests, []);
  assert.equal(ui.nodes.get("banner").hidden, false);
  assert.match(ui.nodes.get("banner").textContent, /whole number/);
});

test("the queue buttons are the design's primary/danger actions and the source log is read-only", async () => {
  const ui = setup();
  const html = ui.nodes.get("queue").innerHTML;
  assert.match(html, /class="btn primary" data-act="price"/);
  assert.match(html, /class="btn danger full" data-act="reject"/);
  await ui.click({ act: "log", oid: "o1" });
  assert.equal(ui.run("[...shown].join()"), "o1");
  await ui.click({ act: "close-log" });
  assert.equal(ui.run("shown.size"), 0);
  assert.deepEqual(ui.requests.map((r) => [r.method, r.url]), [["GET", "/api/sessions/decision%20ui/orders/o1/log"]]);
});
