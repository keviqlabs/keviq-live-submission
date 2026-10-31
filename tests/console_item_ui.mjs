import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole } from "./console_harness.mjs";

function setup(reply = { ok: true, changed: false, version: 4 }) {
  const nodes = new Map(), requests = [];
  const document = {
    activeElement: null,
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {});
      return nodes.get(id);
    },
    querySelector() { return null; },
    addEventListener() {},
  };
  const context = vm.createContext({
    document, location: { pathname: "/console/sku-ui" }, CSS: { escape: (x) => x },
    EventSource: class { addEventListener() {} }, setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch(url, options) {
      if (url === "/api/cost") return { json: async () => ({ today_usd: 0, daily_cap_usd: 30 }) };
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: reply.ok, status: reply.ok ? 200 : 409, json: async () => reply };
    },
  });
  const run = loadConsole(context);
  run(`cards.set("unresolved", {
    card_id: "unresolved", version: 4, sku: null, status: "draft", name: "Observed shoe",
    price: null, stock: 0, ts_ms: 1000, provenance: { frame_ts_ms: 1000, speech_event_ids: [] }
  });`);
  return { run, requests, nodes };
}

test("selection sends the exact typed SKU and latest visible card version to the item route only", async () => {
  const ui = setup({ ok: true, changed: true, version: 6 });
  ui.run('itemCodes.set("unresolved", "SYNTH-A"); cards.get("unresolved").version = 5');
  await ui.run('itemAction("assign-item", "unresolved")');
  assert.deepEqual(ui.requests, [{
    url: "/api/sessions/sku-ui/cards/unresolved/item", body: { card_version: 5, sku: "SYNTH-A" },
  }]);
  assert.equal(ui.run('itemCodes.has("unresolved")'), false);
});

test("keeping identity unresolved uses null on the current version and no order or price route", async () => {
  const ui = setup();
  await ui.run('itemAction("keep-item", "unresolved")');
  assert.deepEqual(ui.requests, [{
    url: "/api/sessions/sku-ui/cards/unresolved/item", body: { card_version: 4, sku: null },
  }]);
  assert.equal(ui.run('itemNotes.get("unresolved").key'), "item.kept");
  assert.match(ui.run('itemHtml(cards.get("unresolved"))'), /no order created/);
});

test("unknown drafts expose identity controls before price; known and legacy cards cannot use them", () => {
  const ui = setup();
  assert.match(ui.run('itemHtml(cards.get("unresolved"))'), /assign-item/);
  assert.match(ui.run('priceHtml(cards.get("unresolved"))'), /Pick the catalogue item/);
  assert.doesNotMatch(ui.run('priceHtml(cards.get("unresolved"))'), /data-card-price/);
  for (const status of ["live", "edited", "retired"]) {
    ui.run(`cards.get("unresolved").status = "${status}"`);
    assert.doesNotMatch(ui.run('itemHtml(cards.get("unresolved"))'), /assign-item|keep-item/);
  }
  ui.run('cards.get("unresolved").status = "draft"; cards.get("unresolved").sku = "SYNTH-A"');
  assert.equal(ui.run('itemHtml(cards.get("unresolved"))'), "");
  assert.match(ui.run('priceHtml(cards.get("unresolved"))'), /data-card-price/);
});

test("a stale response preserves the typed code and shows an escaped card-local error", async () => {
  const ui = setup({ ok: false, detail: "version <changed>" });
  ui.run('itemCodes.set("unresolved", "SYNTH-A")');
  await ui.run('itemAction("assign-item", "unresolved")');
  assert.equal(ui.run('itemCodes.get("unresolved")'), "SYNTH-A");
  assert.match(ui.run('itemHtml(cards.get("unresolved"))'), /409.*&lt;changed&gt;/);
});

test("blank selection and an ineligible card produce no request", async () => {
  const ui = setup();
  await ui.run('itemAction("assign-item", "unresolved")');
  ui.run('cards.get("unresolved").status = "live"');
  await ui.run('itemAction("keep-item", "unresolved")');
  assert.deepEqual(ui.requests, []);
});

test("missing and simulated photos show an explicitly labeled illustration", () => {
  const ui = setup();
  for (const url of [null, "sim://sam/fixture.png", "javascript:alert(1)"]) {
    ui.run(`cards.get("unresolved").image_url = ${JSON.stringify(url)}`);
    const html = ui.run('cardHtml(cards.get("unresolved"))');
    assert.match(html, /class="product-placeholder">/);
    assert.match(html, /src="\/static\/placeholder\.svg" alt="" aria-hidden="true"/);
    assert.match(html, /Illustration - no product photo/);
    assert.doesNotMatch(html, /class="product-photo"|onerror=/);
  }
  ui.run('cards.get("unresolved").image_url = "/static/synthetic-photo.png"');
  const html = ui.run('cardHtml(cards.get("unresolved"))');
  assert.match(html, /class="product-photo" src="\/static\/synthetic-photo\.png"/);
  assert.match(html, /class="product-placeholder" hidden/);
});

test("a broken or already-failed photo reveals the honest fallback without another image request", () => {
  const ui = setup();
  ui.run(`globalThis.placeholder = {hidden: true}; globalThis.imageErrors = [];
    globalThis.photo = {hidden: false, complete: false, naturalWidth: 0,
      parentElement: {querySelector: () => placeholder},
      addEventListener: (kind, listener) => { if (kind === "error") imageErrors.push(listener); }};
    bindProductImages({querySelectorAll: () => [photo]});`);
  assert.equal(ui.run("placeholder.hidden"), true);
  ui.run("imageErrors[0]()");
  assert.equal(ui.run("photo.hidden"), true);
  assert.equal(ui.run("placeholder.hidden"), false);
  ui.run("placeholder.hidden = true; photo.hidden = false; photo.complete = true; bindProductImages({querySelectorAll: () => [photo]})");
  assert.equal(ui.run("photo.hidden"), true);
  assert.equal(ui.run("placeholder.hidden"), false);
  assert.deepEqual(ui.requests, []);
});

test("seller-facing drafts use names while identifiers stay in collapsed source details", () => {
  const ui = setup();
  ui.run(`globalThis.row = {comment: {comment_id: "synthetic-comment", user_ref: "synthetic-buyer", text: "one please", ts_ms: 10},
    labeled: {label: "order", action: "none", order_draft: {sku: null, qty: 1, reference_kind: "current_card", referenced_card_id: "unresolved"}}};`);
  let html = ui.run("commentHtml(row)");
  const visible = html.replace(/<details[\s\S]*?<\/details>/g, "").replace(/<[^>]+>/g, "");
  assert.match(visible, /Draft: Observed shoe ×1/);
  assert.doesNotMatch(visible, /current_card|unresolved|ref /);
  assert.match(html, /<details class="comment-source">[\s\S]*current_card[\s\S]*unresolved/);
  ui.run('row.labeled.order_draft.reference_kind = "unresolved"; row.labeled.order_draft.referenced_card_id = null');
  html = ui.run("commentHtml(row)").replace(/<details[\s\S]*?<\/details>/g, "");
  assert.match(html, /Item not identified yet/);
  assert.doesNotMatch(html, /\? ×\?|ref unresolved/);
  const card = ui.run('cardHtml({...cards.get("unresolved"), card_id: "internal-card-777"})');
  assert.match(card, /<details class="card-source">[\s\S]*<span class="mono">internal-card-777<\/span>/);
  assert.doesNotMatch(card.replace(/<details[\s\S]*?<\/details>/g, "").replace(/<[^>]+>/g, ""), /internal-card-777/);
});
