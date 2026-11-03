import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole } from "./console_harness.mjs";

function setup() {
  const nodes = new Map(), requests = [], listeners = new Map(), scrolled = [];
  const document = {
    activeElement: null,
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    querySelector(selector) {
      const id = /^\[data-order-id="([^"]+)"\]$/.exec(selector)?.[1];
      const html = (nodes.get("orders")?.innerHTML || "") + (nodes.get("queue")?.innerHTML || "");
      return id && html.includes(`data-order-id="${id}"`) ? { scrollIntoView() { scrolled.push(id); } } : null;
    },
    addEventListener(kind, listener) { listeners.set(kind, listener); },
  };
  const context = vm.createContext({
    document, location: { pathname: "/console/order-summary-sample" }, CSS: { escape: (s) => s },
    EventSource: class { addEventListener() {} }, setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch(url, options) {
      if (url === "/api/cost") return { json: async () => ({ today_usd: 0, daily_cap_usd: 30 }) };
      requests.push({ url, options });
      return { ok: true, json: async () => ({}) };
    },
  });
  const run = loadConsole(context);
  let sequence = 0;
  const emit = (id, state, amount) => run(`handlers.order(${JSON.stringify({
    type: "order", event_id: `sample-${++sequence}`, order_id: id, state, prev_state: null,
    ts_ms: sequence, actor: "assistant", reason: "draft checks passed", amount, customer_ref: `sample-${id}`,
    caused_by: [], lines: [{ sku: "SAMPLE-1", card_id: "sample-card", card_version: 1, qty: 1, unit_price: amount }],
  })})`);
  const html = (id) => nodes.get(id)?.innerHTML || "";
  const click = (dataset) => listeners.get("click")({ target: { closest: () => ({ dataset }) } });
  const rows = () => [...html("orders").matchAll(/class="order-row" data-order-id="([^"]+)"/g)].map((m) => m[1]);
  return { run, nodes, requests, scrolled, emit, html, click, rows };
}

function seed() {
  const ui = setup();
  ui.emit("confirmed", "CONFIRMED", 690000);
  ui.emit("waiting", "CONFIRM_SENT", 450000);
  ui.emit("cancelled", "CANCELLED", 120000);
  ui.emit("approval", "NEEDS_SELLER", 2070000);
  return ui;
}

function count(ui, state) {
  const item = ui.html("orders-summary").match(new RegExp(`data-order-state="${state}"[^>]*>([\\s\\S]*?)<\\/span>`));
  return item ? Number(item[1].replace(/<[^>]*>/g, "").match(/\d+\s*$/)?.[0]) : null;
}
const value = (ui) => ui.html("orders-summary").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

test("summary counts every order and only confirmed amounts; the queue stays separate", () => {
  const ui = seed();
  for (const state of ["NEEDS_SELLER", "CONFIRM_SENT", "CONFIRMED", "CANCELLED"]) assert.equal(count(ui, state), 1);
  assert.equal(count(ui, "DRAFT"), null);
  assert.match(value(ui), /Confirmed order value: 690\.000 ₫/);
  assert.doesNotMatch(value(ui), /2\.070\.000|450\.000|120\.000/);
  assert.deepEqual(ui.rows(), ["cancelled", "waiting", "confirmed"]);
  assert.match(ui.html("queue"), /data-order-id="approval"/);
  assert.deepEqual(ui.requests, []);
});

test("confirmed and cancelled filters change only the recent list and keep full-session totals", async () => {
  const ui = seed(), queue = ui.html("queue");
  await ui.click({ act: "order-filter", orderFilter: "confirmed" });
  assert.deepEqual(ui.rows(), ["confirmed"]);
  assert.match(ui.html("orders-filter"), /data-order-filter="confirmed" aria-pressed="true"/);
  await ui.click({ act: "order-filter", orderFilter: "cancelled" });
  assert.deepEqual(ui.rows(), ["cancelled"]);
  assert.match(value(ui), /690\.000 ₫/);
  assert.equal(count(ui, "NEEDS_SELLER"), 1);
  assert.equal(ui.html("queue"), queue);
  assert.deepEqual(ui.requests, []);
});

