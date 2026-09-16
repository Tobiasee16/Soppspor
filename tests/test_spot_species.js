/* Which mushroom a saved spot is, and the colour that says so.
 *
 * A spot has carried a species since the schema was written - `species_key`
 * and `species_name` - but nothing ever asked the user for one. It was taken
 * from whatever the map happened to be showing, and the pin drew in a fixed
 * amber, so a forager could not say what they found and could not tell two
 * pins apart afterwards.
 *
 * Two things here fail silently and look fine. The pin colour can drift away
 * from the picker's, because both are a lookup and a second lookup is free to
 * disagree with the first - and the reader has no way to know that the amber
 * dot on the map and the amber dot in the filter now mean different mushrooms.
 * And a species that leaves the build must not hand its colour to whichever
 * mushroom now sits at that position, which is what matching by index rather
 * than by key would do - blek piggsopp has already left once.
 *
 * So the colour is asserted equal to the picker's for every shipped species,
 * derived through the picker's own function rather than re-read from the
 * table, and the departed-species case is checked by name.
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
// SPCOL spans several lines, so it is sliced to the declaration that follows
// it rather than to the end of its first line.
const SPCOL_SRC = shell.slice(shell.indexOf("var SPCOL="),
                              shell.indexOf("var SPFALLBACK="));
function decl(name) {
  const start = shell.indexOf("var " + name + "=");
  if (start < 0) throw new Error("no var " + name);
  return shell.slice(start, shell.indexOf("\n", start) + 1);
}

// The shipped list, in the order the payload carries it. Blek piggsopp is not
// here: it was fitted and measured and did not clear the null control, so it
// is reported in Om and drawn nowhere - which makes it the honest example of a
// key a user may still have saved for a species that has left the build.
const SHIPPED = [
  { key: "craterellus_tubaeformis", no: "Traktkantarell" },
  { key: "cantharellus_cibarius", no: "Kantarell" },
  { key: "amanita_muscaria", no: "Rød fluesopp" },
  { key: "boletus_edulis", no: "Steinsopp" },
  { key: "craterellus_cornucopioides", no: "Svart trompetsopp" },
  { key: "leccinum_versipelle", no: "Rødskrubb" },
  { key: "suillus_luteus", no: "Smørsopp" },
];
const NULL_SP = { key: "synthetic_null", no: "Kontroll", is_null: true };

function sandbox(species) {
  const src = SPCOL_SRC + decl("SPFALLBACK") + decl("SPOT_NEUTRAL")
    + grab("spColour") + grab("spotColour") + grab("spotSpeciesOptions");
  return new Function("D",
    src + "return {spColour,spotColour,spotSpeciesOptions,SPOT_NEUTRAL};")(
    { species: species });
}
const S = sandbox(SHIPPED.concat([NULL_SP]));

// ------------------------------------------------------------- the colour --
// The point of the change the user asked for: pin and filter row agree.
SHIPPED.forEach((sp, i) => {
  const picker = S.spColour(sp, i);
  check("pin colour matches the picker for " + sp.no,
        S.spotColour(sp.key) === picker,
        S.spotColour(sp.key) + " vs " + picker);
});

check("every shipped species gets a colour of its own",
      new Set(SHIPPED.map(sp => S.spotColour(sp.key))).size === SHIPPED.length);

check("a spot with no species chosen draws neutral, not a borrowed colour",
      S.spotColour("") === S.SPOT_NEUTRAL, S.spotColour(""));

// A species that has left the build. Matching by position would hand its key
// whatever colour now sits at that index, which is a pin that silently changes
// meaning between two builds.
check("a departed species falls back to neutral",
      S.spotColour("hydnum_repandum") === S.SPOT_NEUTRAL,
      S.spotColour("hydnum_repandum"));
check("a departed species does not borrow another mushroom's colour",
      SHIPPED.every(sp => S.spotColour("hydnum_repandum") !== S.spotColour(sp.key)));

// Reordering the payload must not repaint anybody's saved pins.
const reordered = sandbox(SHIPPED.slice().reverse().concat([NULL_SP]));
check("a pin keeps its colour when the species list is reordered",
      SHIPPED.every(sp => reordered.spotColour(sp.key) === S.spotColour(sp.key)));

// ------------------------------------------------------------ the chooser --
const chips = S.spotSpeciesOptions({ key: "", name: "" });
check("the chooser offers every shipped species",
      SHIPPED.every(sp => chips.some(c => c.key === sp.key)),
      chips.map(c => c.key).join(","));
// The synthetic null is a validation device. Nobody found one.
check("the chooser never offers the synthetic null",
      !chips.some(c => c.key === "synthetic_null"));
check("the chooser offers a way out for a mushroom not on the list",
      chips.some(c => c.key === ""));
check("each chip carries the colour its pin will draw",
      chips.every(c => c.col === S.spotColour(c.key)));

// A spot saved under a species that has since left the build keeps a chip of
// its own, so opening the card does not quietly drop what it was saved as.
const departed = S.spotSpeciesOptions({ key: "hydnum_repandum", name: "Blek piggsopp" });
check("a stored species missing from the build keeps its own chip",
      departed.some(c => c.key === "hydnum_repandum" && c.label === "Blek piggsopp"),
      departed.map(c => c.label).join(","));
check("that chip is not duplicated for a species still shipped",
      S.spotSpeciesOptions({ key: "boletus_edulis", name: "Steinsopp" })
        .filter(c => c.key === "boletus_edulis").length === 1);

// --------------------------------------------------------------- the save --
// Saving with one species up guesses it, because that is what the reader went
// out for. Saving with several up guesses nothing: SEL[0] is only whichever
// was toggled on first, and a wrong species is worse than none.
function saved(sel) {
  const pushed = [];
  return new Function("SEL", "uuid", "SPOTS", "saveSpots", "renderSpots", "draw",
                      "syncNow", grab("addSpot") + "return addSpot([1000,2000]);")(
    sel, () => "00000000-0000-4000-8000-000000000000", pushed,
    () => {}, () => {}, () => {}, () => {});
}
const one = saved([SHIPPED[1]]);
check("saving with one species up records it",
      one.key === "cantharellus_cibarius" && one.name === "Kantarell",
      one.key + "/" + one.name);
const many = saved([SHIPPED[1], SHIPPED[3], SHIPPED[5]]);
check("saving with several species up records none rather than the first",
      many.key === "" && many.name === "", many.key + "/" + many.name);
check("a spot is still saved dirty so the species reaches the server",
      one.dirty === true && many.dirty === true);

// ------------------------------------------------------------- the change --
function setOn(spot, key) {
  const calls = { save: 0, sync: 0 };
  new Function("D", "s", "key", "saveSpots", "renderSpots", "draw", "syncNow",
               grab("setSpotSpecies") + "setSpotSpecies(s,key);")(
    { species: SHIPPED.concat([NULL_SP]) }, spot, key,
    () => calls.save++, () => {}, () => {}, () => calls.sync++);
  return calls;
}
let spot = { key: "cantharellus_cibarius", name: "Kantarell", dirty: false };
let calls = setOn(spot, "boletus_edulis");
check("choosing a species sets both the key and the name",
      spot.key === "boletus_edulis" && spot.name === "Steinsopp",
      spot.key + "/" + spot.name);
check("choosing a species marks the row dirty and pushes it",
      spot.dirty === true && calls.save === 1 && calls.sync === 1);

spot = { key: "boletus_edulis", name: "Steinsopp", dirty: false };
setOn(spot, "");
// A name left behind a cleared key is a label the pin's colour contradicts.
check("choosing Annet clears the name as well as the key",
      spot.key === "" && spot.name === "", spot.key + "/" + spot.name);

spot = { key: "", name: "", dirty: false };
setOn(spot, "synthetic_null");
check("the null cannot be stored on a spot even if asked for",
      spot.key === "" && spot.name === "");

// ------------------------------------------------------- against the shell --
// Source assertions, because the hardcoded amber is what this replaces and it
// would come back as a one-line edit that no behavioural test above would see.
const drawSrc = grab("drawSpots");
check("the map pin takes its fill from spotColour",
      /fillStyle\s*=\s*spotColour\(/.test(drawSrc), drawSrc.replace(/\s+/g, " "));
check("the map pin no longer hardcodes a colour",
      !/#[0-9a-fA-F]{6}/.test(drawSrc.replace(/strokeStyle\s*=\s*"#fff"/, "")));

const listSrc = grab("renderSpots");
check("the row in Steder takes the same colour as the pin",
      /class="pin" style="background:'\+spotColour\(/.test(listSrc));

const cardSrc = grab("showSpot");
check("the spot card asks which mushroom it was",
      cardSrc.includes("spotSpeciesOptions(") && /Hva fant du/.test(cardSrc));
check("the spot card still offers the note and the delete",
      cardSrc.includes("spotNote") && cardSrc.includes("spotDel"));

// ------------------------------------------------------------- the list --
/* It was a wrapping row of chips while there were eight species. Fourteen made
   it four ragged rows of pills taller than the sheet they sat on, with no order
   a reader could scan. These pin the shape that replaced it: one bounded
   scrolling column, and a list that opens on the answer already stored rather
   than at the top - a spot saved as the last species would otherwise appear to
   offer a list without it. */
