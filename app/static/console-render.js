// Renderer for the seller console: HTML builders plus the functions that write them into the DOM.
// It reads the state maps owned by console.js (the stream adapter) at call time and never fetches
// or posts. Every visible string comes from i18n.js; event data is shown as is.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const vnd = (n) => (n == null ? t("price.none") : n.toLocaleString("vi-VN") + " ₫");
const timeLocale = () => (lang === "vi" ? "vi-VN" : "en-GB");
const time = (ms) => (ms == null ? "—" : new Date(ms).toLocaleTimeString(timeLocale()));
// Cut-outs arrive as data: URLs; fixture placeholders (sim://) and other schemes are not drawn.
const imageUrl = (u) => (/^(data:image\/|https?:|\/)/.test(u || "") ? u : "");
const initial = (s) => String(s || "?").trim().charAt(0).toUpperCase() || "?";
const noteHtml = (n, cls = "card-note") => (n ? `<div class="${cls}${n.kind === "err" ? " err" : ""}">${esc(t(n.key, n.vars))}</div>` : "");
const emptyHtml = (icon, titleKey, bodyKey) =>
  `<div class="empty-state"><div class="empty-icon">${icon}</div><h3>${esc(t(titleKey))}</h3><p>${esc(t(bodyKey))}</p></div>`;
const focusBack = (attr, value) => { if (value) document.querySelector(`[${attr}="${CSS.escape(value)}"]`)?.focus(); };
const statusClass = (s) => (s === "live" ? "live" : s === "draft" ? "warning" : s === "retired" ? "danger" : "");

// ---- live cards -------------------------------------------------------------------------------
function priceHtml(c) {
  if (c.status === "retired") return "";
  if (c.status === "draft" && c.sku === null) return `<div class="card-note">${esc(t("price.pickItemFirst"))}</div>`;
  const id = esc(c.card_id);
  const open = (proposals.get(c.card_id) || []).filter(isOpen).sort((a, b) => b.ts_ms - a.ts_ms);
  return `${open.map((p) => `<div class="proposal"><span>${esc(t("price.heard", { heard: vnd(p.heard_price), time: time(p.ts_ms), current: vnd(p.current_price) }))}</span>
      <button type="button" class="btn small" data-act="use" data-cid="${id}" data-pid="${esc(p.event_id)}">${esc(t("price.use"))}</button></div>`).join("")}
    <div class="card-actions">
      <input type="number" min="1000" step="1000" inputmode="numeric" data-card-price="${id}" value="${esc(typed.get(c.card_id) ?? "")}" placeholder="${esc(t("price.placeholder"))}" aria-label="${esc(t("price.aria", { id: c.card_id }))}">
      <button type="button" class="btn primary" data-act="save" data-cid="${id}">${esc(t("price.save"))}</button>
    </div>${noteHtml(cardNotes.get(c.card_id))}`;
}

function itemHtml(c) {
  if (c.sku !== null || c.status === "retired") return "";
  if (c.status !== "draft") return `<div class="card-note">${esc(t("item.unknownPriced"))}</div>`;
  const id = esc(c.card_id);
  return `<div class="card-actions">
    <input type="text" data-card-sku="${id}" value="${esc(itemCodes.get(c.card_id) ?? "")}" placeholder="${esc(t("item.placeholder"))}" aria-label="${esc(t("item.aria", { id: c.card_id }))}">
    <button type="button" class="btn primary" data-act="assign-item" data-cid="${id}">${esc(t("item.assign"))}</button>
    <button type="button" class="btn soft full" data-act="keep-item" data-cid="${id}">${esc(t("item.keep"))}</button>
    </div>${noteHtml(itemNotes.get(c.card_id))}`;
}

