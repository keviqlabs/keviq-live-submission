import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { loadConsole, read } from "./console_harness.mjs";

// Both languages are complete: the two dictionaries share one key set, every key the scripts and
// pages ask for exists, and the console renders fully in each language from the same events.
function setup() {
  const nodes = new Map(), listeners = new Map(), store = new Map(), streams = [];
  const element = (dataset = {}) => ({
    dataset, attributes: {}, className: "", textContent: "",
    setAttribute(name, value) { this.attributes[name] = String(value); },
  });
  const staticNodes = [], languageButtons = [];
  for (const [, tag, attributes] of read("console.html").matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
    const key = /\bdata-i18n="([^"]+)"/.exec(attributes)?.[1];
    const attr = /\bdata-i18n-attr="([^"]+)"/.exec(attributes)?.[1];
    const language = /\bdata-lang="([^"]+)"/.exec(attributes)?.[1];
    if (!key && !attr && !language) continue;
    const el = element({ ...(key ? { i18n: key } : {}), ...(attr ? { i18nAttr: attr } : {}), ...(language ? { lang: language } : {}) });
    el.tagName = tag;
    const id = /\bid="([^"]+)"/.exec(attributes)?.[1];
    if (id) nodes.set(id, el);
    staticNodes.push(el);
    if (language) languageButtons.push(el);
  }
  const document = {
    activeElement: null, documentElement: { lang: "en" },
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); },
    querySelectorAll(selector) {
      if (selector === "[data-i18n]") return staticNodes.filter((el) => el.dataset.i18n);
      if (selector === "[data-i18n-attr]") return staticNodes.filter((el) => el.dataset.i18nAttr);
      if (selector === "[data-lang]") return languageButtons;
      return [];
    },
    querySelector() { return null; },
    addEventListener(kind, listener) { listeners.set(kind, listener); },
  };
  const context = vm.createContext({
    document, location: { pathname: "/console/i18n-ui", search: "" }, CSS: { escape: (s) => s },
    localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) },
    EventSource: class {
      constructor() { this.listeners = new Map(); streams.push(this); }
      addEventListener(type, listener) { this.listeners.set(type, listener); }
      emit(type, event) { this.listeners.get(type)({ lastEventId: "1", data: JSON.stringify(event) }); }
    }, setInterval() {}, setTimeout() {}, clearTimeout() {},
    async fetch() { return { json: async () => ({ today_usd: 0, daily_cap_usd: 30 }) }; },
  });
  const run = loadConsole(context);
  run(`handlers.card({ type: "card", event_id: "c1", card_id: "c", version: 2, sku: null, status: "draft", name: "Synthetic bag", price: null,
      currency: "VND", stock: 2, variants: [{ size: "M", color: "black", stock: 2 }], image_url: null, ts_ms: 2,
      provenance: { frame_ts_ms: 2, speech_event_ids: ["s1"], edited_by: null } });
    handlers.card({ type: "card", event_id: "d1", card_id: "d", version: 1, sku: "SYN-3", status: "retired", name: "Old item", price: 50000,
      currency: "VND", stock: 0, variants: [], image_url: null, ts_ms: 1, provenance: { frame_ts_ms: 1, speech_event_ids: [], edited_by: "seller" } });
    handlers.comment({ type: "comment", event_id: "m1", comment_id: "k1", user_ref: "buyer-0900000003", text: "lấy 1 cái", ts_ms: 3 });
    handlers.comment_labeled({ type: "comment_labeled", event_id: "l1", comment_id: "k1", label: "order", action: "warn",
      order_draft: { sku: null, qty: 1, reference_kind: "current_card", referenced_card_id: "c" } });
    handlers.comment_attachment({ event_id: "a1", attachment_id: "a", version: 1, comment_id: "k1", comment_event_id: "m1", label_event_id: "l1",
      outcome: "expired", action: "expire", selected_card_id: null, deadline_ms: 4000, candidates: [{ card_id: "c" }] });
    handlers.order({ type: "order", event_id: "o1-e", order_id: "o1", state: "NEEDS_SELLER", prev_state: "DRAFT", actor: "assistant", reason: "r",
      amount: 0, customer_ref: "buyer-0900000003", caused_by: ["m1"], ts_ms: 6, lines: [{ sku: null, card_id: "c", card_version: 2, qty: 1, unit_price: null, candidates: ["c"] }] });
    handlers.order({ type: "order", event_id: "o2-e", order_id: "o2", state: "CONFIRM_SENT", prev_state: "DRAFT", actor: "assistant", reason: "r",
      amount: 50000, customer_ref: "buyer-0900000004", caused_by: [], ts_ms: 7, lines: [{ sku: "SYN-3", card_id: "d", card_version: 1, qty: 1, unit_price: 50000 }] });
    handlers.model_call({ type: "model_call", event_id: "mc", purpose: "transcribe", latency_ms: 80, ok: true, error: null });`);
  // Visible text only: class names and data attributes are English identifiers, not labels.
  const snapshot = () => ["cards", "comments", "queue", "orders", "models", "conn"].map((id) => nodes.get(id).innerHTML ?? nodes.get(id).textContent)
    .join("\n").replace(/<[^>]+>/g, " ");
  return {
    run, nodes, store, snapshot, document, languageButtons, stream: streams[0],
    staticText: (key) => staticNodes.find((el) => el.dataset.i18n === key)?.textContent,
    click(dataset) { return listeners.get("click")({ target: { closest: () => ({ dataset, disabled: false }) } }); },
  };
}

