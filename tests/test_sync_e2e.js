/* End-to-end: the app's own sync code against the live Supabase project.
   test_backend.py checks the database; this checks the client that talks to
   it - the projection at the boundary, the push, the pull, and the merge that
   makes a second device agree with the first. */
const fs = require("fs");
const path = require("path");

const REPO = require("path").join(__dirname, "..");
const DIR = __dirname;
const W = 400, H = 800;
const CFG = JSON.parse(fs.readFileSync(path.join(REPO, "config", "supabase.json"), "utf8"));

// ------------------------------------------------------------- stub DOM --
const listeners = new Map();
function el(id) {
  return {
    id, style: {}, dataset: {}, children: [], hidden: false, textContent: "",
    innerHTML: "", checked: false, disabled: false,
    classList: { _s: new Set(), add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener(t, fn) { const k = id + ":" + t; if (!listeners.has(k)) listeners.set(k, []); listeners.get(k).push(fn); },
    removeEventListener() {}, setAttribute() {}, getAttribute() { return null; },
    appendChild() {}, querySelector() { return el("q"); }, querySelectorAll() { return []; },
    closest() { return null; }, focus() {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: H }),
    setPointerCapture() {}, releasePointerCapture() {}, getContext: () => ctx2d(),
  };
}
function ctx2d() {
  const base = {
    createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
    putImageData() {}, canvas: { width: W, height: H },
  };
  return new Proxy(base, { get: (t, k) => (k in t ? t[k] : (k === "filter" ? "none" : () => {})), set: () => true });
}
const nodes = {};
const node = (id) => nodes[id] || (nodes[id] = el(id));
const rafQ = [];
global.requestAnimationFrame = (cb) => { rafQ.push(cb); return rafQ.length; };
global.cancelAnimationFrame = () => {};
let NOW = 1000;
/* Keep the real performance object: undici (node fetch) calls
   performance.markResourceTiming, and a bare {now} stub crashes it.
   This test has no gesture timing to fake, so only now() is overridden. */
const realPerf = globalThis.performance;
global.performance = new Proxy(realPerf, {
  get(t, k) {
    if (k === "now") return () => NOW;
    const v = t[k];
    return typeof v === "function" ? v.bind(t) : v;
  },
});
global.atob = (s) => Buffer.from(s, "base64").toString("binary");
global.Path2D = class { moveTo() {} lineTo() {} closePath() {} };
global.Image = class { set src(v) { setTimeout(() => this.onerror && this.onerror(), 0); } };
const store = {};
global.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v); },
};
const canvas = node("map");
canvas.parentNode = { getBoundingClientRect: () => ({ left: 0, top: 0, width: W, height: H }) };
global.document = {
  getElementById: node, createElement: (t) => el("c-" + t), querySelectorAll: () => [],
  documentElement: { setAttribute() {}, removeAttribute() {}, getAttribute: () => null },
  body: el("body"), addEventListener() {}, visibilityState: "visible",
};
global.getComputedStyle = () => ({ getPropertyValue: () => "#eee" });
global.window = {
  devicePixelRatio: 2, matchMedia: () => ({ matches: false, addEventListener() {} }),
  addEventListener() {}, localStorage: global.localStorage,
  crypto: require("crypto").webcrypto,
};
global.window.self = global.window.top = global.window;
global.crypto = require("crypto").webcrypto;
global.navigator = {};

// ------------------------------------------------------------- the app ---
const payload = fs.readFileSync(path.join(REPO, "data/processed/web_preview/payload.js"), "utf8");
(0, eval)(payload.replace(/^window\./, "global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = null;
global.window.SOPP_SUPABASE = { url: CFG.url, anon: CFG.anon };

const shell = fs.readFileSync(path.join(DIR, "..", "web", "shell.html"), "utf8").split("\r\n").join("\n");
let js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));
/* Injected just before the app finishes booting.

   Anchored on `size();` alone, and asserted. The previous anchor included the
   line after it, `view.cx=`, which stopped being the line after it the moment
   a default map centre was added between the two. `String.replace` returns the
   string unchanged when its anchor is gone, so the probe silently vanished and
   the suite failed forty lines later with "cannot read SYNC of undefined" -
   a symptom that says nothing about the cause. The shell will move again. */
const ANCHOR = "\nsize();\n";
if (js.indexOf(ANCHOR) < 0)
  throw new Error("probe anchor missing from shell.html: " + JSON.stringify(ANCHOR) +
                  " - the shell moved, so this test is reaching into nothing");
