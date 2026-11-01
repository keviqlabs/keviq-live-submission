// Runs the script of measure/manual/page.html in a fake DOM against a running local server, so a test can
// click through a run and lose the connection in the middle of it. Used by tests/test_manual_page.py:
//   node tests/manual_page.mjs BASE_URL SCENARIO
// prints what the page showed and what it sent, as JSON.
import { readFileSync } from "node:fs";
import vm from "node:vm";

const [base, scenario] = process.argv.slice(2);
const html = readFileSync(new URL("../measure/manual/page.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const unhandled = [];
process.on("unhandledRejection", (e) => unhandled.push(String(e)));

const all = [];
function element(tag) {
  const listeners = {}, kids = [], lazy = {};
  let inner = "";
  const el = {
    tag, id: "", className: "", textContent: "", disabled: false, onclick: null, style: {},
    scrollTop: 0, scrollHeight: 0, clientHeight: 0, kids,
    classList: {
      set: new Set(),
      add(c) { this.set.add(c); }, remove(c) { this.set.delete(c); }, contains(c) { return this.set.has(c); },
      toggle(c, on) { if (on ?? !this.set.has(c)) this.set.add(c); else this.set.delete(c); },
    },
    get innerHTML() { return inner; },
    set innerHTML(s) { inner = s; kids.length = 0; for (const k in lazy) delete lazy[k]; },
    children: new Proxy([], { get: (_, i) => (/^\d+$/.test(i) ? (lazy["#" + i] ??= element("td")) : undefined) }),
    querySelector(sel) { return (lazy[sel] ??= element(sel)); },
    appendChild(c) { kids.push(c); return c; },
    append(...cs) { for (const c of cs) kids.push(c); },
    addEventListener(kind, fn) { (listeners[kind] ??= []).push(fn); },
    fire(kind) { for (const fn of listeners[kind] ?? []) fn({ preventDefault() {} }); },
  };
  all.push(el);
  return el;
}
function byId(id) {
  for (let i = all.length - 1; i >= 0; i--) if (all[i].id === id) return all[i];
  return null;
}
for (const id of ["banner", "timer", "phase", "badges", "counts", "v", "catalog", "feed", "more", "panel", "overlay", "sheet"]) {
  element("static").id = id;
}
const docListeners = {};
const document = {
  hidden: false, getElementById: byId, createElement: element,
  addEventListener(kind, fn) { (docListeners[kind] ??= []).push(fn); },
};
// The user leaves the tab and comes back.
function leaveAndReturn() {
  for (const hidden of [true, false]) {
    document.hidden = hidden;
    for (const fn of docListeners.visibilitychange ?? []) fn();
  }
}

// The clock the page reads with performance.now(); it starts away from 0 so perf_ms and video_ms differ.
const PERF0 = 2_500.25;
let now = PERF0;
const v = byId("v");
Object.assign(v, {
  currentTime: 0, duration: 351.533, playbackRate: 1, paused: true, ended: false,
  play() { v.paused = false; return Promise.resolve(); },
  pause() { if (v.paused) return; v.paused = true; queueMicrotask(() => v.fire("pause")); },
});

// Every POST to /log goes to the real server unless the network is "down" (no answer at all), the line is
// "refused" (sent with a field the server forbids, so it answers 422 and stores nothing), or the mode is a
// status such as "409" or "500" (answered with that status here, without reaching the server). After hold(),
// the next line waits in flight until release(mode) lets it go as that mode.
const net = { mode: "ok", gate: null, open: null };
function hold() { net.gate = new Promise((r) => (net.open = r)); }
function release(mode) { const open = net.open; net.gate = net.open = null; open(mode); }
const sent = [], pending = new Set();
function fakeFetch(url, opts = {}) {
  const gate = net.gate;
  const p = (async () => {
    if (url !== "/log") return fetch(base + url, opts);
    const mode = gate ? await gate : net.mode;
    const body = JSON.parse(opts.body);
    const line = { ...body, via: mode };
    sent.push(line);
    if (mode === "down") throw new TypeError("Failed to fetch");
    if (/^\d+$/.test(mode)) {
      line.status = Number(mode);
      return new Response("{}", { status: line.status });
    }
    const out = mode === "refused" ? JSON.stringify({ ...body, refused_by_test: true }) : opts.body;
    const r = await fetch(base + url, { ...opts, body: out });
    line.status = r.status;
    return r;
  })();
  pending.add(p);
  p.finally(() => pending.delete(p)).catch(() => {});
  return p;
}

const context = vm.createContext({
  document, performance: { now: () => now }, navigator: {}, window: { addEventListener() {} },
  innerWidth: 1400, setInterval() { return 0; }, queueMicrotask, console, fetch: fakeFetch,
});
const run = (code) => vm.runInContext(code, context);

// Lets the page react without waiting for a line held in flight.
async function flush() { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); }
async function idle() {
  for (let quiet = 0; quiet < 3;) {
    if (pending.size) { quiet = 0; await Promise.allSettled([...pending]); } else quiet++;
    await new Promise((r) => setImmediate(r));
  }
}
function click(label, cls = "act") {
  for (let i = all.length - 1; i >= 0; i--) {
    const el = all[i];
    if (String(el.textContent) === label && el.className.split(" ").includes(cls)) {
      if (el.disabled) return false;
      el.onclick();
      return true;
    }
  }
  throw new Error("no button " + label);
}
async function at(ms) { now = PERF0 + ms; v.currentTime = ms / 1000; run("tick()"); await idle(); }
const comment = (i) => run(`D.comments[${i}].comment_id`);
const select = (id) => run(`nodes[${JSON.stringify(id)}].onclick()`);

const clicks = [];
// Clicks an action on the selected comment and notes the click's own times.
async function act(kind, label, id, settle = idle) {
  const when = { kind, comment_id: id, video_ms: run("clock()"), perf_ms: now };
  if (click(label)) clicks.push(when);
  await settle();
}
async function start() {
  vm.runInContext(script, context);
  await idle();
  click("Bắt đầu", "");
  await idle();
}
async function finish(tail = 30_000) {
  now = PERF0 + 351_533;
  v.currentTime = v.duration;
  v.ended = true;
  v.fire("ended");
  await idle();
  now += tail + 100;
  run("tick()");
  await idle();
}

const text = (s) => String(s).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
function seen() {
  const sheet = byId("sheet");
  const tags = {};
  for (let i = 0; i < 4; i++) {
    const id = comment(i);
    tags[id] = run(`nodes[${JSON.stringify(id)}]`) ? run(`nodes[${JSON.stringify(id)}]`).querySelector(".tags").kids.map((t) => t.textContent) : null;
  }
  return {
    counts: byId("counts").textContent, flash: byId("flash")?.textContent ?? "",
    sheet: text(sheet.innerHTML + " " + sheet.kids.map((k) => k.textContent).join(" ")),
    overlay: byId("overlay").style.display ?? "", banner: byId("banner").textContent,
    tally: JSON.parse(run("JSON.stringify(tally)")), running: run("running"), locked: run("locked"), tags,
    paused: v.paused,
  };
}

async function order(id) {
  select(id);
  click("SP01 giày đen", "pick");
  click("40", "pick");
  click("+", "");
  await act("order", "Tạo đơn", id);
}
async function ask(id, settle = idle) {
  select(id);
  click("SP02 giày trắng", "pick");
  await act("ask", "Hỏi khách", id, settle);
}

const out = { scenario };
if (scenario.startsWith("start-")) {
  net.mode = scenario.slice("start-".length);  // the start line is lost ...
  await start();
  net.mode = "ok";                              // ... and the connection is back at once
  await at(40_000);
  Object.assign(out, seen(), { sent, unhandled });
  console.log(JSON.stringify(out));
  process.exit(0);
}
await start();
await at(40_000);
const [c0, c1, c2, c3] = [0, 1, 2, 3].map(comment);
out.comments = [c0, c1, c2, c3];
if (scenario === "healthy") {
  await order(c0);
  await at(41_000);
  hold();                                   // a pause waits in flight ...
  v.pause();
  await flush();
  await ask(c1, flush);
  await flush();
  now += 300;                               // ... the ask clicked behind it is sent 300 ms later
  release("ok");
  await idle();
  await at(42_000);
  select(c2);
  await act("hide", "Ẩn bình luận", c2);
  click("Ẩn bình luận");                  // a second click on a final hide
  await idle();
  await at(43_000);
  select(c3);
  hold();                                   // the reply waits in flight, then is stored
  await act("reply", "Trả lời", c3, flush);
  out.in_flight = seen();
  release("ok");
  await idle();
  out.after_store = seen();
  await finish();
} else if (scenario === "down" || scenario === "refused") {
  await order(c0);
  await at(41_000);
  net.mode = scenario;
  await ask(c1);
  out.after_loss = seen();
  net.mode = "ok";                          // the connection is back before the end
  leaveAndReturn();
  await idle();
  await at(42_000);
  select(c3);
  await act("reply", "Trả lời", c3);
  await finish();
} else if (scenario === "queued-down" || scenario === "queued-refused") {
  await order(c0);
  await at(41_000);
  hold();                                   // the ask waits in flight ...
  select(c1);
  click("SP02 giày trắng", "pick");
  await act("ask", "Hỏi khách", c1, flush);
  out.in_flight = seen();
  out.second_click = click("Hỏi khách");
  out.playing_when_paused = !v.paused;
  v.pause();                                // ... a pause and a tab switch are logged behind it ...
  leaveAndReturn();
  await flush();
  release(scenario.slice("queued-".length)); // ... and the ask is then lost
  net.mode = "ok";                          // the connection is back at once
  await idle();
  out.after_loss = seen();
  leaveAndReturn();
  await idle();
  await at(42_000);
  select(c3);
  await act("reply", "Trả lời", c3);
  await finish();
} else if (scenario.startsWith("held-")) {
  // held-<kind>-<mode>: an order, ask, hide or reply waits in flight, then is lost as <mode>
  const [, kind, mode] = scenario.split("-");
  await order(c0);
  await at(41_000);
  hold();
  select(c1);
  if (kind === "order") {
    click("SP01 giày đen", "pick");
    click("40", "pick");
  } else if (kind === "ask") {
    click("SP02 giày trắng", "pick");
  }
  const label = { order: "Tạo đơn", ask: "Hỏi khách", hide: "Ẩn bình luận", reply: "Trả lời" }[kind];
  await act(kind, label, c1, flush);
  out.in_flight = seen();
  release(mode);
  net.mode = "ok";
  await idle();
  out.after_loss = seen();
  await at(42_000);
  select(c3);
  await act("reply", "Trả lời", c3);
  await finish();
} else if (scenario.startsWith("tail-")) {
  // a reply in the 30 s after the video ended is lost; the tail then runs out
  await order(c0);
  now = PERF0 + 351_533;
  v.currentTime = v.duration;
  v.ended = true;
  v.fire("ended");
  await idle();
  now += 10_000;
  run("tick()");
  select(c3);
  net.mode = scenario.slice("tail-".length);
  await act("reply", "Trả lời", c3);
  net.mode = "ok";
  out.after_loss = seen();
  now += 25_000;
  run("tick()");
  await idle();
} else if (scenario === "lock-down") {
  await order(c0);
  now = PERF0 + 351_533;
  v.currentTime = v.duration;
  v.ended = true;
  v.fire("ended");
  await idle();
  hold();                                   // only the lock line is held, then lost
  now += 30_100;
  run("tick()");
  await flush();
  out.lock_in_flight = seen();
  release("down");
  await idle();
} else {
  throw new Error("unknown scenario " + scenario);
}
Object.assign(out, seen(), { sent, clicks, unhandled });
console.log(JSON.stringify(out));
