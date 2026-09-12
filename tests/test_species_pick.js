/* Which species the app opens on.
 *
 * This used to be stored as the index into D.species, which is correct exactly
 * as long as that list never changes. Adding five species to the middle of it
 * shifts every later entry, so a returning user who had chosen one mushroom
 * would reopen the app looking at a different one - silently, with no error
 * anywhere. The preference is a key now, and these check both that it survives
 * a reordering and that the old integer form still resolves for someone whose
 * phone has one saved.
 */
const fs = require("fs");
const path = require("path");

const shell = fs.readFileSync(path.join(__dirname, "..", "web", "shell.html"), "utf8")
  .split("\r\n").join("\n");

// The resolver is an IIFE, not a named function, so it is lifted by its opening.
const start = shell.indexOf("var SEL=[],current=null,selected=-1;");
const end = shell.indexOf("})();", start) + 5;
if (start < 0 || end < 5) throw new Error("species resolver not found in the shell");
const src = shell.slice(start, end);

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// `pref` is the old single-key/index form; `sel` the list the app stores now.
function resolve(speciesList, pref, sel) {
  const D = { species: speciesList };
  const PREF = { species: pref, sel: sel === undefined ? null : sel };
  return new Function("D", "PREF", src + "return current;")(D, PREF);
}

const NULL = { key: "synthetic_null", no: "Kontroll", is_null: true };
const four = [
  { key: "craterellus_tubaeformis", no: "Traktkantarell" },
  { key: "cantharellus_cibarius", no: "Kantarell" },
  { key: "amanita_muscaria", no: "Rød fluesopp" },
  NULL,
];
// What the list became once the product species were added. Blek piggsopp is
// deliberately absent: it was fitted and measured but did not clear the null
// control on transfer, so it is not in the shipped payload - which makes it the
// realistic example of a key someone may still have saved for a species that
// has since left the build.
const shipped = [
  four[0], four[1], four[2],
  { key: "boletus_edulis", no: "Steinsopp" },
  { key: "craterellus_cornucopioides", no: "Svart trompetsopp" },
  { key: "leccinum_versipelle", no: "Rødskrubb" },
  { key: "suillus_luteus", no: "Smørsopp" },
];

check("a saved key picks that species",
      resolve(shipped, "suillus_luteus").no === "Smørsopp",
      resolve(shipped, "suillus_luteus").no);

// The regression this was written for.
check("a saved key survives four species being added",
      resolve(shipped, "amanita_muscaria").no === resolve(four, "amanita_muscaria").no,
      resolve(shipped, "amanita_muscaria").no);

check("an unknown key falls back to the first species, not to nothing",
      resolve(shipped, "cantharellus_pallens").no === "Traktkantarell",
      String(resolve(shipped, "cantharellus_pallens") && resolve(shipped, "cantharellus_pallens").no));

check("no preference opens the first species",
      resolve(shipped, "").no === "Traktkantarell");

// Old phones have an integer in localStorage from before this change.
check("an old saved index still resolves",
      resolve(four, 1).no === "Kantarell", resolve(four, 1).no);

check("an old index pointing at the null does not open the null",
      resolve(four, 3).is_null !== true, resolve(four, 3).no);

check("an out-of-range old index does not crash",
      resolve(four, 99).no === "Traktkantarell", resolve(four, 99).no);

// The null must never be selectable by any route.
check("the null is never the opening species, whatever is saved",
      [0, 3, 99, "synthetic_null", ""].every(p => resolve(four, p).is_null !== true));