const css = shell.slice(shell.indexOf(".spList{"), shell.indexOf(".spotRow{"));
check("the list is height-capped rather than growing with the species count",
      /max-height:\s*\d+px/.test(css), css.slice(0, 60));
check("and it scrolls inside that cap",
      /overflow-y:\s*auto/.test(css));
check("a scroll at its end does not drag the sheet behind it",
      /overscroll-behavior:\s*contain/.test(css));
check("the cap leaves a part-row visible, which is what says 'more below'", (() => {
  const cap = +css.match(/max-height:\s*(\d+)px/)[1];
  const row = +css.match(/min-height:\s*(\d+)px/)[1];
  return cap % row !== 0 && cap > row * 3;
})(), css.match(/max-height:\s*(\d+)px/)[1] + " / row "
      + css.match(/min-height:\s*(\d+)px/)[1]);
check("no chip styling survives to contradict the list",
      !shell.includes(".spChip") && !shell.includes('class="spSet"'));

const scrollSrc = grab("scrollToChosen");
check("the card opens on the chosen species",
      cardSrc.includes("scrollToChosen(list)"));
check("a row already in view is left alone rather than centred pointlessly",
      /offsetTop\s*\+\s*at\.offsetHeight\s*<=\s*list\.clientHeight/.test(scrollSrc));
/* It runs under a DOM stub where layout properties do not exist. Reading
   offsetTop off a stub returns undefined, and arithmetic on that yields NaN,
   which would silently set scrollTop to NaN rather than throwing - so the
   guard has to be a type check, not a truthiness check. */
check("it refuses to guess when the list cannot report its geometry",
      /typeof at\.offsetTop\s*!==\s*"number"/.test(scrollSrc), scrollSrc);
check("and never scrolls to a negative offset",
      /Math\.max\(0,/.test(scrollSrc));

console.log(fails ? "\n" + fails + " FAILED" : "\nall good");
process.exit(fails ? 1 : 0);
