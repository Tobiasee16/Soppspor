/* Boots the real shell against a tiled, national payload and drives it.
 *
 * The Kristiansand suites hand the shell a payload with every surface already
 * inside it. This one hands it nothing but metadata and a directory of PNG
 * tiles, which is what the national build actually ships, and then pans.
 *
 * It is a driver rather than a test: tests/test_zone_window.py builds the
 * country, cuts the tiles with the real exporter, writes the plan, runs this,
 * and checks the answers against what the whole country would have given. The
 * split is deliberate - the reference numbers want numpy, and the behaviour
 * wants the interface's own code rather than a copy of it.
 *
 *     node tests/national_boot.js <dir>
 *
 * <dir> holds payload.json, plan.json, optionally cond.json, and the tiles at
 * whatever path the payload's meta.tiles.url points to. One JSON object is
 * written to stdout.
 */
const fs = require("fs");
const path = require("path");
const stub = require(path.join(__dirname, "domstub.js"));

const DIR = process.argv[2];
if (!DIR) throw new Error("usage: node national_boot.js <dir>");
const read = (f) => JSON.parse(fs.readFileSync(path.join(DIR, f), "utf8"));

const payload = read("payload.json");
const plan = read("plan.json");
global.window.SOPP = payload;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = plan.cond ? read("cond.json") : null;
global.window.SOPP_ENV = { hosted: true, roaming: true, bounds: plan.bounds };

/* Tiles come off disk, and the stub is exactly as dumb as a static host: a
 * path either exists or it 404s. Every request is recorded, because "did it
 * ask for tiles it was told are empty" is a real question - one 404 per empty
 * tile per species is most of the Norwegian Sea on every pan. */
const asked = [];
global.fetch = function (url) {
  asked.push(url);
  const p = path.join(DIR, url);
  if (!/\.png$/.test(url) || !fs.existsSync(p))
    return Promise.resolve({ ok: false, status: 404 });
  const b = fs.readFileSync(p);
  const ab = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
  return Promise.resolve({ ok: true, status: 200,
                           arrayBuffer: () => Promise.resolve(ab) });
};

const REPO = path.join(__dirname, "..");
const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));

// Spliced in just before the outermost "})();" - appended after it, every name
// in the app is out of scope and the boot fails with a bare ReferenceError.
const at = js.lastIndexOf("})();");
if (at < 0) throw new Error("could not find the end of the shell IIFE");
const probe = js.slice(0, at) + `
;globalThis.__app = {
  tiled: () => TILED,
  win: () => [Math.round((OX - NOX) / CELL), Math.round((NOY - OY) / CELL), GW, GH],
  blank: () => ZT.blank,
  /* Settled means: nothing outstanding, and every selected species is holding
     bytes for the window that is currently bound. */
  ready: () => ZT.waiting === null &&
               (ZT.blank || SEL.every(function(s){ return s._win === ZT.key; })),
  zones: () => SEL.map(function(s){ return s._zones ? s._zones.paths.length : null; }),
  zoneOrigin: () => SEL.map(function(s){ return s._zones ? [s._zones.ox, s._zones.oy] : null; }),
  bytes: () => (SEL[0].bytes ? Buffer.from(SEL[0].bytes).toString("base64") : null),
  /* A world point, answered through whatever window happens to be loaded. The
     whole question of this suite is whether the answer depends on that. */
  probe: function(e, n){
    var x = Math.floor((e - OX) / CELL), y = Math.floor((OY - n) / CELL);
    if (x < 0 || y < 0 || x >= GW || y >= GH) return null;
    var i = y * GW + x, sp = SEL[0];
    return { byte: sp.bytes[i], h: habitatField(sp)[i], s: smoothField(sp)[i] };
  },
  goTo: function(e, n, mpp){ view.cx = e; view.cy = n; view.mpp = mpp;
                             clampView(); draw(); },
  toggle: function(key){
    var sp = D.species.filter(function(s){ return s.key === key; })[0];
    toggleSpecies(sp); repaint(); draw();
  },
  view: () => [view.cx, view.cy, view.mpp],
};` + js.slice(at);

let bootErr = null;
try { (0, eval)(probe); } catch (e) { bootErr = e; }
if (bootErr) {
  process.stdout.write(JSON.stringify(
    { boot: false, error: bootErr.stack.split("\n").slice(0, 3).join(" | ") }));
  process.exit(0);
}
const app = globalThis.__app;

/* Tiles arrive over promises and the map redraws on animation frames, so
 * settling means turning both handles until nothing is outstanding. The cap is
 * there so a hang is reported as a hang rather than as a hung test. */
async function settle() {
  for (let i = 0; i < 400; i++) {
    stub.flush(2);
    await new Promise((r) => setTimeout(r, 0));
    if (app.ready()) return true;
  }
  return false;
}

(async () => {
  const out = { boot: true, tiled: app.tiled(), steps: [] };
  const note = stub.node("coverNote");
  out.opened = { view: app.view(), settled: await settle(), win: app.win() };
  for (const st of plan.steps) {
    if (st.view) app.goTo(st.view[0], st.view[1], st.view[2]);
    if (st.toggle) app.toggle(st.toggle);
    const settled = await settle();
    out.steps.push({
      name: st.name, settled, win: app.win(), blank: app.blank(),
      note: { hidden: !!note.hidden, html: String(note.innerHTML || "") },
      zones: app.zones(), zoneOrigin: app.zoneOrigin(),
      bytes: st.bytes ? app.bytes() : null,
      probes: (st.probes || []).map((p) => app.probe(p[0], p[1])),
    });
  }
  out.asked = asked;
  process.stdout.write(JSON.stringify(out));
})();
