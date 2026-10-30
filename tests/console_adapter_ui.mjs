import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole } from "./console_harness.mjs";

// The console as a stream adapter: events from the EventSource stub land in state and DOM,
// the resume cursor follows the SSE id, and no event type triggers a write to the API.
function setup() {
  const nodes = new Map(), requests = [], streams = [];
  const document = {
    activeElement: null,
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    querySelector() { return null; },
    addEventListener() {},
  };
  class EventSource {
    constructor(url) { this.url = url; this.listeners = new Map(); streams.push(this); }
    addEventListener(type, fn) { this.listeners.set(type, fn); }
    emit(type, seq, data) { this.listeners.get(type)({ lastEventId: String(seq), data: JSON.stringify(data) }); }
  }
  const context = vm.createContext({
    document, location: { pathname: "/console/adapter-ui" }, CSS: { escape: (s) => s }, EventSource,
    setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch(url, options) {
      if (url === "/api/cost") return { json: async () => ({ today_usd: 1.25, total_usd: 2, daily_cap_usd: 30, daily_warn_usd: 20 }) };
      requests.push({ url, method: options?.method ?? "GET" });
      return { ok: true, json: async () => ({}) };
    },
  });
  const run = loadConsole(context);
  const base = { source: "sim", caused_by: [] };
  return { run, nodes, requests, stream: streams[0], text: (id) => nodes.get(id)?.textContent, html: (id) => nodes.get(id)?.innerHTML ?? "", base };
}

test("the stream opens at cursor 0 and the resume cursor follows the event id", () => {
  const ui = setup();
  assert.equal(ui.stream.url, "/api/sessions/adapter-ui/events?cursor=0");
  ui.stream.emit("frame", 3, { ...ui.base, type: "frame", ts_ms: 1 });
  ui.stream.emit("card", 7, { ...ui.base, type: "card", event_id: "c1", card_id: "c", version: 1, sku: "SYN-1", status: "live",
    name: "Synthetic tee", price: 99000, currency: "VND", stock: 3, variants: [], image_url: null, ts_ms: 2,
    provenance: { frame_ts_ms: 2, speech_event_ids: [], edited_by: null } });
  assert.equal(ui.run("lastSeq"), 7);
  assert.equal(ui.text("seq"), "7");
  assert.equal(ui.text("source"), "sim");
  assert.match(ui.html("cards"), /Synthetic tee/);
  assert.match(ui.html("cards"), /data-card-id="c" data-version="1" data-received-ms="\d+"/);
  assert.equal(ui.text("cards-count"), "1");
  const timing = ui.run("renderTimings[0]");
  assert.equal(timing.card_id, "c");
  assert.ok(Number.isInteger(timing.received_ms) && timing.painted_ms >= timing.received_ms);
  assert.deepEqual(ui.requests, []);
});

test("comments, labels and orders render from events alone; the approval queue counts only NEEDS_SELLER", () => {
  const ui = setup();
  ui.stream.emit("comment", 1, { ...ui.base, type: "comment", event_id: "m1", comment_id: "k1", user_ref: "buyer-0900000001", text: "size M?", ts_ms: 5 });
  assert.match(ui.html("comments"), /unlabeled/);
  ui.stream.emit("comment_labeled", 2, { ...ui.base, type: "comment_labeled", event_id: "l1", comment_id: "k1", label: "stock_question", action: "none", order_draft: null });
  assert.match(ui.html("comments"), /label-chip stock_question/);
  assert.doesNotMatch(ui.html("comments"), /unlabeled|action-line/);
  assert.equal(ui.text("comments-count"), "1");
  const order = (order_id, state) => ({ ...ui.base, type: "order", event_id: `o-${order_id}`, order_id, state, prev_state: "DRAFT", actor: "assistant",
    reason: "synthetic", amount: 99000, customer_ref: "buyer-0900000001", caused_by: ["m1"], ts_ms: 6,
    lines: [{ sku: "SYN-1", card_id: "c", card_version: 1, qty: 1, unit_price: 99000 }] });
  ui.stream.emit("order", 3, order("o1", "NEEDS_SELLER"));
  ui.stream.emit("order", 4, order("o2", "CONFIRMED"));
  assert.equal(ui.text("queue-count"), "1");
  assert.match(ui.html("queue"), /data-order-id="o1"/);
  assert.doesNotMatch(ui.html("queue"), /o2/);
  assert.match(ui.html("orders"), /data-order-id="o2"/);
  assert.deepEqual(ui.requests, []);
});

test("model status shows only measured calls: dot from the last result, p50 with n, dashes before any call", () => {
  const ui = setup();
  assert.equal((ui.html("models").match(/—/g) || []).length, 3);
  const call = (purpose, latency_ms, ok) => ({ ...ui.base, type: "model_call", event_id: purpose + latency_ms, model: "x", purpose, tokens_in: 1, tokens_out: 1, cost_usd: 0, latency_ms, ok, error: ok ? null : "timeout", ts_ms: 1 });
  ui.stream.emit("model_call", 1, call("product_seen", 300, true));
  ui.stream.emit("model_call", 2, call("product_seen", 100, true));
  ui.stream.emit("model_call", 3, call("product_seen", 200, true));
  const spark = ui.run('modelStats.get("spark")');
  assert.deepEqual([spark.n, spark.p50, spark.lastOk], [3, 200, true]);
  assert.match(ui.html("models"), /200 ms · n=3/);
  ui.stream.emit("model_call", 4, call("card_cutout", 50, false));
  assert.match(ui.html("models"), /dot danger/);
  assert.equal((ui.html("models").match(/—/g) || []).length, 1);
  assert.notEqual(ui.nodes.get("banner")?.hidden, false);
  ui.stream.emit("model_call", 5, { ...call("transcribe", 10, false), error: "daily_cap" });
  assert.equal(ui.nodes.get("banner").hidden, false);
  assert.match(ui.nodes.get("banner").textContent, /budget/);
});

test("the paint timestamp survives a later render of the card list", () => {
  const ui = setup();
  const card = (id, version, frame) => ({ ...ui.base, type: "card", event_id: `${id}-v${version}`, card_id: id, version, sku: id.toUpperCase(),
    status: "live", name: `Item ${id}`, price: 99000, currency: "VND", stock: 1, variants: [], image_url: null, ts_ms: frame,
    provenance: { frame_ts_ms: frame, speech_event_ids: [] } });
  const timed = (html, id) => new RegExp(`data-card-id="${id}"[^>]*data-received-ms="\\d+"[^>]*data-painted-ms="\\d+"`).test(html);
  // The first render precedes the paint (no requestAnimationFrame here, so the paint follows at once).
  ui.stream.emit("card", 1, card("a", 1, 10));
  assert.match(ui.html("cards"), /data-card-id="a"[^>]*data-received-ms="\d+"/);
  assert.equal(ui.run("renderTimings.length"), 1);
  // A second card rebuilds the whole list: card a must still carry its paint timestamp.
  ui.stream.emit("card", 2, card("b", 1, 20));
  assert.ok(timed(ui.html("cards"), "a"), "card a lost data-painted-ms on re-render");
  // A plain re-render (language switch, focus restore) keeps both cards' timestamps.
  ui.run("renderCards()");
  assert.ok(timed(ui.html("cards"), "a"));
  assert.ok(timed(ui.html("cards"), "b"));
  // A newer version of a card drops the stale paint timestamp until that version is painted.
  ui.run('paintedAt.set("a", { version: 1, ms: 1 }); cards.get("a").version = 2; renderCards()');
  assert.doesNotMatch(ui.html("cards"), /data-card-id="a"[^>]*data-painted-ms=/);
});
