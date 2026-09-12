"""Marked routes as vector geometry, so they draw like the zones do.

    python scripts/fetch_trails.py

The trails were raster tiles, and that was the wrong shape for them. A tile
server draws a line about two pixels wide, so how thick a path looks on screen
depends entirely on the tile's resolution against your zoom - at the deepest
zoom the embedded 9 m/px sheet turned a footpath into a 120-pixel orange band,
and no amount of tuning fixes that, because the thickness is baked in at fetch
time.

The zones never had this problem: they are traced as vector paths and stroked
at a width measured in screen pixels, so they are crisp at every scale. Trails
should be the same kind of object, and Turrutebasen's WFS will hand over the
actual line geometry in UTM33.

So this fetches it once, simplifies it, and packs it small enough to ship
inside the page. The result is sharp at any zoom, the line width becomes one
number in the interface rather than a fight with a tile server, and it works
offline and inside a published artifact with no network at all.

**Geometry, not styling.** Nothing here decides what a trail looks like; that
is the interface's job. This is coordinates and nothing else.

Kartverket, Turrutebasen, CC BY 4.0. Attribution is in the interface footer.
"""
from __future__ import annotations

import base64
import json
import pathlib
import re
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

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw" / "turrutebasen"
OUT_JSON = ROOT / "data" / "interim" / "trails.json"
OUT_MD = ROOT / "data" / "processed" / "trails.md"
MANIFEST = ROOT / "data" / "interim" / "basemap" / "manifest.json"

WFS = "https://wfs.geonorge.no/skwms1/wfs.turogfriluftsruter"
UA = {"User-Agent": "soppkart/0.1 (habitat suitability research)"}

# AnnenRute returns nothing over this extent; it is kept so the set is explicit
# rather than mysteriously short.
TYPES = ["app:Fotrute", "app:Sykkelrute", "app:AnnenRute"]

# A metre of simplification is well inside where a marked path actually is, and
# removes most of the vertices: this data is densely sampled, roughly forty
# points per line before thinning.
TOLERANCE_M = 1.0
UNIT_M = 0.5          # quantisation; deltas are int16, so +/- 16 km per step
BUCKETS = 6           # viewport culling grid used by the interface


def extent() -> tuple[float, float, float, float]:
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bb = [v["bbox"] for v in man.values()]
    return (min(b[0] for b in bb), min(b[1] for b in bb),
            max(b[2] for b in bb), max(b[3] for b in bb))


