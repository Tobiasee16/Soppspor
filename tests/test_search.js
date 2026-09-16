/* Searching for a place or an address.
 *
 * The fixtures below are real answers from the two registers, captured on
 * 2026-09-15, trimmed to the fields the app reads. They are here rather than
 * fetched because a test that needs the network is a test that fails on a
 * train, but they are real rather than invented because the thing most likely
 * to break this feature is the shape of somebody else's JSON.
 *
 * The dangerous part is the coordinate pair. Both registers answer in
 * EPSG:25833 and neither names the pair the same way: stedsnavn gives
 * {nord, øst} and adresser gives {lat, lon} holding a northing and an easting
 * despite the names. Read those two in the same order and every address in
 * Norway lands in the Norwegian Sea, silently, because nothing downstream
 * knows what a plausible coordinate looks like.
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

/* findRank leans on findNorm, findMatch and findTypeRank now, and on the two
   constants they read. Ranking by relevance before distance is the whole point
   of them, so they come along rather than being stubbed out. */
const DECL = (n) => {
  const i = shell.indexOf("var " + n + "=");
  return shell.slice(i, shell.indexOf(";", i) + 1);
};
const SRC = DECL("FIND_MAJOR") + DECL("FIND_MINOR") +
            ["findInNorway", "findPoint", "findNorm", "findMatch",
             "findTypeRank", "findRank", "findDedupe",
             "findParsePlaces", "findParseAddresses"].map(grab).join("\n");
const F = new Function(SRC +
  "return {findInNorway,findPoint,findNorm,findMatch,findTypeRank,findRank," +
  "findDedupe,findParsePlaces,findParseAddresses};")();

// Kartverket stedsnavn, "Nordmarka", fuzzy, utkoordsys=25833.
const PLACES = {
  navn: [
    { "skrivemåte": "Nordmarka", navneobjekttype: "Skogområde",
      kommuner: [{ kommunenavn: "Oslo" }, { kommunenavn: "Nittedal" }],
      representasjonspunkt: { koordsys: 25833, nord: 6674661.05516, "øst": 254126.01265 } },
    { "skrivemåte": "Nordmarka", navneobjekttype: "Hei",
      kommuner: [{ kommunenavn: "Sigdal" }],
      representasjonspunkt: { koordsys: 25833, nord: 6650000, "øst": 200000 } },
  ],
};
// Geonorge adresser, "Karl Johans gate 1", utkoordsys=25833.
const ADDR = {
  adresser: [
    { adressetekst: "Karl Johans gate 1", poststed: "SARPSBORG", kommunenavn: "SARPSBORG",
      representasjonspunkt: { epsg: "EPSG:25833", lat: 6578259.2163644, lon: 278501.9184523 } },
    { adressetekst: "Karl Johans gate 1", poststed: "OSLO", kommunenavn: "OSLO",
      representasjonspunkt: { epsg: "EPSG:25833", lat: 6649173.0856686, lon: 262386.293834 } },
  ],
};
const OSLO = [262000, 6650000];   // roughly the middle of the city, EPSG:25833

// ------------------------------------------------------- the coordinate pair --
{
  const p = F.findPoint({ nord: 6674661, "øst": 254126 });
  check("a place point comes back as east, north",
        p && p[0] === 254126 && p[1] === 6674661, JSON.stringify(p));

  const a = F.findPoint({ lat: 6649173.0856686, lon: 262386.293834 });
  check("an address point is read as northing and easting, not lat and lon",
        a && Math.round(a[0]) === 262386 && Math.round(a[1]) === 6649173,
        JSON.stringify(a));

  /* The register would have to change its mind about field order for this to
     happen, which is exactly the kind of thing that happens. */
  const sw = F.findPoint({ nord: 254126, "øst": 6674661 });
  check("a pair the wrong way round is put back the right way",
        sw && sw[0] === 254126 && sw[1] === 6674661, JSON.stringify(sw));

  check("a point that is nowhere near Norway is dropped",
        F.findPoint({ nord: 48.85, "øst": 2.35 }) === null &&
        F.findPoint({ lat: 0, lon: 0 }) === null);

  check("a missing point is dropped rather than thrown on",
        F.findPoint(null) === null && F.findPoint({}) === null);
}

