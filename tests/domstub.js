/* Shared stub DOM: enough of a browser to run the real shell script under
   node, so behaviour can be checked without a phone. Extracted from
   test_gestures.js when a second suite needed the same harness.

   Set OB_SEEN=1 in the environment to start as a returning user. */
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
/* Records rather than discards. Zone geometry was previously only ever
   counted - "three paths came back" - because the stub threw the vertices
   away, which left the contour tracer and the stitcher untestable. */
global.Path2D = class {
  constructor() { this.ops = []; }
  moveTo(x, y) { this.ops.push(["M", x, y]); }
  lineTo(x, y) { this.ops.push(["L", x, y]); }
  closePath() { this.ops.push(["Z"]); }
};
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
  // removeItem and clear were missing, which made anything that deletes stored
  // data appear to work while leaving it in place - the worst possible way for
  // a "delete my data" path to be tested.
  removeItem: (k) => { delete store[k]; },
  clear: () => { for (const k of Object.keys(store)) delete store[k]; },
  key: (i) => Object.keys(store)[i] ?? null,
  get length() { return Object.keys(store).length; },
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
/* Records the request instead of answering it. When the app asks for a
   position matters as much as whether it does: on iOS the permission dialog
   appears the moment getCurrentPosition is called, so asking during the
   welcome puts a system prompt over the introduction. No callback is fired,
   so nothing here moves the map. */
/* defineProperty, not assignment. Node 21 added a built-in `navigator` global
   defined as a getter with no setter, so `global.navigator = {...}` silently
   does nothing - the old stub here had been a no-op for exactly that reason,
   invisibly, because the app only ever asked whether `navigator.geolocation`
   existed and was content to be told no. */
Object.defineProperty(global, "navigator", {
  value: {
    geolocation: {
      calls: [],
      getCurrentPosition(ok, err, opts) { this.calls.push({ ok, err, opts }); },
      watchPosition() { return 1; },
      clearWatch() {},
    },
  },
  configurable: true,
  writable: true,
});


module.exports = { node, nodes, el, flush, rafQueue, canvas, listeners,
                   W, H, now: () => NOW, advance: (ms) => { NOW += ms; } };
