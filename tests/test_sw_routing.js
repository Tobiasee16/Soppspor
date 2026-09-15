/* What the service worker does with each request, run rather than read.
 *
 * The worker is the one piece of this app that nothing else can see. It sits
 * between a reader and the origin, only on an installed copy, and every check
 * we have - the deployment check included - asks the origin instead. So a
 * worker bug is invisible to everything except somebody holding the phone.
 *
 * One had been shipped. Every navigation went to shellFirst, and shellFirst
 * answered all of them with `./index.html`, so once the worker was installed,
 * tapping "Personvern" in settings opened the map instead of the privacy
 * policy. The policy was precached, correctly, and never consulted.
 *
 * These run the real listener and the real shellFirst against stubs that can
 * tell the pages apart.
 */
const fs = require("fs");
const path = require("path");

const SW = fs.readFileSync(path.join(__dirname, "..", "web", "sw.js"), "utf8")
  .split("\r\n").join("\n");

let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

// ------------------------------------------------------------- which branch --
function route(url, mode = "no-cors") {
  let picked = "passthrough";
  const listener = SW.slice(SW.indexOf('self.addEventListener("fetch"'),
                            SW.indexOf("\n});", SW.indexOf('self.addEventListener("fetch"')) + 4);
  const self_ = { location: { origin: "https://soppspor.pages.dev" },
                  addEventListener(_t, f) { self_.__fn = f; } };
  const env = {
    self: self_,
    tileFirst: () => (picked = "tileFirst"),
    zoneFirst: () => (picked = "zoneFirst"),
    networkFirst: () => (picked = "networkFirst"),
    shellFirst: () => (picked = "shellFirst"),
    caches: { match: () => ({ then: () => (picked = "cache then network") }) },
    fetch: () => null, TILE_HOSTS: ["cache.kartverket.no", "wms.geonorge.no"],
    DATA: "data", URL,
  };
  const keys = Object.keys(env);
  new Function(...keys, listener)(...keys.map((k) => env[k]));
  self_.__fn({ request: { url, mode, method: "GET" }, respondWith() { } });
  return picked;
}

for (const [name, url, want, mode] of [
  ["the app",                "https://soppspor.pages.dev/", "shellFirst", "navigate"],
  ["a deep link",            "https://soppspor.pages.dev/steder", "shellFirst", "navigate"],
  ["the privacy policy",     "https://soppspor.pages.dev/personvern.html", "shellFirst", "navigate"],
  ["a zone tile",            "https://soppspor.pages.dev/zones/kantarell/0/45_5.png", "zoneFirst"],
  ["a stamped zone tile",    "https://soppspor.pages.dev/zones/kantarell/0/45_5.png?v=abc123", "zoneFirst"],
  ["a habitat mask tile",    "https://soppspor.pages.dev/zones/mask/0/45_5.png?v=abc123", "zoneFirst"],
  ["a context tile",         "https://soppspor.pages.dev/context/45_5.png?v=abc123", "zoneFirst"],
  ["the weather",            "https://soppspor.pages.dev/conditions.json", "networkFirst"],
  ["a Kartverket map tile",  "https://cache.kartverket.no/v1/wmts/1.0.0/topo/x.png", "tileFirst"],
  ["a place search",         "https://api.kartverket.no/stedsnavn/v1/navn?sok=x", "passthrough"],
  ["an address search",      "https://ws.geonorge.no/adresser/v1/sok?sok=x", "passthrough"],
]) {
  const got = route(url, mode);
  check(`${name} -> ${want}`, got === want, got === want ? "" : "got " + got);
}

/* A stamp in the query must not take a tile out of the tile branch: the path
   is what decides, and pathname does not include the query. */
check("stamping a tile url does not change how it is cached",
      route("https://soppspor.pages.dev/zones/kantarell/0/45_5.png") ===
      route("https://soppspor.pages.dev/zones/kantarell/0/45_5.png?v=deadbeef"));

// ------------------------------------------------- which page comes back --
{
  const body = SW.slice(SW.indexOf("const PAGES"),
                        SW.indexOf('self.addEventListener("fetch"'));
  const cache = {
    store: { "./index.html": "THE APP", "./personvern.html": "THE POLICY" },
    async match(k) { return this.store[k]; },
    put() { },
  };
  const shellFirst = new Function("caches", "SHELL", "fetch", "URL",
    body + "; return shellFirst;")(
    { async open() { return cache; } }, "shell", async () => null, URL);

  const want = [
    ["the privacy policy is served as itself",
     "https://soppspor.pages.dev/personvern.html", "THE POLICY"],
    ["the app is served for the root",
     "https://soppspor.pages.dev/", "THE APP"],
    ["and for a deep path the host would fall back on",
     "https://soppspor.pages.dev/steder", "THE APP"],
    ["and for index.html itself",
     "https://soppspor.pages.dev/index.html", "THE APP"],
  ];
  (async () => {
    for (const [name, url, expect] of want) {
      const got = await shellFirst({ url });
      check(name, got === expect, got === expect ? "" : "got " + got);
    }

    /* Offline with nothing cached must not hang or throw. */
    const empty = { async match() { return undefined; }, put() { } };
    const off = new Function("caches", "SHELL", "fetch", "URL",
      body + "; return shellFirst;")(
      { async open() { return empty; } }, "shell", async () => null, URL);
    const res = await off({ url: "https://soppspor.pages.dev/" });
    check("with nothing cached and no network it returns an error, not a hang",
          !!res && res.type === "error", res ? res.type : String(res));

    console.log(fails ? `\n${fails} FAILED` : "\nall service-worker routing checks passed");
    process.exit(fails ? 1 : 0);
  })();
}