function cardHtml(c) {
  const id = esc(c.card_id), url = imageUrl(c.image_url), p = c.provenance || {};
  const chips = [
    p.frame_ts_ms != null ? t("card.frame", { time: time(p.frame_ts_ms) }) : t("card.frameNone"),
    t("card.speech", { n: (p.speech_event_ids || []).length }),
    p.edited_by ? t("card.editedBy", { who: p.edited_by }) : t("card.model"),
    c.sku ? t("card.sku", { sku: c.sku }) : t("card.noSku"),
    t("card.stock", { n: c.stock ?? "—" }),
  ];
  const variants = (c.variants || []).map((v) => `<span class="variant">${esc([v.size, v.color].filter(Boolean).join(" · ") || "—")}
    <span class="muted">${esc(t("card.stock", { n: v.stock ?? "—" }))}</span></span>`).join("");
  const hidden = overlayHidden.has(c.card_id), received = receivedAt.get(c.card_id);
  const paint = paintedAt.get(c.card_id), painted = paint?.version === c.version ? paint.ms : null;
  return `<article class="current-card${c.status === "retired" ? " retired" : ""}" data-card-id="${id}" data-version="${c.version}"${received ? ` data-received-ms="${received}"` : ""}${painted ? ` data-painted-ms="${painted}"` : ""}>
    <div class="product-image">${url ? `<img src="${esc(url)}" alt="" onerror="this.hidden=true">` : `<span class="no-image">${esc(t("card.noImage"))}</span>`}
      <div class="product-top-labels"><span class="pill ${statusClass(c.status)}">${esc(tEnum("card.status", c.status))}</span><span class="pill mono">${esc(t("card.version", { n: c.version }))}</span></div></div>
    <div class="product-head"><span class="label mono">${id}</span></div>
    <h3 class="product-name">${esc(c.name)}</h3>
    ${c.price == null ? `<div class="price unknown">${esc(t("card.noPrice"))}</div>` : `<div class="price mono">${esc(c.price.toLocaleString("vi-VN"))}<span class="currency">₫</span></div>`}
    ${variants ? `<div class="variants">${variants}</div>` : ""}
    <div class="provenance">${chips.map((s) => `<span class="source-chip">${esc(s)}</span>`).join("")}</div>
    ${itemHtml(c)}${priceHtml(c)}
    ${c.status === "retired" ? "" : `<div class="card-actions"><button type="button" class="btn soft small full" data-act="${hidden ? "overlay-show" : "overlay-hide"}" data-cid="${id}">${esc(t(hidden ? "card.showOverlay" : "card.hideOverlay"))}</button></div>`}
  </article>`;
}

// Newest appearance first: the card whose latest frame is most recent, then the higher version.
function renderCards() {
  const active = document.activeElement?.dataset || {};
  const rank = (c) => (versions.get(c.card_id) || [c]).reduce((best, v) => {
    const frame = v.provenance?.frame_ts_ms;
    return frame != null && (frame > best[0] || (frame === best[0] && v.version > best[1])) ? [frame, v.version] : best;
  }, [-1, 0]);
  const list = [...cards.values()].sort((a, b) => rank(b)[0] - rank(a)[0] || rank(b)[1] - rank(a)[1]);
  $("cards").innerHTML = list.length ? list.map(cardHtml).join("") : emptyHtml("▣", "cards.empty.title", "cards.empty.body");
  $("cards-count").textContent = String(list.length);
  focusBack("data-card-price", active.cardPrice);
  focusBack("data-card-sku", active.cardSku);
}

