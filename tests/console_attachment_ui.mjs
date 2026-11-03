import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole } from "./console_harness.mjs";

function setup() {
  const nodes = new Map(), requests = [], listeners = new Map();
  const document = {
    activeElement: null,
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    querySelector() { return null; },
    addEventListener(kind, listener) { listeners.set(kind, listener); },
  };
  const context = vm.createContext({
    document, location: { pathname: "/console/synthetic-binding" }, CSS: { escape: (s) => s },
    EventSource: class { addEventListener() {} }, setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch(url, options) {
      if (url === "/api/cost") return { json: async () => ({ today_usd: 0, daily_cap_usd: 30 }) };
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({}) };
    },
  });
  const run = loadConsole(context);
  run(`for (const [id, frame] of [["a", 10], ["b", 20]]) handlers.card({
    type: "card", event_id: id + "-v1", card_id: id, version: 1, sku: id.toUpperCase(), status: "edited",
    name: "Item " + id, price: 150000, stock: 5, ts_ms: frame, image_url: null,
    provenance: { frame_ts_ms: frame, speech_event_ids: [] }
  });`);
  return { run, nodes, requests, async click(dataset) {
    const button = { dataset, disabled: false };
    await listeners.get("click")({ target: { closest: () => button } });
  } };
}

function expired(ui, candidates = ["b", "a"]) {
  ui.run(`handlers.comment({ event_id: "comment-event", comment_id: "intent", user_ref: "synthetic-buyer", ts_ms: 25, text: "chốt" });
    handlers.comment_attachment({ event_id: "attachment-v1", attachment_id: "attachment", version: 1,
      comment_id: "intent", comment_event_id: "comment-event", label_event_id: "label-event",
      outcome: "expired", action: "expire", selected_card_id: null, deadline_ms: 4000,
      candidates: ${JSON.stringify(candidates.map((card_id) => ({ card_id })))} });`);
}

test("a returning item stays current after the previous item's later seller edit", () => {
  const ui = setup();
  ui.run(`handlers.card({ ...cards.get("a"), event_id: "a-v2", version: 2, ts_ms: 32, provenance: { frame_ts_ms: 30, speech_event_ids: [] } });
    handlers.card({ ...cards.get("b"), event_id: "b-v7", version: 7, ts_ms: 99 });`);
  const html = ui.nodes.get("cards").innerHTML;
  assert.ok(html.indexOf("Item a") < html.indexOf("Item b"));
});

test("pre-order selection sends only explicit identity and expected attachment version", async () => {
  const ui = setup();
  expired(ui);
  await ui.click({ act: "attachment-select", aid: "attachment", choice: "a" });
  assert.deepEqual(ui.requests, [{ url: "/api/sessions/synthetic-binding/attachments/attachment/decision",
    body: { attachment_version: 1, decision: "select", card_id: "a" } }]);
});

test("an empty snapshot has keep/reject controls without an invented selection", async () => {
  const ui = setup();
  expired(ui, []);
  assert.doesNotMatch(ui.nodes.get("comments").innerHTML, /attachment-select/);
  assert.match(ui.nodes.get("comments").innerHTML, /attachment-keep/);
  await ui.click({ act: "attachment-keep", aid: "attachment" });
  assert.deepEqual(ui.requests[0].body, { attachment_version: 1, decision: "keep_unresolved" });
});

test("once an order exists, only its explicit candidate approve/reject controls remain", async () => {
  const ui = setup();
  expired(ui);
  ui.run(`handlers.order({ type: "order", order_id: "different-id-encoding", state: "NEEDS_SELLER", ts_ms: 4000,
    actor: "assistant", reason: "seller choice", amount: 150000, customer_ref: "synthetic-buyer",
    caused_by: ["comment-event", "attachment-v1"], lines: [{ sku: "B", card_id: "b", card_version: 1,
      qty: 1, unit_price: 150000, candidates: ["b", "a"] }] });`);
  assert.doesNotMatch(ui.nodes.get("comments").innerHTML, /attachment-select|attachment-keep|attachment-reject/);
  assert.match(ui.nodes.get("queue").innerHTML, /choose-order/);
  await ui.click({ act: "choose-order", oid: "different-id-encoding", choice: "a" });
  assert.deepEqual(ui.requests[0].body, { decision: "approve", card_id: "a" });
});

test("unresolved order comments appear in the seller queue without invented actions", () => {
  const ui = setup();
  ui.run(`handlers.comment({ event_id: "cm-1", comment_id: "cm", user_ref: "buyer", ts_ms: 1, text: "this one" });
    handlers.comment_labeled({ comment_id: "cm", label: "order", order_draft: { reference_kind: "unresolved", qty: 1, variant: null } });`);
  assert.match(ui.nodes.get("unresolved").innerHTML, /this one/);
  assert.match(ui.nodes.get("unresolved").innerHTML, /No item selection available/);
  assert.doesNotMatch(ui.nodes.get("unresolved").innerHTML, /attachment-select/);
  assert.equal(ui.nodes.get("queue-count").textContent, "1");
});

test("an expired attachment offers only its frozen candidates and disappears after an order", () => {
  const ui = setup();
  expired(ui);
  ui.run(`handlers.comment_labeled({ comment_id: "intent", label: "order", order_draft: { reference_kind: "unresolved", qty: 1, variant: null } });`);
  assert.match(ui.nodes.get("unresolved").innerHTML, /data-choice="a"/);
  assert.match(ui.nodes.get("unresolved").innerHTML, /data-choice="b"/);
  ui.run(`handlers.order({ type: "order", order_id: "order", state: "NEEDS_SELLER", ts_ms: 30,
    customer_ref: "buyer", reason: "choice", amount: 150000, caused_by: ["comment-event"],
    lines: [{ card_id: "a", card_version: 1, sku: "A", qty: 1, unit_price: 150000 }] });`);
  assert.equal(ui.nodes.get("unresolved").innerHTML, "");
  assert.match(ui.nodes.get("comments").innerHTML, /data-act="open-order"/);
});

test("a pending comment does not display an empty-queue message and keeps its menu on unrelated events", () => {
  const ui = setup();
  expired(ui);
  ui.run(`handlers.comment_labeled({ comment_id: "intent", label: "order", order_draft: { reference_kind: "unresolved", qty: 1, variant: null } });`);
  assert.equal(ui.nodes.get("queue").innerHTML, "");
  ui.nodes.get("unresolved").innerHTML = "opened controls intact";
  ui.run(`handlers.comment({ event_id: "unrelated", comment_id: "other", user_ref: "buyer", text: "hello", ts_ms: 50 });`);
  assert.equal(ui.nodes.get("unresolved").innerHTML, "opened controls intact");
});
