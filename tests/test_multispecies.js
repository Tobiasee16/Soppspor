/* Several species on one map at once.
 *
 * Two things here can fail silently and look fine. The first is the palette:
 * if two species end up perceptually identical for a colour-blind reader, the
 * map is unreadable for them and looks perfect to us, so the separation is
 * measured rather than eyeballed. The second is the selection rules - the last
 * species must not be removable and the cap must hold, because an empty map
 * and an eight-colour mud both read as "the app is broken".
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
const palSrc = ["spColour", "rgbOf", "mix", "rgba", "levelsFor", "levelsForOverlay"]
  .map(grab).join("\n");
const SPCOL = shell.slice(shell.indexOf("var SPCOL="), shell.indexOf("var SPFALLBACK="));
const FALL = shell.slice(shell.indexOf("var SPFALLBACK="),
                         shell.indexOf("\n", shell.indexOf("var SPFALLBACK=")) + 1);
const pal = new Function(SPCOL + FALL + palSrc +
  "return {spColour,rgbOf,mix,rgba,levelsFor,levelsForOverlay,SPCOL};")();

// ------------------------------------------------------ colour separation --
// CIELAB distance under normal vision and the three dichromacies. Same method
// the palette was chosen with, kept here so a future edit to SPCOL cannot
// quietly reintroduce a pair that collapses.
const lin = c => (c /= 255, c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
function lab([r, g, b]) {
  const X = 0.4124*r + 0.3576*g + 0.1805*b,
        Y = 0.2126*r + 0.7152*g + 0.0722*b,
        Z = 0.0193*r + 0.1192*g + 0.9505*b;
  const f = t => t > 216/24389 ? Math.cbrt(t) : (841/108) * t + 4/29;
  const fx = f(X/0.95047), fy = f(Y), fz = f(Z/1.08883);
  return [116*fy - 16, 500*(fx-fy), 200*(fy-fz)];
}
const toLMS = ([r,g,b]) => [17.8824*r+43.5161*g+4.11935*b,
                            3.45565*r+27.1554*g+3.86714*b,
                            0.0299566*r+0.184309*g+1.46709*b];
const fromLMS = ([L,M,S]) => [0.0809444479*L-0.130504409*M+0.116721066*S,
                              -0.0102485335*L+0.0540193266*M-0.113614708*S,
                              -0.000365296938*L-0.00412161469*M+0.693511405*S];
const SIMS = {
  normal: v => v,
  protan: ([L,M,S]) => fromLMS([2.02344*M - 2.52581*S, M, S]),
  deutan: ([L,M,S]) => fromLMS([L, 0.494207*L + 1.24827*S, S]),
  tritan: ([L,M,S]) => fromLMS([L, M, -0.395913*L + 0.801109*M]),
};
function seen(hex, kind) {
  const rgb = [1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16)));
  return lab(kind === "normal" ? rgb : SIMS[kind](toLMS(rgb)));
}
const dE = (a, b) => Math.hypot(a[0]-b[0], a[1]-b[1], a[2]-b[2]);

const colours = Object.values(pal.SPCOL);
check("every species has its own colour",
      new Set(colours).size === colours.length, colours.length + " colours");

const MIN = 12;
for (const kind of Object.keys(SIMS)) {
  let worst = Infinity, pair = "";
  for (let i = 0; i < colours.length; i++)
    for (let j = i + 1; j < colours.length; j++) {
      const d = dE(seen(colours[i], kind), seen(colours[j], kind));
      if (d < worst) { worst = d; pair = colours[i] + " vs " + colours[j]; }
    }
  check(`colours stay apart under ${kind} vision`, worst >= MIN,
        `worst dE ${worst.toFixed(1)} (${pair})`);
}
for (const hex of colours) {
  const L = lab([1, 3, 5].map(i => lin(parseInt(hex.slice(i, i + 2), 16))))[0];
  check(`${hex} reads on both a light and a dark page`, L >= 25 && L <= 80,
        "L* " + L.toFixed(1));
}

// ------------------------------------------------------------ the ramps --
{
  const one = pal.levelsFor("#0072B2");
  check("a single species still gets three bands", one.length === 3);
  check("its thresholds are the documented 70/85/95 per cent",
        one.map(l => l.p).join(",") === "0.7,0.85,0.95", one.map(l => l.p).join(","));
  const alpha = l => +l.fill.match(/([\d.]+)\)$/)[1];
  check("the ramp darkens upward rather than jumping about",
        alpha(one[0]) < alpha(one[1]) && alpha(one[1]) < alpha(one[2]),
        one.map(l => alpha(l)).join(" < "));
  const over = pal.levelsForOverlay("#0072B2");
  /* The thresholds must not move when a species is added. They used to: the
     overlay drew only the 85th and 95th, so toggling a second species pulled
     the outer boundary in from the 70th and the shading a reader was looking
     at collapsed to a thin ring, with nothing on screen to say why. Only the
     rendering may differ between the two modes. */
  check("several species are drawn at the same thresholds as one",
        over.map(l => l.p).join(",") === one.map(l => l.p).join(","),
        over.map(l => l.p).join(",") + " vs " + one.map(l => l.p).join(","));
  check("but as outlines rather than stacked fills",
        over.filter(l => l.fill === "rgba(0,0,0,0)").length >= one.length - 1,
        over.map(l => l.fill).join(" "));
  check("the overlay's lower band is an outline, not a fill",
        over[0].fill === "rgba(0,0,0,0)");
  check("an unknown species key still gets a colour",
        /^#[0-9A-F]{6}$/i.test(pal.spColour({ key: "nothing_known" }, 3)));
}

// ------------------------------------------------------ selection rules --
{
  const src = grab("toggleSpecies");
  function harness(sel, max) {
    const state = { SEL: sel.slice(), calls: 0 };
    const fn = new Function("state", "SEL_MAX", `
      let SEL = state.SEL;
      function setSel(l) { state.SEL = SEL = l; state.calls++; }
      ${src}
      return toggleSpecies;`)(state, max);
    return { fn, state };
  }
  const a = { key: "a" }, b = { key: "b" }, c = { key: "c" },
        d = { key: "d" }, e = { key: "e" };

  let h = harness([a], 4);
  check("removing the only species is refused", h.fn(a) === "last");
  check("and nothing changed when it was refused",
        h.state.SEL.length === 1 && h.state.calls === 0);

  h = harness([a], 4);
  check("adding a second species works", h.fn(b) === "on" && h.state.SEL.length === 2);

  h = harness([a, b, c], 4);
  check("removing one of several works",
        h.fn(b) === "off" && h.state.SEL.map(x => x.key).join("") === "ac",
        h.state.SEL.map(x => x.key).join(""));

  h = harness([a, b, c, d], 4);
  check("a fifth species is refused at the cap", h.fn(e) === "full");
  check("and the cap refusal changes nothing", h.state.SEL.length === 4);

  h = harness([a, b, c, d], 4);
  check("at the cap you can still remove one", h.fn(a) === "off",
        h.state.SEL.map(x => x.key).join(""));

  // Re-adding the species that is already first must not duplicate it.
  h = harness([a, b], 4);
  h.fn(a);
  check("toggling the first species off leaves the rest intact",
        h.state.SEL.map(x => x.key).join("") === "b");
}

console.log(fails ? `\n${fails} FAILED` : "\nall multi-species checks passed");
process.exit(fails ? 1 : 0);
