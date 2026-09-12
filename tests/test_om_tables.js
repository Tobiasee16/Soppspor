/* The Om page's accuracy table and its "one species did not make it" note.
 *
 * Both used to be literal HTML: three species and their AUCs, typed in by
 * hand. A hand-typed number goes stale the next time the model is refitted,
 * and a stale accuracy claim on a map is worse than no claim, because the
 * reader has no way to tell it drifted. Both are built from the payload now,
 * and these check that they say what the payload says - including the case
 * that matters most, a species measured and then deliberately kept off the
 * map, which must be named rather than silently absent.
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

function iife(marker) {
  const start = shell.indexOf(marker);
  if (start < 0) throw new Error("not found in the shell: " + marker);
  const open = shell.indexOf("{", start);
  let i = open, depth = 0;
  for (; i < shell.length; i++) {
    if (shell[i] === "{") depth++;
    else if (shell[i] === "}") { depth--; if (!depth) break; }
  }
  return shell.slice(open + 1, i);
}

function run(marker, id, D) {
  const el = { innerHTML: "", hidden: false };
  const document = { getElementById: x => (x === id ? el : null) };
  new Function("D", "document", iife(marker))(D, document);
  return el;
}

const PAYLOAD = {
  meta: { null_transfer_auc: 0.496,
          excluded_species: [{ key: "hydnum_repandum", no: "Blek piggsopp",
                               scientific: "Hydnum repandum", transfer_auc: 0.519,
                               transfer_ci: "0.321-0.684", held_out_n: 7 }] },
  species: [
    { key: "cantharellus_cibarius", no: "Kantarell", transfer_auc: 0.737, held_out_n: 45 },
    { key: "boletus_edulis", no: "Steinsopp", transfer_auc: 0.613, held_out_n: 35 },
    { key: "synthetic_null", no: "Kontroll", is_null: true },
  ],
};

// ------------------------------------------------------------ the table --
{
  const t = run("function aucTable()", "aucTable", PAYLOAD);
  const rows = t.innerHTML.split("<tr>").slice(1);
  check("one row per shipped species plus a header and the control",
        rows.length === 4, rows.length + " rows");
  check("the null is not listed as a mushroom",
        !/Kontroll<\/td>.*Kontroll/.test(t.innerHTML) &&
        (t.innerHTML.match(/Kontroll/g) || []).length === 1);
  check("best-scoring species comes first",
        t.innerHTML.indexOf("Kantarell") < t.innerHTML.indexOf("Steinsopp"));
  check("AUCs are shown with a Norwegian decimal comma",
        /0,74/.test(t.innerHTML) && /0,61/.test(t.innerHTML));
  check("each species carries the record count behind its number",
        /45/.test(t.innerHTML) && /35/.test(t.innerHTML));
  check("the control's row uses the measured null, not a hardcoded 0,50",
        /0,50/.test(t.innerHTML), "null_transfer_auc 0.496 -> 0,50");

  // A refit that moved the null must move this cell too.
  const moved = JSON.parse(JSON.stringify(PAYLOAD));
  moved.meta.null_transfer_auc = 0.61;
  check("a different null value actually reaches the table",
        /0,61/.test(run("function aucTable()", "aucTable", moved).innerHTML));
}

// -------------------------------------------------- the excluded species --
{
  const el = run("function droppedSpecies()", "dropped", PAYLOAD);
  check("the excluded species is named, not silently missing",
        /Blek piggsopp/.test(el.innerHTML) && /Hydnum repandum/.test(el.innerHTML));
  check("its own number is shown", /0,519/.test(el.innerHTML), el.innerHTML.slice(0, 0));
  check("so is the null it failed to clear", /0,50/.test(el.innerHTML));
  check("so is how few records it rests on", /7 funn/.test(el.innerHTML));
  check("the interval is readable rather than a range string",
        /0,321 til 0,684/.test(el.innerHTML));
  check("the block is shown", el.hidden === false);

  // When every species passes, the section must disappear rather than sit empty.
  const clean = JSON.parse(JSON.stringify(PAYLOAD));
  clean.meta.excluded_species = [];
  check("nothing excluded means the block is hidden, not blank",
        run("function droppedSpecies()", "dropped", clean).hidden === true);

  // An older payload with no such key at all must not throw.
  const old = { meta: {}, species: [] };
  let threw = false;
  try { run("function droppedSpecies()", "dropped", old); } catch (e) { threw = true; }
  check("a payload predating the rule does not crash the page", !threw);
}

console.log(fails ? `\n${fails} FAILED` : "\nall Om table checks passed");
process.exit(fails ? 1 : 0);
