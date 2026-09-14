/* Runs the real shell script against a stub DOM and drives it with synthetic
   pointer events, so the pan/zoom behaviour can be checked without a phone.
   The bug this exists for: lifting one finger out of a pinch used to leave a
   stale reference point, and the next move jumped the map by the distance
   between the two fingers. */
const fs = require("fs");
const path = require("path");

const DIR = __dirname;
const W = 400, H = 800;

// ---------------------------------------------------------------- stubs --
const listeners = new Map();
function el(id) {
  const e = {
    id, style: {}, dataset: {}, children: [], hidden: false,
    textContent: "", innerHTML: "", checked: false, className: "",
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle(c, on) { on === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c)) : (on ? this._s.add(c) : this._s.delete(c)); },
      contains(c) { return this._s.has(c); },
    },
    addEventListener(type, fn) {
      const key = (id || "?") + ":" + type;
      if (!listeners.has(key)) listeners.set(key, []);
      listeners.get(key).push(fn);
    },
    removeEventListener() {},
    setAttribute() {}, getAttribute() { return null; },
    appendChild(c) { this.children.push(c); },
    querySelector() { return el("q"); },
    querySelectorAll() { return []; },
    closest() { return null; },
    getBoundingClientRect() { return { left: 0, top: 0, width: W, height: H }; },
    setPointerCapture() {}, releasePointerCapture() {},
    getContext() { return ctx2d(); },
    focus() {},
  };
  return e;
}
function ctx2d() {
  const base = {
    createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    getImageData: (x, y, w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    putImageData() {}, canvas: { width: W, height: H },
  };
  return new Proxy(base, {
    get(t, k) {
      if (k in t) return t[k];
      if (k === "filter") return "none";
      return () => {};
    },
    set() { return true; },
  });
}

const nodes = {};
function node(id) { return nodes[id] || (nodes[id] = el(id)); }

const rafQueue = [];
global.requestAnimationFrame = (cb) => { rafQueue.push(cb); return rafQueue.length; };
global.cancelAnimationFrame = () => {};
function flush(times = 1, t = 16) {
  for (let i = 0; i < times; i++) {
    const q = rafQueue.splice(0, rafQueue.length);
    q.forEach((cb) => cb(NOW += t));
  }
}

let NOW = 1000;
global.performance = { now: () => NOW };
global.atob = (s) => Buffer.from(s, "base64").toString("binary");
global.Path2D = class { moveTo() {} lineTo() {} closePath() {} };
global.Image = class {
  constructor() { this.complete = false; this.naturalWidth = 0; }
  set src(v) { this._src = v; setTimeout(() => this.onerror && this.onerror(), 0); }
  get src() { return this._src; }
};

const store = {};
// OB_SEEN=1 simulates a returning user, so the welcome logic can be checked
// both ways without a second harness.
if (process.env.OB_SEEN) store["soppkart.welcomed"] = "1";
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};

const canvas = node("map");
canvas.parentNode = { getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: H }) };

global.document = {
  getElementById: node,
  createElement: (tag) => el("created-" + tag),
  querySelectorAll: () => [],
  documentElement: { setAttribute() {}, removeAttribute() {}, getAttribute() { return null; } },
  body: el("body"),
};
global.getComputedStyle = () => ({ getPropertyValue: () => "#eee" });
global.window = {
  devicePixelRatio: 3,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {},
  self: {}, top: {},
  localStorage: global.localStorage,
};
global.window.self = global.window.top = global.window;
global.navigator = {};

// ------------------------------------------------------------- the app --
const REPO = require("path").join(__dirname, "..");
const payload = fs.readFileSync(
  path.join(REPO, "data", "processed", "web_preview", "payload.js"), "utf8");
