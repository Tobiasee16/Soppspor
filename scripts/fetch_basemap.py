"""Kartverket basemap for the interface, fetched once and embedded as tiles.

    python scripts/fetch_basemap.py

The prototype cannot load map tiles at runtime. A published artifact runs under
a content policy that blocks every external image and network call, so a normal
slippy map against a tile server is not available at all. The basemap therefore
has to ship *inside* the page, which means fetching it here, at a fixed extent,
and compressing it to something a browser can hold.

**Norge i bilder (aerial imagery) is not available.** `wms.geonorge.no/skwms1/
wms.nib` answers `Bruker kan ikke autentiseres` to an anonymous request - it
needs a Kartverket account. That is the same class of blocker as FKB-AR5: a
credential, not a missing route.

Three decisions worth writing down, because each one was a fix for something
visibly wrong in an earlier build:

**One layer, `Norges_grunnkart`, in colour.** `topo` carries contour bands,
coloured elevation and dense marginalia that at phone size is noise competing
with the overlay. `Norges_grunnkart_graatone` is calm but greyscale, so water
and land are both pale grey and the reader loses the single strongest cue for
where they are. The colour grunnkart has pale blue water, muted green forest
and warm buff settlement - close to what a modern phone map looks like - with
place names and roads and no contour clutter. It is lightly desaturated and
brightened here so the blue suitability overlay reads on top of it.

**The extent is the modelled area, not the kommune bounding box.** Kristiansand's
official boundary runs far out into the Skagerrak: the kommune bbox is 45 x 64 km
and roughly 40 % of it is open sea, and only 22 % of the grid holds a modelled
cell at all. Sheets fetched over the bbox spent most of their pixels on empty
water. The extent here is the bounding box of cells the model actually scores,
taken from the exported payload, plus a margin to pan into.

**Tiles, not one sheet.** A single image over this extent at a usable resolution
is 25+ megapixels, which is one enormous decode a phone has to hold in full. A
grid lets the interface draw only the tiles on screen. It also fixed the real
complaint: the old single sheet was 31.7 m/px against an app that zooms to 5,
so zooming in produced a blur.

Everything here is Kartverket open data under CC BY 4.0. Attribution is carried
in the interface footer, which is a licence condition and not a courtesy.
"""
from __future__ import annotations

import base64
import io
import json
import math
import pathlib
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

from src.geometry import Boundary

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "interim" / "basemap"
OUT_MD = ROOT / "data" / "processed" / "basemap.md"
PAYLOAD = ROOT / "data" / "processed" / "web_preview" / "payload.js"

WMS = "https://wms.geonorge.no/skwms1/wms.{service}"
USER_AGENT = "soppspor/0.1 (habitat suitability research; support@soppspor.no)"

# Two layers, because they answer different questions. `plain` is the default
# and is for orientation - where am I, what road is that. `topo` is for reading
# the ground itself: contours, marsh, tracks, vegetation boundaries. Topo is
# fetched at lower quality because it is the secondary layer and the page has a
# 16 MB ceiling that both layers have to fit inside together.
#
# Topo is carried at a coarser resolution than plain, and that is a measured
# trade rather than a guess. At a matched 9 m/px and *lower* JPEG quality topo
# still came out 40 % larger (5.57 MB against 3.99 MB), because contour lines
# are high-frequency detail everywhere on the sheet and JPEG cannot compress
# them. Both layers inlined at 9 m/px reached 15.8 MB base64 against a 16 MB
# page ceiling. 12 m/px costs topo about half its pixels and buys the headroom.
#
# key, service, WMS layer, m/px, JPEG quality, saturation, brightness
LAYERS = [
    ("plain", "norges_grunnkart", "Norges_grunnkart", 9.0, 76, 0.80, 1.05),
    ("topo", "topo", "topo", 12.0, 70, 0.80, 1.07),
]

# Marked routes used to be fetched here as a transparent raster overlay. They
# are now vector geometry from Turrutebasen's WFS instead - see
# scripts/fetch_trails.py, which explains why. A raster route line has its
# thickness baked in at fetch time and turns into a wide smear at deep zoom;
# the vector version is sharp at any scale and costs about the same.
OVERLAYS: list = []

