// Overlay adapter: draws the newest live card of the session into overlay.html. Card events only;
// no comments, reactions or input are read or drawn. Strings live in i18n.js.
const sid = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());
currentLang();
applyStaticText();
const $ = (id) => document.getElementById(id);
const size = queryParam("size");
$("stage").dataset.cardSize = ["30", "36", "43"].includes(size) ? size : "36";

const cards = new Map();
const appearances = new Map();
const hideKey = `keviq.overlay.hidden.${sid}`;
let hiddenIds = new Set();
let hiddenHere = false;      // the overlay's own hide button
let current = null;          // {card_id, price} of the card on screen
let oldPrice = null;         // previous price while the 3 s "price just changed" variant runs
let changedTimer = null;

function readHidden() {
  try { hiddenIds = new Set(JSON.parse(localStorage.getItem(hideKey) || "[]")); } catch { hiddenIds = new Set(); }
}
readHidden();
if (typeof BroadcastChannel === "function") {
  new BroadcastChannel("keviq-overlay").onmessage = (m) => { if (m.data?.sid === sid) { hiddenIds = new Set(m.data.hidden || []); render(); } };
}
if (typeof window !== "undefined") window.addEventListener("storage", (ev) => { if (ev.key === hideKey) { readHidden(); render(); } });

// Same choice as the console: the card whose latest frame is most recent, then the higher version.
function pick() {
  return [...cards.values()]
    .filter((c) => (c.status === "live" || c.status === "edited" || c.status === "draft") && !hiddenIds.has(c.card_id))
    .sort((a, b) => {
      const x = appearances.get(a.card_id) || [-1, 0], y = appearances.get(b.card_id) || [-1, 0];
      return y[0] - x[0] || y[1] - x[1];
    })[0];
}

const money = (n) => n.toLocaleString("vi-VN") + " ₫";

function render() {
  const live = pick(), el = $("card"), hide = $("hide");
  hide.hidden = false;
  hide.textContent = t(hiddenHere ? "overlay.show" : "overlay.hide");
  if (hiddenHere || !live) { el.hidden = true; $("toast").hidden = true; return; }
  el.hidden = false;
  el.className = `overlay-product${oldPrice !== null ? " changed" : ""}`;
  const img = $("img"), url = /^(data:image\/|https?:|\/)/.test(live.image_url || "") ? live.image_url : "";
  img.hidden = !url;
  if (url && img.getAttribute("src") !== url) img.src = url;
  $("version").textContent = t("card.version", { n: live.version });
  $("state").textContent = live.status === "draft" ? t("overlay.updating") : "";
  $("name").textContent = live.name;
  const price = $("price");
  price.className = `new-price${live.price == null ? " unknown" : ""}`;
  price.textContent = live.price == null ? t("card.noPrice") : money(live.price);
  const old = $("old-price");
  old.hidden = oldPrice == null;
  old.textContent = oldPrice == null ? "" : money(oldPrice);
  const sizes = [...new Set((live.variants || []).filter((v) => v.stock > 0).map((v) => v.size).filter(Boolean))];
  $("meta").textContent = sizes.length ? t("overlay.size", { sizes: sizes.join(" · ") }) : "";
}

// A new version of the card on screen with another price: highlight for 3 s, then back to normal.
function flashPrice(previous) {
  oldPrice = previous;
  const toast = $("toast");
  toast.textContent = t("overlay.priceUpdated");
  toast.hidden = false;
  clearTimeout(changedTimer);
  changedTimer = setTimeout(() => { oldPrice = null; toast.hidden = true; render(); }, 3000);
}

function onCard(c) {
  const cur = cards.get(c.card_id), frame = c.provenance?.frame_ts_ms;
  if (!cur || cur.version <= c.version) cards.set(c.card_id, c);
  const position = appearances.get(c.card_id) || [-1, 0];
  if (frame != null && (frame > position[0] || (frame === position[0] && c.version > position[1]))) appearances.set(c.card_id, [frame, c.version]);
  const now = pick();
  if (now && current && now.card_id === current.card_id && now.price !== current.price) flashPrice(current.price);
  else if (!now || !current || now.card_id !== current.card_id) { oldPrice = null; $("toast").hidden = true; }
  current = now ? { card_id: now.card_id, price: now.price } : null;
  render();
}

$("hide").addEventListener("click", () => { hiddenHere = !hiddenHere; render(); });
render();
const es = new EventSource(`/api/sessions/${encodeURIComponent(sid)}/events?cursor=0`);
es.addEventListener("card", (m) => onCard(JSON.parse(m.data)));
