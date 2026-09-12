/* The scale bar is set in pixels from a round distance, so it can silently
   exceed the screen if no ladder entry is small enough. That is what happened
   at the deepest zoom: the smallest rung was 100 m, which at 0.15 m/px is 667
   pixels of bar on a 400 pixel phone. */
const fs = require("fs");

const shell = fs.readFileSync(__dirname + "/../web/shell.html", "utf8")
  .split("\r\n").join("\n");

const start = shell.indexOf("function scalebar()");
let i = shell.indexOf("{", start), depth = 0;
for (; i < shell.length; i++) {
  if (shell[i] === "{") depth++;
  else if (shell[i] === "}") { depth--; if (!depth) break; }
}
const src = shell.slice(start, i + 1);

let widthPx = 0, label = "";
const el = {
  sbar: { style: { set width(v) { widthPx = parseFloat(v); }, get width() { return widthPx + "px"; } } },
  sbtxt: { set textContent(v) { label = v; }, get textContent() { return label; } },
};
let CW = 0, view = {};
const run = new Function("document", "CW_", "view_",
  "var CW=CW_,view=view_;" + src + "return scalebar;")(
  { getElementById: (id) => el[id] }, 0, {});

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// Rebuild with the right closure each time: the function reads CW and view
// from scope, so bind them per call.
function bar(cw, mpp) {
  const fn = new Function("document", "CW", "view", src + "scalebar();")(
    { getElementById: (id) => el[id] }, cw, { mpp });
  return { px: widthPx, label };
}

console.log("screen  m/px      bar      label");
let worst = 0, worstAt = null;
// the app's full range: 0.15 m/px at the deepest live-tile zoom, ~67 zoomed out
for (const cw of [360, 400, 430]) {
  for (const mpp of [0.15, 0.3, 0.8, 2.5, 5, 12, 30, 67, 100]) {
    const r = bar(cw, mpp);
    const frac = r.px / cw;
    if (frac > worst) { worst = frac; worstAt = [cw, mpp, r]; }
    if (cw === 400) {
      console.log(`${cw}     ${String(mpp).padEnd(6)}   ${r.px.toFixed(0).padStart(4)}px   ${r.label}`);
    }
    if (!(r.px > 0 && isFinite(r.px))) {
      check(`bar is a real width at ${cw}px / ${mpp} m/px`, false, JSON.stringify(r));
    }
  }
}
console.log();
check("the bar never exceeds a third of the screen", worst <= 0.35,
  `worst ${(worst * 100).toFixed(0)}% of width at ${worstAt[0]}px / ${worstAt[1]} m/px`);
check("the deepest zoom gets a sensible short label",
  ["1 m", "2 m", "5 m", "10 m", "25 m"].includes(bar(400, 0.15).label),
  bar(400, 0.15).label);
check("zoomed out still reads in km", bar(400, 67).label.endsWith("km"),
  bar(400, 67).label);

console.log(fails ? `\n${fails} FAILED` : "\nall scale bar checks passed");
process.exit(fails ? 1 : 0);
