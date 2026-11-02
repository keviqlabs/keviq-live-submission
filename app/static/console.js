// Stream adapter for the seller console: owns the session state, reads the SSE stream, calls the
// app's seller endpoints and asks console-render.js to draw. Strings live in i18n.js.
const sid = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
currentLang();
let latestSource = null;
const overlayUrl = () => `/overlay/${encodeURIComponent(sid)}?lang=${lang}`;
$("sid").textContent = sid;
$("overlay-link").href = overlayUrl();

const cards = new Map();     // card_id -> latest version
const versions = new Map();  // card_id -> every version seen
const proposals = new Map(); // card_id -> price_proposal events (D-024)
const typed = new Map();     // card_id -> price the seller typed, kept across renders and errors
const cardNotes = new Map(); // card_id -> {key, vars, kind} under the card: the last price save's error or note
const itemCodes = new Map(); // card_id -> catalog code typed by the seller
const itemNotes = new Map(); // card_id -> item selection result
const comments = new Map();  // comment_id -> {comment, labeled}
const orders = new Map();    // order_id -> latest order event
const orderComments = new Map(); // order_id -> real source comment identity
const attachments = new Map(); // attachment_id -> latest immutable decision version
const logs = new Map();      // order_id -> source log (C6) from /orders/{id}/log
const shown = new Set();     // the order whose source log is open (at most one)
const prices = new Map();    // order_id -> unit price the seller typed
const receivedAt = new Map(); // card_id -> ms the newest version arrived (render timing)
const paintedAt = new Map();  // card_id -> {version, ms} of the last paint, so a re-render keeps data-painted-ms
const renderTimings = [];    // {card_id, version, received_ms, painted_ms}; raw samples only, nothing summarised on screen
const modelStats = new Map(); // spark|sam|voice -> {n, p50, lastOk, latencies}
const MODEL_GROUP = { product_seen: "spark", card_from_speech: "spark", label_comments: "spark", card_cutout: "sam", transcribe: "voice" };
const api = (path) => `/api/sessions/${encodeURIComponent(sid)}${path}`;
const orderApi = (oid, path) => api(`/orders/${encodeURIComponent(oid)}${path}`);
if (typeof window !== "undefined") window.__renderTimings = renderTimings;

// Cards the seller hid on the overlay: local to this browser (stored per session, shared with the
// overlay tab through storage + a broadcast channel). The API has no hide flag.
const hideKey = `keviq.overlay.hidden.${sid}`;
const overlayHidden = new Set((() => { try { return JSON.parse(localStorage.getItem(hideKey) || "[]"); } catch { return []; } })());
const hideChannel = typeof BroadcastChannel === "function" ? new BroadcastChannel("keviq-overlay") : null;
function saveHidden() {
  const list = [...overlayHidden];
  try { localStorage.setItem(hideKey, JSON.stringify(list)); } catch { /* storage is optional */ }
  hideChannel?.postMessage({ sid, hidden: list });
}

// D-024: open while the card is not retired, its price is the proposal's current_price and no seller version is
// newer than card_version. Only the seller's route changes a priced card's price, so a newer version with another
// price is a seller version (worker versions copy edited_by, so that field alone does not tell).
function isOpen(p) {
  const c = cards.get(p.card_id);
  return Boolean(c) && c.status !== "retired" && c.price === p.current_price
    && (versions.get(p.card_id) || []).every((v) => v.version <= p.card_version || v.price === p.current_price);
}

