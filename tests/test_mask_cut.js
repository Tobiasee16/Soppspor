/* The field the contours are traced from, once there is a mask to cut with.
 *
 * At 128 m a blank cell cannot be drawn as anything but a diamond, because a
 * contour closing around one cell passes through the midpoints of its four
 * edges. Nationally there are 63,011 blank patches exactly one cell across, so
 * the map came back from the phone stippled with little rotated squares that
 * matched nothing on the ground.
 *
 * drawField is the half of the answer that lives in the interface: it grows
 * the field into its blanks twice, so a contour runs straight over a small
 * tarn or a house, and the true shape is cut out afterwards at 32 m by
 * mkErase. The two properties that matter are opposite in direction, which is
 * why both are checked here:
 *
 *   - a small blank is filled, or the diamond is still there;
 *   - a large blank keeps a blank middle, or a zone is drawn right across a
 *     lake and has to be erased in its entirety, which puts the whole burden
 *     on a mask tile that may not have arrived yet.
 *
 * And one that is not about drawing at all: smoothField must come back
 * untouched, because that is what the card reads. The map may be smoother than
 * the grid; it may not make a claim the card would contradict.
 */
const fs = require("fs");
const path = require("path");

const shell = fs.readFileSync(path.join(__dirname, "..", "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

function grab(name) {
  const start = shell.indexOf("function " + name + "(");
  if (start < 0) throw new Error("no function " + name);
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(start, i + 1);
}

/* A grid of solid habitat at 0.8, holed twice: one cell at (5,5), and a 7x7
 * block at (14..20, 14..20) standing in for a lake. */
const W = 30, H = 30;
function scene() {
  const by = new Uint8Array(W * H).fill(200);
  const P = new Float32Array(W * H).fill(0.8);
  const hole = (x, y) => { by[y * W + x] = 0; P[y * W + x] = 0; };
  hole(5, 5);
  for (let y = 14; y <= 20; y++) for (let x = 14; x <= 20; x++) hole(x, y);
  return { bytes: by, _field: P };
}

function run(withMask) {
  const sp = scene();
  const fn = new Function("MSK", "GW", "GH", "smoothField", "sp",
    grab("drawField") + "return drawField(sp);");
  const out = fn(withMask ? { url: "x" } : null, W, H, (s) => s._field, sp);
  return { out, sp };
}

// ------------------------------------------------------------- with a mask --
{
  const { out, sp } = run(true);
  const at = (x, y) => out[y * W + x];

  check("a one-cell blank is filled, so no diamond is traced round it",
        at(5, 5) > 0.7, "value at the hole: " + at(5, 5).toFixed(3));

  check("the middle of a seven-cell lake stays blank",
        at(17, 17) === 0, "value at the lake centre: " + at(17, 17));

  /* Two passes reach two cells in and no further. The rim it does fill is what
   * mkErase then cuts back to the real shoreline. */
  check("the lake's rim is grown, but only two cells deep",
        at(14, 17) > 0 && at(15, 17) > 0 && at(16, 17) === 0,
        "columns 14..17: " +
        [at(14, 17), at(15, 17), at(16, 17), at(17, 17)].map(v => v.toFixed(2)).join(" "));

  check("ordinary habitat is left exactly as it was",
        at(2, 2) === Math.fround(0.8), String(at(2, 2)));

  check("the field the card reads is not the one that was grown",
        sp._field[5 * W + 5] === 0,
        "smoothField at the hole: " + sp._field[5 * W + 5]);

  check("the grown field is cached rather than rebuilt per band",
        sp._df instanceof Float32Array);
}

// ---------------------------------------------------------- without a mask --
{
  const { out } = run(false);
  check("with no mask shipped, the field is handed back untouched",
        out[5 * W + 5] === 0 && out[17 * W + 17] === 0,
        "blanks stay blank, so blank cells stay under every threshold");
}

/* ------------------------------------------------- where the erase lands --
 * Nobody can see this one on a phone: erasing half a tile off would take the
 * wrong side of every shoreline in Norway and still look like a map. So the
 * arithmetic is checked against numbers worked out by hand.
 *
 * The national origin is at (0, 100000). A tile is 256 cells of 128 m, so
 * 32768 m across. The viewport is 400x800 CSS px at 10 m per pixel centred on
 * (40000, 50000), which reaches 38000..42000 east and 46000..54000 north - all
 * of it inside tile row 1, column 1.
 */
{
  const view = { cx: 40000, cy: 50000, mpp: 10, rot: 0 };
  const CW = 400, CH = 800, NOX = 0, NOY = 100000;
  const w2s = (e, n) => [(e - view.cx) / view.mpp + CW / 2,
                         (view.cy - n) / view.mpp + CH / 2];
  const drawn = [], ops = [];
  const g = {
    save() { ops.push("save"); }, restore() { ops.push("restore"); },
    setTransform() {},
    set globalCompositeOperation(v) { ops.push(v); },
    drawImage(img, x, y, w, h) { drawn.push({ img, x, y, w, h }); },
  };
  const MSK = { url: "zones/mask/{z}/{row}_{col}.png", px: 1024,
                tiles: [["1_1", "1_2"]] };
  const has = MSK.tiles.map(ids => {
    const h = {}; for (const i of ids) h[i] = 1; return h;
  });
  const MK = { cache: {}, order: [], MAX: 8, has };
  for (const id of MSK.tiles[0]) MK.cache["0/" + id] = { img: { id }, ok: true };
  MK.order = MSK.tiles[0].map(id => "0/" + id);

  const ARGS = ["MSK", "MK", "TILED", "ZT", "CELL", "view", "CW", "CH",
                "NOX", "NOY", "DPR", "w2s", "requestDraw", "Image", "g"];
  /* vhw/vhh ride along: the erase window follows the rotated viewport now,
     so stubbing them would test a viewport the app no longer has. */
  const SRC = grab("vhw") + grab("vhh")
            + grab("mkURL") + grab("mkTile") + grab("mkErase") + "mkErase(g);";
  new Function(...ARGS, SRC)(
    MSK, MK, true, { z: 0, tile: 256 }, 128, view, CW, CH, NOX, NOY,
    1, w2s, () => {}, class {}, g);

  check("it erases with exactly the tiles the screen overlaps",
        drawn.length === 1 && drawn[0].img.id === "1_1",
        drawn.map(d => d.img.id).join(",") || "nothing drawn");

  /* 32768 m at 10 m per pixel is 3276.8, and both edges round the same way so
   * neighbouring tiles meet on one integer and leave no unerased hairline. */
  check("the tile lands on the ground it covers",
        drawn[0] && drawn[0].x === -523 && drawn[0].y === -1323 &&
        drawn[0].w === 3277 && drawn[0].h === 3277,
        drawn[0] ? [drawn[0].x, drawn[0].y, drawn[0].w, drawn[0].h].join(" ") : "-");

  check("it takes out the zones and puts the brush back",
        ops.indexOf("destination-out") > 0 && ops[ops.length - 1] === "restore",
        ops.join(" "));

  /* A tile the exporter never wrote must not be requested: most of the
   * Norwegian Sea is one of those, and a 404 apiece is the cost of asking. */
  const asked = [];
  const MK2 = { cache: {}, order: [], MAX: 8, has: [{}] };
  new Function(...ARGS, SRC)(
    MSK, MK2, true, { z: 0, tile: 256 }, 128, view, CW, CH, NOX, NOY, 1, w2s,
    () => {}, class { set src(v) { asked.push(v); } }, g);
  check("a tile that was never written is never requested",
        asked.length === 0, asked.join(","));

  check("the url names the level, row and column",
        new Function("MSK", grab("mkURL") + "return mkURL(0,1,2);")(MSK)
          === "zones/mask/0/1_2.png");
}

console.log(fails ? `\n${fails} FAILED` : "\nall mask-cut checks passed");
process.exit(fails ? 1 : 0);
