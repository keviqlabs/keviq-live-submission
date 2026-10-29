// Minimal seller console: renders the session event stream into three columns.
const sid = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
const $ = (id) => document.getElementById(id);
$("sid").textContent = sid;
$("overlay-link").href = `/overlay/${encodeURIComponent(sid)}`;

const cards = new Map();     // card_id -> latest version
const versions = new Map();  // card_id -> every version seen
const proposals = new Map(); // card_id -> price_proposal events (D-024)
const typed = new Map();     // card_id -> price the seller typed, kept across renders and errors
const cardNotes = new Map(); // card_id -> {text, kind} under the card: the last price save's error or note
const itemCodes = new Map(); // card_id -> catalog code typed by the seller
const itemNotes = new Map(); // card_id -> item selection result
const comments = new Map();  // comment_id -> {comment, labeled}
const orders = new Map();    // order_id -> latest order event
const orderComments = new Map(); // order_id -> real source comment identity
const attachments = new Map(); // attachment_id -> latest immutable decision version
const vnd = (n) => (n == null ? "giá chưa có" : n.toLocaleString("vi-VN") + " ₫");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const time = (ms) => new Date(ms).toLocaleTimeString("vi-VN");

// D-024: open while the card is not retired, its price is the proposal's current_price and no seller version is
// newer than card_version. Only the seller's route changes a priced card's price, so a newer version with another
// price is a seller version (worker versions copy edited_by, so that field alone does not tell).
function isOpen(p) {
  const c = cards.get(p.card_id);
  return Boolean(c) && c.status !== "retired" && c.price === p.current_price
    && (versions.get(p.card_id) || []).every((v) => v.version <= p.card_version || v.price === p.current_price);
}

function priceHtml(c) {
  if (c.status === "retired") return "";
  if (c.status === "draft" && c.sku === null) return `<div class="prov">Chọn mã món trước khi duyệt giá.</div>`;
  const id = esc(c.card_id), note = cardNotes.get(c.card_id);
  const open = (proposals.get(c.card_id) || []).filter(isOpen).sort((a, b) => b.ts_ms - a.ts_ms);
  return `${open.map((p) => `<div class="proposal">Nghe được ${vnd(p.heard_price)} lúc ${time(p.ts_ms)} (đang ${vnd(p.current_price)})
      <button data-act="use" data-cid="${id}" data-pid="${esc(p.event_id)}">Dùng giá này</button></div>`).join("")}
    <div class="actions">
      <input type="number" min="1000" step="1000" data-card-price="${id}" value="${esc(typed.get(c.card_id) ?? "")}" placeholder="giá mới" aria-label="giá mới cho ${id}">
      <button data-act="save" data-cid="${id}">Lưu giá</button>
    </div>
    ${note ? `<div class="${note.kind}">${esc(note.text)}</div>` : ""}`;
}

function renderCards() {
  const focused = document.activeElement?.dataset?.cardPrice;
  const focusedItem = document.activeElement?.dataset?.cardSku;
  const rank = (c) => (versions.get(c.card_id) || [c]).reduce((best, v) => {
    const frame = v.provenance.frame_ts_ms;
    return frame != null && (frame > best[0] || (frame === best[0] && v.version > best[1])) ? [frame, v.version] : best;
  }, [-1, 0]);
  const list = [...cards.values()].sort((a, b) => rank(b)[0] - rank(a)[0] || rank(b)[1] - rank(a)[1]);
  $("cards").innerHTML = list.map((c) => `
    <div class="item">
      ${c.image_url ? `<img class="thumb" src="${esc(c.image_url)}" alt="">` : ""}
      <div class="row"><b>${esc(c.name)}</b><span class="tag ${esc(c.status)}">${esc(c.status)} v${c.version}</span></div>
      <div>${vnd(c.price)} · ${esc(c.sku ?? "no sku")} · stock ${c.stock}</div>
      <div class="prov">frame ${c.provenance.frame_ts_ms ? time(c.provenance.frame_ts_ms) : "–"} · speech ${esc(c.provenance.speech_event_ids.join(", ") || "–")} · ${c.provenance.edited_by ? "edited by " + esc(c.provenance.edited_by) : "model"}</div>
      ${itemHtml(c)}
      ${priceHtml(c)}
    </div>`).join("");
  if (focused) document.querySelector(`[data-card-price="${CSS.escape(focused)}"]`)?.focus();
  if (focusedItem) document.querySelector(`[data-card-sku="${CSS.escape(focusedItem)}"]`)?.focus();
}