(0, eval)(payload.replace(/^window\./, "global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = null;

const shell = fs.readFileSync(path.join(DIR, "..", "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");   // Python writes CRLF on Windows
const js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));

// the app closes over `view`; expose it for assertions
// Anchored on the boot call to size() rather than on the line after it: what
// follows has changed twice, and an anchor that moves with unrelated edits
// fails loudly here every time.
const probe = js.replace("\nsize();\n", "\nglobal.__view=view;\nsize();\n");
if (probe === js) throw new Error("could not install the view probe");
(0, eval)(probe);
flush(3);

const view = global.__view;
function fire(type, ev) {
  (listeners.get("map:" + type) || []).forEach((fn) => fn(ev));
}
const down = new Set();
function pe(id, x, y) {
  down.add(id);
  return { pointerId: id, clientX: x, clientY: y, preventDefault() {}, deltaY: 0 };
}
/* Each case starts with no fingers on the glass. Without this the previous
   case's pointer is still down and the next gesture is read as a pinch. */
function resetPointers() {
  [...down].forEach((id) => fire("pointerup", { pointerId: id, clientX: -1, clientY: -1,
                                                preventDefault() {} }));
  down.clear();
  NOW += 500;
}

let failures = 0;
function check(name, cond, detail) {
  console.log((cond ? "PASS  " : "FAIL  ") + name + (detail ? "   " + detail : ""));
  if (!cond) failures++;
}

check(process.env.OB_SEEN ? "a returning user is not shown the welcome"
                          : "a first-time user sees the welcome",
  node("ob").classList.contains("on") === !process.env.OB_SEEN);

// 1. a pinch, then one finger lifts, then the other pans a little.
//    The map must move by that little, not leap to where the other finger was.
view.cx = 86000; view.cy = 6464000; view.mpp = 20;
fire("pointerdown", pe(1, 100, 400)); NOW += 16;
fire("pointerdown", pe(2, 300, 400)); NOW += 16;
fire("pointermove", pe(2, 340, 400)); NOW += 16;   // spread -> zoom in
flush(2);
const afterPinch = { cx: view.cx, cy: view.cy, mpp: view.mpp };
fire("pointerup", pe(2, 340, 400)); NOW += 16;      // second finger leaves
fire("pointermove", pe(1, 105, 400));               // first finger nudges 5 px
flush(2);
const jump = Math.hypot(view.cx - afterPinch.cx, view.cy - afterPinch.cy);
check("one finger lifts out of a pinch without the map jumping",
  jump < 5 * afterPinch.mpp * 1.6,
  `moved ${jump.toFixed(0)} m for a 5 px drag (${(5 * afterPinch.mpp).toFixed(0)} m expected)`);

// 2. pinching in zooms in
check("pinch out zooms in", afterPinch.mpp < 20,
  `mpp 20 -> ${afterPinch.mpp.toFixed(2)}`);

// 3. a single tap is a tap, not a drag, even with a 6 px wobble
resetPointers();
let opened = false;
const card = node("card");
card.classList.add = function (c) { if (c === "on") opened = true; Object.getPrototypeOf(this); };
fire("pointerdown", pe(3, 200, 400)); NOW += 30;
fire("pointermove", pe(3, 204, 403)); NOW += 30;
fire("pointerup", pe(3, 204, 403));
flush(2);
check("a tap with a 5 px wobble still opens the cell", opened);

// 4. wheel zoom is monotone and bounded
view.mpp = 40;
const seq = [];
for (let i = 0; i < 14; i++) {
  fire("wheel", { clientX: 200, clientY: 400, deltaY: -120, deltaMode: 0,
                  ctrlKey: false, preventDefault() {} });
  flush(1);
  seq.push(view.mpp);
}
const monotone = seq.every((v, i) => i === 0 || v <= seq[i - 1] + 1e-9);
check("wheel zoom is monotone", monotone, seq.map((v) => v.toFixed(2)).join(" "));
check("wheel zoom respects the floor with no live tiles",
  view.mpp >= 2.5 - 1e-9, `floor reached ${view.mpp.toFixed(2)}`);

// 5. a stray pointer that never lifts must not poison later gestures
resetPointers();
fire("pointerdown", pe(9, 50, 50));
fire("lostpointercapture", pe(9, 50, 50));
view.mpp = 20; view.cx = 86000;
fire("pointerdown", pe(10, 200, 400)); NOW += 16;
fire("pointermove", pe(10, 180, 400));
flush(2);
check("a lost pointer does not leave the map in pinch mode",
  Math.abs(view.cx - (86000 + 20 * 20)) < 1,
  `cx ${view.cx.toFixed(0)}, expected ${86000 + 400}`);

// 6. holding a finger on the map saves a private spot. Real timer, so this
//    one has to wait rather than tick the fake clock.
resetPointers();
view.cx = 86000; view.cy = 6464000; view.mpp = 20;
fire("pointerdown", pe(20, 200, 400));
setTimeout(() => {
  let saved = [];
  try { saved = JSON.parse(store["soppkart.spots"] || "[]"); } catch (e) {}
  check("holding the map saves a spot", saved.length === 1,
    `${saved.length} spot(s) stored`);
  check("the saved spot carries real coordinates and a date",
    saved.length === 1 && isFinite(saved[0].e) && isFinite(saved[0].n) && !!saved[0].d,
    saved[0] ? `${saved[0].e} E ${saved[0].n} N on ${saved[0].d}` : "none");

  // and a short press must not save anything
  const before = saved.length;
  fire("pointerdown", pe(21, 250, 420));
  fire("pointerup", pe(21, 250, 420));
  setTimeout(() => {
    let after = [];
    try { after = JSON.parse(store["soppkart.spots"] || "[]"); } catch (e) {}
    check("a normal tap does not save a spot", after.length === before,
      `${before} -> ${after.length}`);
    console.log(failures ? `\n${failures} FAILED` : "\nall gesture tests passed");
    process.exit(failures ? 1 : 0);
  }, 700);
}, 700);
