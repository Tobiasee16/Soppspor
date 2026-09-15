/* The basemap picker, and the two ways it can lie.
 *
 * The map underneath the zones comes from Kartverket's open tile cache, chosen
 * by `PREF.base` and looked up in `RT_LAYER`. Both failures here are silent:
 *
 *   - a picker button whose value has no `RT_LAYER` entry falls back to
 *     `RT_LAYER.plain`, so the reader taps "Papir" and gets grey topo with
 *     nothing to say the choice did not take;
 *   - a default `PREF.base` that is not a real layer does the same thing on
 *     first launch, which is the one launch nobody is watching.
 *
 * Neither throws, neither logs, and the map looks entirely plausible either
 * way - it is just not the map that was asked for. So the picker's buttons and
 * the layer table are checked against each other rather than separately.
 *
 * The layer NAMES themselves (topo, topograatone, toporaster) are Kartverket's
 * and are verified against its WMTS capabilities in data/SOURCES.md, not here;
 * a test that needs the network is not a test that runs on every commit.
 */
const fs = require("fs");
const path = require("path");
const REPO = path.join(__dirname, "..");

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

/* Pull the two declarations out of the shell and evaluate them, rather than
   matching strings, so a reformat does not break the test and a changed value
   does. */
function evalDecl(name) {
  const m = shell.match(new RegExp("var\\s+" + name + "\\s*=\\s*(\\{[^;]*?\\});"));
  if (!m) throw new Error("no declaration of " + name + " in the shell");
  return new Function("return (" + m[1] + ");")();
}

const RT_LAYER = evalDecl("RT_LAYER");
const PREF = evalDecl("PREF");

/* The picker's own buttons, read out of the markup. */
const seg = shell.slice(shell.indexOf('id="segBase"'));
const buttons = [];
const re = /data-v="([a-z]+)"/g;
let m;
while ((m = re.exec(seg.slice(0, seg.indexOf("</div>")))) !== null) buttons.push(m[1]);

console.log("picker offers:", buttons.join(", "));
console.log("layers known:  ", Object.keys(RT_LAYER).join(", "));

check("the picker offers at least the three basemaps", buttons.length >= 3,
  buttons.length + " buttons");

buttons.forEach(function (v) {
  check("picker option '" + v + "' has a real layer behind it",
    Object.prototype.hasOwnProperty.call(RT_LAYER, v),
    RT_LAYER[v] || "no RT_LAYER entry");
});

check("the paper map is Kartverket's toporaster", RT_LAYER.paper === "toporaster",
  RT_LAYER.paper);

/* The owner asked for the topographic sheet on launch: a beginner in a wood is
   reading contours and paths, not judging the palette. */
check("the app opens on the topographic sheet", PREF.base === "topo", PREF.base);
check("and that default is a layer that exists",
  Object.prototype.hasOwnProperty.call(RT_LAYER, PREF.base), PREF.base);

/* The picker must be able to reach the default, or the highlighted button and
   the map disagree from the first frame. */
check("the default is one the picker can show", buttons.indexOf(PREF.base) >= 0,
  PREF.base + " / " + buttons.join(","));

/* Papir is fetched at runtime, so a build carrying its own sheets has none.
   The button hides itself there - same rule as the trails row, and the same
   [hidden] trap as 25j. */
check("the paper button hides itself in a build with embedded sheets",
  /basePaper[\s\S]{0,600}?hidden\s*=\s*true/.test(shell));
check("and such a build does not strand the preference on 'paper'",
  /PREF\.base\s*===?\s*"paper"[\s\S]{0,120}?PREF\.base\s*=\s*"topo"/.test(shell));

console.log(fails ? "\n" + fails + " FAILED" : "\nall passed");
process.exit(fails ? 1 : 0);
