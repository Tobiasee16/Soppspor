/* Pulls the contour code out of the shell and runs it against fields whose
   correct answer is known, so a broken case in the marching-squares table or a
   ring that fails to close is caught here rather than on a phone. */
const fs = require("fs");

const t = fs.readFileSync(__dirname + "/../web/shell.html", "utf8");
// drawField sits just above buildZones and feeds it, so the slice starts
// there. With MSK null it hands the field straight back, which is the
// behaviour every case below was written against.
const start = t.indexOf("function drawField(");
const end = t.indexOf("function drawZones()");
if (start < 0 || end < 0) throw new Error("could not locate the contour block");
const src = t.slice(start, end);

let GW = 0, GH = 0, FIELD = null;
const rings = [];

class Path2D {
  constructor() { this.cur = null; this.rings = []; }
  moveTo(x, y) { this.cur = [[x, y]]; this.rings.push(this.cur); }
  lineTo(x, y) { this.cur.push([x, y]); }
  closePath() { this.closed = (this.closed || 0) + 1; }
}
function smoothField() { return FIELD; }
function pctLUT() { return null; }

const run = new Function(
  "GW", "GH", "Path2D", "smoothField", "pctLUT", "MSK",
  src + "\nreturn buildZones;");

function build(w, h, field, levels) {
  GW = w; GH = h; FIELD = field;
  // buildZones takes the species and its band list and *returns* the paths now,
  // instead of writing one module-level global: several species are drawn at
  // once and each keeps its own set.
  const buildZones = run(GW, GH, Path2D, smoothField, pctLUT, null);
  return buildZones({ key: "test" }, levels);
}

function check(name, paths, expectRings) {
  let ok = true, msgs = [];
  paths.forEach((p, i) => {
    if (!p) { msgs.push(`level ${i}: no path`); ok = false; return; }
    p.rings.forEach((r, j) => {
      const a = r[0], b = r[r.length - 1];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (d > 0.01) { msgs.push(`level ${i} ring ${j} open by ${d.toFixed(3)}`); ok = false; }
    });
    msgs.push(`level ${i}: ${p.rings.length} ring(s), ` +
      `${p.rings.reduce((s, r) => s + r.length, 0)} points`);
    if (expectRings && expectRings[i] !== undefined && p.rings.length !== expectRings[i]) {
      msgs.push(`  EXPECTED ${expectRings[i]} rings`); ok = false;
    }
  });
  console.log((ok ? "PASS  " : "FAIL  ") + name);
  msgs.forEach(m => console.log("        " + m));
  return ok;
}

let allOk = true;

// 1. One smooth bump. Every threshold should yield exactly one closed ring.
{
  const w = 60, h = 60, f = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const d = Math.hypot(x - 30, y - 30) / 22;
    f[y * w + x] = Math.exp(-d * d * 3);
  }
  allOk &= check("single bump -> one ring per level",
    build(w, h, f, [{ p: 0.3 }, { p: 0.55 }, { p: 0.8 }]), [1, 1, 1]);
}

// 2. An annulus: high values in a ring, low in the middle. Each level must
//    produce two rings (outer and inner), which is the case even-odd filling
//    exists to handle.
{
  const w = 80, h = 80, f = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const r = Math.hypot(x - 40, y - 40);
    f[y * w + x] = Math.exp(-Math.pow((r - 22) / 7, 2));
  }
  allOk &= check("annulus -> two rings per level",
    build(w, h, f, [{ p: 0.4 }, { p: 0.7 }]), [2, 2]);
}

// 3. A region running off the edge of the grid. The padded border is what
//    makes this close rather than leaving an unfillable open curve.
{
  const w = 50, h = 50, f = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) f[y * w + x] = x < 20 ? 1 : 0;
  allOk &= check("region touching the grid edge -> closed",
    build(w, h, f, [{ p: 0.5 }]), [1]);
}

// 4. Two separate bumps -> two rings.
{
  const w = 90, h = 50, f = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const a = Math.exp(-Math.pow(Math.hypot(x - 22, y - 25) / 11, 2));
    const b = Math.exp(-Math.pow(Math.hypot(x - 68, y - 25) / 11, 2));
    f[y * w + x] = Math.max(a, b);
  }
  allOk &= check("two bumps -> two rings",
    build(w, h, f, [{ p: 0.5 }]), [2]);
}

console.log(allOk ? "\nall contour tests passed" : "\nCONTOUR TESTS FAILED");
process.exit(allOk ? 0 : 1);