async function itemAction(act, cid) {
  const c = cards.get(cid);
  if (!c || c.status !== "draft" || c.sku !== null) return;
  const sku = act === "keep-item" ? null : String(itemCodes.get(cid) ?? "").trim();
  if (sku === "") {
    itemNotes.set(cid, { key: "item.enterCode", kind: "err" });
    return renderCards();
  }
  try {
    const r = await fetch(api(`/cards/${encodeURIComponent(cid)}/item`), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ card_version: c.version, sku }),
    });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) {
      itemNotes.set(cid, { key: "item.failed", vars: { status: r.status, detail: typeof out.detail === "string" ? out.detail : r.statusText }, kind: "err" });
    } else {
      if (out.changed) itemCodes.delete(cid);
      itemNotes.set(cid, { key: out.changed ? "item.assigned" : "item.kept", kind: "note" });
    }
  } catch {
    itemNotes.set(cid, { key: "item.noResult", kind: "err" });
  }
  renderCards();
}

// The seller's price for a card (POST /cards/{id}/price): errors stay under the card with the typed text.
async function setPrice(cid, body) {
  const r = await fetch(api(`/cards/${encodeURIComponent(cid)}/price`), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = out.detail, why = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : r.statusText;
    cardNotes.set(cid, { key: "price.failed", vars: { status: r.status, detail: why }, kind: "err" });
  } else if (out.changed) {
    cardNotes.delete(cid);
    if (!body.proposal_event_id) typed.delete(cid);
  } else {
    cardNotes.set(cid, { key: "price.same", vars: { price: vnd(body.price) }, kind: "note" });
  }
  renderCards();
}

async function cardAction(act, cid, pid) {
  const c = cards.get(cid);
  if (act === "use") {
    const p = (proposals.get(cid) || []).find((x) => x.event_id === pid);
    return setPrice(cid, { price: p.heard_price, card_version: c.version, proposal_event_id: pid });
  }
  const raw = String(typed.get(cid) ?? "").trim();
  if (!/^\d+$/.test(raw)) {
    cardNotes.set(cid, { key: "price.invalid", kind: "err" });
    return renderCards();
  }
  return setPrice(cid, { price: Number(raw), card_version: c.version });
}

async function loadLog(oid) {
  const r = await fetch(orderApi(oid, "/log"));
  if (r.ok) { logs.set(oid, await r.json()); renderOrders(); }
}

// The stream replays many events at once; refetch each stale log once it settles.
const stale = new Set();
let staleTimer = null;
function refreshLogs(ids) {
  for (const id of ids) stale.add(id);
  clearTimeout(staleTimer);
  staleTimer = setTimeout(() => { for (const id of stale) loadLog(id); stale.clear(); }, 250);
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  if (r.ok) return r.json();
  const err = await r.json().catch(() => ({}));
  banner(t("error.request", { status: r.status, detail: typeof err.detail === "string" ? err.detail : r.statusText }), "warn");
  return null;
}

// A customer tapping a quick reply, as the Page webhook body Meta would send (simulated sessions only).
function simReply(o, yes) {
  const ts = Date.now(), psid = o.customer_ref.replace(/[^A-Za-z0-9.:-]/g, "-");
  const message = { mid: `m_${psid}_${ts}`, text: yes ? "Đồng ý" : "Huỷ", quick_reply: { payload: `${yes ? "CONFIRM" : "CANCEL"}:${o.order_id}` } };
  return { object: "page", entry: [{ id: "sim-page", time: ts, messaging: [{ sender: { id: psid }, recipient: { id: "sim-page" }, timestamp: ts, message }] }] };
}

const unitPrice = (raw) => (/^\d+$/.test(raw) && Number(raw) > 0 ? Number(raw) : null);

function renderSource() { $("source").textContent = latestSource == null ? t("status.unknown") : tEnum("source", latestSource); }

function rerender() {
  renderSource();
  $("overlay-link").href = overlayUrl();
  tick();
  renderConnection(connState);
  renderModels();
  renderCards();
  renderComments();
  renderOrders();
}

