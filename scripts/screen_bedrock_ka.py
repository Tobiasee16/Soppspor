"""Does bedrock lime content separate any of the eight species?

    python scripts/screen_bedrock_ka.py [--block-m 25000] [--no-forest-filter]

This is a **screen**, run before contemplating a national refit, and it is the
script behind FINDINGS section 30. It fits nothing. It samples NGU's NiN
kalkinnhold (KA) class at each species' thinned precise presences and at the
target-group background, and reports how far apart they sit.

The comparison is block-matched, never national, for the reason set out at
length in `src/ingest/berggrunn.py`: done nationally it reports the single
largest effect in the whole table for *Craterellus cornucopioides*, pointing the
wrong way, and that effect is entirely latitude.

Both sides are also restricted to AR50 forest by default. All eight species are
forest fungi and the background is every fungus anyone recorded, alpine and mire
included, so without that restriction a shared shift could just be "richer rock
carries more forest". It does not remove the shift; it slightly strengthens it,
which is the finding rather than a detail.

Requires the national layers already on disk: the Kalkinnhold geodatabase under
`data/interim/berggrunn/`, `data/interim/ar50_norge_32m.tif`, and
`data/interim/target_group_pool_norge.json`.
"""
from __future__ import annotations

import argparse
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

from src.ingest import artskart
from src.ingest.berggrunn import (DEFAULT_BLOCK_M, KALKINNHOLD_LAYER, KA_LABELS,
                                  MIN_BACKGROUND_PER_BLOCK, block_id)
from src.features.thin import thin

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "artskart"
GDB = ROOT / "data" / "interim" / "berggrunn" / "KalkinnholdBerggrunn.gdb"
AR50 = ROOT / "data" / "interim" / "ar50_norge_32m.tif"
POOL = ROOT / "data" / "interim" / "target_group_pool_norge.json"
OUT_MD = ROOT / "data" / "processed" / "berggrunn_ka.md"

MAX_PRECISION_M = 250
THIN_M = 1000
AR50_FOREST = 30
BOOTSTRAPS = 600
SEED = 20260916

# Ordered as FINDINGS 30d reports them: the two nominated as most likely to show
# a lime preference first, so a reader sees the registered guess before the rest.
SPECIES = [
    ("craterellus_cornucopioides", "svart trompetsopp"),
    ("hydnum_repandum", "blek piggsopp"),
    ("craterellus_tubaeformis", "traktkantarell"),
    ("cantharellus_cibarius", "kantarell"),
    ("boletus_edulis", "steinsopp"),
    ("leccinum_versipelle", "rødskrubb"),
    ("suillus_luteus", "smørsopp"),
    ("amanita_muscaria", "rød fluesopp"),
]


def load_polygons():
    import geopandas as gpd
    if not GDB.exists():
        raise SystemExit(
            f"{GDB} is not on disk. It is a 27 MB download from NGU; see the "
            f"'NGU bedrock and its lime content' section of data/SOURCES.md for "
            f"the UUID and the order route.")
    g = gpd.read_file(GDB, layer=KALKINNHOLD_LAYER)
    # BergartGrense rows are boundary features carrying no lime class.
    g = g[g.objekttype == "BergartFlate"].to_crs("EPSG:25833")
    g = g[["kalkinnhold_hovedbergart", "geometry"]].rename(
        columns={"kalkinnhold_hovedbergart": "ka"})
    return g[g.ka.notna()]


def sample(points, polygons, ar, block_m):
    """`(east, north, ka, ar50)` for every point that lands on mapped bedrock."""
    import geopandas as gpd
    from shapely.geometry import Point
    gdf = gpd.GeoDataFrame(
        {"e": [p[0] for p in points], "n": [p[1] for p in points]},
        geometry=[Point(*p) for p in points], crs="EPSG:25833")
    j = gpd.sjoin(gdf, polygons, how="left", predicate="within")
    # A point on a shared polygon edge joins twice; keep one, deterministically.
    j = j[~j.index.duplicated(keep="first")].dropna(subset=["ka"])
    vals = np.fromiter((v[0] for v in ar.sample(zip(j.e, j.n))),
                       dtype=np.uint8, count=len(j))
    return np.column_stack([j.e.to_numpy(), j.n.to_numpy(),
                            j.ka.to_numpy().astype(float), vals.astype(float)])


def presences(key, polygons, ar, block_m):
    obs = artskart.load(RAW / f"{key}.json")
    pts = artskart.usable_points(obs)
    pts = [p for p in pts
           if p["precision_m"] is not None and p["precision_m"] <= MAX_PRECISION_M]
    pts = [(p["east"], p["north"]) for p in thin(pts, THIN_M)]
    return sample(pts, polygons, ar, block_m)


def block_stats(arr, block_m):
    """Per-block mean KA and count, as parallel arrays keyed by block id."""
    ids = np.array([block_id(e, n, block_m) for e, n, _, _ in arr], dtype=np.int64)
    uniq, inv = np.unique(ids, return_inverse=True)
    count = np.bincount(inv).astype(float)
    total = np.bincount(inv, weights=arr[:, 2])
    return uniq, total / count, count


