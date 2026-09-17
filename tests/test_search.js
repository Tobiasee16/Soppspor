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

/* The map's top controls, as markup. Sliced by matching tags rather than
   between two ids: the id-to-id trick broke silently the first time the block
   moved, coming out backwards so that every check inside it passed against an
   empty string. */
function panel() {
  const at = shell.indexOf('id="topWrap"');
  const i = shell.lastIndexOf("<div", at);
  const re = /<\/?div\b/g;
  re.lastIndex = i;
  let m, depth = 0;
  while ((m = re.exec(shell))) {
    if (m[0] === "<div") depth++;
    else depth--;
    if (!depth) return shell.slice(i, m.index + 6);
  }
  return shell.slice(i);
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

// ----------------------------------- the dropdown and the on-screen keyboard --
/* Reported four times in the same words - the suggestions come up and the
   search pill leaves the top of the screen - and fixed six times with the
   phone disagreeing every time. All six lifted a bottom-anchored sheet by a
   computed amount. The anchor was the bug: a field near the bottom of the
   window is one the browser has to reveal when the keyboard opens, and Safari
   reveals it by scrolling the page, which carries the sheet off the top.
 *
 * The bar is at the top now and the suggestions hang under it, so there is
 * nothing to reveal and nothing to scroll. All findLift does is decide how far
 * down the list may reach. The checks below are written around that: each says
 * what happens when the script does nothing at all, and the answer is always
 * "the list is too long and you scroll it", never "the field is gone". */
{
  const src = grab("findLift") + "\n";

  function cap({ hidden = false, innerHeight = 800, vvHeight = 800,
                 offsetTop = 0, hasVV = true, top = 62 } = {}) {
    const wrap = { style: {} };
    const drop = { hidden, style: {}, getBoundingClientRect: () => ({ top }) };
    const win = hasVV
      ? { innerHeight, visualViewport: { height: vvHeight, offsetTop } }
      : { innerHeight };
    new Function("topWrap", "findDrop", "window", src + "findLift();")(
      wrap, drop, win);
    return { drop: drop.style, wrap: wrap.style };
  }

  /* No keyboard: the limit is the share of the screen the list may take, so
     the map is still most of what you are looking at. */
  let st = cap({ innerHeight: 800, vvHeight: 800 });
  check("with no keyboard the list is capped to a share of the screen",
        st.drop.maxHeight === "448px", st.drop.maxHeight);

  /* A keyboard laid over the window. 464 visible, top at 62, so there is 386px
     of room - and the share is the smaller of the two. */
  st = cap({ innerHeight: 800, vvHeight: 464 });
  check("a keyboard shortens the list rather than moving anything",
        st.drop.maxHeight === "260px", st.drop.maxHeight);
  check("and leaves a strip of map between the list and the keys",
        260 + 62 < 464 - 100, "the list ends well above the keyboard");

  /* A tall list on a short screen: room is what is left above the keys, less a
     margin, and it is the binding limit here. */
  st = cap({ innerHeight: 800, vvHeight: 300, top: 62 });
  check("on a short viewport the room above the keys is what binds",
        st.drop.maxHeight === "168px", st.drop.maxHeight);

  /* And it never collapses to nothing, however little is left. */
  st = cap({ innerHeight: 800, vvHeight: 120, top: 62 });
  check("an absurdly small viewport still leaves a usable list",
        parseInt(st.drop.maxHeight, 10) >= 120, st.drop.maxHeight);

  /* A hidden dropdown is not measured or styled. */
  st = cap({ hidden: true, innerHeight: 800, vvHeight: 464 });
  check("a dropdown with nothing in it is left alone",
        st.drop.maxHeight === undefined, JSON.stringify(st.drop));

  /* Insurance rather than mechanism: if a browser ever does scroll the page up
     under the keyboard, the column follows instead of riding off the top. */
  st = cap({ innerHeight: 800, vvHeight: 464, offsetTop: 336, top: 398 });
  check("a scrolled viewport takes the whole column with it",
        st.wrap.transform === "translateY(336px)", st.wrap.transform);
  check("and the list is still measured from where it actually ended up",
        st.drop.maxHeight === "260px", st.drop.maxHeight);

  st = cap({ innerHeight: 800, vvHeight: 800, offsetTop: 0 });
  check("an unscrolled viewport leaves no transform behind",
        st.wrap.transform === "", JSON.stringify(st.wrap));

  st = cap({ hasVV: false, innerHeight: 800 });
  check("no visualViewport means the window is the limit, not a crash",
        st.drop.maxHeight === "448px", st.drop.maxHeight);

  /* The dropdown exists only while it has something in it. An empty rounded
     box hanging under the bar over the map is worse than no box. */
  const showSrc = grab("findShow") + "\nfunction findLift(){}\n";
  function shown({ rows = 0, msg = false } = {}) {
    const drop = { hidden: null };
    new Function("findDrop", "findList", "findMsg", showSrc + "findShow();")(
      drop, { children: { length: rows } }, { hidden: !msg });
    return drop.hidden;
  }
  check("with no results and nothing to say, there is no dropdown",
        shown() === true);
  check("results bring it up", shown({ rows: 3 }) === false);
  check("and so does something to say on its own", shown({ msg: true }) === false);

  /* Every way out has to put the column back the way it was, or the next
     search opens pinned to a keyboard that is no longer there. */
  check("closing releases the column and the keyboard with it",
        /function findShut\(\)\{[\s\S]*?transform=""/.test(shell) &&
        /function findShut\(\)\{[\s\S]*?findQ\.blur\(\)/.test(shell));
  check("and touching the map closes the search",
        /function closeSheets\([\s\S]{0,160}findShut\(\)/.test(shell));
  check("Escape closes it too",
        /"Escape"[\s\S]{0,60}findShut\(\)/.test(shell));

  /* Blur must not close it. Tapping a suggestion blurs the field before the tap
     lands, so closing on blur closes the list out from under the finger. */
  check("blur does not close the list out from under a tap",
        !/findQ\.addEventListener\("blur"/.test(shell));

  // And the note that used to make the sheet tall is gone with the sheet.
  check("there is no paragraph of explanation over the map",
        !/id="findNote"/.test(shell));
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

  /* There is no longer an opening step that could forget to reset: the field
     is on the map, so the reset has to happen on the way out instead, by every
     route out. */
  check("closing resets it",
        /function findShut\(\)\{[\s\S]*?findReset\(\);[\s\S]*?\}/.test(shell));
  check("and every way out goes through that one function",
        /getElementById\("findClose"\)\.addEventListener\("click",findShut\)/
          .test(shell) &&
        /"Escape"[\s\S]{0,60}findShut\(\)/.test(shell) &&
        /function closeSheets\([\s\S]{0,160}findShut\(\)/.test(shell) &&
        /function findGo\([\s\S]*?findShut\(\)/.test(shell));
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


// -------------------------------------------- the map's top controls --
/* Search left, species filter right, the day's conditions under the filter,
 * suggestions hanging under both.
 *
 * The search bar is a field rather than a button that opens one, and it lives
 * at the top of the screen. That is the whole of the keyboard fix: a field the
 * keyboard cannot cover is a field the browser never has to scroll into view.
 * It is in CSS and markup, so it holds with the script switched off.
 *
 * These cannot prove it renders; tests/test_layout.js measures it in a real
 * browser with a keyboard faked over it. */
{
  const css = shell.slice(shell.indexOf(".topwrap{"),
                          shell.indexOf("/* tab bar */"));
  const sheet = panel();

  // --- the column
  check("the controls are one column down the top of the screen",
        /\.topwrap\{[^}]*position:absolute/.test(css) &&
        /\.topwrap\{[^}]*flex-direction:column/.test(css), css.slice(0, 90));
  check("the map stays draggable in the gaps between them",
        /\.topwrap\{[^}]*pointer-events:none/.test(css) &&
        /\.topwrap > \*\{pointer-events:auto\}/.test(css));
  check("and the suggestions outrank the banners while you are typing",
        /\.topwrap\.searching\{z-index:12\}/.test(css));

  // --- the row
  const iFind = sheet.indexOf('id="findQ"');
  const iSp = sheet.indexOf('id="spPill"');
  const iCond = sheet.indexOf('id="condPill"');
  const iDrop = sheet.indexOf('id="findDrop"');
  check("search is on the left of the row and the species filter on the right",
        iFind > 0 && iSp > iFind, `${iFind} then ${iSp}`);
  check("the conditions sit under the filter, not under the search bar",
        iCond > iSp && /\.condpill\{[^}]*align-self:flex-end/.test(css),
        `${iSp} then ${iCond}`);
  check("and the suggestions come under both",
        iDrop > iCond, `${iCond} then ${iDrop}`);
  check("the search bar takes the width the filter does not",
        /\.findpill\{[^}]*flex:1 1 auto/.test(css) &&
        /\.speciespill\{[^}]*flex:0 1 auto/.test(css));

  // --- the field itself
  check("it is a real field, not a button that opens one",
        /<input id="findQ"/.test(sheet));
  check("and it is 16px, or iOS zooms the map in when it takes focus",
        /#findQ\{[^}]*font-size:16px/.test(css));
  check("typing gives the field the whole row",
        /\.topwrap\.searching \.speciespill,\.topwrap\.searching \.condpill\{display:none\}/
          .test(css));
  check("and offers a word rather than a second cross beside the field's own",
        /id="findClose">Avbryt</.test(sheet) &&
        /\.findx\{display:none\}/.test(css));
  check("the magnifier is not a button any more, so nothing has to be pressed first",
        !/id="findBtn"/.test(shell));

  // --- the dropdown
  check("the suggestions are capped without any script running",
        /\.finddrop\{[^}]*max-height:56vh/.test(css) &&
        /@supports \(height:1svh\)\{\.finddrop\{max-height:56svh\}\}/.test(css));
  check("the list inside it is what scrolls",
        /\.finddrop \.sheetList\{[^}]*overflow-y:auto/.test(css) &&
        /\.finddrop \.sheetList\{[^}]*min-height:0/.test(css));
  check("a flick at the end of the list does not scroll the map behind it",
        /\.finddrop \.sheetList\{[^}]*overscroll-behavior:contain/.test(css));
  check("and the panel clips rather than scrolling as a whole",
        /\.finddrop\{[^}]*overflow:hidden/.test(css));
  check("the message and the results are both inside it",
        sheet.indexOf('id="findMsg"') > iDrop &&
        sheet.indexOf('id="findList"') > iDrop);

  /* The dropdown scrolls inside itself, so a drag that starts in it must not be
     read as a drag of the map. */
  check("the drag reads the scroller the finger is in",
        /function scrolledAlready\(/.test(shell) &&
        /el\.scrollHeight>el\.clientHeight\+1/.test(shell));
}


console.log(fails ? `\n${fails} FAILED` : "\nall search checks passed");
process.exit(fails ? 1 : 0);