// --------------------------------------------------- how a row reads --------
// An AUC with no idea how many records stand behind it is exactly what this
// list must not show: 0.58 from 7 records and 0.74 from 45 print the same
// width and mean very different things.
{
  const start = shell.indexOf("function renderInto(el)");
  let i = shell.indexOf("{", start), depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  const src = shell.slice(start, i + 1);

  // `selected` is which species are ticked; the row shows a colour swatch and a
  // tick for those, so the renderer needs the selection and the palette now.
  function render(list, thinN, selected) {
    const rows = [];
    const el = { innerHTML: "", appendChild(r) { rows.push(r.innerHTML); } };
    const document = { createElement: () => ({
      addEventListener() {}, setAttribute() {}, className: "" }) };
    const D = { species: list };
    new Function("D", "document", "THIN_N", "SEL", "spColour", "el",
      src + "renderInto(el);")(
        D, document, thinN, selected || [list[0]],
        (sp, n) => "#0072B2", el);
    return rows;
  }
  const strip = h => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

  const list = [
    { key: "cantharellus_cibarius", no: "Kantarell", scientific: "Cantharellus cibarius",
      transfer_auc: 0.742, held_out_n: 45 },
    { key: "hydnum_repandum", no: "Blek piggsopp", scientific: "Hydnum repandum",
      transfer_auc: 0.58, held_out_n: 7 },
    { key: "boletus_edulis", no: "Steinsopp", scientific: "Boletus edulis",
      transfer_auc: 0.66 },
    NULL,
  ];
  const rows = render(list, 20);

  check("the null control is not offered in the picker", rows.length === 3,
        rows.length + " rows for 4 species");
  check("a well-supported species shows its AUC and its record count",
        /0\.74/.test(rows[0]) && /45 funn/.test(rows[0]), strip(rows[0]));
  check("a thinly-supported species is marked, not merely numbered",
        /få funn \(7\)/.test(rows[1]) && /class="thin"/.test(rows[1]), strip(rows[1]));
  check("a species with no count still shows its AUC",
        /0\.66/.test(rows[2]) && !/funn/.test(rows[2]), strip(rows[2]));
  check("every row closes the tags it opens",
        rows.every(h => (h.match(/<span/g) || []).length ===
                        (h.match(/<\/span>/g) || []).length));
  const one = n => render([{ key: "k", no: "N", scientific: "s",
                             transfer_auc: 0.6, held_out_n: n }], 20)[0];
  check("a count on the threshold is not marked thin", !/thin/.test(one(20)));
  check("one record below the threshold is marked thin", /thin/.test(one(19)));
}
// ------------------------------------------- several species remembered -----
// The preference became a list when the map learned to show more than one
// species at a time. Both older forms still live in people's browsers, so all
// three have to resolve, and none of them may land on the null control.
{
  const sel = (list, pref, selArr) => {
    const D = { species: list };
    const PREF = { species: pref, sel: selArr === undefined ? null : selArr };
    return new Function("D", "PREF", src + "return SEL;")(D, PREF);
  };
  const names = s => s.map(x => x.no).join(",");

  check("a saved list restores every species in it",
        names(sel(shipped, "", ["boletus_edulis", "suillus_luteus"]))
          === "Steinsopp,Smørsopp",
        names(sel(shipped, "", ["boletus_edulis", "suillus_luteus"])));

  check("the saved order is kept, because the first one is the primary",
        names(sel(shipped, "", ["suillus_luteus", "boletus_edulis"]))
          === "Smørsopp,Steinsopp");

  check("species that have left the build are dropped, not fatal",
        names(sel(shipped, "", ["hydnum_repandum", "boletus_edulis"])) === "Steinsopp",
        names(sel(shipped, "", ["hydnum_repandum", "boletus_edulis"])));

  check("a list of only departed species still leaves a usable map",
        sel(shipped, "", ["hydnum_repandum"]).length === 1);

  check("a duplicated key is not shown twice",
        sel(shipped, "", ["boletus_edulis", "boletus_edulis"]).length === 1);

  check("the previous single-key preference still resolves",
        names(sel(shipped, "amanita_muscaria", null)) === "Rød fluesopp");

  check("the preference before that, a position, still resolves",
        names(sel(four, 1, null)) === "Kantarell");

  check("no stored value ever selects the null control",
        [null, [], ["synthetic_null"], ["synthetic_null", "cantharellus_cibarius"]]
          .every(v => sel(four, "", v).every(x => x.is_null !== true)));

  check("the selection is never empty", 
        [null, [], ["nothing"], undefined].every(v => sel(shipped, "", v).length >= 1));
}



console.log(fails ? `\n${fails} FAILED` : "\nall species-pick checks passed");
process.exit(fails ? 1 : 0);
