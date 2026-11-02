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
const moneyHtml = (text) => esc(text).replace(/\d[\d.]* \u20ab/g, (value) => `<span class="money">${value}</span>`);
const cardName = (c) => c?.name || c?.sku || t("card.unnamedItem");
const cardNameFor = (id) => cardName(cards.get(id));

// Recognize complete server phrases only. Unknown reasons stay visible and the source log keeps the original.
function reasonText(value) {
  const reason = String(value ?? "");
  const exact = {
    "customer asked for a discount: waiting for the shop": "reason.discount",
    "draft checks passed": "reason.checksPassed",
    "draft checks passed: price matches live card, qty within stock": "reason.checksPassed",
    "price matches live card, qty within stock": "reason.priceAndStock",
    "customer confirmed in Messenger": "reason.confirmed",
    "card has no price yet": "reason.noPrice",
    "qty not given, drafted 1": "reason.qtyDefault",
    "the customer confirms again": "reason.confirmAgain",
    "cannot message the customer: outside 24h since the customer's last message": "reason.outsideWindow",
  };
  if (Object.hasOwn(exact, reason)) return t(exact[reason]);
  let m;
  if ((m = /^amount (\d+) > seller threshold (\d+)$/.exec(reason)))
    return t("reason.threshold", { amount: vnd(Number(m[1])), limit: vnd(Number(m[2])) });
  if ((m = /^qty (\d+) > stock (\d+)$/.exec(reason)))
    return t("reason.stock", { qty: m[1], stock: m[2] });
  if ((m = /^unit price (\d+) != (?:live )?card price (\d+)(?:: needs seller approval)?$/.exec(reason)))
    return t("reason.priceMismatch", { price: vnd(Number(m[1])), cardPrice: vnd(Number(m[2])) });
  if ((m = /^unit price (\d+) = card price$/.exec(reason)))
    return t("reason.priceMatches", { price: vnd(Number(m[1])) });
  if ((m = /^seller set unit price (\d+) \(card price (\d+|None)\)$/.exec(reason)))
    return t("reason.priceSet", { price: vnd(Number(m[1])), cardPrice: vnd(m[2] === "None" ? null : Number(m[2])) });
  if ((m = /^customer asked for a discount: waiting for the shop \("([\s\S]*)"\)$/.exec(reason)))
    return t("reason.discountQuote", { phrase: m[1] });
  if ((m = /^customer confirmed in Messenger \(order on card v(\d+), v(\d+) at the ok\)$/.exec(reason)))
    return t("reason.confirmedVersions", { ordered: m[1], current: m[2] });
  if (/^customer chose CANCEL:[A-Za-z0-9.:-]+ in Messenger$/.test(reason)) return t("reason.cancelled");
  if ((m = /^seller approved: (.*?) \(was: ([\s\S]*)\)$/.exec(reason)))
    return t("reason.approved", { detail: reasonText(m[1]), previous: reasonText(m[2]) });
  if ((m = /^seller rejected \(was: ([\s\S]*)\)$/.exec(reason)))
    return t("reason.rejected", { previous: reasonText(m[1]) });
  if ((m = /^card [A-Za-z0-9._:-]+ changed before the customer's ok: v\d+ ([\d.]+\u0111|\?) -> v\d+ ([\d.]+\u0111|\?)$/.exec(reason))) {
    const amount = (s) => vnd(s === "?" ? null : Number(s.replace(/\D/g, "")));
    return t("reason.priceChanged", { from: amount(m[1]), to: amount(m[2]) });
  }
  const variant = (s) => s.replace(/^size ([^,]+)(.*)$/, (_, size, suffix) => t("reason.size", { size }) + suffix);
  if ((m = /^card [A-Za-z0-9._:-]+ changed before the customer's ok: v\d+ no longer lists (.+) \(v\d+ did\)$/.exec(reason)))
    return t("reason.variantChanged", { variant: variant(m[1]) });
  if ((m = /^variant (.+) (not on|ambiguous on) [A-Za-z0-9._:-]+ v\d+$/.exec(reason)))
    return t(m[2] === "not on" ? "reason.variantMissing" : "reason.variantAmbiguous", { variant: variant(m[1]) });
  if ((m = /^(\d+) already held by other orders$/.exec(reason))) return t("reason.held", { qty: m[1] });
  if (/^drafted from comment [A-Za-z0-9._:-]+ on [A-Za-z0-9._:-]+ v\d+ \((?:[\d.]+\u0111|\?)\)$/.test(reason))
    return t("reason.drafted");
  if (/^draft at (?:live )?card [A-Z] v\d+(?:: live at comment ts| price(?: \(comment ts\))?)$/.test(reason))
    return t("reason.drafted");
  if (reason.includes("; ")) {
    const parts = reason.split("; "), translated = parts.map(reasonText);
    if (translated.every((part, i) => part === parts[i])) return reason;
    return translated.reduce((text, part) => text + (/[.!?]$/.test(text) ? " " : "; ") + part);
  }
  return reason;
}

function productImageHtml(c) {
  const url = imageUrl(c.image_url);
  return `${url ? `<img class="product-photo" src="${esc(url)}" alt="${esc(cardName(c))}">` : ""}
    <div class="product-placeholder"${url ? " hidden" : ""}><img src="/static/placeholder.svg" alt="" aria-hidden="true">
      <span class="image-disclaimer">${esc(t("card.illustration"))}</span></div>`;
}

function bindProductImages(root) {
  for (const img of root.querySelectorAll?.("img.product-photo") || []) {
    const fallback = () => {
      img.hidden = true;
      const placeholder = img.parentElement?.querySelector(".product-placeholder");
      if (placeholder) placeholder.hidden = false;
    };
    img.addEventListener("error", fallback, { once: true });
    if (img.complete && img.naturalWidth === 0) fallback();
  }
}

// ---- live cards -------------------------------------------------------------------------------
function priceHtml(c) {
  if (c.status === "retired") return "";
  if (c.status === "draft" && c.sku === null) return `<div class="card-note">${esc(t("price.pickItemFirst"))}</div>`;
  const id = esc(c.card_id);
  const open = (proposals.get(c.card_id) || []).filter(isOpen).sort((a, b) => b.ts_ms - a.ts_ms);
  return `${open.map((p) => `<div class="proposal"><span>${esc(t("price.heard", { heard: vnd(p.heard_price), time: time(p.ts_ms), current: vnd(p.current_price) }))}</span>
      <button type="button" class="btn small" data-act="use" data-cid="${id}" data-pid="${esc(p.event_id)}">${esc(t("price.use"))}</button></div>`).join("")}
    <div class="card-actions">
      <input type="number" min="1000" step="1000" inputmode="numeric" data-card-price="${id}" value="${esc(typed.get(c.card_id) ?? "")}" placeholder="${esc(t("price.placeholder"))}" aria-label="${esc(t("price.aria", { id: cardName(c) }))}">
      <button type="button" class="btn primary" data-act="save" data-cid="${id}">${esc(t("price.save"))}</button>
    </div>${noteHtml(cardNotes.get(c.card_id))}`;
}

function itemHtml(c) {
  if (c.sku !== null || c.status === "retired") return "";
  if (c.status !== "draft") return `<div class="card-note">${esc(t("item.unknownPriced"))}</div>`;
  const id = esc(c.card_id);
  return `<div class="card-actions">
    <input type="text" data-card-sku="${id}" value="${esc(itemCodes.get(c.card_id) ?? "")}" placeholder="${esc(t("item.placeholder"))}" aria-label="${esc(t("item.aria", { id: cardName(c) }))}">
    <button type="button" class="btn primary" data-act="assign-item" data-cid="${id}">${esc(t("item.assign"))}</button>
    <button type="button" class="btn soft full" data-act="keep-item" data-cid="${id}">${esc(t("item.keep"))}</button>
    </div>${noteHtml(itemNotes.get(c.card_id))}`;
}

function cardHtml(c) {
  const id = esc(c.card_id), p = c.provenance || {};
  const chips = [
    p.frame_ts_ms != null ? t("card.frame", { time: time(p.frame_ts_ms) }) : t("card.frameNone"),
    t("card.speech", { n: (p.speech_event_ids || []).length }),
    p.edited_by ? t("card.editedBy", { who: tEnum("actor", p.edited_by) }) : t("card.model"),
    t("card.version", { n: c.version }),
  ];
  const variants = (c.variants || []).map((v) => `<span class="variant">${esc([v.size, v.color].filter(Boolean).join(" · ") || "—")}
    <span class="muted">${esc(t("card.stock", { n: v.stock ?? "—" }))}</span></span>`).join("");
  const hidden = overlayHidden.has(c.card_id), received = receivedAt.get(c.card_id);
  const paint = paintedAt.get(c.card_id), painted = paint?.version === c.version ? paint.ms : null;
  return `<article class="current-card${c.status === "retired" ? " retired" : ""}" data-card-id="${id}" data-version="${c.version}"${received ? ` data-received-ms="${received}"` : ""}${painted ? ` data-painted-ms="${painted}"` : ""}>
    <div class="product-image">${productImageHtml(c)}
      <div class="product-top-labels"><span class="pill ${statusClass(c.status)}">${esc(tEnum("card.status", c.status))}</span></div></div>
    <div class="product-head"><span class="label">${esc(c.sku ? t("card.sku", { sku: c.sku }) : t("card.noSku"))}</span></div>
    <h3 class="product-name">${esc(cardName(c))}</h3>
    ${c.price == null ? `<div class="price unknown">${esc(t("card.noPrice"))}</div>` : `<div class="price mono money">${esc(vnd(c.price))}</div>`}
    ${variants ? `<div class="variants">${variants}</div>` : `<div class="variants">${esc(t("card.stock", { n: c.stock ?? "—" }))}</div>`}
    <details class="card-source"><summary>${esc(t("card.details"))}</summary><span class="mono">${id}</span>
      <div class="provenance">${chips.map((s) => `<span class="source-chip">${esc(s)}</span>`).join("")}</div></details>
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
  bindProductImages($("cards"));
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
  const name = cardNameFor(a.selected_card_id);
  let note = a.action === "reject" ? t("attachment.rejected") : a.selected_card_id
    ? `${t("attachment.kept", { name })} ${t(hasOrder ? "attachment.inOrder" : "attachment.waiting")}` : t("attachment.choose");
  if (!a.candidates.length) note = t("attachment.noCandidates");
  const pending = !(hasOrder || a.action === "reject");
  const choices = a.outcome === "expired" && !a.selected_card_id ? a.candidates.map((c) =>
    `<button type="button" class="btn primary small" data-act="attachment-select" data-aid="${aid}" data-choice="${esc(c.card_id)}">${esc(t("attachment.select", { name: cardNameFor(c.card_id) }))}</button>`) : [];
  const other = [`<button type="button" class="btn small" data-act="attachment-keep" data-aid="${aid}">${esc(t("attachment.keep"))}</button>`,
    `<button type="button" class="btn danger small" data-act="attachment-reject" data-aid="${aid}">${esc(t("attachment.reject"))}</button>`];
  const buttons = [...choices, ...other];
  const controls = pending ? `<div class="candidates">${buttons[0]}<details class="more-actions"><summary aria-label="${esc(t("comment.more"))}">⋯</summary>${buttons.slice(1).join("")}</details></div>` : "";
  return `<div class="${pending ? "question" : "card-note"}">${esc(note)} <span class="muted">${esc(t("attachment.meta", { version: a.version, time: time(a.deadline_ms) }))}</span>${controls}</div>`;
}

// Only a proven comment-to-order relation can supply an order status or action.
function commentOrder(commentId) {
  const matches = [...orderComments.entries()].filter(([, cid]) => cid === commentId).map(([oid]) => orders.get(oid)).filter(Boolean);
  return matches.length === 1 ? matches[0] : null;
}
function commentAttachment(commentId) {
  return [...attachments.values()].find((a) => a.comment_id === commentId);
}
function unresolvedComment(row) {
  if (!row.comment || row.labeled?.label !== "order") return false;
  if ([...orderComments.values()].includes(row.comment.comment_id)) return false;
  const a = commentAttachment(row.comment.comment_id);
  if (a?.action === "reject" || a?.selected_card_id) return false;
  return a?.outcome === "expired" || row.labeled?.order_draft?.reference_kind === "unresolved";
}
function proposedTotal(order, raw) {
  const price = /^\d+$/.test(String(raw).trim()) ? Number(raw) : NaN;
  const qty = order.lines.reduce((sum, line) => sum + line.qty, 0);
  const amount = price * qty;
  return price > 0 && Number.isSafeInteger(price) && Number.isSafeInteger(amount) ? vnd(amount) : "—";
}

function commentHtml({ comment: c, labeled: l }) {
  const chip = l ? l.label : "unlabeled", d = l?.order_draft;
  const order = commentOrder(c.comment_id), attachment = commentAttachment(c.comment_id);
  const status = order ? tEnum("order.state", order.state) : attachment?.outcome === "expired" ? t("comment.expired") : "";
  const openOrder = order ? `<button type="button" class="btn small" data-act="open-order" data-oid="${esc(order.order_id)}">${esc(t("comment.openOrder"))}</button>` : "";
  const details = order ? order.lines.map((line) => [lineText(line), [line.variant?.size, line.variant?.color].filter(Boolean).join(" · ")].filter(Boolean).join(" · ")).join("; ") : [d?.variant?.size, d?.variant?.color].filter(Boolean).join(" · ");
  const action = l && l.action !== "none"
    ? `<div class="action-line ${l.action === "hide" ? "danger" : "warning"}">${esc(tEnum("comment.action", l.action))}</div>` : "";
  const draft = d ? `<span class="muted">${esc(d.referenced_card_id && cards.has(d.referenced_card_id)
    ? t("comment.draftItem", { name: cardNameFor(d.referenced_card_id), qty: d.qty ?? "?" }) : t("comment.unresolved"))}</span>` : "";
  const source = d ? `<details class="comment-source"><summary>${esc(t("comment.source"))}</summary>${esc(t("comment.draft", { sku: d.sku ?? "?", qty: d.qty ?? "?", kind: d.reference_kind, card: d.referenced_card_id ?? "—" }))}</details>` : "";
  return `<div class="comment-row${l?.action === "hide" ? " hidden-comment" : ""}" data-comment-id="${esc(c.comment_id)}">
    <div class="comment-top"><span class="avatar">${esc(initial(c.user_ref))}</span><strong>${esc(c.user_ref)}</strong><time>${esc(time(c.ts_ms))}</time></div>
    <div class="comment-text">${esc(c.text)}</div>
    <div class="comment-footer"><span class="label-chip ${esc(chip)}">${esc(l ? tEnum("label", l.label) : t("comment.unlabeled"))}</span>${draft}</div>
    ${status ? `<div class="comment-status">${esc(status)}</div>` : ""}${details ? `<div class="comment-detail">${esc(details)}</div>` : ""}${openOrder}${action}${attachmentHtml(c.comment_id)}${source}</div>`;
}

function renderComments() {
  const list = [...comments.values()].filter((r) => r.comment).sort((a, b) => b.comment.ts_ms - a.comment.ts_ms).slice(0, 200);
  $("comments").innerHTML = list.length ? list.map(commentHtml).join("") : emptyHtml("…", "comments.empty.title", "comments.empty.body");
  $("comments-count").textContent = String(list.length);
  renderUnresolved();
  updateQueueCount();
}

let lastUnresolvedHtml = null;
function renderUnresolved() {
  const list = [...comments.values()].filter(unresolvedComment).sort((a, b) => b.comment.ts_ms - a.comment.ts_ms);
  const html = list.length ? `<h3 class="label">${esc(t("queue.unresolved"))} (${list.length})</h3>` + list.map((row) => {
    const attachment = commentAttachment(row.comment.comment_id);
    return `<div class="approval-card"><strong>${esc(row.comment.user_ref)}</strong> · ${esc(time(row.comment.ts_ms))}<p>${esc(row.comment.text)}</p><div>${esc(t("comment.unresolvedQueue"))}</div>${attachment ? attachmentHtml(row.comment.comment_id) : `<small>${esc(t("comment.noSelection"))}</small>`}</div>`;
  }).join("") : "";
  if (html !== lastUnresolvedHtml) { $("unresolved").innerHTML = html; lastUnresolvedHtml = html; }
}

// ---- approval queue, recent orders, source log --------------------------------------------------
const lineText = (l) => {
  const pinned = (versions.get(l.card_id) || []).find((c) => c.version === l.card_version);
  const current = cards.get(l.card_id);
  const card = pinned || (current?.version === l.card_version ? current : null);
  return t("order.itemLine", { name: card?.name || l.sku || t("card.unnamedItem"), qty: l.qty, price: vnd(l.unit_price) });
};

function approvalHtml(o) {
  const id = esc(o.order_id), ask = logs.get(o.order_id)?.discount_request;
  const candidates = o.lines.find((line) => line.candidates)?.candidates;
  const value = prices.get(o.order_id) ?? (ask?.kind === "target" ? ask.value : "");
  const reject = `<button type="button" class="btn danger full" data-act="reject" data-oid="${id}">${esc(t("queue.reject"))}</button>`;
  const buttons = candidates
    ? [...candidates.map((cid) => `<button type="button" class="btn primary" data-act="choose-order" data-oid="${id}" data-choice="${esc(cid)}">${esc(t("queue.choose", { name: cardNameFor(cid) }))}</button>`), reject]
    : [`<button type="button" class="btn primary" data-act="price" data-oid="${id}">${esc(t("queue.approvePrice"))}</button>`,
       `<button type="button" class="btn" data-act="card" data-oid="${id}">${esc(t("queue.keepCardPrice"))}</button>`, reject];
  const actions = `${buttons[0]}<details class="more-actions"><summary aria-label="${esc(t("comment.more"))}">⋯</summary>${buttons.slice(1).join("")}</details>`;
  return `<div class="approval-card" data-order-id="${id}">
    <div class="approval-title"><strong>${esc(o.customer_ref)}</strong><span class="wait mono">${esc(time(o.ts_ms))}</span></div>
    <div class="approval-reason">${esc(t(candidates ? "queue.pickItem" : "queue.needsDecision"))}</div>
    <div class="approval-detail">${o.lines.map((l) => `<div class="order-line">${moneyHtml(lineText(l))}</div>`).join("")}<div class="order-reason">${moneyHtml(reasonText(o.reason))}</div>${ask ? ` · ${esc(t("log.asked"))}: “${esc(ask.phrase)}”${ask.value != null ? ` → <span class="money">${esc(vnd(ask.value))}</span>` : ""}` : ""}</div>
    <div class="approval-money"><div><span>${esc(t("queue.amount"))}</span><strong class="mono money">${esc(vnd(o.amount))}</strong></div>
      <div class="final"><span>${esc(t("queue.unitPrice"))}</span><input type="number" min="1" step="1000" inputmode="numeric" data-price="${id}" value="${esc(value)}" placeholder="${esc(t(candidates ? "queue.unitPriceOptional" : "queue.unitPrice"))}" aria-label="${esc(t("queue.unitPriceAria", { id: o.customer_ref }))}"></div></div>
    <div class="proposed-total">${esc(t("queue.proposedTotal"))}: <strong data-price-total="${id}">${esc(proposedTotal(o, value))}</strong></div>
    <div class="approve-actions">${actions}</div>
    <div class="row approval-foot"><button type="button" class="btn soft small" data-act="log" data-oid="${id}">${esc(t("order.source"))}</button></div>
  </div>`;
}

function orderRowHtml(o) {
  const id = esc(o.order_id), st = o.state, done = st === "CONFIRMED" || st === "CLOSED";
  const pill = done ? "live" : st === "CANCELLED" ? "danger" : st === "CONFIRM_SENT" ? "warning" : "";
  const sim = st === "CONFIRM_SENT" ? `<div class="order-sim"><span class="pill">${esc(t("queue.sim"))}</span>
    <button type="button" class="btn small" data-act="yes" data-oid="${id}">${esc(t("queue.customerYes"))}</button>
    <button type="button" class="btn small" data-act="no" data-oid="${id}">${esc(t("queue.customerNo"))}</button></div>` : "";
  return `<div class="order-row" data-order-id="${id}"><div class="order-icon">${done ? "✓" : st === "CANCELLED" ? "×" : "…"}</div>
    <div class="order-main"><div class="order-heading"><strong>${esc(o.customer_ref)}</strong><strong class="order-total money">${esc(vnd(o.amount))}</strong></div>
      <div class="order-meta">${o.lines.map((l) => `<small class="order-line">${moneyHtml(lineText(l))}</small>`).join("")}</div>
      <small class="order-reason">${moneyHtml(reasonText(o.reason))}</small>
      <small class="order-activity">${esc(t("order.activity", { actor: tEnum("actor", o.actor), time: time(o.ts_ms) }))}</small>${sim}</div>
    <div class="order-actions"><span class="pill ${pill}">${esc(tEnum("order.state", st))}</span>
    <button type="button" class="btn small" data-act="log" data-oid="${id}">${esc(t("order.source"))}</button></div></div>`;
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

function updateQueueCount() {
  const ordersWaiting = [...orders.values()].filter((o) => o.state === "NEEDS_SELLER").length;
  const unresolved = [...comments.values()].filter(unresolvedComment).length;
  $("queue-count").textContent = String(ordersWaiting + unresolved);
  if (!ordersWaiting) $("queue").innerHTML = unresolved ? "" : emptyHtml("✓", "queue.empty.title", "queue.empty.body");
}

function renderOrders() {
  const focused = document.activeElement?.dataset?.price;
  const list = [...orders.values()].sort((a, b) => b.ts_ms - a.ts_ms);
  const queue = list.filter((o) => o.state === "NEEDS_SELLER"), rest = list.filter((o) => o.state !== "NEEDS_SELLER");
  $("queue").innerHTML = queue.length ? queue.map(approvalHtml).join("") : emptyHtml("✓", "queue.empty.title", "queue.empty.body");
  updateQueueCount();
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
