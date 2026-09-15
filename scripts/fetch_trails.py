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
import io
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile

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
UA = {"User-Agent": "soppspor/0.1 (habitat suitability research; support@soppspor.no)"}

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


def fetch(type_name: str, ext, tag: str = "") -> str:
    """GML for one feature type, cached - the footpath query takes minutes.

    `tag` goes in the cache name so the national run, which asks for the
    same three types once per fylke, does not keep handing back Agder.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_DIR / ((tag + "_" if tag else "")
                       + type_name.replace(":", "_") + ".gml")
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


def pack(simp, ext):
    """The wire format, as one dict ready to be JSON-encoded.

    Lifted out of main() when the national build started writing one of
    these per fylke. Both paths must produce identical structures or the
    interface needs two readers, so there is one producer and the caller
    supplies only the extent the coordinates are measured against.
    """
    after = int(sum(len(x) for x in simp))
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

    return {
        "crs": "EPSG:25833", "origin": [ox, oy], "unit_m": UNIT_M,
        "buckets": BUCKETS, "extent": [round(v, 1) for v in ext],
        "lines": len(counts), "vertices": after,
        "starts": b64(starts_a), "deltas": b64(deltas_a),
        "counts": b64(counts_a), "bucket": b64(buckets_a),
        "source": "Kartverket Turrutebasen (WFS), CC BY 4.0",
        "simplified_m": TOLERANCE_M,
    }
OUT_JSON.parent.mkdir(parents=True, exist_ok=True)


FYLKER_DIR = ROOT / "data" / "interim" / "fylker"
NAT_DIR = ROOT / "data" / "processed" / "trails"


def fylker():
    """(slug, name, geometry, bbox, fylkesnummer) per fylke.

    Reuses the boundaries the national pipeline already pulls from Kartverket's
    kommuneinfo API, so there is no second definition of where a fylke is.
    """
    from shapely.geometry import shape
    out = []
    for f in sorted(FYLKER_DIR.glob("boundary_*_25833.geojson")):
        d = json.loads(f.read_text(encoding="utf-8"))
        feat = d["features"][0]
        geom = shape(feat["geometry"])
        name = feat.get("properties", {}).get("fylkesnavn", f.stem)
        slug = f.stem.replace("boundary_", "").replace("_25833", "")
        code = feat.get("properties", {}).get("fylkesnummer", "")
        out.append((slug, name, geom, tuple(geom.bounds), code))
    return out


UUID = "d1422d17-6d95-4ef1-96ab-8af31744dd63"     # Turrutebasen, Geonorge
AREA_API = "https://nedlasting.geonorge.no/api/codelists/area/" + UUID
ORDER_API = "https://nedlasting.geonorge.no/api/order"


def order_fylke(code: str, name: str) -> bytes:
    """The whole of one fylke, as a zip, from Geonorge's download API.

    Not the WFS. Measured on 2026-09-15: a 50 km bbox takes 98 seconds and
    returns 15 MB of GML, so covering the mainland that way is about 130
    requests, two gigabytes and several hours - and the 600 s timeout starts
    refusing the bigger cells long before that. The download API hands over a
    fylke at a time, already clipped and already in EPSG:25833, in one request.

    It also removes work rather than moving it: Geonorge decides which routes
    belong to which fylke, so there is no point-in-polygon pass here and no
    second opinion about where a boundary is.

    Open data, no account, no key. The email goes in the order because the API
    asks for one; nothing is sent to it - deliveryNotificationByEmail is false
    on this dataset.
    """
    body = json.dumps({
        "email": "support@soppspor.no",
        "orderLines": [{
            "metadataUuid": UUID,
            "areas": [{"code": code, "type": "fylke", "name": name}],
            "projections": [{"code": "25833", "name": "EUREF89 UTM sone 33, 2d",
                             "codespace": "http://www.opengis.net/def/crs/EPSG/0/25833"}],
            "formats": [{"name": "GML"}],
        }],
    }).encode("utf-8")
    req = urllib.request.Request(
        ORDER_API, data=body,
        headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        order = json.loads(r.read().decode("utf-8"))
    files = order.get("files") or []
    if not files:
        raise SystemExit(f"{name}: the order came back with no files: "
                         f"{json.dumps(order)[:400]}")
    url = files[0]["downloadUrl"]
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=900) as r:
        return r.read()


def gml_from_zip(blob: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = [n for n in z.namelist() if n.lower().endswith(".gml")]
    if not names:
        raise SystemExit(f"no .gml inside the bundle, only {z.namelist()[:5]}")
    return z.read(names[0]).decode("utf-8", "replace")


def national() -> int:
    """One packed file per fylke, so a reader downloads their own region.

    Nationally Turrutebasen is about 150,000 lines - some 26 times Kristiansand
    rather than the 500 times the area ratio would suggest, because trails
    follow people and not ground. Small enough to ship whole, far too large to
    put inside a 0.27 MB page, so it becomes files fetched when the switch is
    on and the view reaches them.

    Split per fylke rather than per tile. A fylke file is whole: no line is
    clipped at a boundary, nothing needs an index of which tiles exist, and a
    reader who has loaded Agder has every marked route in Agder for good,
    offline, including ground they have never panned across. Tiles would buy
    proportionality and sell exactly that, which is the wrong trade for an app
    whose point is the walk past the end of the signal. See FINDINGS 28.
    """
    fy = fylker()
    if not fy:
        raise SystemExit(f"no fylke boundaries in {FYLKER_DIR} - run the "
                         "national pipeline first")

    with urllib.request.urlopen(
            urllib.request.Request(AREA_API, headers=UA), timeout=120) as r:
        areas = json.loads(r.read().decode("utf-8"))
    by_code = {a["code"]: a["name"] for a in areas if a.get("type") == "fylke"}

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NAT_DIR.mkdir(parents=True, exist_ok=True)
    index, total_kb, total_lines = [], 0.0, 0

    for slug, name, _geom, bb, code in fy:
        if code not in by_code:
            raise SystemExit(f"{name} (code {code}) is not an area Geonorge "
                             f"offers for this dataset: {sorted(by_code)[:8]}")
        cache = RAW_DIR / f"{slug}_25833.zip"
        if cache.exists():
            print(f"{name}: cached ({cache.stat().st_size / 1e6:.1f} MB)", flush=True)
            blob = cache.read_bytes()
        else:
            t0 = time.time()
            blob = order_fylke(code, by_code[code])
            cache.write_bytes(blob)
            print(f"{name}: {len(blob) / 1e6:.1f} MB in {time.time() - t0:.0f}s",
                  flush=True)

        raw = lines_from(gml_from_zip(blob))
        if not raw:
            print(f"  no routes", flush=True)
            continue
        pts = np.vstack(raw)
        mx, my = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        if not (-100000 < mx < 1200000 and 6400000 < my < 8000000):
            raise SystemExit(f"{name}: coordinates are not inside Norway "
                             f"(mean {mx:.0f} {my:.0f}). Axis order is probably "
                             "northing-first; do not ship this.")

        simp = [q for q in (simplify(x, TOLERANCE_M) for x in raw) if len(q) >= 2]
        if not simp:
            continue
        # Packed against the fylke's own bounds, so the int16 deltas and the
        # 6x6 culling grid are both measured on the region the file covers.
        payload = pack(simp, bb)
        payload["fylke"] = name
        out = NAT_DIR / f"{slug}.json"
        out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        kb = out.stat().st_size / 1e3
        total_kb += kb
        total_lines += len(simp)
        index.append({"slug": slug, "fylke": name,
                      "bbox": [round(v, 1) for v in bb],
                      "file": f"{slug}.json", "lines": len(simp),
                      "kb": round(kb, 1)})
        print(f"  {len(raw)} lines -> {len(simp)} kept, {kb:.0f} kB", flush=True)

    if not index:
        raise SystemExit("no fylke produced any routes - refusing to write an "
                         "empty layer")
    (NAT_DIR / "index.json").write_text(
        json.dumps({"crs": "EPSG:25833",
                    "source": "Kartverket Turrutebasen via Geonorge, CC BY 4.0",
                    "simplified_m": TOLERANCE_M,
                    "fylker": index}, separators=(",", ":")),
        encoding="utf-8")
    print()
    print(f"{len(index)} fylker, {total_lines:,} routes, "
          f"{total_kb / 1e3:.1f} MB total")
    print(f"largest: {max(index, key=lambda f: f['kb'])['fylke']} "
          f"{max(f['kb'] for f in index):.0f} kB")
    print(f"written: {NAT_DIR.relative_to(ROOT)}")
    return 0


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

    payload = pack(simp, ext)
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
    raise SystemExit(national() if "--national" in sys.argv else main())