test("switching language preserves the filter and translates the summary and buttons", async () => {
  const ui = seed();
  await ui.click({ act: "order-filter", orderFilter: "confirmed" });
  await ui.click({ lang: "vi" });
  assert.deepEqual(ui.rows(), ["confirmed"]);
  assert.match(value(ui), /Giá trị đơn đã xác nhận: 690\.000 ₫/);
  assert.match(ui.html("orders-filter"), /Tất cả/);
  assert.match(ui.html("orders-filter"), /data-order-filter="confirmed" aria-pressed="true"/);
  await ui.click({ lang: "en" });
  assert.deepEqual(ui.rows(), ["confirmed"]);
  assert.match(value(ui), /Confirmed order value/);
});

test("latest states replace prior totals, closed joins confirmed, and draft counts are conditional", async () => {
  const ui = seed();
  ui.emit("confirmed", "CLOSED", 690000);
  ui.emit("second", "CLOSED", 150000);
  ui.emit("draft", "DRAFT", 90000);
  assert.equal(count(ui, "CONFIRMED"), 2);
  assert.equal(count(ui, "DRAFT"), 1);
  assert.match(value(ui), /840\.000 ₫/);
  await ui.click({ act: "order-filter", orderFilter: "confirmed" });
  assert.deepEqual(ui.rows(), ["second", "confirmed"]);
  ui.emit("confirmed", "CANCELLED", 690000);
  assert.equal(count(ui, "CONFIRMED"), 1);
  assert.equal(count(ui, "CANCELLED"), 2);
  assert.match(value(ui), /150\.000 ₫/);
  assert.deepEqual(ui.rows(), ["second"]);
  ui.emit("draft", "CONFIRM_SENT", 90000);
  assert.equal(count(ui, "DRAFT"), null);
});

test("opening an order hidden by a filter reveals it before scrolling without a request", async () => {
  const ui = seed();
  await ui.click({ act: "order-filter", orderFilter: "cancelled" });
  await ui.click({ act: "open-order", oid: "confirmed" });
  assert.deepEqual(ui.rows(), ["cancelled", "waiting", "confirmed"]);
  assert.deepEqual(ui.scrolled, ["confirmed"]);
  assert.match(ui.html("orders-filter"), /data-order-filter="all" aria-pressed="true"/);
  await ui.click({ act: "order-filter", orderFilter: "waiting" });
  await ui.click({ act: "open-order", oid: "approval" });
  assert.deepEqual(ui.rows(), ["waiting"]);
  assert.deepEqual(ui.scrolled, ["confirmed", "approval"]);
  assert.deepEqual(ui.requests, []);
});

test("an empty filter shows a translated empty state and zero confirmed value", async () => {
  const ui = setup();
  ui.emit("waiting", "CONFIRM_SENT", 450000);
  await ui.click({ act: "order-filter", orderFilter: "confirmed" });
  assert.deepEqual(ui.rows(), []);
  assert.match(ui.html("orders"), /class="empty-state"/);
  assert.match(value(ui), /Confirmed order value: 0 ₫/);
  assert.equal(count(ui, "CONFIRMED"), 0);
  assert.equal(count(ui, "CONFIRM_SENT"), 1);
  await ui.click({ lang: "vi" });
  assert.match(ui.html("orders"), /Không có đơn/);
});

test("unresolved comments do not inflate order counts and filters do not cap the list", async () => {
  const ui = seed();
  ui.run(`handlers.comment({comment_id:"sample-unresolved",event_id:"sample-comment",user_ref:"sample",text:"this item",ts_ms:8});
    handlers.comment_labeled({comment_id:"sample-unresolved",label:"order",action:"none",order_draft:{reference_kind:"unresolved",referenced_card_id:null}});`);
  assert.equal(ui.nodes.get("queue-count").textContent, "2");
  assert.equal(count(ui, "NEEDS_SELLER"), 1);
  for (let i = 0; i < 205; i++) ui.emit(`extra-${i}`, "CONFIRM_SENT", 1000);
  await ui.click({ act: "order-filter", orderFilter: "waiting" });
  assert.equal(ui.rows().length, 206);
  assert.equal(count(ui, "CONFIRM_SENT"), 206);
  assert.equal(ui.rows()[0], "extra-204");
  assert.deepEqual(ui.requests, []);
});
