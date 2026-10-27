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

function orderHtml(o) {
  const lines = o.lines.map((l) => `${esc(l.sku ?? l.card_id)} v${l.card_version} ×${l.qty} @ ${vnd(l.unit_price)}`).join("<br>");
  return `<div class="item">
    <div class="row"><b>${esc(o.order_id)}</b><span class="tag">${esc(o.state)}</span></div>
    <div>${lines}</div><div>total ${vnd(o.amount)} · ${esc(o.customer_ref)}</div>
    <div class="prov">${esc(o.reason)} · by ${esc(o.actor)} at ${time(o.ts_ms)} · from ${esc((o.caused_by || []).join(", ") || "–")}</div>
  </div>`;
}

function renderOrders() {
  const list = [...orders.values()].sort((a, b) => b.ts_ms - a.ts_ms);
  $("queue").innerHTML = list.filter((o) => o.state === "NEEDS_SELLER").map(orderHtml).join("") || `<div class="muted">nothing waiting</div>`;
  $("orders").innerHTML = list.filter((o) => o.state !== "NEEDS_SELLER").map(orderHtml).join("");
}

function banner(text, level) {
  const b = $("banner");
  b.textContent = text;
  b.className = level;
}

const handlers = {
  card: (e) => { const cur = cards.get(e.card_id); if (!cur || cur.version <= e.version) cards.set(e.card_id, e); renderCards(); },
  comment: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), comment: e }); renderComments(); },
  comment_labeled: (e) => { comments.set(e.comment_id, { ...(comments.get(e.comment_id) || {}), labeled: e }); renderComments(); },
  order: (e) => { orders.set(e.order_id, e); renderOrders(); },
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
  for (const type of ["frame", "speech", "product_seen", "message_out", "message_in"]) {
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
