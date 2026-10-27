// Minimal seller console: renders the session event stream into three columns.
const sid = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
const $ = (id) => document.getElementById(id);
$("sid").textContent = sid;
$("overlay-link").href = `/overlay/${encodeURIComponent(sid)}`;

const cards = new Map();     // card_id -> latest version
const comments = new Map();  // comment_id -> {comment, labeled}
const orders = new Map();    // order_id -> latest order event
const vnd = (n) => (n == null ? "giá chưa có" : n.toLocaleString("vi-VN") + " ₫");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const time = (ms) => new Date(ms).toLocaleTimeString("vi-VN");

function renderCards() {
  const list = [...cards.values()].sort((a, b) => b.ts_ms - a.ts_ms);
  $("cards").innerHTML = list.map((c) => `
    <div class="item">
      ${c.image_url ? `<img class="thumb" src="${esc(c.image_url)}" alt="">` : ""}
      <div class="row"><b>${esc(c.name)}</b><span class="tag ${esc(c.status)}">${esc(c.status)} v${c.version}</span></div>
      <div>${vnd(c.price)} · ${esc(c.sku ?? "no sku")} · stock ${c.stock}</div>
      <div class="prov">frame ${c.provenance.frame_ts_ms ? time(c.provenance.frame_ts_ms) : "–"} · speech ${esc(c.provenance.speech_event_ids.join(", ") || "–")} · ${c.provenance.edited_by ? "edited by " + esc(c.provenance.edited_by) : "model"}</div>
    </div>`).join("");
}

function renderComments() {
  const list = [...comments.values()].filter((r) => r.comment).sort((a, b) => b.comment.ts_ms - a.comment.ts_ms).slice(0, 200);
  $("comments").innerHTML = list.map(({ comment: c, labeled: l }) => `
    <div class="item ${l && l.action === "hide" ? "hidden-comment" : ""}">
      <div class="row"><span>${esc(c.user_ref)} · ${time(c.ts_ms)}</span>${l ? `<span class="tag ${esc(l.label)}">${esc(l.label)}${l.action !== "none" ? " · " + esc(l.action) : ""}</span>` : `<span class="tag">unlabeled</span>`}</div>
      <div>${esc(c.text)}</div>
      ${l && l.order_draft ? `<div class="prov">draft: ${esc(l.order_draft.sku ?? "?")} ×${l.order_draft.qty ?? "?"} · ref ${esc(l.order_draft.reference_kind)} → ${esc(l.order_draft.referenced_card_id ?? "–")}</div>` : ""}
    </div>`).join("");
}

const logs = new Map();    // order_id -> source log (C6) from /orders/{id}/log
const shown = new Set();   // order_ids whose source log is open
const prices = new Map();  // order_id -> unit price the seller typed
const api = (path) => `/api/sessions/${encodeURIComponent(sid)}${path}`;
const orderApi = (oid, path) => api(`/orders/${encodeURIComponent(oid)}${path}`);

function logHtml(g) {
  if (!g) return `<div class="log muted">loading…</div>`;
  const c = g.comment, k = g.card, ask = g.discount_request;
  return `<div class="log">
    <div>comment ${c ? `${esc(c.comment_id)} · ${esc(c.user_ref)} · ${time(c.ts_ms)}: “${esc(c.text)}”` : "–"}</div>
    ${g.label ? `<div>label ${esc(g.label.label)} · ${esc(g.label.event_id)}</div>` : ""}
    <div>card ${k ? `${esc(k.card_id)} v${k.version} · ${esc(k.name)} · ${vnd(k.price)} · ${esc(k.status)} at ${time(k.ts_ms)}` : "–"}</div>
    ${ask ? `<div>customer asked: “${esc(ask.phrase)}”${ask.value != null ? ` → ${vnd(ask.value)}` : ""}</div>` : ""}
    <ol>${g.steps.map((s) => `<li>${esc(s.prev_state ?? "–")} → <b>${esc(s.state)}</b> · ${esc(s.actor)} · ${time(s.ts_ms)} · ${esc(s.reason)}</li>`).join("")}</ol>
    ${g.messages.map((m) => `<div>${m.type === "message_out" ? "→" : "←"} ${esc(m.template ?? m.type)} · ${time(m.ts_ms)}${m.text ? `: <span class="msg">${esc(m.text)}</span>` : ""}</div>`).join("")}
  </div>`;
}

function actionsHtml(o) {
  const id = esc(o.order_id);
  if (o.state === "NEEDS_SELLER") {
    const ask = logs.get(o.order_id)?.discount_request;
    const value = prices.get(o.order_id) ?? (ask?.kind === "target" ? ask.value : "");
    return `<div class="actions">
      <input type="number" min="1" step="1000" data-price="${id}" value="${esc(value)}" placeholder="đơn giá" aria-label="unit price for ${id}">
      <button data-act="price" data-oid="${id}">Duyệt đơn giá này</button>
      <button data-act="card" data-oid="${id}">Giữ giá thẻ</button>
      <button data-act="reject" data-oid="${id}" class="bad">Từ chối</button>
    </div>`;
  }
  if (o.state === "CONFIRM_SENT") {
    return `<div class="actions"><span class="tag">sim</span>
      <button data-act="yes" data-oid="${id}">Khách đồng ý (sim)</button>
      <button data-act="no" data-oid="${id}">Khách huỷ (sim)</button>
    </div>`;
  }
  return "";
}