// ---- comments ---------------------------------------------------------------------------------
function attachmentHtml(commentId) {
  const a = [...attachments.values()].find((value) => value.comment_id === commentId);
  if (!a) return "";
  const aid = esc(a.attachment_id);
  const hasOrder = [...orderComments.values()].some((cid) => cid === commentId);
  const name = cards.get(a.selected_card_id)?.name || a.selected_card_id;
  let note = a.action === "reject" ? t("attachment.rejected") : a.selected_card_id
    ? `${t("attachment.kept", { name })} ${t(hasOrder ? "attachment.inOrder" : "attachment.waiting")}` : t("attachment.choose");
  if (!a.candidates.length) note = t("attachment.noCandidates");
  const pending = !(hasOrder || a.action === "reject");
  const choices = a.outcome === "expired" && !a.selected_card_id ? a.candidates.map((c) =>
    `<button type="button" class="btn primary small" data-act="attachment-select" data-aid="${aid}" data-choice="${esc(c.card_id)}">${esc(t("attachment.select", { name: cards.get(c.card_id)?.name || c.card_id }))}</button>`).join("") : "";
  const controls = pending ? `<div class="candidates">${choices}
    <button type="button" class="btn small" data-act="attachment-keep" data-aid="${aid}">${esc(t("attachment.keep"))}</button>
    <button type="button" class="btn danger small" data-act="attachment-reject" data-aid="${aid}">${esc(t("attachment.reject"))}</button></div>` : "";
  return `<div class="${pending ? "question" : "card-note"}">${esc(note)} <span class="muted">${esc(t("attachment.meta", { version: a.version, time: time(a.deadline_ms) }))}</span>${controls}</div>`;
}

function commentHtml({ comment: c, labeled: l }) {
  const chip = l ? l.label : "unlabeled", d = l?.order_draft;
  const action = l && l.action !== "none"
    ? `<div class="action-line ${l.action === "hide" ? "danger" : "warning"}">${esc(tEnum("comment.action", l.action))}</div>` : "";
  const draft = d ? `<span class="muted">${esc(t("comment.draft", { sku: d.sku ?? "?", qty: d.qty ?? "?", kind: d.reference_kind, card: d.referenced_card_id ?? "—" }))}</span>` : "";
  return `<div class="comment-row${l?.action === "hide" ? " hidden-comment" : ""}" data-comment-id="${esc(c.comment_id)}">
    <div class="comment-top"><span class="avatar">${esc(initial(c.user_ref))}</span><strong>${esc(c.user_ref)}</strong><time>${esc(time(c.ts_ms))}</time></div>
    <div class="comment-text">${esc(c.text)}</div>
    <div class="comment-footer"><span class="label-chip ${esc(chip)}">${esc(l ? tEnum("label", l.label) : t("comment.unlabeled"))}</span>${draft}</div>
    ${action}${attachmentHtml(c.comment_id)}</div>`;
}

function renderComments() {
  const list = [...comments.values()].filter((r) => r.comment).sort((a, b) => b.comment.ts_ms - a.comment.ts_ms).slice(0, 200);
  $("comments").innerHTML = list.length ? list.map(commentHtml).join("") : emptyHtml("…", "comments.empty.title", "comments.empty.body");
  $("comments-count").textContent = String(list.length);
}

// ---- approval queue, recent orders, source log --------------------------------------------------
const lineText = (l) => t("order.line", { sku: l.sku ?? l.card_id, version: l.card_version, qty: l.qty, price: vnd(l.unit_price) });

