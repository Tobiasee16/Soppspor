/* The projection, and the one thing it must never get wrong.
 *
 * `w2s` puts a world coordinate on screen and `s2w` takes a screen coordinate
 * back to the world. They are inverses, and almost everything on the map
 * tolerates a small error in them: a zone drawn a metre out looks identical, a
 * basemap tile a pixel off is invisible.
 *
 * One path does not tolerate it. A long press runs
 *
 *     var w = s2w(cx - r.left, cy - r.top);
 *     var s = addSpot(w);
 *
 * so a wrong inverse **saves somebody's mushroom spot at the wrong place**,
 * silently, as a perfectly plausible pin in the wrong wood. Saved spots are the
 * one thing in this app that is genuinely the user's, and a corrupted one does
 * not announce itself. `tap()` has the same shape for setting position by hand.
 *
 * So the invariant is asserted directly and exhaustively:
 *
 *     s2w(w2s(p)) === p     for every rotation, zoom and point
 *
 * This file was written **before** rotation existed, which is the point of it.
 * At rot = 0 it passes against the projection as it has always been; at any
 * other rotation it fails until the transform is actually rotation-aware. A
 * guard written afterwards only ever confirms what was already built.
 */
const fs = require("fs");
const path = require("path");
const REPO = path.join(__dirname, "..");

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name + " in the shell");
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

/* A view somewhere real: Kristiansand, in EPSG:25833. */
function make(rotDeg, mpp) {
  const env = {
    CW: 412, CH: 780,
    view: { cx: 440000, cy: 6450000, mpp: mpp, rot: rotDeg * Math.PI / 180 },
  };
  const fn = new Function("env",
    "with(env){" + grab("w2s") + "\n" + grab("s2w") + "\nreturn {w2s:w2s,s2w:s2w,env:env};}");
  return fn(env);
}

const ROTS = [0, 15, 30, 45, 90, 137, 180, -30, -90];
const MPPS = [8, 64, 512];
const EPS = 0.01;                      // one centimetre, on a 0.5 m quantised map

/* ---- the invariant ---- */
ROTS.forEach(function (rot) {
  let worst = 0, worstAt = null;
  MPPS.forEach(function (mpp) {
    const P = make(rot, mpp);
    for (let dx = -3; dx <= 3; dx++) {
      for (let dy = -3; dy <= 3; dy++) {
        const e = P.env.view.cx + dx * mpp * 60;
        const n = P.env.view.cy + dy * mpp * 60;
        const s = P.w2s(e, n);
        const back = P.s2w(s[0], s[1]);
        const err = Math.max(Math.abs(back[0] - e), Math.abs(back[1] - n));
        if (err > worst) { worst = err; worstAt = [e, n, mpp]; }
      }
    }
  });
  check("round-trip survives " + rot + "°", worst < EPS,
    "worst " + worst.toFixed(4) + " m" + (worstAt ? " at mpp " + worstAt[2] : ""));
});

/* ---- the centre is the centre, whatever the rotation ---- */
ROTS.forEach(function (rot) {
  const P = make(rot, 64);
  const s = P.w2s(P.env.view.cx, P.env.view.cy);
  check("the view centre lands on screen centre at " + rot + "°",
    Math.abs(s[0] - P.env.CW / 2) < 1e-6 && Math.abs(s[1] - P.env.CH / 2) < 1e-6,
    s.map(function (v) { return v.toFixed(2); }).join(", "));
});

/* ---- the spot-saving path, named for what it protects ---- */
ROTS.forEach(function (rot) {
  const P = make(rot, 32);
  /* Somewhere a finger might land: a third across, two thirds down. */
  const sx = P.env.CW / 3, sy = P.env.CH * 2 / 3;
  const w = P.s2w(sx, sy);                  // what armLongPress would store
  const back = P.w2s(w[0], w[1]);           // where that pin would then draw
  check("a long press at " + rot + "° saves the ground under the finger",
    Math.abs(back[0] - sx) < 1e-6 && Math.abs(back[1] - sy) < 1e-6,
    "pin would draw " + Math.hypot(back[0] - sx, back[1] - sy).toFixed(3) + " px away");
});