function itemHtml(c) {
  if (c.sku !== null || c.status === "retired") return "";
  if (c.status !== "draft") return `<div class="prov">Chưa xác định mã món. Thẻ này chưa hỗ trợ gán mã khi đã có giá.</div>`;
  const id = esc(c.card_id), note = itemNotes.get(c.card_id);
  return `<div class="actions">
    <input type="text" data-card-sku="${id}" value="${esc(itemCodes.get(c.card_id) ?? "")}" placeholder="mã món trong danh mục" aria-label="mã món cho ${id}">
    <button data-act="assign-item" data-cid="${id}">Chọn mã món</button>
    <button data-act="keep-item" data-cid="${id}">Giữ chưa rõ</button>
    </div>${note ? `<div class="${note.kind}">${esc(note.text)}</div>` : ""}`;
}

async function itemAction(act, cid) {
  const c = cards.get(cid);
  if (!c || c.status !== "draft" || c.sku !== null) return;
  const sku = act === "keep-item" ? null : String(itemCodes.get(cid) ?? "").trim();
  if (sku === "") {
    itemNotes.set(cid, { text: "Nhập đúng mã món trong danh mục.", kind: "err" });
    return renderCards();
  }
  try {
    const r = await fetch(api(`/cards/${encodeURIComponent(cid)}/item`), {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ card_version: c.version, sku }),
    });
    const out = await r.json().catch(() => ({}));
    if (!r.ok) {
      itemNotes.set(cid, { text: `Không chọn được (${r.status}): ${typeof out.detail === "string" ? out.detail : r.statusText}`, kind: "err" });
    } else {
      if (out.changed) itemCodes.delete(cid);
      itemNotes.set(cid, { text: out.changed ? "Đã chọn món. Thẻ vẫn chờ duyệt giá." : "Giữ món chưa rõ; chưa tạo đơn.", kind: "prov" });
    }
  } catch {
    itemNotes.set(cid, { text: "Chưa nhận được kết quả. Tải lại thẻ trước khi chọn tiếp.", kind: "err" });
  }
  renderCards();
}

// The seller's price for a card (POST /cards/{id}/price): errors stay under the card with the typed text.
async function setPrice(cid, body) {
  const r = await fetch(api(`/cards/${encodeURIComponent(cid)}/price`), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const out = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = out.detail, why = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : r.statusText;
    cardNotes.set(cid, { text: `Không lưu được (${r.status}): ${why}`, kind: "err" });
  } else if (out.changed) {
    cardNotes.delete(cid);
    if (!body.proposal_event_id) typed.delete(cid);
  } else {
    cardNotes.set(cid, { text: `Giá đã là ${vnd(body.price)}, không có version mới.`, kind: "prov" });
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
    cardNotes.set(cid, { text: "Nhập giá là số nguyên VND.", kind: "err" });
    return renderCards();
  }
  return setPrice(cid, { price: Number(raw), card_version: c.version });
}

function renderComments() {
  const list = [...comments.values()].filter((r) => r.comment).sort((a, b) => b.comment.ts_ms - a.comment.ts_ms).slice(0, 200);
  $("comments").innerHTML = list.map(({ comment: c, labeled: l }) => `
    <div class="item ${l && l.action === "hide" ? "hidden-comment" : ""}">
      <div class="row"><span>${esc(c.user_ref)} · ${time(c.ts_ms)}</span>${l ? `<span class="tag ${esc(l.label)}">${esc(l.label)}${l.action !== "none" ? " · " + esc(l.action) : ""}</span>` : `<span class="tag">unlabeled</span>`}</div>
      <div>${esc(c.text)}</div>
      ${l && l.order_draft ? `<div class="prov">draft: ${esc(l.order_draft.sku ?? "?")} ×${l.order_draft.qty ?? "?"} · ref ${esc(l.order_draft.reference_kind)} → ${esc(l.order_draft.referenced_card_id ?? "–")}</div>` : ""}
      ${attachmentHtml(c.comment_id)}
    </div>`).join("");
}