document.addEventListener("input", (ev) => {
  const id = ev.target.dataset?.price, cid = ev.target.dataset?.cardPrice;
  if (id) {
    prices.set(id, ev.target.value);
    const output = document.querySelector(`[data-price-total="${CSS.escape(id)}"]`);
    if (output && orders.has(id)) output.textContent = proposedTotal(orders.get(id), ev.target.value);
  }
  if (cid) typed.set(cid, ev.target.value);
  if (ev.target.dataset?.cardSku) itemCodes.set(ev.target.dataset.cardSku, ev.target.value);
});
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act], button[data-lang]");
  if (!b) return;
  if (b.dataset.lang) { setLang(b.dataset.lang, rerender); return; }
  const oid = b.dataset.oid, act = b.dataset.act;
  if (!act) return;
  if (act.startsWith("attachment-")) {
    const attachment = attachments.get(b.dataset.aid);
    if (!attachment) return;
    b.disabled = true;
    const decision = { "attachment-select": "select", "attachment-keep": "keep_unresolved", "attachment-reject": "reject" }[act];
    const body = { attachment_version: attachment.version, decision };
    if (decision === "select") body.card_id = b.dataset.choice;
    await post(api(`/attachments/${encodeURIComponent(attachment.attachment_id)}/decision`), body);
    b.disabled = false;
    return;
  }
  if (act === "assign-item" || act === "keep-item") {
    b.disabled = true;
    await itemAction(act, b.dataset.cid);
    b.disabled = false;
    return;
  }
  if (act === "use" || act === "save") {
    b.disabled = true;
    await cardAction(act, b.dataset.cid, b.dataset.pid);
    b.disabled = false;
    return;
  }
  if (act === "overlay-hide" || act === "overlay-show") {
    if (act === "overlay-hide") overlayHidden.add(b.dataset.cid); else overlayHidden.delete(b.dataset.cid);
    saveHidden();
    renderCards();
    return;
  }
  if (act === "open-order") {
    document.querySelector(`[data-order-id="${CSS.escape(oid)}"]`)?.scrollIntoView?.({ behavior: "smooth", block: "center" });
    return;
  }
  if (act === "log") {
    if (!shown.delete(oid)) { shown.add(oid); loadLog(oid); }
    renderOrders();
    return;
  }
  if (act === "close-log") { shown.clear(); renderOrders(); return; }
  if (act === "choose-order") {
    const body = { decision: "approve", card_id: b.dataset.choice };
    const raw = String(prices.get(oid) ?? "").trim();
    if (raw) {
      const price = unitPrice(raw);
      if (price == null) { banner(t("queue.invalidPrice"), "warn"); return; }
      body.unit_price = price;
    }
    b.disabled = true;
    await post(orderApi(oid, "/decision"), body);
  } else if (act === "price") {
    const price = unitPrice(String(prices.get(oid) ?? document.querySelector(`[data-price="${CSS.escape(oid)}"]`)?.value ?? "").trim());
    if (price == null) { banner(t("queue.invalidPrice"), "warn"); return; }
    b.disabled = true;
    await post(orderApi(oid, "/decision"), { decision: "approve", unit_price: price });
  } else if (act === "card" || act === "reject") {
    b.disabled = true;
    await post(orderApi(oid, "/decision"), { decision: act === "card" ? "approve" : "reject" });
  } else if (act === "yes" || act === "no") {
    b.disabled = true;
    await post(api("/sim/messenger"), simReply(orders.get(oid), act === "yes"));
  }
  b.disabled = false;
});
$("log-dialog").addEventListener?.("close", () => { if (shown.size) { shown.clear(); renderOrders(); } });

// Render timing for a later C8 measurement: received = handler entry, painted = two animation
// frames after the DOM write (an approximation of the first paint). Raw samples only.
function markPainted(e) {
  const received = receivedAt.get(e.card_id);
  const paint = () => {
    const painted = Date.now();
    const prev = paintedAt.get(e.card_id);
    if (!prev || prev.version <= e.version) paintedAt.set(e.card_id, { version: e.version, ms: painted });
    const el = document.querySelector(`[data-card-id="${CSS.escape(e.card_id)}"]`);
    el?.setAttribute?.("data-painted-ms", String(painted));
    renderTimings.push({ card_id: e.card_id, version: e.version, received_ms: received, painted_ms: painted });
    if (renderTimings.length > 500) renderTimings.shift();
    if (typeof console !== "undefined") console.debug?.("card painted", e.card_id, e.version, received, painted);
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => requestAnimationFrame(paint)); else paint();
}

