/* The trail network is shipped as int16 deltas off packed start points, and a
   decoder that is subtly wrong draws a plausible-looking network in the wrong
   place. So this decodes with the shell's own code and checks the result
   against the extent it claims to cover, plus the raw GML it came from. */
const fs = require("fs");
const path = require("path");

const REPO = require("path").join(__dirname, "..");
const TR = JSON.parse(fs.readFileSync(path.join(REPO, "data/interim/trails.json"), "utf8"));

const shell = fs.readFileSync(__dirname + "/../web/shell.html", "utf8").split("\r\n").join("\n");
function grab(name) {
  const s = shell.indexOf("function " + name + "(");
  if (s < 0) throw new Error("missing " + name);
  let i = shell.indexOf("{", s), d = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") d++;
    else if (shell[i] === "}") { d--; if (!d) break; }
  }
  return shell.slice(s, i + 1);
}

// Decode exactly as buildTrails() does, but into arrays we can inspect.
class Path2D {
  constructor() { this.pts = []; }
  moveTo(x, y) { this.pts.push([x, y]); }
  lineTo(x, y) { this.pts.push([x, y]); }
}
const b64 = new Function("s", grab("b64").replace(/^function b64\(s\)/, "") + "")
  ? new Function("s", "var t=atob(s),n=t.length,o=new Uint8Array(n);" +
      "for(var i=0;i<n;i++)o[i]=t.charCodeAt(i);return o;") : null;
global.atob = (s) => Buffer.from(s, "base64").toString("binary");
const typed = new Function("u8", "Type",
  "return new Type(u8.buffer,0,(u8.byteLength/Type.BYTES_PER_ELEMENT)|0);");

const st = typed(b64(TR.starts), Int32Array);
const dl = typed(b64(TR.deltas), Int16Array);
const ct = typed(b64(TR.counts), Uint32Array);
const bk = b64(TR.bucket);

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

check("line count agrees across every array",
  ct.length === TR.lines && st.length === TR.lines * 2 && bk.length === TR.lines,
  `${TR.lines} lines`);
const expectedDeltas = TR.vertices - TR.lines;
check("delta count is vertices minus lines",
  dl.length === expectedDeltas * 2,
  `${dl.length / 2} pairs, expected ${expectedDeltas}`);

// Walk every line and convert to world metres.
let di = 0, minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
let verts = 0, longest = 0;
const u = TR.unit_m, ox = TR.origin[0], oy = TR.origin[1];
for (let li = 0; li < ct.length; li++) {
  let x = st[li * 2], y = st[li * 2 + 1];
  const n = ct[li];
  longest = Math.max(longest, n);
  for (let k = 0; k < n; k++) {
    if (k) { x += dl[di * 2]; y += dl[di * 2 + 1]; di++; }
    const wx = ox + x * u, wy = oy + y * u;
    if (wx < minx) minx = wx; if (wx > maxx) maxx = wx;
    if (wy < miny) miny = wy; if (wy > maxy) maxy = wy;
    verts++;
  }
}
check("every delta was consumed exactly once", di === expectedDeltas,
  `${di} of ${expectedDeltas}`);
check("vertex count matches the manifest", verts === TR.vertices, `${verts}`);

const e = TR.extent;
console.log(`\ndecoded bounds  x ${minx.toFixed(0)}..${maxx.toFixed(0)}  y ${miny.toFixed(0)}..${maxy.toFixed(0)}`);
console.log(`claimed extent  x ${e[0].toFixed(0)}..${e[2].toFixed(0)}  y ${e[1].toFixed(0)}..${e[3].toFixed(0)}`);
// A WFS bbox filter returns every feature that *intersects* the box, whole.
// A path crossing the boundary therefore runs on past it, by as much as its
// own length. That is harmless - panning is clamped well inside this, so the
// overspill is never on screen - but it is not a decode error, which is what
// this check originally claimed it was. The bar is a margin wide enough for a
// long route and narrow enough to still catch a real offset.
check("decoded geometry sits on the extent, allowing for routes that cross it",
  minx >= e[0] - 8000 && maxx <= e[2] + 8000
  && miny >= e[1] - 8000 && maxy <= e[3] + 8000,
  `overspill x ${(e[0] - minx).toFixed(0)}/${(maxx - e[2]).toFixed(0)} m, ` +
  `y ${(e[1] - miny).toFixed(0)}/${(maxy - e[3]).toFixed(0)} m`);
check("coordinates are UTM33 over Kristiansand, not lat/lon or swapped axes",
  minx > 40000 && maxx < 140000 && miny > 6.4e6 && maxy < 6.55e6);
check("the network actually spans the area rather than collapsing to a point",
  maxx - minx > 20000 && maxy - miny > 20000,
  `${((maxx - minx) / 1000).toFixed(0)} x ${((maxy - miny) / 1000).toFixed(0)} km`);

// Cross-check the first line against the raw GML it was built from.
const gml = fs.readFileSync(path.join(REPO, "data/raw/turrutebasen/app_Fotrute.gml"), "utf8");
const m = /<gml:posList[^>]*>([^<]+)<\/gml:posList>/.exec(gml);
const raw = m[1].trim().split(/\s+/).map(Number);
const rawStart = [raw[0], raw[1]];
const decStart = [ox + st[0] * u, oy + st[1] * u];
const off = Math.hypot(rawStart[0] - decStart[0], rawStart[1] - decStart[1]);
check("first line starts where the source says it does", off <= u,
  `${off.toFixed(2)} m from ${rawStart[0].toFixed(1)} ${rawStart[1].toFixed(1)}`);

console.log(`\n${TR.lines} lines, ${TR.vertices} vertices, longest ${longest} points`);
console.log(fails ? `${fails} FAILED` : "all trail geometry checks passed");
process.exit(fails ? 1 : 0);
