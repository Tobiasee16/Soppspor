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
/* Asserted rather than assumed: `String.replace` with a missing anchor
   returns the string unchanged, which would leave `__view` undefined and fail
   somewhere unrelated. This anchor has already drifted once. */
if (js.indexOf("\nsize();\n") < 0)
  throw new Error("probe anchor missing from shell.html - the shell moved");
const probe = js.replace("\nsize();\n",
  "\nglobal.__view=view;global.__s2w=s2w;global.__w2screen=w2screen;"
  + "global.__panBy=panBy;global.__GRID=GRID;\nsize();\n");
if (probe === js) throw new Error("could not install the view probe");
(0, eval)(probe);
flush(3);

const view = global.__view;
const s2w = global.__s2w, w2screen = global.__w2screen;
const panBy = global.__panBy, GRID = global.__GRID;
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

// ----------------------------------------------- panning a rotated map --
/* Reported from the phone: with the map turned, dragging it "still thinks it's
   pointing north". It did. The one-finger pan did
   `view.cx -= dx * mpp; view.cy += dy * mpp`, which is the screen-to-world
   direction only while rot is 0, and the momentum glide repeated the same two
   lines. Everything else that moves the view already anchored through s2w,
   which is why the pinch held its ground and only the drag felt wrong.

   The property checked is not a sign convention, it is what a drag means: the
   ground under your finger stays under your finger. That holds at any angle, so
   the same assertion runs at several, and it failed at every non-zero one
   before the fix.

   Three things this had to be taught, all harness rather than product. The
   cases sit in the middle of the grid, because a rotated viewport is wider than
   an upright one and a clamped drag fails the invariant for a reason that is
   not the bug. Each drag ends slowly, because a quick release starts a momentum
   glide and the stub runs queued animation frames even after stopGlide has
   cancelled them - a glide left over from one case was still moving the map
   during the next, producing errors that grew with the iteration count and
   looked convincingly like they grew with the angle. And the whole block sits
   ahead of the long-press case rather than after it: that one leaves a finger
   down and waits on a real timer, so a resetPointers here lifted its finger and
   cancelled the press it was about to check. */
const viewBefore = { cx: view.cx, cy: view.cy, mpp: view.mpp, rot: view.rot };
const MID = { x: (GRID[0] + GRID[2]) / 2, y: (GRID[1] + GRID[3]) / 2 };

resetPointers();
function dragKeepsGroundUnderFinger(rot, dx, dy) {
  view.cx = MID.x; view.cy = MID.y; view.mpp = 6; view.rot = rot;
  const want = { cx: view.cx, cy: view.cy };
  const from = { x: 200, y: 400 };
  const held = s2w(from.x, from.y);        // world point under the finger
  fire("pointerdown", pe(31, from.x, from.y)); NOW += 16;
  fire("pointermove", pe(31, from.x + dx, from.y + dy));
  flush(2);
  const now = w2screen(held[0], held[1]);  // where that point ended up
  const c = Math.cos(rot), sn = Math.sin(rot);
  const drift = Math.hypot(
    view.cx - (want.cx - (dx * c + dy * sn) * 6),
    view.cy - (want.cy + (-dx * sn + dy * c) * 6));
  NOW += 300;                              // released slowly: starts no glide
  resetPointers();
  flush(2);
  return { off: Math.hypot(now[0] - (from.x + dx), now[1] - (from.y + dy)), drift };
}
for (const deg of [0, 30, 90, 150, -90]) {
  const r = dragKeepsGroundUnderFinger(deg * Math.PI / 180, 60, -40);
  check(`a drag on a map turned ${deg}\u00b0 keeps the ground under the finger`,
        r.off < 1.5 && r.drift < 1, `${r.off.toFixed(2)} px adrift`);
}

/* And the direction, stated once concretely, because an invariant can be
   satisfied by two wrongs that cancel. Turned a quarter turn, north lies to the
   right of the screen, so dragging right must walk the view south, not west. */
