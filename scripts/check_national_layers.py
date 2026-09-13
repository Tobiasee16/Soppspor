"""Does each national layer agree with the Agder layer it has to match?

    python scripts/check_national_layers.py

Every national raster is built on a different grid from its Agder counterpart -
SR16's national mosaic starts at x = -99513.02 and Agder's at -21824.00, so the
two are offset by a fraction of a cell no matter what (FINDINGS 17d). Sampling
the same coordinate in both therefore lands in *neighbouring* cells, and a raw
disagreement figure means nothing on its own.

**The baseline is the key.** For each band this compares two things:

    national vs Agder          how much they differ
    Agder vs Agder shifted     how much the grid offset alone explains

If the first is at least as good as the second, the disagreement is entirely
the grid. If it is worse, something is wrong with the layer.

This check has earned its keep twice. It explained the SR16 "40% of cells
disagree" alarm as a benign 9 m offset, and it caught the national slope band
being written in radians into a column named slope_deg - a factor of 57.3,
which every unit test passed because the tests compared the new code against
the function it wrapped rather than against the pipeline's own convention.

Run it after building any national layer. A layer that cannot beat its own
offset baseline should not reach a model.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np

INT = ROOT / "data" / "interim"
N_POINTS = 4000
SEED = 20260913

# (label, national, agder, bands, categorical)
LAYERS = [
    ("terrain", "terrain_norge_32m.tif", "terrain_agder_32m.tif",
     [(1, "elevation"), (2, "slope_deg"), (3, "northness"),
      (4, "eastness"), (5, "curvature")], False),
    ("ar50", "ar50_norge_32m.tif", "ar50_agder_32m.tif", [(1, "ar50_group")], True),
    ("losmasser", "losmasser_norge_32m.tif", "losmasser_agder_32m.tif",
     [(1, "losmasse_group")], True),
]
SR16 = [("sr16", f"sr16_norge/SRR{c}.tif", f"sr16/sr16_42_SRR{c}.tif",
         [(1, c)], c == "TRESLAG")
        for c in ("TRESLAG", "VOLMB", "GRFLATE", "MHOYDE", "LAI",
                  "TREALDER", "KRONEDEK", "BONITET")]


def agder_points(n: int, seed: int):
    from src.geometry import Boundary
    fc = json.loads((INT / "boundary_agder_25833.geojson").read_text(encoding="utf-8"))
    bnd = Boundary(fc["features"][0]["geometry"])
    rng = np.random.default_rng(seed)
    minx, miny, maxx, maxy = bnd.bbox
    pts = []
    while len(pts) < n:
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if (x, y) in bnd:
            pts.append((x, y))
    return pts


def grid_offset_m(nat_path, reg_path) -> float:
    """How far apart the two grids are, in metres, along x."""
    import rasterio
    with rasterio.open(nat_path) as a, rasterio.open(reg_path) as b:
        res = abs(a.transform.a)
        cells = (b.transform.c - a.transform.c) / res
    return (cells % 1.0) * res


def check(label, nat, reg, bands, categorical, pts) -> list[tuple]:
    import rasterio
    nat_p, reg_p = INT / nat, INT / reg
    if not nat_p.exists() or not reg_p.exists():
        print(f"{label:10s} SKIP (missing {nat if not nat_p.exists() else reg})")
        return []
    off = grid_offset_m(nat_p, reg_p)
    shifted = [(x + off, y) for x, y in pts]
    rows = []
    with rasterio.open(nat_p) as rn, rasterio.open(reg_p) as rr:
        for idx, name in bands:
            vn = np.array([v[0] for v in rn.sample(pts, indexes=[idx])], float)
            vr = np.array([v[0] for v in rr.sample(pts, indexes=[idx])], float)
            vs = np.array([v[0] for v in rr.sample(shifted, indexes=[idx])], float)
            nod_n, nod_r = rn.nodata, rr.nodata
            def good(a, nod):
                m = np.isfinite(a)
                if nod is not None and np.isfinite(nod):
                    m &= ~np.isclose(a, nod)
                return m
            ok = good(vn, nod_n) & good(vr, nod_r)
            ok2 = good(vs, nod_r) & good(vr, nod_r)
            if ok.sum() < 30 or ok2.sum() < 30:
                print(f"{label:10s}{name:14s} too few comparable points")
                continue
            if categorical:
                # Agreement rate, not correlation: a class code is a label.
                m1 = float((vn[ok] == vr[ok]).mean())
                m2 = float((vs[ok2] == vr[ok2]).mean())
                metric = "agree"
            else:
                m1 = float(np.corrcoef(vn[ok], vr[ok])[0, 1])
                m2 = float(np.corrcoef(vs[ok2], vr[ok2])[0, 1])
                metric = "corr"
            verdict = "ok" if m1 >= m2 else "WORSE THAN THE OFFSET ALONE"
            rows.append((label, name, metric, m1, m2, off, verdict))
    return rows


def main() -> int:
    pts = agder_points(N_POINTS, SEED)
    print(f"{len(pts):,} random points inside Agder, seed {SEED}\n")
    print(f"{'layer':10s}{'band':14s}{'metric':7s}{'national':>10s}"
          f"{'offset only':>13s}{'offset m':>10s}   verdict")
    bad = []
    for spec in LAYERS + SR16:
        for row in check(*spec, pts):
            label, name, metric, m1, m2, off, verdict = row
            print(f"{label:10s}{name:14s}{metric:7s}{m1:>10.4f}{m2:>13.4f}"
                  f"{off:>10.1f}   {verdict}")
            if verdict != "ok":
                bad.append(row)

    print()
    if bad:
        print(f"{len(bad)} band(s) agree with Agder WORSE than the grid offset "
              f"alone explains. That is not a grid artefact; do not fit on them.")
        for label, name, *_ in bad:
            print(f"  {label}/{name}")
        return 1
    print("every national band beats its own offset baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
