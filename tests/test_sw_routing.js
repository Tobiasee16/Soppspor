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

  /* Enough of a Response to be cloned and read twice, which is what shellFirst
     does to decide whether the shell has moved under the reader. */
  const res = (text, ok = true) => ({
    ok, text: async () => text, clone() { return res(text, ok); }, __text: text,
  });

  function build(store, network) {
    const posted = [];
    const cache = {
      store,
      async match(k) { return this.store[k]; },
      put(k, v) { this.store[k] = v; },
    };
    const self_ = { clients: { async matchAll() {
      return [{ postMessage: (m) => posted.push(m) }]; } } };
    const fn = new Function("caches", "SHELL", "fetch", "URL", "self",
      body + "; return shellFirst;")(
      { async open() { return cache; } }, "shell",
      async () => (network === null ? null : res(network)), URL, self_);
    return { fn, posted, cache };
  }

  const pages = () => ({ "./index.html": res("THE APP"),
                         "./personvern.html": res("THE POLICY") });

  (async () => {
    for (const [name, url, expect] of [
      ["the privacy policy is served as itself",
       "https://soppspor.pages.dev/personvern.html", "THE POLICY"],
      ["the app is served for the root",
       "https://soppspor.pages.dev/", "THE APP"],
      ["and for a deep path the host would fall back on",
       "https://soppspor.pages.dev/steder", "THE APP"],
      ["and for index.html itself",
       "https://soppspor.pages.dev/index.html", "THE APP"],
    ]) {
      const { fn } = build(pages(), "THE APP");
      const got = await fn({ url });
      check(name, got && got.__text === expect, got ? got.__text : String(got));
    }

    /* The banner exists because the reader is looking at the previous version
       while the current one arrives behind them. It must fire on exactly that
       and on nothing else. */
    {
      const { fn, posted } = build(pages(), "A NEWER APP");
      const served = await fn({ url: "https://soppspor.pages.dev/" });
      await new Promise((r) => setTimeout(r, 0));
      check("the page served is still the cached one", served.__text === "THE APP");
      check("but a changed shell is announced",
            posted.length === 1 && posted[0].type === "shell-updated",
            JSON.stringify(posted));
    }
    {
      const { fn, posted } = build(pages(), "THE APP");
      await fn({ url: "https://soppspor.pages.dev/" });
      await new Promise((r) => setTimeout(r, 0));
      check("an unchanged shell says nothing", posted.length === 0,
            JSON.stringify(posted));
    }
    {
      const { fn, posted } = build({}, "THE APP");
      await fn({ url: "https://soppspor.pages.dev/" });
      await new Promise((r) => setTimeout(r, 0));
      check("a first install has nothing to announce", posted.length === 0,
            JSON.stringify(posted));
    }
    {
      const { fn, posted } = build(pages(), "A NEWER POLICY");
      await fn({ url: "https://soppspor.pages.dev/personvern.html" });
      await new Promise((r) => setTimeout(r, 0));
      check("a changed policy page does not offer to reload the map",
            posted.length === 0, JSON.stringify(posted));
    }
    {
      const { fn, cache } = build(pages(), "A NEWER APP");
      await fn({ url: "https://soppspor.pages.dev/" });
      await new Promise((r) => setTimeout(r, 0));
      check("and the fresh copy is kept for next launch",
            cache.store["./index.html"].__text === "A NEWER APP",
            cache.store["./index.html"].__text);
    }

    /* Offline with nothing cached must not hang or throw. */
    const { fn: off } = build({}, null);
    const r = await off({ url: "https://soppspor.pages.dev/" });
    check("with nothing cached and no network it returns an error, not a hang",
          !!r && r.type === "error", r ? r.type : String(r));

    console.log(fails ? `\n${fails} FAILED` : "\nall service-worker routing checks passed");
    process.exit(fails ? 1 : 0);
  })();
}