const dictionaries = () => {
  const context = vm.createContext({});
  vm.runInContext(read("i18n.js"), context);
  return vm.runInContext("I18N", context);
};

test("en and vi hold the same keys and no empty string", () => {
  const { en, vi } = dictionaries();
  assert.deepEqual(Object.keys(vi).sort(), Object.keys(en).sort());
  for (const d of [en, vi]) for (const [k, v] of Object.entries(d)) assert.ok(typeof v === "string" && v.trim(), `empty ${k}`);
  for (const k of Object.keys(en)) {
    const vars = (s) => (s.match(/\{[a-z]+\}/gi) || []).sort();
    assert.deepEqual(vars(vi[k]), vars(en[k]), `placeholders differ for ${k}`);
  }
});

test("every key used by the scripts and the pages exists in the dictionary", () => {
  const { en } = dictionaries();
  const used = new Set();
  for (const name of ["console-render.js", "console.js", "overlay.js"]) {
    const src = read(name);
    for (const m of src.matchAll(/\bt\(\s*"([a-zA-Z0-9_.]+)"/g)) used.add(m[1]);
    for (const m of src.matchAll(/\bt\(`([a-z]+)\.\$\{/g)) used.add(`${m[1]}.*`);
  }
  for (const name of ["console.html", "overlay.html"]) {
    for (const m of read(name).matchAll(/data-i18n="([^"]+)"/g)) used.add(m[1]);
    for (const m of read(name).matchAll(/data-i18n-attr="([^"]+)"/g)) for (const pair of m[1].split(",")) used.add(pair.split(":")[1]);
  }
  const missing = [...used].filter((k) => !(k.endsWith(".*") ? Object.keys(en).some((e) => e.startsWith(k.slice(0, -1))) : k in en));
  assert.deepEqual(missing, []);
  assert.ok(used.size > 60, `only ${used.size} keys referenced`);
  for (const prefix of ["label", "order.state", "card.status", "actor", "comment.action", "conn", "source"]) {
    assert.ok(Object.keys(en).some((k) => k.startsWith(prefix + ".")), `enum prefix ${prefix} missing`);
  }
});

test("the console renders the same events in English by default and fully in Vietnamese after one switch", async () => {
  const ui = setup();
  assert.equal(ui.run("lang"), "en");
  const english = ui.snapshot();
  assert.match(english, /Item not chosen/);
  await ui.click({ lang: "vi" });
  assert.equal(ui.run("lang"), "vi");
  assert.equal(ui.store.get("keviq.lang"), "vi");
  assert.equal(ui.nodes.get("overlay-link").href, "/overlay/i18n-ui?lang=vi");
  const vietnamese = ui.snapshot();
  assert.notEqual(vietnamese, english);
  for (const text of ["Synthetic bag", "Old item", "lấy 1 cái", "buyer-0900000003", "SYN-3"]) {
    assert.ok(english.includes(text) && vietnamese.includes(text), `data text kept: ${text}`);
  }
  const { en, vi } = dictionaries();
  // Labels long enough not to be substrings of other labels ("edited" sits inside "edited by …").
  const englishOnly = Object.keys(en).filter((k) => en[k] !== vi[k] && !en[k].includes("{") && en[k].length >= 8 && english.includes(en[k]));
  assert.ok(englishOnly.length > 10, `expected English labels in the English render, saw ${englishOnly.length}`);
  for (const k of englishOnly) assert.ok(vietnamese.includes(vi[k]), `Vietnamese render lacks ${k}: ${vi[k]}`);
  for (const k of englishOnly) assert.ok(!vietnamese.includes(en[k]), `English label left after the switch: ${k}`);
  assert.doesNotMatch(vietnamese, /\b(cards|queue|order|comment|card|price|item|attachment|models|conn|status)\.[a-z]+(\.[a-zA-Z_]+)?\b/);
});


test("one language click updates static headings, current language and dynamic content synchronously", () => {
  const ui = setup();
  const { en, vi } = dictionaries();
  ui.click({ lang: "vi" });
  for (const key of ["app.title", "queue.title", "cards.title", "comments.title", "status.localTime", "nav.overlay"]) {
    assert.equal(ui.staticText(key), vi[key], `${key} updates in the same click`);
  }
  assert.equal(ui.document.documentElement.lang, "vi");
  for (const button of ui.languageButtons) {
    const selected = button.dataset.lang === "vi";
    assert.equal(button.className, selected ? "active" : "");
    assert.equal(button.attributes["aria-pressed"], String(selected));
  }
  assert.match(ui.nodes.get("queue").innerHTML, /Chưa chọn món/);
  assert.match(ui.nodes.get("orders").innerHTML, /chờ khách/);
  ui.click({ lang: "en" });
  assert.equal(ui.staticText("queue.title"), en["queue.title"]);
  assert.equal(ui.document.documentElement.lang, "en");
  assert.equal(ui.languageButtons.find((b) => b.dataset.lang === "en").attributes["aria-pressed"], "true");
  assert.match(ui.nodes.get("orders").innerHTML, /awaiting customer/);
});

test("the current source translates on receipt and language changes, while unknown data stays unchanged", () => {
  const ui = setup();
  ui.stream.emit("frame", { source: "replay" });
  assert.equal(ui.nodes.get("source").textContent, "replay");
  ui.click({ lang: "vi" });
  assert.equal(ui.nodes.get("source").textContent, "phát lại");
  ui.stream.emit("frame", { source: "sim" });
  assert.equal(ui.nodes.get("source").textContent, "mô phỏng");
  ui.stream.emit("frame", { source: "facebook" });
  assert.equal(ui.nodes.get("source").textContent, "Facebook");
  ui.stream.emit("frame", { source: "synthetic-unknown-source" });
  assert.equal(ui.nodes.get("source").textContent, "synthetic-unknown-source");
  ui.click({ lang: "en" });
  assert.equal(ui.nodes.get("source").textContent, "synthetic-unknown-source");
});

test("seller and customer waiting states have consistent display labels without changing their values", () => {
  const { en, vi } = dictionaries();
  for (const dictionary of [en, vi]) {
    for (const key of ["queue.title", "queue.needsDecision", "nav.queue"]) {
      assert.equal(dictionary[key].toLowerCase(), dictionary["order.state.NEEDS_SELLER"]);
    }
  }
  assert.equal(en["order.state.CONFIRM_SENT"], "awaiting customer");
  assert.equal(vi["order.state.CONFIRM_SENT"], "chờ khách");
});
