"""Build the hosted version of the interface, for a static host.

    python scripts/build_hosted.py [outdir]      # default: dist/

The artifact build (`build_interface.py`) inlines everything into one file
because a published artifact blocks external requests. That works and it caps
the product at one kommune: 10.4 MB of the 14.8 MB page is basemap covering a
45x64 km window, and Norway's mainland is 111 times that area. No amount of
compression closes that gap - see EXPANSION.md.

A hosted build drops the inlined basemap entirely and fetches tiles from
Kartverket's cache at runtime, in EPSG:25833, the map's own projection. Three
things follow:

* **The page stops growing with area.** It is the shell, the model payload and
  the routes - the basemap is no longer part of the download at all.
* **The conditions layer actually refreshes.** The app already asks for a
  fresher `conditions.json` on every launch; in the artifact that request is
  blocked by the content policy, so the published link is frozen at whatever
  weather was baked in at build time. Here it works.
* **It can roam.** Panning is bounded by mainland Norway rather than by the
  edge of the sheets we happened to ship.

What does *not* change is where the estimate exists. The model is fitted on
Agder and validated on Kristiansand; everywhere else the app shows the map, the
marked routes, the rainfall and your own saved places, and says plainly that
there are no zones yet. A suitability surface that has not been measured
anywhere is not something to hand a beginner.

Output is a plain static directory. Any host will serve it - Cloudflare Pages,
Netlify, GitHub Pages - and none of them need a build step of their own.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_interface as bi

SHELL = ROOT / "web" / "shell.html"
WEB = ROOT / "web"
PAYLOAD = bi.PAYLOAD
COND = bi.COND
TRAILS = bi.TRAILS

# Mainland Norway in EPSG:25833, with a 20 km margin, computed from the
# extreme points rather than eyeballed: Lindesnes, Nordkinn, Utsira and
# Hornoya. 1,215 km wide by 1,525 km tall.
NORWAY = [-95223, 6434491, 1120013, 7959232]

HEAD = """<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#f2f2f7" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#000000" media="(prefers-color-scheme:dark)">
<meta name="description" content="Soppkart - et anslag fra offentlige kartdata om hvor terrenget ligner på steder sopp faktisk er funnet.">
<meta name="robots" content="noindex">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="icons/icon-180.png">
<!-- iOS honours the manifest for `display` from 16.4, but the status bar is
     still controlled here and nowhere else. Left unset it defaults to the same
     thing, and defaults are what break quietly when Apple changes them.

     `default` rather than `black-translucent` on purpose. Translucent would let
     the map run underneath the Dynamic Island, which looks better in a
     screenshot and puts the white system clock on top of a pale grey basemap,
     where it cannot be read. The status bar instead takes theme-color: light
     grey with black text, or black with white, whichever matches. Legibility
     beats edge-to-edge on a thing people use outdoors in sunlight. -->
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Soppkart">
<meta name="mobile-web-app-capable" content="yes">
<title>Soppkart</title>
</head>
<body>
"""

TAIL = """
<script>
/* Registered last so a failure here can never stop the app rendering. The
   worker is what makes the map usable with no signal: it keeps the shell and
   every Kartverket tile already looked at, which is why the tile source is a
   WMTS cache with stable URLs rather than a WMS with a bbox per view. */