function approvalHtml(o) {
  const id = esc(o.order_id), ask = logs.get(o.order_id)?.discount_request;
  const candidates = o.lines.find((line) => line.candidates)?.candidates;
  const value = prices.get(o.order_id) ?? (ask?.kind === "target" ? ask.value : "");
  const reject = `<button type="button" class="btn danger full" data-act="reject" data-oid="${id}">${esc(t("queue.reject"))}</button>`;
  const actions = candidates
    ? candidates.map((cid) => `<button type="button" class="btn primary" data-act="choose-order" data-oid="${id}" data-choice="${esc(cid)}">${esc(t("queue.choose", { name: cards.get(cid)?.name || cid }))}</button>`).join("") + reject
    : `<button type="button" class="btn primary" data-act="price" data-oid="${id}">${esc(t("queue.approvePrice"))}</button>
       <button type="button" class="btn" data-act="card" data-oid="${id}">${esc(t("queue.keepCardPrice"))}</button>${reject}`;
  return `<div class="approval-card" data-order-id="${id}">
    <div class="approval-title"><strong>${esc(o.customer_ref)}</strong><span class="wait mono">${esc(time(o.ts_ms))}</span></div>
    <div class="approval-reason">${esc(t(candidates ? "queue.pickItem" : "queue.needsDecision"))}</div>
    <div class="approval-detail">${o.lines.map((l) => esc(lineText(l))).join("<br>")}<br>${esc(o.reason)}${ask ? ` · ${esc(t("log.asked"))}: “${esc(ask.phrase)}”${ask.value != null ? ` → ${esc(vnd(ask.value))}` : ""}` : ""}</div>
    <div class="approval-money"><div><span>${esc(t("queue.amount"))}</span><strong class="mono">${esc(vnd(o.amount))}</strong></div>
      <div class="final"><span>${esc(t("queue.unitPrice"))}</span><input type="number" min="1" step="1000" inputmode="numeric" data-price="${id}" value="${esc(value)}" placeholder="${esc(t(candidates ? "queue.unitPriceOptional" : "queue.unitPrice"))}" aria-label="${esc(t("queue.unitPriceAria", { id: o.order_id }))}"></div></div>
    <div class="approve-actions">${actions}</div>
    <div class="row approval-foot"><button type="button" class="btn soft small" data-act="log" data-oid="${id}">${esc(t("order.source"))}</button><span class="muted mono">${id}</span></div>
  </div>`;
}

function orderRowHtml(o) {
  const id = esc(o.order_id), st = o.state, done = st === "CONFIRMED" || st === "CLOSED";
  const pill = done ? "live" : st === "CANCELLED" ? "danger" : st === "CONFIRM_SENT" ? "warning" : "";
  const sim = st === "CONFIRM_SENT" ? `<span class="row"><span class="pill">${esc(t("queue.sim"))}</span>
    <button type="button" class="btn small" data-act="yes" data-oid="${id}">${esc(t("queue.customerYes"))}</button>
    <button type="button" class="btn small" data-act="no" data-oid="${id}">${esc(t("queue.customerNo"))}</button></span>` : "";
  return `<div class="order-row" data-order-id="${id}"><div class="order-icon">${done ? "✓" : st === "CANCELLED" ? "×" : "…"}</div>
    <div class="order-main"><strong>${esc(o.customer_ref)} · ${esc(vnd(o.amount))}</strong>
      <small>${o.lines.map((l) => esc(lineText(l))).join(", ")}</small>
      <small>${esc(t("order.by", { reason: o.reason, actor: tEnum("actor", o.actor), time: time(o.ts_ms) }))}</small>${sim}</div>
    <span class="pill ${pill}">${esc(tEnum("order.state", st))}</span>
    <button type="button" class="btn small" data-act="log" data-oid="${id}">${esc(t("order.source"))}</button></div>`;
}

function logHtml(g) {
  if (!g) return `<p class="muted">${esc(t("log.loading"))}</p>`;
  const c = g.comment, k = g.card, ask = g.discount_request;
  const entry = (title, body, cls = "") => `<div class="audit-entry ${cls}"><h3>${title}</h3><p>${body}</p></div>`;
  return entry(esc(t("log.comment")), c ? `${esc(c.comment_id)} · ${esc(c.user_ref)} · ${esc(time(c.ts_ms))}: “${esc(c.text)}”` : "—")
    + (g.label ? entry(esc(t("log.label")), `${esc(tEnum("label", g.label.label))} · ${esc(g.label.event_id)}`) : "")
    + entry(esc(t("log.card")), k ? `${esc(k.card_id)} v${esc(k.version)} · ${esc(k.name)} · ${esc(vnd(k.price))} · ${esc(tEnum("card.status", k.status))} · ${esc(time(k.ts_ms))}` : "—")
    + (ask ? entry(esc(t("log.asked")), `“${esc(ask.phrase)}”${ask.value != null ? ` → ${esc(vnd(ask.value))}` : ""}`) : "")
    + `<div class="audit-entry"><h3>${esc(t("log.steps"))}</h3><div class="audit-path">${(g.steps || []).map((s) =>
      `<span>${esc(tEnum("order.state", s.prev_state ?? "—"))} → <b>${esc(tEnum("order.state", s.state))}</b> · ${esc(tEnum("actor", s.actor))} · ${esc(time(s.ts_ms))} · ${esc(s.reason)}</span>`).join("")}</div></div>`
    + (g.messages || []).map((m) => entry(`${m.type === "message_out" ? "→" : "←"} ${esc(m.template ?? m.type)} · ${esc(time(m.ts_ms))}`, m.text ? esc(m.text) : "", "msg")).join("");
}

