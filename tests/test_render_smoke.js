/* Boots the real shell against the real payload and draws.
 *
 * The unit suites check pieces in isolation - the palette, the toggle rules,
 * the contour tracer - and all of them can pass while the assembled page
 * throws on load. This one runs the whole script, selects several species the
 * way a tap does, and repaints. It is deliberately not clever: what it is
 * looking for is an exception, and it asserts the few facts that would be
 * wrong if the drawing had quietly stopped happening.
 */
const fs = require("fs");
const path = require("path");
const stub = require("./domstub.js");

const REPO = path.join(__dirname, "..");
const payload = fs.readFileSync(
  path.join(REPO, "data/processed/web_preview/payload.js"), "utf8");
(0, eval)(payload.replace(/^window\./, "global.window."));
global.window.SOPP = global.window.SOPP || global.SOPP;
global.window.SOPP_BASE = {};
global.window.SOPP_COND = JSON.parse(
  fs.readFileSync(path.join(REPO, "data/processed/conditions.json"), "utf8"));
// The hosted build roams the whole country; the artifact build cannot leave the
// sheets it carries. Run in whichever mode the environment asks for, so both
// are exercised:  SOPP_ENV=hosted node tests/test_render_smoke.js
const HOSTED = process.env.SOPP_ENV === "hosted";
if (HOSTED) {
  global.window.SOPP_ENV = { hosted: true, roaming: true,
                             bounds: [-95223, 6434491, 1120013, 7959232] };
}
global.window.SOPP_TRAILS = JSON.parse(
  fs.readFileSync(path.join(REPO, "data/interim/trails.json"), "utf8"));