// -------------------------------------------------------------- the parsing --
{
  const places = F.findParsePlaces(PLACES);
  check("both places parse", places.length === 2, String(places.length));
  check("the first is Nordmarka in Oslo",
        places[0].name === "Nordmarka" && places[0].where === "Oslo" &&
        places[0].what === "Skogområde",
        [places[0].name, places[0].what, places[0].where].join(" | "));
  check("its point is the one the register gave",
        Math.round(places[0].e) === 254126 && Math.round(places[0].n) === 6674661,
        places[0].e + ", " + places[0].n);

  const addr = F.findParseAddresses(ADDR);
  check("both addresses parse", addr.length === 2, String(addr.length));
  check("an address carries its post town",
        addr[1].name === "Karl Johans gate 1" && addr[1].where === "OSLO",
        [addr[1].name, addr[1].where].join(" | "));

  check("an empty answer is an empty list, not a crash",
        F.findParsePlaces({}).length === 0 && F.findParseAddresses({}).length === 0 &&
        F.findParsePlaces(null).length === 0);
}

// --------------------------------------------------------------- the order --
{
  const all = F.findParsePlaces(PLACES).concat(F.findParseAddresses(ADDR));

  /* A digit in the query is somebody typing a street number. */
  const withNumber = F.findRank("Karl Johans gate 1", all, OSLO[0], OSLO[1]);
  check("a query with a number puts addresses first",
        withNumber[0].kind === "adresse" && withNumber[1].kind === "adresse",
        withNumber.map(r => r.kind).join(","));

  const withoutNumber = F.findRank("Nordmarka", all, OSLO[0], OSLO[1]);
  check("a query without one puts places first",
        withoutNumber[0].kind === "sted" && withoutNumber[1].kind === "sted",
        withoutNumber.map(r => r.kind).join(","));

  /* The register orders by its own relevance; the reader wants what is near.
     Sarpsborg comes back before Oslo and must not stay there. */
  const addrs = withNumber.filter(r => r.kind === "adresse");
  check("the nearest of two identical street names wins",
        addrs[0].where === "OSLO", addrs.map(r => r.where).join(" then "));

  const fromSouth = F.findRank("Karl Johans gate 1", all, 278000, 6578000);
  check("and it is the middle of the screen that decides, not a fixed city",
        fromSouth.filter(r => r.kind === "adresse")[0].where === "SARPSBORG",
        fromSouth.filter(r => r.kind === "adresse").map(r => r.where).join(" then "));

  check("ranking does not disturb the list it was given",
        all[0].kind === "sted" && all[3].kind === "adresse");
}

// ------------------------------------------------- staying inside the build --
{
  /* The Kristiansand build cannot leave the sheets it carries, so a result in
     Tromsø has to be refused out loud rather than clamped to the edge of the
     map and left looking like a bug. */
  const KRS = [60000, 6440000, 120000, 6490000];
  const fn = new Function("BOUNDS", "view", "clampView", "findShut", "findSay",
                          "draw", "selected", "r",
                          grab("findGo") + "findGo(r);");
  const view = { cx: 0, cy: 0, mpp: 100 };

  let said = null, moved = false, shut = false;
  fn(KRS, view, () => { moved = true; }, () => { shut = true; },
     (t) => { said = t; }, () => { }, -1,
     { kind: "sted", e: 650000, n: 7700000 });
  check("a result outside this build's map is refused, not clamped",
        !moved && /utenfor kartet/.test(said || ""), said || "nothing said");
  check("and the sheet stays up, so the reason can be read",
        !shut);

  let moved2 = false, shut2 = false;
  fn(KRS, view, () => { moved2 = true; }, () => { shut2 = true; },
     () => { }, () => { }, -1,
     { kind: "adresse", e: 87241, n: 6466868 });
  check("a result inside it moves the map there",
        moved2 && view.cx === 87241 && view.cy === 6466868 && view.mpp === 3,
        [view.cx, view.cy, view.mpp].join(", "));
  check("and the sheet gets out of the way", shut2);

  /* A named wood is a region; dropping the reader among one of its trees helps
     nobody, so a place lands further out than a street number does. */
  fn(KRS, view, () => { }, () => { }, () => { }, () => { }, -1,
     { kind: "sted", e: 87241, n: 6466868 });
  check("a place lands further out than an address", view.mpp === 9,
        String(view.mpp));
}

