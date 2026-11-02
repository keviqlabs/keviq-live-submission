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

test("known approval reasons translate and format amounts without changing the stored reason", () => {
  const ui = setup();
  ui.run('orders.get("o1").reason = "amount 2070000 > seller threshold 2000000"; renderOrders()');
  const english = ui.nodes.get("queue").innerHTML;
  assert.match(english, /Total <span class="money">2\.070\.000 ₫<\/span> exceeds/);
  assert.match(english, /2\.000\.000 ₫/);
  assert.doesNotMatch(english, /amount 2070000/);
  ui.run('setLang("vi", rerender)');
  const vietnamese = ui.nodes.get("queue").innerHTML;
  assert.match(vietnamese, /Tổng <span class="money">2\.070\.000 ₫<\/span>/);
  assert.doesNotMatch(vietnamese, /seller threshold|Total /);
  assert.equal(ui.run('orders.get("o1").reason'), "amount 2070000 > seller threshold 2000000");
  assert.deepEqual(ui.requests, []);
});

test("complete known reasons are localized while unknown details remain escaped and lossless", () => {
  const ui = setup();
  assert.equal(ui.run('reasonText("customer confirmed in Messenger")'), "Customer confirmed in Messenger.");
  assert.equal(ui.run('reasonText("draft checks passed: price matches live card, qty within stock")'),
    "Order checks passed; awaiting the customer.");
  assert.equal(ui.run('reasonText("drafted from comment cm-one on card-one v2 (150.000đ)")'),
    "Drafted from the customer comment.");
  assert.equal(ui.run(`reasonText("card internal-card changed before the customer's ok: v1 150.000đ -> v2 180.000đ; the customer confirms again")`),
    "Card price changed from 150.000 ₫ to 180.000 ₫ before the customer confirmed. Customer must confirm again.");
  assert.equal(ui.run('reasonText("variant size 40 not on internal-card v2")'),
    "Requested option is not listed on this item: size 40.");
  const unknown = 'new rule <img src=x onerror="bad()">; unrecognized suffix';
  ui.run(`orders.get("o1").reason = ${JSON.stringify(unknown)}; renderOrders()`);
  assert.equal(ui.run(`reasonText(${JSON.stringify(unknown)})`), unknown);
  for (const reason of ["constructor", "toString", "__proto__"])
    assert.equal(ui.run(`reasonText(${JSON.stringify(reason)})`), reason);
  assert.match(ui.nodes.get("queue").innerHTML, /new rule &lt;img src=x onerror=&quot;bad\(\)&quot;&gt;/);
  assert.doesNotMatch(ui.nodes.get("queue").innerHTML, /<img src=x/);
  assert.equal(ui.run('reasonText("draft checks passed: a new server condition")'),
    "draft checks passed: a new server condition");
  assert.match(ui.run('logHtml({steps: [{reason: "amount 2070000 > seller threshold 2000000"}]})'),
    /amount 2070000 &gt; seller threshold 2000000/);
});

test("recent orders use the pinned item name and keep reason, activity and money separate", () => {
  const ui = setup();
  ui.run(`versions.set("c", [{card_id: "c", version: 2, name: "Original item"}]);
    cards.set("c", {card_id: "c", version: 3, name: "Renamed item"});
    orders.get("o1").state = "CONFIRM_SENT";
    orders.get("o1").reason = "draft checks passed"; renderOrders()`);
  const html = ui.nodes.get("orders").innerHTML;
  assert.match(html, /Original item/);
  assert.doesNotMatch(html, /Renamed item/);
  assert.match(html, /class="order-total money">150\.000 ₫/);
  assert.match(html, /class="order-reason">Order checks passed/);
  assert.match(html, /class="order-activity">By assistant at/);
  assert.match(html, /class="order-sim"/);
  assert.match(html, /class="order-actions"/);
  assert.match(html, /data-act="yes" data-oid="o1"/);
  assert.match(html, /data-act="no" data-oid="o1"/);
  assert.doesNotMatch(html.replace(/<[^>]+>/g, ""), /\bo1\b|\bc v2\b/);
});

test("proposed total uses entered unit price and every line quantity", () => {
  const ui = setup();
  ui.run(`orders.get("o1").lines[0].qty = 2; prices.set("o1", "400000"); renderOrders()`);
  assert.match(ui.nodes.get("queue").innerHTML, /data-price-total="o1">800\.000 ₫/);
  assert.equal(ui.run('proposedTotal(orders.get("o1"), "12k")'), "—");
  assert.equal(ui.run('proposedTotal(orders.get("o1"), "99999999999999999999")'), "—");
});

test("new comments do not rebuild the order queue while the seller is deciding", () => {
  const ui = setup();
  ui.nodes.get("queue").innerHTML = "seller controls intact";
  ui.run(`handlers.comment({ comment_id: "new", event_id: "cm", user_ref: "buyer", text: "hi", ts_ms: 10 });`);
  assert.equal(ui.nodes.get("queue").innerHTML, "seller controls intact");
});