/* ---- known answers, because the round-trip alone proves nothing ----
 *
 * Worth recording why this section exists. The round-trip above passed at
 * every rotation against a projection that ignored rotation entirely: w2s and
 * s2w were consistent inverses of each other while both being wrong, and a
 * self-consistency check cannot tell that from correct. So the geometry is
 * pinned against answers worked out by hand instead.
 *
 * Convention: `view.rot` rotates the map counterclockwise on screen, so at
 * +90 degrees a point due north of the centre draws to the RIGHT of it.
 */
{
  const mpp = 64, d = 5000, px = d / mpp;      // 5 km north, 78.125 px

  const at0 = make(0, mpp);
  let s = at0.w2s(at0.env.view.cx, at0.env.view.cy + d);
  check("at 0° a point 5 km north draws straight up",
    Math.abs(s[0] - at0.env.CW / 2) < 1e-6
    && Math.abs((at0.env.CH / 2 - s[1]) - px) < 1e-6,
    "offset " + (s[0] - at0.env.CW / 2).toFixed(1) + ", "
    + (at0.env.CH / 2 - s[1]).toFixed(1));

  const at90 = make(90, mpp);
  s = at90.w2s(at90.env.view.cx, at90.env.view.cy + d);
  check("at 90° that same point draws to the right",
    Math.abs((s[0] - at90.env.CW / 2) - px) < 1e-6
    && Math.abs(s[1] - at90.env.CH / 2) < 1e-6,
    "offset " + (s[0] - at90.env.CW / 2).toFixed(1) + ", "
    + (s[1] - at90.env.CH / 2).toFixed(1));

  const at180 = make(180, mpp);
  s = at180.w2s(at180.env.view.cx, at180.env.view.cy + d);
  check("at 180° it draws straight down",
    Math.abs(s[0] - at180.env.CW / 2) < 1e-6
    && Math.abs((s[1] - at180.env.CH / 2) - px) < 1e-6,
    "offset " + (s[0] - at180.env.CW / 2).toFixed(1) + ", "
    + (s[1] - at180.env.CH / 2).toFixed(1));

  /* Distance from the centre cannot change with rotation - if it does, the
     transform is scaling as well as turning. */
  const at37 = make(37, mpp);
  s = at37.w2s(at37.env.view.cx, at37.env.view.cy + d);
  const r37 = Math.hypot(s[0] - at37.env.CW / 2, s[1] - at37.env.CH / 2);
  check("rotation turns without scaling", Math.abs(r37 - px) < 1e-6,
    r37.toFixed(4) + " px against " + px + " expected");
}

/* ---- the viewport's bounding box, which is what stops tiles going missing ----
 *
 * Every tile window and cull test is computed from vhw/vhh rather than from
 * the screen size, because a rotated viewport is a rotated rectangle and the
 * tiles it needs are the ones covering its bounding box. The invariant is not
 * "vhw is bigger" - at 90 degrees on a tall screen it is smaller - it is that
 * **all four corners of what the reader can see fall inside the box**. Miss
 * that and tiles vanish diagonally, which reads as a loading glitch rather
 * than as a bug.
 */
function halves(rotDeg, mpp) {
  const env = {
    CW: 412, CH: 780,
    view: { cx: 440000, cy: 6450000, mpp: mpp, rot: rotDeg * Math.PI / 180 },
  };
  return new Function("env", "with(env){" + grab("vhw") + grab("vhh")
    + grab("s2w") + "\n"
    + "return {vhw:vhw,vhh:vhh,s2w:s2w,env:env};}")(env);
}

[0, 15, 30, 45, 60, 90, 137, -30].forEach(function (rot) {
  const H = halves(rot, 64);
  const hw = H.vhw(), hh = H.vhh();
  const cx = H.env.view.cx, cy = H.env.view.cy;
  let inside = true, worst = 0;
  [[0, 0], [H.env.CW, 0], [0, H.env.CH], [H.env.CW, H.env.CH]].forEach(function (c) {
    const w = H.s2w(c[0], c[1]);
    const ox = Math.abs(w[0] - cx) - hw, oy = Math.abs(w[1] - cy) - hh;
    worst = Math.max(worst, ox, oy);
    if (ox > 1e-6 || oy > 1e-6) inside = false;
  });
  check("every screen corner is inside the tile box at " + rot + "°", inside,
    "worst overhang " + worst.toFixed(3) + " m");
});

{
  const a = halves(0, 64), b = halves(90, 64);
  check("at 90° the box swaps its sides",
    Math.abs(a.vhw() - b.vhh()) < 1e-6 && Math.abs(a.vhh() - b.vhw()) < 1e-6,
    a.vhw().toFixed(0) + "/" + a.vhh().toFixed(0) + " against "
    + b.vhw().toFixed(0) + "/" + b.vhh().toFixed(0));
  const d = halves(45, 64);
  check("at 45° the box is wider than the screen", d.vhw() > a.vhw(),
    d.vhw().toFixed(0) + " m against " + a.vhw().toFixed(0) + " m");
}

/* ---- north stays north at zero ---- */
{
  const P = make(0, 64);
  const s = P.w2s(P.env.view.cx, P.env.view.cy + 10000);
  check("with no rotation, north is up",
    Math.abs(s[0] - P.env.CW / 2) < 1e-6 && s[1] < P.env.CH / 2,
    s.map(function (v) { return v.toFixed(1); }).join(", "));
}

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