const probe = `
global.__app={syncNow:syncNow,addSpot:addSpot,removeSpot:removeSpot,
  get SPOTS(){return SPOTS;}, set SPOTS(v){SPOTS=v;}, get SESSION(){return SESSION;},
  get SYNC(){return SYNC;}, fromUTM33:fromUTM33, saveSpots:saveSpots};
size();
`;
js = js.replace(ANCHOR, probe);
(0, eval)(js);

const app = global.__app;
let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
/* The app kicks off a sync on launch, and syncNow() no-ops while one is in
   flight, so every step here waits for the queue to go quiet first. */
async function settle(maxMs = 15000) {
  const t0 = Date.now();
  while ((app.SYNC.busy || app.SYNC.chain > 0) && Date.now() - t0 < maxMs) await sleep(50);
  await sleep(150);
}

async function server(pathname, token) {
  const r = await fetch(CFG.url.replace(/\/$/, "") + pathname, {
    headers: { apikey: CFG.anon, Authorization: "Bearer " + token },
  });
  return { status: r.status, body: await r.json() };
}

(async () => {
  await settle();                       // let the launch sync finish first
  console.log("— push —");
  const spot = app.addSpot([86800, 6464300]);          // Kristiansand centre
  check("a saved spot starts dirty", spot.dirty === true);
  await app.syncNow();
  await settle();

  check("sync reached a session", !!(app.SESSION && app.SESSION.access_token),
    app.SYNC.state + (app.SYNC.detail ? " " + app.SYNC.detail : ""));
  check("sync finished clean", app.SYNC.state === "ok", app.SYNC.detail);
  check("the pushed spot is no longer dirty", app.SPOTS[0].dirty === false);

  const tok = app.SESSION.access_token;
  const { status, body } = await server(`/rest/v1/spots?id=eq.${spot.id}&select=*`, tok);
  check("the row exists on the server", status === 200 && body.length === 1,
    `HTTP ${status}, ${body.length} row(s)`);

  console.log("\n— the coordinate survived the round trip —");
  const row = body[0] || {};
  check("lat/lon landed in Kristiansand",
    row.lat > 58.0 && row.lat < 58.3 && row.lon > 7.8 && row.lon < 8.2,
    `${row.lat} N ${row.lon} E`);
  const back = app.fromUTM33(86800, 6464300);
  check("stored lat/lon matches the client's own conversion",
    Math.abs(row.lat - back[0]) < 1e-6 && Math.abs(row.lon - back[1]) < 1e-6);
  check("species travelled with it", row.species_name === "Traktkantarell", row.species_name);

  console.log("\n— a second device —");
  // Same account, empty local store: exactly what a new phone looks like.
  app.SPOTS = [];
  app.saveSpots();
  delete store["soppkart.pulledAt"];
  await app.syncNow();
  await settle();
  check("the spot came back from the server", app.SPOTS.length === 1,
    `${app.SPOTS.length} spot(s)`);
  const got = app.SPOTS[0] || {};
  check("coordinates survived UTM -> lon/lat -> UTM",
    Math.abs(got.e - 86800) <= 1 && Math.abs(got.n - 6464300) <= 1,
    `${got.e} E ${got.n} N (saved 86800 E 6464300 N)`);
  check("it is not marked dirty on arrival", got.dirty === false);

  console.log("\n— delete propagates —");
  app.removeSpot(spot.id);
  await sleep(800);
  await settle();
  const after = await server(`/rest/v1/spots?id=eq.${spot.id}&select=id,deleted_at`, tok);
  const tombstoned = after.body.length === 1 && !!after.body[0].deleted_at;
  check("the server row is tombstoned, not silently dropped", tombstoned,
    JSON.stringify(after.body));

  app.SPOTS = [];
  app.saveSpots();
  delete store["soppkart.pulledAt"];
  await app.syncNow();
  await settle();
  check("a fresh device does not resurrect the deleted spot", app.SPOTS.length === 0,
    `${app.SPOTS.length} spot(s)`);

  console.log("\n— cleanup —");
  await fetch(CFG.url.replace(/\/$/, "") + `/rest/v1/spots?id=eq.${spot.id}`, {
    method: "DELETE", headers: { apikey: CFG.anon, Authorization: "Bearer " + tok },
  });
  const left = await server("/rest/v1/spots?select=id", tok);
  check("test rows removed", left.body.length === 0, `${left.body.length} left`);

  console.log(fails ? `\n${fails} FAILED` : "\nall end-to-end sync checks passed");
  process.exit(fails ? 1 : 0);
})();
