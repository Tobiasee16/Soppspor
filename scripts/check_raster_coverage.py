"""Check that every presence and background point lands on a usable feature cell.

This exists because of a bug it would have caught. Masking sea out of the DTM
left every land cell touching water with a NaN slope - Horn's 3x3 kernel sums
its neighbours, so one masked neighbour poisons the centre. Against Agder's
coastline that is a one-cell halo of missing data following the whole shore,
and it silently deleted 28 of 904 thinned presence records.

The deletion count was not the problem. The problem was that it was UNEVEN
ACROSS SPECIES - 1.2% of C. tubaeformis against 6.9% of A. muscaria - and the
entire Phase 0 design rests on comparing those three species against each
other. A defect that removes coastal records preferentially from the coastal
species tilts the ordering test, and it does so invisibly: every number still
computes, nothing errors, the AUCs just quietly mean something else.

So this check does not merely count losses. It fails when losses differ across
species by more than a tolerance, because an even loss is an inconvenience and
an uneven one is a bias.

    python scripts/check_raster_coverage.py            # exits non-zero on failure
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import rasterio
import yaml

from src.features.background import draw_background, null_and_background
from src.features.thin import thin
from src.geometry import Boundary
from src.ingest import artskart

ROOT = pathlib.Path(__file__).resolve().parents[1]
RASTER = ROOT / "data" / "interim" / "terrain_agder_32m.tif"
CACHE = ROOT / "data" / "interim" / "target_group_pool_agder.json"
RAW = ROOT / "data" / "raw" / "artskart"
OUT = ROOT / "data" / "processed" / "raster_coverage.md"

AGDER_NAMES = {"Agder", "Aust-Agder", "Vest-Agder"}

# How much the per-species loss rates may differ before this is a bias rather
# than an inconvenience. 2 percentage points: the halo defect spread them by
# 5.7, and anything approaching that scale distorts a three-way comparison
# whose whole purpose is to rank species against each other.
SPREAD_TOLERANCE_PP = 2.0

# Above this, even an even loss is too much data thrown away to proceed
# quietly, whatever the cause.
MAX_LOSS_PCT = 5.0


def sample(ds, coords: list[tuple[float, float]]):
    """Band values per point, plus a flag for points outside the raster footprint.

    The two cases are kept apart deliberately. A point outside the footprint
    means the raster does not cover the study area; a point inside it whose
    cells are NaN means the mask removed it. They have different fixes, and
    collapsing them into one 'missing' count hides which one you have.
    """
    if not coords:
        return np.empty((0, ds.count)), np.zeros(0, dtype=bool)
    vals = np.full((len(coords), ds.count), np.nan)
    left, bottom, right, top = ds.bounds
    within = np.array([left <= e < right and bottom < n <= top for e, n in coords])
    idx = np.flatnonzero(within)
    if idx.size:
        vals[idx] = np.array(list(ds.sample([coords[i] for i in idx])), dtype="float64")
    return vals, ~within


def report(name: str, coords: list[tuple[float, float]], ds) -> dict:
    if not coords:
        return {"name": name, "n": 0, "lost": 0, "pct": 0.0,
                "outside": 0, "masked": 0, "partial": 0}
    vals, outside = sample(ds, coords)
    nan = np.isnan(vals)
    all_nan = nan.all(axis=1)
    any_nan = nan.any(axis=1)
    # Some bands present and others missing is the halo signature: the cell
    # exists and has an elevation, but a derivative could not be computed on
    # it. A whole-row NaN inside the footprint is the mask doing its job.
    partial = int((any_nan & ~all_nan).sum())
    return {
        "name": name,
        "n": len(coords),
        "lost": int(any_nan.sum()),
        "pct": 100.0 * int(any_nan.sum()) / len(coords),
        "outside": int(outside.sum()),
        "masked": int((all_nan & ~outside).sum()),
        "partial": partial,
    }


def main() -> int:
    if not RASTER.exists():
        raise SystemExit(f"missing {RASTER} - run scripts/build_terrain.py first")

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    cv_slot = area["spatial_cv"]["cv_extent"]
    fc = json.loads((ROOT / area[cv_slot]["boundary_file"]).read_text(encoding="utf-8"))
    boundary = Boundary(fc["features"][0]["geometry"], name=area[cv_slot]["name"])

    if not CACHE.exists():
        raise SystemExit(f"missing {CACHE} - run scripts/report_background.py first")
    cached = json.loads(CACHE.read_text(encoding="utf-8"))
    pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
            for e, n, pr, i in cached["pool"]]

    species_rows = []
    with rasterio.open(RASTER) as ds:
        bands = [ds.descriptions[i] or f"band{i + 1}" for i in range(ds.count)]
        for sp in spp["species"]:
            path = RAW / f"{sp['key']}.json"
            obs = [o for o in artskart.load(path) if o.get("County") in AGDER_NAMES]
            pts = artskart.usable_points(obs)
            pts = [p for p in pts if (p["east"], p["north"]) in boundary]
            pts = [p for p in pts
                   if p["precision_m"] is not None and p["precision_m"] <= max_prec]
            coords = [(p["east"], p["north"]) for p in thin(pts, thin_m)]
            species_rows.append(report(sp["scientific_name"], coords, ds))

        # Background and the null control face the same raster and must be
        # checked on it too: a background point on a NaN cell is a background
        # point the model never sees, which quietly reshapes the comparison
        # set the AUC is measured against.
        n_null = max(r["n"] for r in species_rows)
        null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
        other_rows = [
            report("target-group background", bg_set.coords, ds),
            report("synthetic null (NOT REAL DATA)", null_set.coords, ds),
        ]

        # Which band is doing the losing. If the answer is 'twi' alone, the
        # cause is routing; if it is every band at once, the cause is the mask.
        per_band = {}
        pvals, _ = sample(ds, bg_set.coords)
        for i, b in enumerate(bands):
            per_band[b] = int(np.isnan(pvals[:, i]).sum())

    rows = species_rows + other_rows
    pcts = [r["pct"] for r in species_rows]
    spread = max(pcts) - min(pcts)
    worst = max(rows, key=lambda r: r["pct"])

    w = []
    a = w.append
    a("# Feature raster coverage of the point sets\n")
    a("Generated by `scripts/check_raster_coverage.py`.\n")
    a(f"Raster: `{RASTER.relative_to(ROOT)}`, bands: {', '.join(bands)}.\n")
    a("A point on a NaN cell is a point the model never sees. Counted per set,")
    a("because an even loss is an inconvenience and an uneven one is a bias in")
    a("the ordering test.\n")
    a("| set | points | lost | % | outside raster | masked cell | partial |")
    a("|---|---|---|---|---|---|---|")
    for r in rows:
        a(f"| {r['name']} | {r['n']:,} | {r['lost']} | {r['pct']:.1f}% | "
          f"{r['outside']} | {r['masked']} | {r['partial']} |")
    a("")
    a(f"Spread across the three species: **{spread:.1f} percentage points** "
      f"(tolerance {SPREAD_TOLERANCE_PP:.1f}).\n")
    a("Losses by band, measured on the background set:\n")
    a("| band | points with no value |")
    a("|---|---|")
    for b, n in per_band.items():
        a(f"| {b} | {n} |")
    a("")

    ok = True
    if spread > SPREAD_TOLERANCE_PP:
        ok = False
        a(f"**FAIL** - the three species lose records at meaningfully different")
        a("rates. That is a bias in the comparison, not an inconvenience: the")
        a("ordering test compares these three against each other. Find the")
        a("cause before fitting anything.\n")
    if worst["pct"] > MAX_LOSS_PCT:
        ok = False
        a(f"**FAIL** - {worst['name']} loses {worst['pct']:.1f}% of its points, "
          f"above the {MAX_LOSS_PCT:.0f}% ceiling.\n")
    if ok:
        a("**PASS** - losses are small and even across species.\n")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(w), encoding="utf-8")
    print("\n".join(w))
    print(f"written: {OUT.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