if ("serviceWorker" in navigator) {
  window.addEventListener("load", function () {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  });
}
</script>
</body>
</html>
"""


def main() -> int:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"
    if not PAYLOAD.exists():
        raise SystemExit(f"missing {PAYLOAD} - run scripts/export_web_preview.py")
    if not SHELL.exists():
        raise SystemExit(f"missing {SHELL}")

    out.mkdir(parents=True, exist_ok=True)

    # --- the page -------------------------------------------------------
    shell = SHELL.read_text(encoding="utf-8")
    marker = "<script>\n(function(){"
    if marker not in shell:
        raise SystemExit("shell has no '<script>\\n(function(){' insertion point")

    env = {"hosted": True, "roaming": True, "bounds": NORWAY}
    env_js = "window.SOPP_ENV=" + json.dumps(env, separators=(",", ":")) + ";"

    # No inlined basemap. This is the whole point of the hosted build, and it
    # is stated rather than left as an empty object someone later "fixes".
    base_js = ("/* hosted build: the basemap comes from Kartverket's WMTS cache "
               "at runtime, not from bytes in this file */\n"
               "window.SOPP_BASE={};")

    payload = PAYLOAD.read_text(encoding="utf-8")

    # Conditions and routes stay external files rather than inlined script.
    # Weather is on a daily clock and the app refetches it on launch; making it
    # a file means a weather update is a one-file upload, not a rebuild.
    cond_js = "window.SOPP_COND=null;"
    if COND.exists():
        shutil.copyfile(COND, out / "conditions.json")
        cond = json.loads(COND.read_text(encoding="utf-8"))
        cond_js = "window.SOPP_COND=" + json.dumps(cond, separators=(",", ":")) + ";"
        print(f"  conditions: {cond['last_day']} ({cond['lag_days']}d behind)")

    trails_js = "window.SOPP_TRAILS=null;"
    if TRAILS.exists():
        tr = json.loads(TRAILS.read_text(encoding="utf-8"))
        trails_js = "window.SOPP_TRAILS=" + json.dumps(tr, separators=(",", ":")) + ";"
        print(f"  trails: {tr['lines']} routes, {tr['vertices']} vertices")

    injected = ("<script>" + payload + "</script>\n"
                "<script>" + env_js + "</script>\n"
                "<script>" + base_js + "</script>\n"
                "<script>" + cond_js + "</script>\n"
                "<script>" + trails_js + "</script>\n"
                "<script>" + bi.supabase_config() + "</script>\n" + marker)
    page = HEAD + shell.replace(marker, injected, 1) + TAIL
    (out / "index.html").write_text(page, encoding="utf-8")

    # --- everything else the host serves --------------------------------
    for name in ("manifest.webmanifest", "sw.js"):
        src = WEB / name
        if src.exists():
            shutil.copyfile(src, out / name)
        else:
            print(f"  WARNING: {name} missing from web/")
    if (WEB / "icons").exists():
        shutil.copytree(WEB / "icons", out / "icons", dirs_exist_ok=True)

    # Headers for hosts that read them. Long-lived caching for the immutable
    # assets, none for the page and the weather.
    (out / "_headers").write_text(
        "/*\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  X-Content-Type-Options: nosniff\n"
        "/index.html\n"
        "  Cache-Control: no-cache\n"
        "/sw.js\n"
        "  Cache-Control: no-cache\n"
        "/conditions.json\n"
        "  Cache-Control: no-cache\n"
        "/icons/*\n"
        "  Cache-Control: public, max-age=31536000, immutable\n",
        encoding="utf-8")

    # Single-page fallback. The app never creates a deep path itself - it reads
    # the magic link's hash and then replaceState's back to where it started -
    # but a bookmark, a stray link or a shared URL would otherwise land on the
    # host's 404 page instead of the map. `serve_interface.py --dist` already
    # does this; without the file the deployed copy would differ from the one
    # that was tested, which is the worst kind of difference.
    #
    # Real files win over this rule, so conditions.json and the icons are
    # unaffected.
    (out / "_redirects").write_text("/*  /index.html  200" + chr(10),
                                    encoding="utf-8")

    total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    page_mb = len(page.encode("utf-8")) / 1e6
    print(f"\n  index.html        {page_mb:6.2f} MB")
    for f in sorted(out.rglob("*")):
        if f.is_file() and f.name != "index.html":
            print(f"  {f.relative_to(out).as_posix():18s}{f.stat().st_size / 1e6:6.2f} MB")
    print(f"\nhosted build -> {out}  ({total / 1e6:.2f} MB total)")
    print(f"compare: the artifact build is 14.77 MB and cannot leave Kristiansand.")
    print("\nserve it locally with:  python scripts/serve_interface.py --dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