// ---------------------------------------- the search never touches a cache --
{
  /* A cached map tile is harmless. A cached search response is a record of what
     somebody typed, sitting on their phone. The worker must leave these alone,
     and the way it does that is by not listing their hosts as tile hosts. */
  const sw = fs.readFileSync(path.join(__dirname, "..", "web", "sw.js"), "utf8");
  const m = sw.match(/TILE_HOSTS\s*=\s*\[([^\]]*)\]/);
  check("the worker declares its tile hosts", !!m, m ? m[1] : "not found");
  for (const host of ["api.kartverket.no", "ws.geonorge.no"]) {
    check(`${host} is not cached by the worker`,
          !!m && m[1].indexOf(host) < 0, m ? m[1] : "");
    check(`${host} is what the app actually calls`,
          shell.indexOf("https://" + host) > 0);
  }
  check("nothing about a search is written to storage",
        !/localStorage[.]setItem[(]"[^"]*(?:sok|search|find)/i.test(shell));
}

// ---------------------------------------- the sheet and the on-screen keyboard --
/* Reported: typing brought suggestions up and they vanished off the top of the
   screen. Two bugs stacked, and only one of them had been fixed.
 *
 * iOS lays the keyboard over the window instead of shrinking it, so a sheet
 * anchored to the bottom ends up behind the keys. findLift already raised it by
 * the covered height. What it did not do was shrink it: `.card` caps at 74% of
 * its parent, which is the whole window, and that stops meaning anything once a
 * third of the window is keyboard. The sheet grows upward from its new bottom
 * edge, so it overshot the top and took the results with it - worst exactly
 * when results appeared, because that is when the sheet was tallest. */
{
  const src = (() => {
    const i = shell.indexOf("function findLift(");
    let j = shell.indexOf("{", i), d = 0;
    for (; j < shell.length; j++) {
      if (shell[j] === "{") d++;
      else if (shell[j] === "}") { d--; if (!d) break; }
    }
    return shell.slice(i, j + 1);
  })();

  function lift({ open = true, innerHeight = 800, vvHeight = 800, offsetTop = 0,
                  hasVV = true } = {}) {
    const sheet = { style: {}, classList: { contains: () => open } };
    const win = hasVV
      ? { innerHeight, visualViewport: { height: vvHeight, offsetTop } }
      : { innerHeight };
    new Function("findSheet", "window", src + "findLift();")(sheet, win);
    return sheet.style;
  }

  let st = lift({ vvHeight: 800 });
  check("with no keyboard the sheet keeps its own bottom and cap",
        st.bottom === "" && st.maxHeight === "",
        `bottom ${JSON.stringify(st.bottom)}, maxHeight ${JSON.stringify(st.maxHeight)}`);

  st = lift({ innerHeight: 800, vvHeight: 450 });
  check("an overlaying keyboard lifts the sheet clear of it",
        st.bottom === "350px", st.bottom);
  check("and caps it to what is still visible, which is the half that was missing",
        st.maxHeight === "442px", st.maxHeight);
  check("the cap is never taller than the visible viewport",
        parseInt(st.maxHeight, 10) <= 450, st.maxHeight);

  /* Android resizes the window instead of overlaying, so there is nothing to
     lift and the CSS cap is already a percentage of the smaller window. */
  st = lift({ innerHeight: 450, vvHeight: 450 });
  check("a window that resizes for the keyboard needs no lift at all",
        st.bottom === "" && st.maxHeight === "", JSON.stringify(st));

  /* A tiny split-screen keyboard must not collapse the sheet to nothing. */
  st = lift({ innerHeight: 800, vvHeight: 120 });
  check("an absurdly small viewport still leaves a usable sheet",
        parseInt(st.maxHeight, 10) >= 160, st.maxHeight);

  st = lift({ open: false, innerHeight: 800, vvHeight: 450 });
  check("a closed sheet is left with neither a lift nor a cap",
        st.bottom === "" && st.maxHeight === "", JSON.stringify(st));

  st = lift({ hasVV: false });
  check("no visualViewport means no styling at all, not a crash",
        st.bottom === undefined && st.maxHeight === undefined);

  // Both ways out of the sheet have to undo both properties.
  check("closing and dragging shut each clear the cap as well as the lift",
        /findShut\(\)\{[\s\S]*?maxHeight=""/.test(shell) &&
        /makeDismissable\(findSheet[\s\S]{0,120}maxHeight=""/.test(shell));

  // And the wall of text that made the sheet tall in the first place.
  const sheetMarkup = shell.slice(shell.indexOf('id="findSheet"'),
                                  shell.indexOf('id="spSheet"'));
  const note = (sheetMarkup.match(/<p class="about"[\s\S]*?<\/p>/) || [""])[0];
  check("the note under the field is a line, not a paragraph",
        note.replace(/<[^>]+>/g, "").trim().split(/\s+/).length <= 16,
        note.replace(/<[^>]+>/g, "").trim());
  check("but it still says where the query goes", /Kartverket/.test(note));
}


// ------------------------------------------------- the sheet starts empty --
/* Reopening search used to show the last query still in the field, the last
   results still listed, and their distances still measured from wherever the
   map was when they were found. So the first thing anybody did was clear a box
   and ignore a list describing somewhere they had already gone. */
{
  const src = (() => {
    const i = shell.indexOf("function findReset(");
    let j = shell.indexOf("{", i), d = 0;
    for (; j < shell.length; j++) {
      if (shell[j] === "{") d++;
      else if (shell[j] === "}") { d--; if (!d) break; }
    }
    return shell.slice(i, j + 1);
  })();

  let aborted = false, said = null, seq = 0, cleared = 0;
  const findQ = { value: "Bergen" };
  const findList = { set innerHTML(v) { if (v === "") cleared++; } };
  new Function("findTimer", "findCtl", "findSeq", "findQ", "findList", "findSay",
               "clearTimeout",
               src + "findReset(); return findSeq;")(
    1, { abort() { aborted = true; } }, seq, findQ, findList,
    (t) => { said = t; }, () => {});

  check("it empties the field", findQ.value === "", JSON.stringify(findQ.value));
  check("it empties the result list", cleared === 1, String(cleared));
  check("it clears the message", said === "", JSON.stringify(said));
  check("it aborts a request still in flight", aborted);
  check("and bumps the sequence, so a late answer is ignored",
        /findSeq\+\+/.test(src));

  check("closing the sheet resets it",
        /function findShut\(\)\{[\s\S]*?findReset\(\);[\s\S]*?\}/.test(shell));
  check("and so does opening it, before the field is focused", (() => {
    const open = shell.slice(shell.indexOf('getElementById("findBtn")'));
    const reset = open.indexOf("findReset()");
    const focus = open.indexOf("findQ.focus()");
    return reset > -1 && focus > -1 && reset < focus;
  })());
}


// ------------------------------------------------------------- the ranking --
/* The complaint was the search itself, not the sheet it sits in, and it was
   right. Rows were sorted by kind and then purely by distance from the middle
   of the screen, which throws away the register's own relevance order - and
   that order is good. Kartverket answers «Bergen» with the city first.
   Re-sorted by distance from Kristiansand, an outfield called Bergen in
   Fyresdal beat the city by 300 km and the answer everybody meant fell off the
   list. Then the same name arrived once per feature it belongs to, so four
   rows reading «Bergen» crowded out everything else. */
{
  const grab = (n) => {
    const i = shell.indexOf("function " + n + "(");
    let j = shell.indexOf("{", i), d = 0;
    for (; j < shell.length; j++) {
      if (shell[j] === "{") d++;
      else if (shell[j] === "}") { d--; if (!d) break; }
    }
    return shell.slice(i, j + 1);
  };
  const decl = (n) => {
    const i = shell.indexOf("var " + n + "=");
    return shell.slice(i, shell.indexOf(";", i) + 1);
  };
  const M = new Function(
    decl("FIND_MAJOR") + decl("FIND_MINOR") +
    ["findNorm", "findMatch", "findTypeRank", "findRank", "findDedupe"]
      .map(grab).join("\n") +
    "return {findNorm,findMatch,findTypeRank,findRank,findDedupe};")();

  const KRS = [86000, 6464000];
  const rank = (q, rows, at = KRS) =>
    M.findDedupe(M.findRank(q, rows, at[0], at[1]));

  const bergen = [
    { kind: "sted", name: "Bergen", what: "Utmark", where: "Fyresdal",
      e: 120000, n: 6580000 },
    { kind: "sted", name: "Bergengen", what: "Bruk", where: "Gjøvik",
      e: 250000, n: 6740000 },
    { kind: "sted", name: "Bergen", what: "By", where: "Bergen",
      e: -30000, n: 6740000 },
    { kind: "sted", name: "Bergen", what: "Kommune", where: "Bergen",
      e: -30000, n: 6742000 },
  ];
  let out = rank("Bergen", bergen);
  check("the city beats a nearer outfield with the same name",
        out[0].what === "By" && out[0].where === "Bergen",
        out.map((r) => `${r.name}/${r.what}`).join(", "));
  check("and the same place is not listed twice under two feature types",
        out.filter((r) => r.name === "Bergen" && r.where === "Bergen").length === 1,
        String(out.length));
  check("a longer name never outranks the exact one",
        out.findIndex((r) => r.name === "Bergen") <
        out.findIndex((r) => r.name === "Bergengen"));

  /* Distance is still the right answer when the name does not decide. Norway
     has nine thousand Storvatnet and the one you want is the near one. */
  const storvatnet = [
    { kind: "sted", name: "Storvatnet", what: "Innsjø", where: "Narvik",
      e: 590000, n: 7590000 },
    { kind: "sted", name: "Storvatnet", what: "Vann", where: "Birkenes",
      e: 100000, n: 6480000 },
  ];
  check("among equal names the nearest still wins",
        rank("Storvatnet", storvatnet)[0].where === "Birkenes",
        rank("Storvatnet", storvatnet)[0].where);

  // A half-typed name should still find the thing it is the start of.
  const prefix = [
    { kind: "sted", name: "Bergveien", what: "Veg", where: "Oslo",
      e: 260000, n: 6650000 },
    { kind: "sted", name: "Storebergve", what: "Vad", where: "Valle",
      e: 90000, n: 6570000 },
  ];
  check("a prefix beats a name that merely contains the letters",
        rank("Bergve", prefix)[0].name === "Bergveien",
        rank("Bergve", prefix)[0].name);

  // Street numbers still mean addresses, but only once the name ties.
  const mixed = [
    { kind: "sted", name: "Karl Johans gate", what: "Veg", where: "Oslo",
      e: 262000, n: 6649000 },
    { kind: "adresse", name: "Karl Johans gate 1", what: "Adresse",
      where: "OSLO", e: 262100, n: 6649100 },
  ];
  check("a typed street number puts the address first",
        rank("Karl Johans gate 1", mixed)[0].kind === "adresse",
        rank("Karl Johans gate 1", mixed)[0].name);

  check("æ, ø and å are matched however they were typed",
        M.findMatch("Bo", "Bø") === 0 && M.findMatch("Alesund", "Ålesund") === 0,
        `${M.findMatch("Bo", "Bø")} ${M.findMatch("Alesund", "Ålesund")}`);

  check("a town outranks a ford, and an unknown type sits between them",
        M.findTypeRank("By") < M.findTypeRank("Innsjø") &&
        M.findTypeRank("Innsjø") < M.findTypeRank("Vad"),
        [M.findTypeRank("By"), M.findTypeRank("Innsjø"),
         M.findTypeRank("Vad")].join(" < "));

  // The query itself.
  check("places are asked for as a prefix rather than fuzzily",
        /sok="\+encodeURIComponent\(q\+"\*"\)/.test(shell));
  check("and fuzzy is kept only for when the prefix finds nothing",
        /Ingen n\u00f8yaktige treff/.test(shell) &&
        (shell.match(/fuzzy=true/g) || []).length === 1);
}


console.log(fails ? `\n${fails} FAILED` : "\nall search checks passed");
process.exit(fails ? 1 : 0);
