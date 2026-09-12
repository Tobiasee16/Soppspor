/* The hosted bundle is a different product from the artifact, and the thing
 * that makes it different is an absence: it must not carry a basemap. That is
 * easy to break by accident and invisible when broken - the page would simply
 * be 10 MB heavier and still work on the desk of whoever built it.
 *
 * So these assert the absence, the national bounds, and that every file the
 * page references is actually in the directory. Run after build_hosted.py.
 */
const fs = require("fs");
const path = require("path");

const DIST = path.join(__dirname, "..", "dist");
let fails = 0;
const check = (n, ok, d) => {
  console.log((ok ? "PASS  " : "FAIL  ") + n + (d ? "   " + d : ""));
  if (!ok) fails++;
};

if (!fs.existsSync(path.join(DIST, "index.html"))) {
  console.log("SKIP  no dist/ - run: python scripts/build_hosted.py");
  process.exit(0);
}

const html = fs.readFileSync(path.join(DIST, "index.html"), "utf8");
const bytes = Buffer.byteLength(html);

// ------------------------------------------------------- the absence --
check("no inlined basemap imagery", !/data:image\/jpeg;base64/.test(html),
      /data:image\/jpeg;base64/.test(html) ? "found inlined JPEG" : "");
check("SOPP_BASE is empty", /window\.SOPP_BASE=\{\};/.test(html));
check("the page is small enough to stop being the constraint",
      bytes < 6e6, (bytes / 1e6).toFixed(2) + " MB");
// The artifact build was 14.77 MB; this one must be far below it or the whole
// exercise achieved nothing.
check("it is well under the artifact build it replaces", bytes < 14.77e6 * 0.5,
      (bytes / 1e6).toFixed(2) + " MB vs 14.77 MB");

// -------------------------------------------------------- the presence --
check("it declares itself hosted", /window\.SOPP_ENV=\{[^}]*"hosted":true/.test(html));
check("it declares itself roaming", /"roaming":true/.test(html));
const nb = html.match(/"bounds":\[([-\d,]+)\]/);
check("it carries national bounds", !!nb, nb ? nb[1] : "missing");
if (nb) {
  const b = nb[1].split(",").map(Number);
  check("those bounds span Norway, not one kommune",
        (b[2] - b[0]) > 1_000_000 && (b[3] - b[1]) > 1_400_000,
        `${((b[2]-b[0])/1000).toFixed(0)} x ${((b[3]-b[1])/1000).toFixed(0)} km`);
}
check("the model payload is still inlined", /window\.SOPP=\{/.test(html));
check("the routes are still inlined", /window\.SOPP_TRAILS=/.test(html));
check("tiles come from Kartverket's cache",
      html.includes("https://cache.kartverket.no/v1/wmts/1.0.0/"));

// ------------------------------------------------ it is a complete site --
check("it is a whole document, not a fragment",
      /^<!doctype html>/i.test(html.trim()) && /<\/html>\s*$/i.test(html.trim()));
check("the language is declared as Norwegian", /<html lang="no">/.test(html));
check("it registers the service worker", /serviceWorker/.test(html));
for (const f of ["sw.js", "manifest.webmanifest", "conditions.json",
                 "icons/icon-180.png", "icons/icon-192.png",
                 "icons/icon-512.png", "_headers", "_redirects"]) {
  check(`dist/${f} exists`, fs.existsSync(path.join(DIST, f)));
}

// Anything the HTML asks for by relative path must be on disk, or the first
// user gets a 404 for it and nobody notices until they report a blank icon.
const refs = [...html.matchAll(/(?:href|src)="(?!https?:|data:|#)([^"]+)"/g)]
  .map(m => m[1]).filter(u => !u.startsWith("/"));
const missing = [...new Set(refs)].filter(u => !fs.existsSync(path.join(DIST, u)));
check("every relative reference resolves to a file in dist",
      missing.length === 0, missing.join(", ") || [...new Set(refs)].join(", "));

// The deployed copy must behave like the one that was tested. serve_interface
// falls back to index.html for unknown paths; without _redirects, Cloudflare
// Pages would serve its own 404 instead - so a bookmark or a shared link would
// work locally and fail in production, which is the worst place to find out.
{
  const r = fs.readFileSync(path.join(DIST, "_redirects"), "utf8");
  check("unknown paths fall back to the app", /^\/\*\s+\/index\.html\s+200/m.test(r),
        JSON.stringify(r));
}

// -------------------------------------------------------- the manifest --
{
  const m = JSON.parse(fs.readFileSync(path.join(DIST, "manifest.webmanifest"), "utf8"));
  check("the manifest can be installed", m.display === "standalone" ||
        m.display === "fullscreen", String(m.display));
  check("the manifest has icons", Array.isArray(m.icons) && m.icons.length > 0);
  for (const ic of m.icons || []) {
    const src = ic.src.replace(/^\.?\//, "");
    check(`manifest icon ${src} exists`, fs.existsSync(path.join(DIST, src)));
  }
}

// ------------------------------------------------------ the service worker --
{
  const sw = fs.readFileSync(path.join(DIST, "sw.js"), "utf8");
  check("the worker caches Kartverket tiles",
        sw.includes("cache.kartverket.no"));
  check("the worker treats the weather as network-first",
        /conditions\.json/.test(sw) && /networkFirst/.test(sw));
  check("the worker caps the tile cache", /TILE_LIMIT\s*=\s*\d+/.test(sw),
        (sw.match(/TILE_LIMIT\s*=\s*(\d+)/) || [])[1] + " tiles");
}

console.log(fails ? `\n${fails} FAILED` : "\nall hosted-build checks passed");
process.exit(fails ? 1 : 0);
