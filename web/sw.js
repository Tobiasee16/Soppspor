/* Service worker: makes soppkart work with no signal, which is the condition
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
const VERSION = "soppkart-v2";
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
      .then((c) => c.addAll(["./", "./index.html", "./manifest.webmanifest"]))
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

async function shellFirst(request) {
  const cache = await caches.open(SHELL);
  const key = "./index.html";
  const hit = await cache.match(key);
  const fresh = fetch(request)
    .then((res) => {
      if (res && res.ok) cache.put(key, res.clone());
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
  if (request.mode === "navigate" || url.pathname.endsWith("/index.html")
      || url.pathname === "/") {
    event.respondWith(shellFirst(request));
    return;
  }
  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request)),
  );
});