const shell = fs.readFileSync(path.join(REPO, "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");
const js = shell.slice(shell.indexOf("<script>") + 8, shell.lastIndexOf("</script>"));

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// Expose the internals the assertions need, without altering behaviour. The
// whole shell is one IIFE under "use strict", so the probe has to be spliced
// in just before the outermost "})();" - appended after it, every name in the
// app is out of scope and the boot fails with a bare ReferenceError.
const at = js.lastIndexOf("})();");
if (at < 0) throw new Error("could not find the end of the shell IIFE");
const probe = js.slice(0, at) + `
;globalThis.__app = {
  SEL: () => SEL, D, toggleSpecies, setSel, repaint, draw, show,
  spColour, levelsFor, levelsForOverlay, ringLabel, BANDS,
  legend: () => document.getElementById("legend").innerHTML,
  pill: () => document.getElementById("pillName").textContent,
  card: () => document.getElementById("cardBody").innerHTML,
  BOUNDS: () => BOUNDS,
  trailsRowHidden: () => !!document.getElementById("rowTrails").hidden,
  goTo: (e, n, mpp) => { view.cx = e; view.cy = n; view.mpp = mpp;
                         clampView(); draw(); },
  /* Whether the scored area is on screen - what decides the dimming veil and
     the "no zones here yet" line. */
  coverage: () => {
    var t = w2s(GRID[0], GRID[3]), b = w2s(GRID[2], GRID[1]);
    return (b[0] - t[0]) > CW * 0.12 && (b[1] - t[1]) > CH * 0.12 &&
           t[0] < CW && b[0] > 0 && t[1] < CH && b[1] > 0;
  },
};` + js.slice(at);

let booted = true, bootErr = null;
try { (0, eval)(probe); } catch (e) { booted = false; bootErr = e; }
check("the shell boots against the real payload", booted,
      booted ? "" : bootErr && bootErr.stack.split("\n").slice(0, 2).join(" | "));
if (!booted) { console.log("\n1 FAILED"); process.exit(1); }

const app = global.__app;
stub.flush(3);

// ------------------------------------------------------------ one species --
check("it opens with exactly one species selected", app.SEL().length === 1,
      app.SEL().map(s => s.no).join(","));
check("the payload's shipped species are all there", app.D.species.length === 7,
      app.D.species.length + " species");
check("blek piggsopp is not among them",
      !app.D.species.some(s => /piggsopp/i.test(s.no)));
check("the pill names the one species", app.pill() === app.SEL()[0].no, app.pill());
// The inline build carries Turrutebasen, so the switch has something to govern.
check("the marked-routes switch is offered", !app.trailsRowHidden());
/* The label is derived from the threshold, so this checks they agree rather
   than that some particular words are present. A ring drawn at one percentile
   and labelled with another is the defect it is here for. */
const legendText = () => app.legend().replace(/<[^>]+>/g, " ").replace(/ /g, " ");
check("every shade is labelled with the percentile it is drawn at",
      app.levelsFor("#0072B2").every(lv =>
        legendText().includes(app.ringLabel(lv.p).replace(/ /g, " "))),
      legendText());
/* "Svært høy" on the card starts at .90 and the middle ring is drawn at .85,
   so the legend must not borrow the card's words for it. */
check("the legend does not reuse the card's band words",
      !app.BANDS.some(b => new RegExp(b[1], "i").test(legendText())),
      legendText());

// zones actually got built for it
check("zone geometry exists for the selected species",
      !!app.SEL()[0]._zones && app.SEL()[0]._zones.paths.length === 3,
      String(app.SEL()[0]._zones && app.SEL()[0]._zones.paths.length));

// ------------------------------------------------------- several species --
const others = app.D.species.filter(s => s !== app.SEL()[0] && !s.is_null);
let threw = null;
try {
  app.toggleSpecies(others[0]);
  app.toggleSpecies(others[1]);
  stub.flush(3);
} catch (e) { threw = e; }
check("adding species does not throw", !threw,
      threw ? threw.stack.split("\n").slice(0, 2).join(" | ") : "");
check("three species are selected", app.SEL().length === 3,
      app.SEL().map(s => s.no).join(", "));
check("each has its own geometry, rebuilt for the overlay bands",
      app.SEL().every(s => s._zones && s._zones.overlay === true &&
                           s._zones.paths.length === 3),
      app.SEL().map(s => s._zones && s._zones.paths.length).join(","));
check("no two selected species share a colour",
      new Set(app.SEL().map(s => app.spColour(s, app.D.species.indexOf(s)))).size === 3,
      app.SEL().map(s => app.spColour(s, app.D.species.indexOf(s))).join(" "));
check("the pill stops naming one species", /3 arter/.test(app.pill()), app.pill());
check("the legend names each species instead of the shades",
      app.SEL().every(s => app.legend().includes(s.no)), legendText());
/* Two contours per species, and a wood that clears the outer one but not the
   inner draws a single ring - the ordinary case. Without this row the reader
   cannot tell which ring that is. */
check("the legend also says which contour is which",
      app.levelsForOverlay("#0072B2").every(lv =>
        legendText().includes(app.ringLabel(lv.p).replace(/ /g, " "))),
      legendText());

// a repaint and a draw with three species up must be uneventful
threw = null;
try { app.repaint(); app.draw(); stub.flush(2); } catch (e) { threw = e; }
check("repaint and draw succeed with three species", !threw,
      threw ? threw.stack.split("\n").slice(0, 2).join(" | ") : "");

// ------------------------------------------------------------- the card --
// A cell known to be inside the kommune: find one the primary species scored.
const D = app.D;
const idx = (() => {
  const by = app.SEL()[0].bytes;
  for (let i = 0; i < by.length; i++) if (by[i]) return i;
  return -1;
})();
check("the payload has at least one scored cell", idx >= 0, String(idx));
threw = null;
try { app.show(idx); } catch (e) { threw = e; }
check("tapping a cell with several species up does not throw", !threw,
      threw ? threw.stack.split("\n").slice(0, 2).join(" | ") : "");
const card = app.card();
check("the card reports every selected species",
      app.SEL().every(s => card.includes(s.no)),
      card.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").slice(0, 120));
check("the card's rows are ordered best first", (() => {
  const widths = [...card.matchAll(/width:([\d.]+)%/g)].map(m => +m[1]);
  return widths.every((w, i) => i === 0 || widths[i - 1] >= w);
})(), [...card.matchAll(/width:([\d.]+)%/g)].map(m => m[1]).join(" >= "));

// ------------------------------------------------------ back to one again --
threw = null;
try {
  app.setSel([app.SEL()[0]]);
  stub.flush(2);
} catch (e) { threw = e; }
check("narrowing back to one species does not throw", !threw,
      threw ? threw.stack.split("\n").slice(0, 2).join(" | ") : "");
check("and its three bands come back",
      app.SEL()[0]._zones.paths.length === 3 && app.SEL()[0]._zones.overlay === false,
      String(app.SEL()[0]._zones.paths.length));
// ------------------------------------------------------ roaming, or not --
{
  const b = app.BOUNDS();
  const wide = (b[2] - b[0]) / 1000, tall = (b[3] - b[1]) / 1000;
  if (HOSTED) {
    check("a hosted build may pan the whole country",
          wide > 1000 && tall > 1400, wide.toFixed(0) + " x " + tall.toFixed(0) + " km");
    const e = 651000, n = 7734000;            // Tromso: no model coverage at all
    check("Tromso is inside the pannable area",
          e > b[0] && e < b[2] && n > b[1] && n < b[3]);
    let threw = null;
    try { app.goTo(e, n, 40); } catch (err) { threw = err; }
    check("panning to ground the model never saw does not throw", !threw,
          threw ? String(threw.message) : "");
    check("and the estimate does not follow it there",
          app.coverage() === false, String(app.coverage()));
    // Back over Kristiansand the coverage veil returns.
    app.goTo(85000, 6461000, 30);
    check("over Kristiansand the scored area is on screen again",
          app.coverage() === true, String(app.coverage()));
  } else {
    check("an inline build stays inside the sheets it carries",
          wide < 200 && tall < 200, wide.toFixed(0) + " x " + tall.toFixed(0) + " km");
  }
}


console.log(fails ? `\n${fails} FAILED` : "\nall render smoke checks passed");
process.exit(fails ? 1 : 0);
