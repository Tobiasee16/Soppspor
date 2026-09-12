/* The WMTS tile maths, and the hosted build that depends on it.
 *
 * This is the failure mode the trails work already taught us once: an index
 * that is subtly wrong does not error, it draws a perfectly plausible map of
 * the wrong place. Kartverket's utm33n matrix counts rows from the *north*,
 * so an inverted row is the single likeliest mistake and would put Kristiansand
 * somewhere in the Barents Sea.
 *
 * The anchor below was checked against the service itself on 2026-09-12: the
 * WMTS tile at level 13 row 3809 col 3805 and an independent WMS render of the
 * bbox this code computes for it correlate at 0.968. That is what makes it an
 * anchor rather than a restatement of the same arithmetic.
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
// RT_BUDGET is declared between two of the functions, so it is lifted by name
// rather than by slicing a contiguous block.
const consts = shell.slice(shell.indexOf("var RT_BASE="),
                           shell.indexOf("function rtResOf("))
  + (shell.match(/var RT_BUDGET=\d+;/) || [""])[0];
const src = consts + ["rtResOf", "rtCount", "rtLevel", "rtTileBox", "rtRange"].map(grab).join("\n");

function api(view, DPR, CW, CH) {
  return new Function("view", "DPR", "CW", "CH",
    src + "return {rtResOf,rtCount,rtLevel,rtTileBox,rtRange,RT_BASE,RT_LAYER,RT_OX,RT_OY,RT_RES0,RT_TILE,RT_ZMAX,RT_BUDGET};")(
      view, DPR, CW, CH);
}
const T = api({ cx: 0, cy: 0, mpp: 10 }, 1, 400, 800);

// --------------------------------------------------------- the grid itself --
check("origin matches the service's WMTSCapabilities",
      T.RT_OX === -2500000 && T.RT_OY === 9045984, T.RT_OX + ", " + T.RT_OY);
check("level 0 is 21664 m/px", T.rtResOf(0) === 21664);
check("each level halves", T.rtResOf(5) === 21664 / 32);
check("level 18 is about 8 cm/px", Math.abs(T.rtResOf(18) - 0.0826416) < 1e-6,
      T.rtResOf(18).toFixed(6));
check("tiles are 256 px", T.RT_TILE === 256);

// ------------------------------------------------- the anchor, verified --
{
  // Inland Kristiansand kommune, EPSG:25833.
  const e = 76000, n = 6467000, z = 13;
  const span = T.RT_TILE * T.rtResOf(z);
  const col = Math.floor((e - T.RT_OX) / span);
  const row = Math.floor((T.RT_OY - n) / span);
  check("the verified anchor tile is still the one computed",
        row === 3809 && col === 3805, "row " + row + " col " + col);
  const b = T.rtTileBox(z, row, col);
  check("and the point falls inside that tile's bbox",
        b[0] <= e && e <= b[2] && b[1] <= n && n <= b[3],
        "[" + b.map(v => Math.round(v)).join(", ") + "]");
  check("the bbox is one tile across", Math.abs((b[2] - b[0]) - span) < 1e-6 &&
        Math.abs((b[3] - b[1]) - span) < 1e-6);
}

// --------------------------------------------- rows count from the north --
{
  const z = 10, span = T.RT_TILE * T.rtResOf(z);
  const north = T.rtTileBox(z, 100, 500), south = T.rtTileBox(z, 101, 500);
  check("a higher row number is further south", north[1] > south[1],
        north[1].toFixed(0) + " vs " + south[1].toFixed(0));
  const west = T.rtTileBox(z, 100, 500), east = T.rtTileBox(z, 100, 501);
  check("a higher column number is further east", east[0] > west[0]);
  check("adjacent tiles touch exactly, no gap and no overlap",
        Math.abs(north[1] - south[3]) < 1e-9 && Math.abs(west[2] - east[0]) < 1e-9);
}

// ------------------------------------------------------- level selection --
{
  // A tile should never be coarser than the screen, or the map is blurred.
  for (const [mpp, dpr] of [[100, 1], [10, 2], [2, 3], [0.15, 3], [0.15, 1]]) {
    const t = api({ cx: 76000, cy: 6467000, mpp }, dpr, 400, 800);
    const z = t.rtLevel(), res = t.rtResOf(z);
    const want = mpp / dpr * 1.3;
    // Level 18 is the finest the service publishes. Past that the basemap is
    // upscaled and there is nothing to be done about it, so the assertion is
    // "fine enough, or already as fine as Kartverket goes".
    check(`at ${mpp} m/px and DPR ${dpr}, level ${z} is as fine as available`,
          res <= want + 1e-9 || z === t.RT_ZMAX,
          `${res.toFixed(4)} m/px vs wanted ${want.toFixed(4)}`
          + (z === t.RT_ZMAX && res > want ? `  (service floor, ${(res / want).toFixed(2)}x upscale)` : ""));
    check(`  ...and not needlessly finer than one level`,
          z === 0 || t.rtResOf(z - 1) > want, `level ${z}`);
  }
  const deep = api({ cx: 0, cy: 0, mpp: 0.001 }, 3, 400, 800);
  check("an absurd zoom clamps to the deepest level the service has",
        deep.rtLevel() === T.RT_ZMAX, String(deep.rtLevel()));
}

// -------------------------------------------------- the range on screen --
{
  const t = api({ cx: 76000, cy: 6467000, mpp: 2 }, 3, 400, 800);
  const z = t.rtLevel(), g = t.rtRange(z);
  const nx = g.c1 - g.c0 + 1, ny = g.r1 - g.r0 + 1;
  check("a phone screen needs a sane number of tiles", nx * ny > 0 && nx * ny <= 80,
        nx + " x " + ny + " = " + nx * ny + " tiles");
  // The range must actually cover the viewport corners.
  const tl = t.rtTileBox(z, g.r0, g.c0), br = t.rtTileBox(z, g.r1, g.c1);
  const left = 76000 - 400 * 2 / 2, right = 76000 + 400 * 2 / 2;
  const bot = 6467000 - 800 * 2 / 2, top = 6467000 + 800 * 2 / 2;
  check("the tile range covers the whole viewport",
        tl[0] <= left && br[2] >= right && br[1] <= bot && tl[3] >= top);
}

// ------------------------------------------------------------ the URL --
{
  const m = shell.match(/im\.src=RT_BASE\+\(RT_LAYER\[name\]\|\|RT_LAYER\.plain\)\s*\n?\s*\+"\/default\/utm33n\/"\+z\+"\/"\+row\+"\/"\+col\+"\.png";/);
  check("the URL is the capabilities template, matrix set utm33n", !!m);
  check("the base is Kartverket's cache",
        T.RT_BASE === "https://cache.kartverket.no/v1/wmts/1.0.0/", T.RT_BASE);
  check("both basemap choices name a layer the service publishes",
        ["topo", "topograatone", "toporaster", "sjokartraster"]
          .indexOf(T.RT_LAYER.plain) >= 0 &&
        ["topo", "topograatone", "toporaster", "sjokartraster"]
          .indexOf(T.RT_LAYER.topo) >= 0,
        T.RT_LAYER.plain + " / " + T.RT_LAYER.topo);
}

// ------------------------------------------------- Norway, and the bounds --
{
  const m = shell.match(/var NORWAY=\[([-\d,]+)\];/);
  check("the national bounds are declared", !!m);
  const nb = m[1].split(",").map(Number);
  // Lindesnes, Nordkinn, Utsira and Hornoya in EPSG:25833 - the extremes the
  // box was computed from. Every one of them must be inside it.
  const extremes = {
    "Lindesnes": [30342, 6454491], "Nordkinn": [953831, 7939232],
    "Utsira": [-75223, 6618242], "Hornoya": [1100013, 7887536],
    "Kristiansand": [76000, 6467000],
  };
  for (const [name, [x, y]] of Object.entries(extremes)) {
    check(`${name} is inside the national bounds`,
          x >= nb[0] && x <= nb[2] && y >= nb[1] && y <= nb[3]);
  }
  check("the box is about the size of Norway",
        Math.abs((nb[2] - nb[0]) / 1000 - 1215) < 5 &&
        Math.abs((nb[3] - nb[1]) / 1000 - 1525) < 5,
        `${((nb[2]-nb[0])/1000).toFixed(0)} x ${((nb[3]-nb[1])/1000).toFixed(0)} km`);
}
// ------------------------------------------------------- the tile budget --
// Asking for device-pixel sharpness unconditionally wanted 352 tiles on an
// 820x1180 tablet at DPR 3 - 21 MB for one screenful, and a sixth of the daily
// per-IP budget spent on a single view. Worse, drawLiveTiles used to skip the
// target level past a fixed threshold, so the map went blank rather than soft.
// Sweep the screens and zooms people actually have, and pin the ceiling.
{
  const screens = [[390, 844], [400, 800], [430, 932], [360, 780], [820, 1180]];
  let worst = 0, worstAt = "", bad = 0;
  for (const [CW, CH] of screens)
    for (const DPR of [1, 2, 3])
      for (let mpp = 0.15; mpp < 5000; mpp *= 1.15) {
        const t = api({ cx: 76000, cy: 6467000, mpp }, DPR, CW, CH);
        const z = t.rtLevel(), n = t.rtCount(z);
        if (n > worst) { worst = n; worstAt = CW + "x" + CH + " DPR" + DPR + " "
                         + mpp.toFixed(2) + " m/px -> z" + z; }
        if (n === 0 || z < 0 || z > t.RT_ZMAX) bad++;
      }
  check("no screen and zoom exceeds the tile budget", worst <= T.RT_BUDGET,
        "worst " + worst + " of " + T.RT_BUDGET + " at " + worstAt);
  check("and none asks for nothing, or for a level the service lacks",
        bad === 0, String(bad));
}

// The budget must still leave an ordinary phone genuinely sharp, or it has
// solved the data problem by making the map worse.
{
  const t = api({ cx: 76000, cy: 6467000, mpp: 5 }, 3, 400, 800);
  const z = t.rtLevel();
  const upscale = t.rtResOf(z) / (5 / 3);
  check("a phone at 5 m/px still gets near device-pixel tiles", upscale <= 1.35,
        upscale.toFixed(2) + "x upscale at level " + z + ", " + t.rtCount(z) + " tiles");
}


console.log(fails ? `\n${fails} FAILED` : "\nall tile checks passed");
process.exit(fails ? 1 : 0);