// One source log is open at a time, in the <dialog>.
function renderLog() {
  const oid = [...shown][0], dlg = $("log-dialog");
  if (!oid) { if (dlg.open) dlg.close?.(); return; }
  $("log-title").textContent = `${t("log.title")} · ${oid}`;
  $("log-body").innerHTML = logHtml(logs.get(oid));
  if (!dlg.open) dlg.showModal?.();
}

function renderOrders() {
  const focused = document.activeElement?.dataset?.price;
  const list = [...orders.values()].sort((a, b) => b.ts_ms - a.ts_ms);
  const queue = list.filter((o) => o.state === "NEEDS_SELLER"), rest = list.filter((o) => o.state !== "NEEDS_SELLER");
  $("queue").innerHTML = queue.length ? queue.map(approvalHtml).join("") : emptyHtml("✓", "queue.empty.title", "queue.empty.body");
  $("queue-count").textContent = String(queue.length);
  $("orders").innerHTML = rest.map(orderRowHtml).join("");
  renderLog();
  focusBack("data-price", focused);
}

// ---- status bar -------------------------------------------------------------------------------
// Model status comes only from model_call events of this session: dot = last call ok/error,
// p50 = median latency_ms over the calls seen (shown with n). No call seen → "—".
function renderModels() {
  $("models").innerHTML = [["spark", "models.spark"], ["sam", "models.sam"], ["voice", "models.voice"]].map(([group, key]) => {
    const s = modelStats.get(group);
    const state = !s ? t("models.idle") : s.lastOk ? t("models.ok") : t("models.error");
    const detail = s && s.n ? t("models.p50", { ms: s.p50, n: s.n }) : t("status.unknown");
    return `<div class="model" title="${esc(state)}"><span class="dot ${!s ? "idle" : s.lastOk ? "" : "danger"}"></span><span>${esc(t(key))}</span><small class="mono">${esc(detail)}</small></div>`;
  }).join("");
}

function renderCost(c) {
  $("cost").textContent = c.today_usd.toFixed(4);
  $("cap").textContent = String(c.daily_cap_usd);
  const bar = $("cost-bar"), pct = c.daily_cap_usd ? Math.min(100, (c.today_usd / c.daily_cap_usd) * 100) : 0;
  if (bar.style) bar.style.width = `${pct}%`;
  $("cost-box").className = `cost${c.today_usd >= c.daily_cap_usd ? " danger" : c.daily_warn_usd != null && c.today_usd >= c.daily_warn_usd ? " warning" : ""}`;
}

function renderConnection(state, seq) {
  $("conn").textContent = t(`conn.${state}`);
  $("conn-dot").className = `dot ${state === "live" ? "" : state === "reconnecting" ? "danger" : "idle"}`;
  if (seq != null) $("seq").textContent = String(seq);
}

function banner(text, level) {
  const b = $("banner");
  b.textContent = text;
  b.className = `banner ${level === "stop" ? "danger" : "warning"}`;
  b.hidden = !text;
}

let toastTimer = null;
function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 2500);
}