def shift_with_ci(sp, bg, block_m, rng):
    """Block-matched shift plus a block-resampled 95% interval.

    The bootstrap resamples **blocks**, not records. Records inside a block are
    spatially dependent, so resampling them would treat one well-surveyed wood
    as many independent observations and return an interval far too narrow.
    """
    bg_id, bg_mean, bg_n = block_stats(bg, block_m)
    keep = bg_n >= MIN_BACKGROUND_PER_BLOCK
    bg_id, bg_mean = bg_id[keep], bg_mean[keep]

    sp_id, sp_mean, sp_n = block_stats(sp, block_m)
    usable = np.isin(sp_id, bg_id)
    if not usable.any():
        return float("nan"), float("nan"), float("nan"), 0, 0, float("nan")
    sp_id, sp_mean, sp_n = sp_id[usable], sp_mean[usable], sp_n[usable]
    expected = bg_mean[np.searchsorted(bg_id, sp_id)]

    observed = float(np.average(sp_mean, weights=sp_n)
                     - np.average(expected, weights=sp_n))
    idx = rng.integers(0, len(sp_id), size=(BOOTSTRAPS, len(sp_id)))
    draws = (np.average(sp_mean[idx], weights=sp_n[idx], axis=1)
             - np.average(expected[idx], weights=sp_n[idx], axis=1))
    lo, hi = (float(v) for v in np.percentile(draws, [2.5, 97.5]))
    own = float(np.average(sp_mean, weights=sp_n))
    return observed, lo, hi, int(sp_n.sum()), len(sp_id), own


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--block-m", type=float, default=DEFAULT_BLOCK_M,
                    help="matching block size in metres (default 25000)")
    ap.add_argument("--no-forest-filter", action="store_true",
                    help="do not restrict both sides to AR50 forest")
    args = ap.parse_args()

    import rasterio
    rng = np.random.default_rng(SEED)
    polygons = load_polygons()
    ar = rasterio.open(AR50)
    print(f"bedrock polygons: {len(polygons):,}", flush=True)

    pool = json.loads(POOL.read_text(encoding="utf-8"))
    bg_pts = [tuple(p[:2]) for side in ("training", "holdout")
              for p in pool["pools"][side]["points"]]
    bg = sample(bg_pts, polygons, ar, args.block_m)
    forest = bg[:, 3] == AR50_FOREST
    print(f"background: {len(bg):,} on mapped bedrock, "
          f"{forest.sum():,} of them AR50 forest ({100 * forest.mean():.0f}%)",
          flush=True)
    if not args.no_forest_filter:
        bg = bg[forest]

    rows = []
    for key, no_name in SPECIES:
        sp = presences(key, polygons, ar, args.block_m)
        if not args.no_forest_filter:
            sp = sp[sp[:, 3] == AR50_FOREST]
        rows.append((key, no_name) + shift_with_ci(sp, bg, args.block_m, rng))
        print(f"  screened {key}", flush=True)

    rows.sort(key=lambda r: -r[2])
    print()
    head = f"{'species':<30}{'n':>7}{'blocks':>8}{'own KA':>9}{'shift':>9}   95% CI"
    print(head)
    print("-" * len(head))
    for key, no_name, shift, lo, hi, n, blocks, own in rows:
        star = " *" if (lo > 0 or hi < 0) else ""
        print(f"{key:<30}{n:>7}{blocks:>8}{own:>9.3f}{shift:>+9.3f}   "
              f"[{lo:+.3f}, {hi:+.3f}]{star}")

    spread = max(r[2] for r in rows) - min(r[2] for r in rows)
    sd = float(bg[:, 2].std())
    print()
    print(f"spread across the eight species: {spread:.3f} KA units "
          f"({spread / sd:.2f} background SD)")
    print(f"background SD: {sd:.2f}")
    print()
    print("The spread is what a per-species model fitted against target-group")
    print("background could use. It is not the shared shift, which every species")
    print("has and which that comparison subtracts. See FINDINGS 30.")

    write_report(rows, spread, sd, args)
    print(f"\nwrote {OUT_MD.relative_to(ROOT)}")
    return 0


def write_report(rows, spread, sd, args):
    lines = [
        "# Bedrock lime content (NiN KA) against the eight species",
        "",
        "Generated by `scripts/screen_bedrock_ka.py`. **No model fitted.**",
        "",
        f"Block size {args.block_m:,.0f} m; "
        f"{'AR50 forest only' if not args.no_forest_filter else 'all land cover'}; "
        f"thinning {THIN_M} m; precision <= {MAX_PRECISION_M} m; "
        f"{BOOTSTRAPS} block bootstraps, seed {SEED}.",
        "",
        "| Species | n | blocks | own KA | shift vs background | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    for key, no_name, shift, lo, hi, n, blocks, own in rows:
        latin = key.replace("_", " ").capitalize()
        lines.append(f"| *{latin}* ({no_name}) | {n:,} | {blocks} "
                     f"| {own:.3f} | {shift:+.3f} | [{lo:+.3f}, {hi:+.3f}] |")
    lines += [
        "",
        f"Background SD {sd:.2f}. Spread across species **{spread:.3f}** "
        f"({spread / sd:.2f} SD).",
        "",
        "## What this does and does not say",
        "",
        "The shared shift is real: every species sits on somewhat richer bedrock",
        "than the fungal community recorded around it, and restricting both sides",
        "to forest strengthens rather than removes that. It is also invisible to",
        "every model in this project, because a per-species fit against",
        "target-group background can only use what distinguishes one species from",
        "the community, and the spread between species is about a tenth of a",
        "standard deviation.",
        "",
        "The KA scale is NGU's, on the NiN ecocline: "
        + ", ".join(f"{k} {v}" for k, v in sorted(KA_LABELS.items())) + ".",
        "",
        "Full reading, including the geographic confound that nearly produced a",
        "false finding: FINDINGS section 30.",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