def fetch(type_name: str, ext) -> str:
    """GML for one feature type, cached - the footpath query takes minutes."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_DIR / (type_name.replace(":", "_") + ".gml")
    if cache.exists():
        print(f"  {type_name}: cached ({cache.stat().st_size / 1e6:.1f} MB)", flush=True)
        return cache.read_text(encoding="utf-8", errors="replace")
    bbox = f"{ext[0]:.0f},{ext[1]:.0f},{ext[2]:.0f},{ext[3]:.0f},urn:ogc:def:crs:EPSG::25833"
    url = (f"{WFS}?service=WFS&version=2.0.0&request=GetFeature"
           f"&typeNames={urllib.parse.quote(type_name)}"
           f"&srsName=urn:ogc:def:crs:EPSG::25833"
           f"&bbox={urllib.parse.quote(bbox, safe=',:')}")
    t0 = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
        raw = r.read()
    text = raw.decode("utf-8", "replace")
    if "ExceptionReport" in text or "ServiceException" in text:
        raise SystemExit(f"{type_name} failed:\n{text[:400]}")
    cache.write_text(text, encoding="utf-8")
    print(f"  {type_name}: {len(raw) / 1e6:.1f} MB in {time.time() - t0:.0f}s", flush=True)
    return text


def lines_from(gml: str) -> list[np.ndarray]:
    out = []
    for m in re.finditer(r"<gml:posList[^>]*>([^<]+)</gml:posList>", gml):
        vals = m.group(1).split()
        if len(vals) < 4:
            continue
        a = np.asarray(vals, dtype=float)
        # The service is asked for EPSG:25833 and answers easting-first here,
        # which is checked below rather than assumed - a swapped axis order is
        # the classic way to get a plausible-looking map of the wrong place.
        out.append(a.reshape(-1, 2))
    return out


def simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    """Douglas-Peucker, iterative so a long line cannot blow the stack."""
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        seg = pts[j] - pts[i]
        L = float(np.hypot(*seg))
        sub = pts[i + 1:j]
        v = sub - pts[i]
        if L < 1e-9:
            d = np.hypot(v[:, 0], v[:, 1])
        else:
            # 2-D cross product written out: numpy 2 removed the 2-vector form
            # of np.cross, and the scalar magnitude is all that is wanted.
            d = np.abs(seg[0] * v[:, 1] - seg[1] * v[:, 0]) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            k += i + 1
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return pts[keep]


def main() -> int:
    ext = extent()
    print(f"extent {ext[0]:.0f} {ext[1]:.0f} {ext[2]:.0f} {ext[3]:.0f}", flush=True)

    raw_lines, per_type = [], {}
    for t in TYPES:
        gml = fetch(t, ext)
        ls = lines_from(gml)
        per_type[t] = {"lines": len(ls), "vertices": int(sum(len(x) for x in ls))}
        raw_lines.extend(ls)

    if not raw_lines:
        raise SystemExit("no trail geometry returned - refusing to write an empty layer")

    allpts = np.vstack(raw_lines)
    if not (ext[0] - 5000 < allpts[:, 0].mean() < ext[2] + 5000
            and ext[1] - 5000 < allpts[:, 1].mean() < ext[3] + 5000):
        raise SystemExit(
            f"coordinates are not inside the extent (mean {allpts.mean(axis=0)}). "
            "The WFS axis order is probably northing-first; do not ship this.")

    before = int(sum(len(x) for x in raw_lines))
    simp = [simplify(p, TOLERANCE_M) for p in raw_lines]
    simp = [p for p in simp if len(p) >= 2]
    after = int(sum(len(x) for x in simp))
    print(f"\n{len(simp)} lines, {before} -> {after} vertices "
          f"({100 * (1 - after / before):.0f} % removed at {TOLERANCE_M} m)", flush=True)

    ox, oy = float(ext[0]), float(ext[1])
    starts, deltas, counts, bucket_of = [], [], [], []
    bw = (ext[2] - ext[0]) / BUCKETS
    bh = (ext[3] - ext[1]) / BUCKETS
    for p in simp:
        q = np.round((p - (ox, oy)) / UNIT_M).astype(np.int64)
        d = np.diff(q, axis=0)
        # An int16 delta covers 16 km at half-metre units; nothing here comes
        # close, but a silent wrap would put a path across the map.
        if np.abs(d).max() > 32000:
            keepmask = np.abs(d).max(axis=1) <= 32000
            if not keepmask.any():
                continue
            q = q[np.concatenate(([True], keepmask))]
            d = np.diff(q, axis=0)
            if len(d) == 0:
                continue
        starts.append(q[0])
        deltas.append(d.astype(np.int16))
        counts.append(len(q))
        cx, cy = float(p[:, 0].mean()), float(p[:, 1].mean())
        bucket_of.append(min(BUCKETS - 1, max(0, int((cx - ext[0]) / bw)))
                         + BUCKETS * min(BUCKETS - 1, max(0, int((cy - ext[1]) / bh))))

    starts_a = np.asarray(starts, dtype=np.int32).ravel()
    deltas_a = np.concatenate(deltas).ravel().astype(np.int16)
    counts_a = np.asarray(counts, dtype=np.uint32)
    buckets_a = np.asarray(bucket_of, dtype=np.uint8)

    def b64(a):
        return base64.b64encode(a.tobytes()).decode("ascii")

    payload = {
        "crs": "EPSG:25833", "origin": [ox, oy], "unit_m": UNIT_M,
        "buckets": BUCKETS, "extent": [round(v, 1) for v in ext],
        "lines": len(counts), "vertices": after,
        "starts": b64(starts_a), "deltas": b64(deltas_a),
        "counts": b64(counts_a), "bucket": b64(buckets_a),
        "source": "Kartverket Turrutebasen (WFS), CC BY 4.0",
        "simplified_m": TOLERANCE_M,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    raw_bytes = starts_a.nbytes + deltas_a.nbytes + counts_a.nbytes + buckets_a.nbytes
    size = OUT_JSON.stat().st_size

    a = []
    p = a.append
    p("# Marked routes, as vector geometry\n")
    p("Generated by `scripts/fetch_trails.py`. Not a model input.\n")
    p("## Why vector and not tiles\n")
    p("A tile server draws a route line about two pixels wide, so its apparent "
      "thickness is fixed at fetch time and scales with the tile's resolution "
      "rather than with the map. At the deepest zoom the embedded 9 m/px sheet "
      "turned a footpath into a band roughly 120 screen pixels across, which "
      "is not a path but a claim that the path is somewhere in there.\n")
    p("The suitability zones never had this problem because they are traced as "
      "vector paths and stroked at a width measured in screen pixels. Routes "
      "are now the same kind of object: sharp at every zoom, thickness set by "
      "one number in the interface, and no network needed to draw them.\n")
    p("## Contents\n")
    p("| feature type | lines | vertices |\n|---|---|---|")
    for t, v in per_type.items():
        p(f"| `{t}` | {v['lines']} | {v['vertices']} |")
    p("")
    p(f"| | |\n|---|---|")
    p(f"| vertices before simplifying | {before:,} |")
    p(f"| after, at {TOLERANCE_M:.1f} m | {after:,} ({100 * (1 - after / before):.0f} % removed) |")
    p(f"| quantisation | {UNIT_M} m, int16 deltas |")
    p(f"| packed | {raw_bytes / 1e3:.0f} kB binary, {size / 1e3:.0f} kB as JSON |")
    p(f"| culling grid | {BUCKETS} x {BUCKETS} buckets |")
    p("")
    p("Simplification is Douglas-Peucker at one metre. That is well inside "
      "where a marked path actually is, and the source is densely sampled, so "
      "it removes most of the vertices for no visible change.\n")
    p("## Checks\n")
    p("The WFS is asked for EPSG:25833 and answers easting-first. That is "
      "verified on every run by testing the mean coordinate against the "
      "extent, because a swapped axis order produces a plausible-looking map "
      "of entirely the wrong place rather than an error.\n")
    p("## Licence\n")
    p("Kartverket, **Turrutebasen**, CC BY 4.0. Attribution is carried in the "
      "interface footer.\n")
    OUT_MD.write_text("\n".join(a), encoding="utf-8")

    print(f"packed {raw_bytes / 1e3:.0f} kB binary -> {size / 1e3:.0f} kB JSON "
          f"(~{size * 1.0 / 1e3:.0f} kB inlined)")
    print(f"written: {OUT_JSON.relative_to(ROOT)}")
    print(f"written: {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