view.cx = MID.x; view.cy = MID.y; view.mpp = 6; view.rot = Math.PI / 2;
{
  const p = w2screen(MID.x, MID.y + 1000);       // a point 1 km due north
  check("a quarter turn puts north to the right of the screen",
        p[0] > W / 2 + 10 && Math.abs(p[1] - H / 2) < 10,
        `north plots at ${p[0].toFixed(0)},${p[1].toFixed(0)}`);
  const before = { cx: view.cx, cy: view.cy };
  panBy(60, 0);                                  // drag right
  check("dragging right on a quarter-turned map moves the view south",
        view.cy < before.cy - 100 && Math.abs(view.cx - before.cx) < 1,
        `cx ${(view.cx - before.cx).toFixed(0)} m, cy ${(view.cy - before.cy).toFixed(0)} m`);
}

/* North-up must be untouched. The fix reduces to the old two lines at rot 0,
   and this says so rather than trusting the algebra. */
view.rot = 0; view.cx = MID.x; view.cy = MID.y; view.mpp = 20;
{
  const before = { cx: view.cx, cy: view.cy };
  panBy(60, -40);
  check("north-up panning is exactly what it always was",
        Math.abs((view.cx - before.cx) + 60 * 20) < 1e-9 &&
        Math.abs((view.cy - before.cy) + 40 * 20) < 1e-9,
        `${(view.cx - before.cx).toFixed(0)}, ${(view.cy - before.cy).toFixed(0)}`);
}

/* The glide reuses the velocities the drag produced and carried its own copy of
   the broken arithmetic. One helper now: a second copy is exactly how the first
   survived being noticed, so what is pinned is that only one remains. Comments
   are stripped first - the helper's own note quotes the line it replaced, and a
   check that its own explanation defeats is worse than no check. */
const code = shell.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check("the drag and the glide both go through the helper",
      /panBy\(dx,dy\)/.test(code) && /panBy\(vx\*dt,vy\*dt\)/.test(code));
check("and no raw screen-delta pan survives outside it",
      (code.match(/view\.cx\s*-=[^;]*view\.mpp/g) || []).length === 1,
      (code.match(/view\.cx\s*-=[^;]*view\.mpp/g) || []).join(" | "));

/* Leave the map as it was found. The next case sets its own centre and scale
   but not its rotation, and a long press read through a turned s2w lands
   somewhere else entirely - possibly outside BOUNDS, where it saves nothing. */
view.cx = viewBefore.cx; view.cy = viewBefore.cy;
view.mpp = viewBefore.mpp; view.rot = viewBefore.rot;
resetPointers();

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