function attachmentHtml(commentId) {
  const a = [...attachments.values()].find((value) => value.comment_id === commentId);
  if (!a) return "";
  const hasOrder = [...orderComments.values()].some((cid) => cid === commentId);
  const selected = cards.get(a.selected_card_id);
  const name = selected?.name || a.selected_card_id;
  let note = a.action === "reject" ? "Shop đã đóng yêu cầu; chưa nhắn khách." : a.selected_card_id
    ? `Đã giữ đúng món ${name}. ${hasOrder ? "Theo dõi trong đơn." : "Chờ mã món và giá được duyệt trên chính thẻ này."}`
    : "Cần shop chọn món từ ảnh chụp lúc hết hạn.";
  if (!a.candidates.length) note = "Không có thẻ trong ảnh chụp; chỉ có thể giữ chưa rõ hoặc từ chối.";
  const controls = hasOrder || a.action === "reject" ? "" : `<div class="actions">
    ${a.outcome === "expired" && !a.selected_card_id ? a.candidates.map((c) => {
      const card = cards.get(c.card_id);
      return `<button data-act="attachment-select" data-aid="${esc(a.attachment_id)}" data-choice="${esc(c.card_id)}">Chọn ${esc(card?.name || c.card_id)}</button>`;
    }).join("") : ""}
    <button data-act="attachment-keep" data-aid="${esc(a.attachment_id)}">Giữ chưa rõ</button>
    <button data-act="attachment-reject" data-aid="${esc(a.attachment_id)}" class="bad">Từ chối yêu cầu</button>
    </div>`;
  return `<div class="prov">${esc(note)} · v${a.version} · hạn ${time(a.deadline_ms)}</div>${controls}`;
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
    const candidates = o.lines.find((line) => line.candidates)?.candidates;
    if (candidates) return `<div class="actions">
      <input type="number" min="1" step="1000" data-price="${id}" value="${esc(value)}" placeholder="giá shop duyệt (tuỳ chọn)" aria-label="đơn giá cho ${id}">
      ${candidates.map((cid) => `<button data-act="choose-order" data-oid="${id}" data-choice="${esc(cid)}">Chọn ${esc(cards.get(cid)?.name || cid)}</button>`).join("")}
      <button data-act="reject" data-oid="${id}" class="bad">Từ chối</button></div>`;
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
    ${o.state === "NEEDS_SELLER" && o.lines.some((l) => l.candidates) ? `<div class="prov">Chưa chọn món. Shop chọn một thẻ bên dưới trước khi xác nhận.</div>` : ""}
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

document.addEventListener("input", (ev) => {
  const id = ev.target.dataset?.price, cid = ev.target.dataset?.cardPrice;
  if (id) prices.set(id, ev.target.value);
  if (cid) typed.set(cid, ev.target.value);
  if (ev.target.dataset?.cardSku) itemCodes.set(ev.target.dataset.cardSku, ev.target.value);
});
document.addEventListener("click", async (ev) => {
  const b = ev.target.closest("button[data-act]");
  if (!b) return;
  const oid = b.dataset.oid, act = b.dataset.act;
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
  if (act === "log") {
    if (shown.delete(oid)) renderOrders(); else { shown.add(oid); renderOrders(); loadLog(oid); }
    return;
  }
  if (act === "choose-order") {
    const body = { decision: "approve", card_id: b.dataset.choice };
    const raw = String(prices.get(oid) ?? "").trim();
    if (raw) {
      if (!/^\d+$/.test(raw) || Number(raw) <= 0) { banner("Nhập đơn giá là số nguyên VND lớn hơn 0.", "warn"); return; }
      body.unit_price = Number(raw);
    }
    b.disabled = true;
    await post(orderApi(oid, "/decision"), body);
  } else if (act === "price") {
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
  comment_attachment: (e) => {
    const current = attachments.get(e.attachment_id);
    if (!current || current.version <= e.version) attachments.set(e.attachment_id, e);
    renderComments();
  },
  card: (e) => {
    const cur = cards.get(e.card_id);
    if (!cur || cur.version <= e.version) cards.set(e.card_id, e);
    versions.set(e.card_id, [...(versions.get(e.card_id) || []), e]);
    renderCards();
    renderComments();
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
