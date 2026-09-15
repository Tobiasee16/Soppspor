/* Service worker: makes Soppspor work with no signal, which is the condition
   it is actually used in. A mushroom wood is the worst coverage you will meet
   all week, and a map that needs the network there is not a map.

   Three caches with different jobs:

   * **The shell** - index.html and its assets - is served
     stale-while-revalidate: the cached copy instantly, a fresh one fetched in
     the background for next launch. Network-first would mean waiting on a
     multi-megabyte download before seeing anything, which is the wrong trade
     for a page that changes rarely.
   * **Map tiles** from Kartverket's WMTS cache are kept as they are used,
     capped, oldest evicted first. Walk an area once with signal and it stays
     available. This is the reason the app uses a tile cache with stable URLs
     rather than a WMS with a bbox per view: a WMS URL is unique to one
     viewport, so nothing it returns is ever a cache hit for anything else, and
     an app built on it can never work offline over ground already walked.
   * **Conditions** - the daily rainfall file - is network-first with a cached
     fallback, because it is the one file whose whole purpose is to be fresh.
     Stale weather shown as current is worse than no weather at all, but
     yesterday's weather offline is better than a blank.

   Kartverket's tiles come back with `Access-Control-Allow-Origin: *` and a
   five-day `Cache-Control`, so the browser's own HTTP cache already does some
   of this work; the point of keeping them here as well is that the HTTP cache
   is evicted on its own schedule and is not available offline by contract.
*/
/* Bumped for the rename to Soppspor. The prefix is part of every cache
   name and `activate` deletes anything that does not start with it, so
   this costs every existing install one full re-download - shell, basemap
   tiles and zone tiles alike - and then cleans the old caches up. That is
   acceptable once, before launch; it is not something to do casually. */
const VERSION = "soppspor-v3";
const SHELL = `${VERSION}-shell`;
const TILES = `${VERSION}-tiles`;
const DATA = `${VERSION}-data`;
const ZONES = `${VERSION}-zones`;

/* Topographic tiles are 60-115 kB each, which is heavier than a typical slippy
   map tile, so this is an entry cap chosen against a size budget rather than a
   round number: 900 tiles is roughly 70 MB. At level 15 that is about 25 km2
   of ground held at walking detail, which is a day out. */
const TILE_LIMIT = 900;

const TILE_HOSTS = ["cache.kartverket.no", "wms.geonorge.no"];

/* Zone tiles are the estimate itself, so they are worth keeping harder than
   the basemap: 40 kB each, and a cap of 600 is about 24 MB, which covers a
   large region at the 128 m display grid. They are immutable - a rebuild
   writes a new directory rather than changing a tile in place - so cache-first
   with no revalidation is correct rather than merely convenient. */
const ZONE_LIMIT = 600;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL)
      /* The policy is precached with the shell. Somebody reading it is often
         doing so because they are about to delete their account or are unsure
         what is held about them, and "you can read what we do with your data,
         provided you have signal" is not much of a commitment. */
      .then((c) => c.addAll(["./", "./index.html", "./manifest.webmanifest",
                             "./personvern.html"]))
      .then(() => self.skipWaiting())
      .catch(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  // keys() returns insertion order, so the front is the least recently added.
  for (let i = 0; i < keys.length - limit; i++) await cache.delete(keys[i]);
}

async function tileFirst(request) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(request);
  if (hit) return hit;
  try {
    const res = await fetch(request);
    // Opaque responses (status 0) are cacheable and unreadable; that is fine
    // for something only ever handed back to an <img>.
    if (res && (res.ok || res.type === "opaque")) {
      await cache.put(request, res.clone());
      trim(TILES, TILE_LIMIT);
    }
    return res;
  } catch (err) {
    // No signal and never fetched. Returning an error lets the app's own
    // failure counter notice, rather than hanging on a request that cannot
    // succeed.
    return Response.error();
  }
}

/* Fresh if the network answers, cached if it does not. Used for the weather,
   which is the only file here that is supposed to change between launches. */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(request, { cache: "no-store" });
    if (res && res.ok) cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(request);
    return hit || Response.error();
  }
}