function noteModelCall(e) {
  const group = MODEL_GROUP[e.purpose];
  if (!group) return;
  const s = modelStats.get(group) || { n: 0, p50: null, lastOk: true, latencies: [] };
  s.n += 1;
  s.lastOk = Boolean(e.ok);
  if (typeof e.latency_ms === "number") {
    s.latencies.push(e.latency_ms);
    if (s.latencies.length > 500) s.latencies.shift();
    const sorted = [...s.latencies].sort((a, b) => a - b);
    s.p50 = sorted[Math.floor((sorted.length - 1) / 2)];
  }
  modelStats.set(group, s);
  renderModels();
}

const handlers = {
  comment_attachment: (e) => {
    const current = attachments.get(e.attachment_id);
    if (!current || current.version <= e.version) attachments.set(e.attachment_id, e);
    renderComments();
  },
  card: (e) => {
    const cur = cards.get(e.card_id);
    if (!cur || cur.version <= e.version) { cards.set(e.card_id, e); receivedAt.set(e.card_id, Date.now()); }
    versions.set(e.card_id, [...(versions.get(e.card_id) || []), e]);
    renderCards();
    renderComments();
    markPainted(e);
  },
  price_proposal: (e) => { proposals.set(e.card_id, [...(proposals.get(e.card_id) || []), e]); renderCards(); },
  comment: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), comment: e }); renderComments(); },
  comment_labeled: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), labeled: e }); renderComments(); },
  order: (e) => {
    const source = [...comments.values()].find((row) => row.comment && (e.caused_by || []).includes(row.comment.event_id));
    if (source) orderComments.set(e.order_id, source.comment.comment_id);
    orders.set(e.order_id, e);
    if (e.state === "NEEDS_SELLER" || shown.has(e.order_id)) refreshLogs([e.order_id]);
    renderOrders();
    renderComments();
  },
  message_out: () => { if (shown.size) refreshLogs(shown); },
  message_in: () => { if (shown.size) refreshLogs(shown); },
  model_call: (e) => {
    noteModelCall(e);
    if (!e.ok && e.error === "daily_cap") banner(t("cost.stop"), "stop");
    refreshCost();
  },
};

let lastSeq = 0, connState = "connecting";
function connect() {
  const es = new EventSource(`/api/sessions/${encodeURIComponent(sid)}/events?cursor=${lastSeq}`);
  es.onopen = () => { connState = "live"; renderConnection(connState); };
  es.onerror = () => { connState = "reconnecting"; renderConnection(connState); };
  const seen = (m) => {
    lastSeq = Number(m.lastEventId) || lastSeq;
    renderConnection(connState, lastSeq);
    const e = JSON.parse(m.data);
    if (e.source) { latestSource = e.source; renderSource(); }
    return e;
  };
  for (const [type, fn] of Object.entries(handlers)) es.addEventListener(type, (m) => fn(seen(m)));
  for (const type of ["frame", "speech", "product_seen"]) es.addEventListener(type, seen);
}

async function refreshCost() {
  try {
    const c = await (await fetch("/api/cost")).json();
    renderCost(c);
    if (c.today_usd >= c.daily_cap_usd) banner(t("cost.stop"), "stop");
    else if (c.daily_warn_usd != null && c.today_usd >= c.daily_warn_usd) banner(t("cost.warn", { warn: c.daily_warn_usd }), "warn");
  } catch { /* cost is informational; the stream keeps running */ }
}

function tick() { $("clock").textContent = new Date().toLocaleTimeString(timeLocale()); }

applyStaticText();
rerender();
connect();
refreshCost();
setInterval(refreshCost, 10000);
setInterval(tick, 1000);