MARGIN_M = 3000.0      # how far past the modelled edge you can pan
MAX_TILE_PX = 1400     # keeps each decode small enough for a phone


def modelled_extent() -> tuple[tuple[float, float, float, float], str]:
    """Bounding box of cells the model actually scores.

    Read from the exported payload, which is the thing the basemap has to line
    up with. Falls back to the kommune bbox with a loud note if the payload has
    not been exported yet - that fallback wastes about two thirds of its pixels
    on open sea, so it is a stopgap and says so.
    """
    if PAYLOAD.exists():
        import numpy as np

        text = PAYLOAD.read_text(encoding="utf-8")
        d = json.loads(text[text.index("{"):text.rindex("}") + 1])
        g, m = d["grid"], d["meta"]
        w, h, cell = g["w"], g["h"], m["cell_m"]
        inside = np.zeros(w * h, dtype=bool)
        for sp in d["species"]:
            inside |= np.frombuffer(base64.b64decode(sp["data"]), dtype=np.uint8) != 0
        ys, xs = np.nonzero(inside.reshape(h, w))
        if len(xs):
            return (m["origin_x"] + int(xs.min()) * cell,
                    m["origin_y"] - (int(ys.max()) + 1) * cell,
                    m["origin_x"] + (int(xs.max()) + 1) * cell,
                    m["origin_y"] - int(ys.min()) * cell), "modelled cells in payload.js"

    print("  payload.js not found - falling back to the kommune bbox, which is "
          "~40% open sea", flush=True)
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    cfg = area["prediction_extent"]
    fc = json.loads((ROOT / cfg["boundary_file"]).read_text(encoding="utf-8"))
    return Boundary(fc["features"][0]["geometry"], name=cfg["name"]).bbox, "kommune bbox (fallback)"


def get_map(service: str, layer: str, bbox, width: int, height: int,
            transparent: bool = False) -> tuple[bytes, str]:
    params = {
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": layer, "styles": "", "srs": "EPSG:25833",
        "bbox": ",".join(f"{v:.0f}" for v in bbox),
        "width": width, "height": height, "format": "image/png",
    }
    if transparent:
        params["transparent"] = "true"
    q = urllib.parse.urlencode(params)
    url = WMS.format(service=service) + "?" + q
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
    if not raw.startswith(b"\x89PNG"):
        # A WMS returns its errors as XML with a 200, so the magic bytes are
        # the only reliable check that an image came back at all.
        raise SystemExit(f"{service}/{layer} did not return a PNG:\n"
                         f"{raw[:400].decode('utf-8', 'replace')}")
    return raw, url


def to_jpeg(png_bytes: bytes, path: pathlib.Path, quality: int,
            saturation: float, brightness: float) -> int:
    from PIL import Image, ImageEnhance

    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    im = ImageEnhance.Color(im).enhance(saturation)
    im = ImageEnhance.Brightness(im).enhance(brightness)
    im.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    return path.stat().st_size