// ------------------------------------------ pulling a sheet down to dismiss --
/* Three goes at this before it worked, and each earlier one is a case below.
 *
 * It began on the `.grab` notch alone - 36x5 px - so a reader pulls the sheet,
 * misses, and concludes it does not drag. Adding the header was not enough: the
 * header is ~44 px and the threshold is 70, so the finger leaves it mid-pull and
 * the move events stop unless pointer capture happened to take. And the body was
 * excluded because the list scrolls, which is exactly where a hand lands.
 *
 * What works: the whole sheet is the handle, moves are tracked on `window` so
 * nothing depends on capture, and a pull only becomes a dismissal if the list
 * was already at the top when the finger went down. */
{
  const src = (() => {
    const i = shell.indexOf("function makeDismissable(");
    let j = shell.indexOf("{", i), d = 0;
    for (; j < shell.length; j++) {
      if (shell[j] === "{") d++;
      else if (shell[j] === "}") { d--; if (!d) break; }
    }
    return shell.slice(i, j + 1);
  })();

  function makeSheet(scrollTop = 0) {
    const classes = new Set(["on"]);
    const win = { handlers: {},
      addEventListener(t, fn) { (this.handlers[t] ||= []).push(fn); },
      removeEventListener(t, fn) {
        this.handlers[t] = (this.handlers[t] || []).filter((f) => f !== fn); },
      fire(t, ev) { (this.handlers[t] || []).slice().forEach((fn) => fn(ev)); },
      count(t) { return (this.handlers[t] || []).length; } };
    const sheet = {
      style: {}, scrollTop, closed: false, handlers: {},
      classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c) },
      querySelector: (sel) => (sel === ".grab" ? {} : null),
      addEventListener(t, fn) { (this.handlers[t] ||= []).push(fn); },
      fire(t, ev) { (this.handlers[t] || []).forEach((fn) => fn(ev)); },
      open: () => classes.has("on"),
    };
    new Function("sheet", "onClose", "window", src + "makeDismissable(sheet,onClose);")(
      sheet, () => { sheet.closed = true; }, win);
    return { sheet, win };
  }
  const plain = { closest: () => null };
  const control = { closest: () => ({}) };
  const ev = (y, target = plain) => ({ clientY: y, target, button: 0,
                                       cancelable: true, preventDefault() {} });

  // A pull from anywhere on the sheet, with the list at the top.
  let { sheet, win } = makeSheet(0);
  sheet.fire("pointerdown", ev(100));
  win.fire("pointermove", ev(200));
  check("a pull from the body moves the sheet when the list is at the top",
        sheet.style.transform === "translateY(100px)", sheet.style.transform);
  win.fire("pointerup", ev(200));
  check("and past the threshold it closes", !sheet.open() && sheet.closed);
  check("the transform is cleared so the sheet is not left offset",
        sheet.style.transform === "");
  check("and the window listeners are removed rather than piling up",
        win.count("pointermove") === 0 && win.count("pointerup") === 0,
        `${win.count("pointermove")} move, ${win.count("pointerup")} up`);

  /* The bug this replaced: the finger leaves the header long before 70 px, so
     anything that only listens on the element it started on stalls. */
  ({ sheet, win } = makeSheet(0));
  sheet.fire("pointerdown", ev(100));
  for (const y of [110, 140, 180, 220]) win.fire("pointermove", ev(y));
  check("the drag survives the finger leaving the element it started on",
        sheet.style.transform === "translateY(120px)", sheet.style.transform);
  win.fire("pointerup", ev(220));
  check("and closes", !sheet.open());

  // Mid-scroll, the same gesture is a scroll and must be left alone.
  ({ sheet, win } = makeSheet(240));
  sheet.fire("pointerdown", ev(100));
  win.fire("pointermove", ev(300));
  check("a pull with the list scrolled down does not drag the sheet",
        !sheet.style.transform, String(sheet.style.transform));
  win.fire("pointerup", ev(300));
  check("and certainly does not close it", sheet.open() && !sheet.closed);

  /* Scrolling to the top and then pulling, in one gesture, is still a scroll.
     The decision is taken when the finger goes down, because by the time it has
     moved the browser has already scrolled. */
  ({ sheet, win } = makeSheet(120));
  sheet.fire("pointerdown", ev(100));
  sheet.scrollTop = 0;                       // the browser scrolled us to the top
  win.fire("pointermove", ev(300));
  check("reaching the top mid-gesture does not turn a scroll into a dismissal",
        sheet.open() && !sheet.style.transform, String(sheet.style.transform));

  // A short tug is not a dismissal.
  ({ sheet, win } = makeSheet(0));
  sheet.fire("pointerdown", ev(100));
  win.fire("pointermove", ev(130));
  win.fire("pointerup", ev(130));
  check("a 30 px tug springs back rather than closing",
        sheet.open() && sheet.style.transform === "");

  // A tap must stay a tap: nothing moved, so nothing is claimed.
  ({ sheet, win } = makeSheet(0));
  let prevented = false;
  sheet.fire("pointerdown", ev(100));
  win.fire("pointermove", { clientY: 103, target: plain, cancelable: true,
                            preventDefault() { prevented = true; } });
  check("a 3 px wobble is not a drag and is not swallowed",
        !sheet.style.transform && !prevented);

  // Controls keep being controls.
  ({ sheet, win } = makeSheet(0));
  sheet.fire("pointerdown", ev(100, control));
  win.fire("pointermove", ev(300));
  check("a press that starts on a button or a row does not drag the sheet",
        sheet.open() && !sheet.style.transform, String(sheet.style.transform));

  // Upward is not a dismissal.
  ({ sheet, win } = makeSheet(0));
  sheet.fire("pointerdown", ev(300));
  win.fire("pointermove", ev(100));
  win.fire("pointerup", ev(100));
  check("dragging up neither lifts nor closes the sheet",
        sheet.open() && !sheet.style.transform);

  // A cancelled pointer has to clean up like any other ending.
  ({ sheet, win } = makeSheet(0));
  sheet.fire("pointerdown", ev(100));
  win.fire("pointermove", ev(200));
  win.fire("pointercancel", ev(200));
  check("a cancelled pointer closes the sheet it had already pulled 100 px",
        !sheet.open());
  check("and leaves no window listeners behind", win.count("pointermove") === 0);

  check("the sheet keeps its overscroll to itself",
        /\.card\{overscroll-behavior:contain\}/.test(shell));
  check("only a sheet with a notch is made dismissable",
        /if\(!sheet\.querySelector\("\.grab"\)\)return;/.test(shell));
}