/* Cache-first and then kept. A tile already looked at must still be there
   with no signal, which is the whole promise of an offline-first map. */
async function zoneFirst(request) {
  const cache = await caches.open(ZONES);
  const hit = await cache.match(request);
  if (hit) return hit;
  const res = await fetch(request);
  if (res && res.ok) {
    await cache.put(request, res.clone());
    trim(ZONES, ZONE_LIMIT);
  }
  return res;
}

/* Pages that are precached as themselves. Everything else that navigates is
   the single-page app, including any deep path a magic link or a bookmark
   produced - those resolve to index.html the same way the host's own fallback
   does.

   This list is the fix for a bug worth remembering. Every navigation went to
   shellFirst and shellFirst answered every one of them with `./index.html`, so
   once the worker was installed, tapping "Personvern" in settings opened the
   map. The policy was precached, correctly, and never consulted. Nothing
   noticed because the deployment check asks the origin, which serves the
   policy perfectly well - the worker sits between the reader and the origin
   and only on an installed copy. */
const PAGES = ["/personvern.html"];

/* Stale-while-revalidate means the reader is looking at the previous version
   and the current one arrives in the background for next launch. That is the
   right trade - network-first would hold a multi-megabyte page hostage to a
   signal the woods do not have - but it leaves somebody reading a version that
   has been replaced with no way to know.

   So when the copy that comes back differs from the copy that was served, the
   windows are told. Comparing the text is exact, where an etag or a length is
   a guess, and it costs one string comparison per launch. Nothing is reloaded
   from here: the page decides what to do about it, because the reader may be
   halfway through writing a note. */
async function announce(msg) {
  const windows = await self.clients.matchAll({ type: "window" });
  for (const w of windows) w.postMessage(msg);
}

async function shellFirst(request) {
  const cache = await caches.open(SHELL);
  let own = null;
  try {
    const p = new URL(request.url).pathname;
    own = PAGES.find((page) => p.endsWith(page)) || null;
  } catch (e) { own = null; }
  const key = own ? "." + own : "./index.html";
  const hit = await cache.match(key);
  /* Cloned before anything can consume either body: `hit` is about to be
     returned to the page, and a Response can only be read once. */
  const prev = hit ? hit.clone() : null;
  const fresh = fetch(request)
    .then(async (res) => {
      if (!res || !res.ok) return res;
      const store = res.clone();
      /* Only the app, and only when there was something to replace - a first
         install has nothing to announce. */
      if (prev && !own) {
        try {
          const [before, after] = await Promise.all([prev.text(), res.clone().text()]);
          if (before !== after) announce({ type: "shell-updated" });
        } catch (e) { /* a body that cannot be read is not worth a banner */ }
      }
      cache.put(key, store);
      return res;
    })
    .catch(() => null);
  return hit || (await fresh) || Response.error();
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  let url;
  try { url = new URL(request.url); } catch (e) { return; }

  if (TILE_HOSTS.indexOf(url.hostname) >= 0) {
    event.respondWith(tileFirst(request));
    return;
  }
  if (url.origin !== self.location.origin) return;   // Supabase and the rest

  if ((url.pathname.indexOf("/zones/") >= 0 ||
       url.pathname.indexOf("/context/") >= 0) && url.pathname.endsWith(".png")) {
    event.respondWith(zoneFirst(request));
    return;
  }
  if (url.pathname.endsWith("/conditions.json")) {
    event.respondWith(networkFirst(request, DATA));
    return;
  }
  /* A fylke's marked routes. Cache-first and kept, which is the whole point of
     splitting them per region: a reader downloads Agder once, on a connection,
     and has every marked route in Agder from then on with no signal at all.
     Turrutebasen changes a few times a year and a route that moved is a far
     smaller problem than a map with no paths on it, so staleness is the right
     trade here in a way it was not for the weather above. */
  if (url.pathname.indexOf("/trails/") >= 0 && url.pathname.endsWith(".json")) {
    event.respondWith(zoneFirst(request));
    return;
  }
  if (request.mode === "navigate" || url.pathname.endsWith("/index.html")
      || url.pathname === "/") {
    event.respondWith(shellFirst(request));
    return;
  }
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request)),
  );
});