function orderHtml(o) {
  const lines = o.lines.map((l) => `${esc(l.sku ?? l.card_id)} v${l.card_version} ×${l.qty} @ ${vnd(l.unit_price)}`).join("<br>");
  return `<div class="item">
    <div class="row"><b>${esc(o.order_id)}</b><span><span class="tag">${esc(o.state)}</span> <button data-act="log" data-oid="${esc(o.order_id)}">Nguồn</button></span></div>
    <div>${lines}</div><div>total ${vnd(o.amount)} · ${esc(o.customer_ref)}</div>
    <div class="prov">${esc(o.reason)} · by ${esc(o.actor)} at ${time(o.ts_ms)} · from ${esc((o.caused_by || []).join(", ") || "–")}</div>
    ${actionsHtml(o)}
    ${shown.has(o.order_id) ? logHtml(logs.get(o.order_id)) : ""}
  </div>`;
}

function renderOrders() {
  const focused = document.activeElement?.dataset?.price;
  const list = [...orders.values()].sort((a, b) => b.ts_ms - a.ts_ms);
  $("queue").innerHTML = list.filter((o) => o.state === "NEEDS_SELLER").map(orderHtml).join("") || `<div class="muted">nothing waiting</div>`;
  $("orders").innerHTML = list.filter((o) => o.state !== "NEEDS_SELLER").map(orderHtml).join("");
  if (focused) document.querySelector(`[data-price="${CSS.escape(focused)}"]`)?.focus();
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
  banner(`${r.status}: ${typeof err.detail === "string" ? err.detail : r.statusText}`, "warn");
  return null;
}

// A customer tapping a quick reply, as the Page webhook body Meta would send (simulated sessions only).
function simReply(o, yes) {
  const ts = Date.now(), psid = o.customer_ref.replace(/[^A-Za-z0-9.:-]/g, "-");
  const message = { mid: `m_${psid}_${ts}`, text: yes ? "Đồng ý" : "Huỷ", quick_reply: { payload: `${yes ? "CONFIRM" : "CANCEL"}:${o.order_id}` } };
  return { object: "page", entry: [{ id: "sim-page", time: ts, messaging: [{ sender: { id: psid }, recipient: { id: "sim-page" }, timestamp: ts, message }] }] };
}

document.addEventListener("input", (ev) => { const id = ev.target.dataset?.price; if (id) prices.set(id, ev.target.value); });
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  const oid = b.dataset.oid, act = b.dataset.act;
  if (act === "log") {
    if (shown.delete(oid)) renderOrders(); else { shown.add(oid); renderOrders(); loadLog(oid); }
    return;
  }
  if (act === "price") {
    const raw = String(prices.get(oid) ?? document.querySelector(`[data-price="${CSS.escape(oid)}"]`)?.value ?? "").trim();
    if (!/^\d+$/.test(raw) || Number(raw) <= 0) { banner("Nhập đơn giá là số nguyên VND lớn hơn 0.", "warn"); return; }
    b.disabled = true;
    await post(orderApi(oid, "/decision"), { decision: "approve", unit_price: Number(raw) });
  } else if (act === "card" || act === "reject") {
    b.disabled = true;
    await post(orderApi(oid, "/decision"), { decision: act === "card" ? "approve" : "reject" });
  } else if (act === "yes" || act === "no") {
    b.disabled = true;
    await post(api("/sim/messenger"), simReply(orders.get(oid), act === "yes"));
  }
  b.disabled = false;
});

function banner(text, level) {
  const b = $("banner");
  b.textContent = text;
  b.className = level;
}

const handlers = {
  card: (e) => { const cur = cards.get(e.card_id); if (!cur || cur.version <= e.version) cards.set(e.card_id, e); renderCards(); },
  comment: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), comment: e }); renderComments(); },
  comment_labeled: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), labeled: e }); renderComments(); },
  order: (e) => {
    orders.set(e.order_id, e);
    if (e.state === "NEEDS_SELLER" || shown.has(e.order_id)) refreshLogs([e.order_id]);
    renderOrders();
  },
  message_out: () => { if (shown.size) refreshLogs(shown); },
  message_in: () => { if (shown.size) refreshLogs(shown); },
  model_call: (e) => { if (!e.ok && e.error === "daily_cap") banner("Daily model budget reached: model calls are stopped until tomorrow (VN time).", "stop"); refreshCost(); },
};

let lastSeq = 0;
function connect() {
  const es = new EventSource(`/api/sessions/${encodeURIComponent(sid)}/events?cursor=${lastSeq}`);
  es.onopen = () => { $("conn").textContent = "live"; };
  es.onerror = () => { $("conn").textContent = "reconnecting"; };
  for (const [type, fn] of Object.entries(handlers)) {
    es.addEventListener(type, (m) => { lastSeq = Number(m.lastEventId) || lastSeq; $("seq").textContent = lastSeq; fn(JSON.parse(m.data)); });
  }
  for (const type of ["frame", "speech", "product_seen"]) {
    es.addEventListener(type, (m) => { lastSeq = Number(m.lastEventId) || lastSeq; $("seq").textContent = lastSeq; });
  }
}

async function refreshCost() {
  try {
    const c = await (await fetch("/api/cost")).json();
    $("cost").textContent = c.today_usd.toFixed(4);
    $("cap").textContent = c.daily_cap_usd;
    if (c.today_usd >= c.daily_cap_usd) banner("Daily model budget reached: model calls are stopped until tomorrow (VN time).", "stop");
    else if (c.today_usd >= c.daily_warn_usd) banner(`Model spend today passed the ${c.daily_warn_usd} USD warning line.`, "warn");
  } catch { /* cost is informational; the stream keeps running */ }
}

connect();
refreshCost();
setInterval(refreshCost, 10000);