// -------------------------------------------- the walk-to arrow and the map --
/* The row answers two questions and they come apart the moment the map turns.
 *
 * The word is a compass bearing: «mot nord-øst» is true of the ground and must
 * not move with the screen, or it stops being worth anything once the phone is
 * in a pocket. The arrow is an instruction about where to look right now, so it
 * has to follow the map - it was briefly left as a pure bearing, on a
 * misreading of which of the two the owner meant to keep.
 *
 * North sits at screen offset (sin rot, -cos rot), «up» turned clockwise by the
 * rotation, and CSS rotate() turns clockwise too, so the screen angle is the
 * bearing plus the rotation. */
{
  const src = (() => {
    const i = shell.indexOf("function walkTo(");
    let j = shell.indexOf("{", i), d = 0;
    for (; j < shell.length; j++) {
      if (shell[j] === "{") d++;
      else if (shell[j] === "}") { d--; if (!d) break; }
    }
    return shell.slice(i, j + 1);
  })();
  const COMPASS = ["nord", "nord-øst", "øst", "sør-øst", "sør", "sør-vest",
                   "vest", "nord-vest"];
  function arrow(rotDeg, east, north) {
    const html = new Function("gpsPos", "gpsAcc", "COMPASS", "view", "esc",
                              src + "return walkTo(" + east + "," + north + ");")(
      [0, 0], 5, COMPASS, { rot: rotDeg * Math.PI / 180, cx: 0, cy: 0, mpp: 10 },
      (x) => x);
    return { deg: +(html.match(/rotate\((\d+)deg\)/) || [0, -1])[1],
             word: (html.match(/mot ([a-zø-]+)/) || [0, ""])[1] };
  }

  const flat = arrow(0, 0, 1000);                    // due north of the reader
  check("due north reads as north with the map upright",
        flat.deg === 0 && flat.word === "nord", `${flat.deg}° ${flat.word}`);

  const ne = arrow(0, 1000, 1000);
  check("north-east is 45 degrees and named so",
        ne.deg === 45 && ne.word === "nord-øst", `${ne.deg}° ${ne.word}`);

  /* The arrow follows the map. Turn the map a quarter turn clockwise and north
     lies to the right, so an arrow pointing at something due north has to
     point right too. */
  for (const rot of [30, 90, 180, 270]) {
    const a = arrow(rot, 0, 1000);
    check(`the arrow turns with a map rotated ${rot}°`,
          a.deg === rot % 360, `${a.deg}°, expected ${rot % 360}°`);
  }

  /* The word does not. It is a fact about the ground. */
  for (const rot of [30, 90, 180, 270]) {
    check(`the named direction ignores a map rotated ${rot}°`,
          arrow(rot, 0, 1000).word === "nord", arrow(rot, 0, 1000).word);
    check(`and so does it for north-east at ${rot}°`,
          arrow(rot, 1000, 1000).word === "nord-øst", arrow(rot, 1000, 1000).word);
  }

  check("a rotated map still points at the right ground",
        arrow(90, 1000, 1000).deg === 135, String(arrow(90, 1000, 1000).deg));
  check("the arrow reads the rotation; that is the whole point",
        /view\.rot/.test(src));
}