def main() -> int:
    (minx, miny, maxx, maxy), provenance = modelled_extent()
    extent = (minx - MARGIN_M, miny - MARGIN_M, maxx + MARGIN_M, maxy + MARGIN_M)
    span_x, span_y = extent[2] - extent[0], extent[3] - extent[1]
    print(f"extent from {provenance}: {span_x / 1000:.1f} x {span_y / 1000:.1f} km "
          f"(+{MARGIN_M:.0f} m margin)", flush=True)

    # Every layer shares one tile grid, so the tiles line up cell for cell and
    # the interface can swap sheets without recomputing anything. Only the
    # pixel size inside each tile changes with the layer's resolution.
    finest = min(spec[3] for spec in LAYERS)
    cols = math.ceil(span_x / finest / MAX_TILE_PX)
    rows = math.ceil(span_y / finest / MAX_TILE_PX)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for pat in ("*.jpg", "*.png"):
        for stale in OUT_DIR.glob(pat):
            stale.unlink()

    manifest, urls, per_layer = {}, [], {}
    t0 = time.time()
    for name, service, layer, target, quality, sat, bright in LAYERS:
        tile_w = int(round(span_x / cols / target))
        tile_h = int(round(span_y / rows / target))
        m_per_px = span_x / (tile_w * cols)
        print(f"{name}: {cols}x{rows} tiles of {tile_w}x{tile_h} px = "
              f"{tile_w * cols}x{tile_h * rows} "
              f"({tile_w * cols * tile_h * rows / 1e6:.1f} Mpx) at "
              f"{m_per_px:.1f} m/px", flush=True)
        subtotal = 0
        for r in range(rows):
            for c in range(cols):
                x0 = extent[0] + span_x * c / cols
                x1 = extent[0] + span_x * (c + 1) / cols
                y1 = extent[3] - span_y * r / rows
                y0 = extent[3] - span_y * (r + 1) / rows
                bbox = (x0, y0, x1, y1)
                png, url = get_map(service, layer, bbox, tile_w, tile_h)
                path = OUT_DIR / f"{name}_t{r}_{c}.jpg"
                n = to_jpeg(png, path, quality, sat, bright)
                # The key carries the layer, so the interface can group tiles
                # into switchable sheets without a second manifest.
                manifest[f"{name}/t{r}_{c}"] = {
                    "file": path.name, "bbox": [round(v, 2) for v in bbox],
                    "crs": "EPSG:25833", "width": tile_w, "height": tile_h,
                    "m_per_px": round(m_per_px, 3), "bytes": n,
                    "layer": name, "row": r, "col": c}
                urls.append(url)
                subtotal += n
        per_layer[name] = {"service": service, "layer": layer, "quality": quality,
                           "bytes": subtotal, "m_per_px": m_per_px,
                           "tile": (tile_w, tile_h)}
        print(f"  {name} ({service}): {subtotal / 1e6:.2f} MB "
              f"in {rows * cols} tiles, q{quality}", flush=True)

    for name, service, layer, target in OVERLAYS:
        from PIL import Image

        tile_w = int(round(span_x / cols / target))
        tile_h = int(round(span_y / rows / target))
        m_per_px = span_x / (tile_w * cols)
        print(f"{name}: {cols}x{rows} tiles of {tile_w}x{tile_h} px at "
              f"{m_per_px:.1f} m/px, transparent", flush=True)
        subtotal = 0
        for r in range(rows):
            for c in range(cols):
                x0 = extent[0] + span_x * c / cols
                x1 = extent[0] + span_x * (c + 1) / cols
                y1 = extent[3] - span_y * r / rows
                y0 = extent[3] - span_y * (r + 1) / rows
                bbox = (x0, y0, x1, y1)
                png, url = get_map(service, layer, bbox, tile_w, tile_h,
                                   transparent=True)
                path = OUT_DIR / f"{name}_t{r}_{c}.png"
                im = Image.open(io.BytesIO(png)).convert("RGBA")
                # Eight colours is plenty for route lines and cuts the sheet by
                # roughly four times. FASTOCTREE is the only quantiser Pillow
                # offers that keeps the alpha channel, which is the whole point.
                im.quantize(colors=8, method=Image.FASTOCTREE).save(
                    path, "PNG", optimize=True)
                n = path.stat().st_size
                manifest[f"{name}/t{r}_{c}"] = {
                    "file": path.name, "bbox": [round(v, 2) for v in bbox],
                    "crs": "EPSG:25833", "width": tile_w, "height": tile_h,
                    "m_per_px": round(m_per_px, 3), "bytes": n,
                    "layer": name, "kind": "overlay", "row": r, "col": c}
                urls.append(url)
                subtotal += n
        per_layer[name] = {"service": service, "layer": layer, "quality": 0,
                           "bytes": subtotal, "m_per_px": m_per_px,
                           "tile": (tile_w, tile_h)}
        print(f"  {name} ({service}): {subtotal / 1e6:.2f} MB in "
              f"{rows * cols} transparent tiles", flush=True)

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    (OUT_DIR / "urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")

    total = sum(v["bytes"] for v in manifest.values())
    w_, a = [], None
    a = w_.append
    a("# Basemap tiles for the interface\n")
    a("Generated by `scripts/fetch_basemap.py`. Not a model input; nothing here "
      "reaches a feature stack.\n")
    a("## Why the basemap is embedded rather than tiled from a server\n")
    a("A published artifact runs under a content policy that blocks every "
      "external image and network request. A normal slippy map against a tile "
      "server is therefore not possible at all - not slow, not degraded, "
      "blocked. The basemap ships inside the page as fixed tiles instead.\n")
    a("## No aerial imagery\n")
    a("`wms.geonorge.no/skwms1/wms.nib` (Norge i bilder) answers an anonymous "
      "request with `Bruker kan ikke autentiseres` - it needs a Kartverket "
      "account. That is a credential, not a missing route, the same class of "
      "blocker as FKB-AR5.\n")
    a("## Layers\n")
    a("| key | service | resolution | tile | quality | size |\n"
      "|---|---|---|---|---|---|")
    for name, v in per_layer.items():
        a(f"| `{name}` | `{v['service']}` / `{v['layer']}` | "
          f"{v['m_per_px']:.1f} m/px | {v['tile'][0]}x{v['tile'][1]} px | "
          f"q{v['quality']} | {v['bytes'] / 1e6:.2f} MB |")
    a("")
    a("`trails` is not a background at all but a transparent overlay of marked "
      "routes from Turrutebasen, drawn over whichever background is showing. "
      "The zones say where to look and nothing about how to get there, and a "
      "zone you cannot walk into is decoration. It is cheap to carry because "
      "the sheet is about 99 % transparent and quantises to eight colours.\n")
    a("`plain` is the default and is for orientation: pale blue water, muted "
      "green forest, warm buff settlement, place names and roads, no contour "
      "clutter. `topo` is the secondary layer and is for reading the ground "
      "itself - contours, marsh, tracks, vegetation boundaries - which is what "
      "you want once you are standing in it.\n")
    a("Topo is carried coarser, and that is measured rather than assumed. At a "
      "matched 9 m/px and *lower* JPEG quality it still came out 40 % larger "
      "than plain (5.57 MB against 3.99 MB): contour lines are high-frequency "
      "detail across the whole sheet and JPEG cannot compress them. Both "
      "layers at 9 m/px reached 15.8 MB once base64-encoded, against a 16 MB "
      "page ceiling.\n")
    a("`norges_grunnkart_graatone` was the default in an earlier build and has "
      "been dropped: it is calm, but greyscale means water and land are both "
      "pale grey, which removes the strongest cue for where you are.\n")
    a("## Extent\n")
    a(f"Taken from **{provenance}**: the bounding box of cells the model "
      f"actually scores, plus {MARGIN_M:.0f} m to pan into. Kristiansand's "
      "official boundary runs far out into the Skagerrak, so the kommune "
      "bounding box is 45 x 64 km of which roughly 40 % is open sea, and only "
      "22 % of the model grid holds a scored cell. Earlier sheets were fetched "
      "over that bbox and spent most of their pixels on empty water.\n")
    a(f"| | |\n|---|---|")
    a(f"| extent (EPSG:25833) | {extent[0]:.0f}, {extent[1]:.0f}, "
      f"{extent[2]:.0f}, {extent[3]:.0f} |")
    a(f"| span | {span_x / 1000:.1f} x {span_y / 1000:.1f} km |")
    a(f"| grid | {cols} x {rows} tiles per layer |")
    a(f"| total | {total / 1e6:.2f} MB across {len(per_layer)} layers |")
    a("")
    a("The interface draws only the tiles that intersect the viewport, and only "
      "the layer on screen is ever decoded - the other one stays an unfetched "
      "data URI until you switch to it, which keeps a phone from holding both "
      "sheets in memory at once. The deepest zoom is 5 m/px, so plain upscales "
      "by under 2x; the single sheet this replaced was 31.7 m/px against the "
      "same zoom, which is why zooming in produced a blur.\n")
    a("## Licence\n")
    a("Kartverket, **CC BY 4.0**. Attribution is carried in the interface "
      "footer, which is a licence condition rather than a courtesy.\n")
    a("Resolved URLs are in `urls.txt` beside the images; per-tile bounds are "
      "in `manifest.json`.\n")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(w_), encoding="utf-8")
    print(f"\ntotal {total / 1e6:.2f} MB in {len(manifest)} tiles, "
          f"{time.time() - t0:.0f}s")
    print(f"written: {(OUT_DIR / 'manifest.json').relative_to(ROOT)}")
    print(f"written: {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
