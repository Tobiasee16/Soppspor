"""The national target-group background pool, thinned once and cached.

    python scripts/build_background_pool.py

Writes `data/interim/target_group_pool_norge.json`.

The Agder pool is built inline by `run_models.py`, which filters 82,129 records
through a Python point-in-polygon against one boundary. Nationally that is
1.3 million records against fifteen boundaries, and pure-Python PIP is the
wrong tool by two orders of magnitude - so the fylke label is assigned once
here, with a spatial index, and cached.

The treatment has to match what the presences get, or the difference between
the two becomes a signal in itself and the bias correction turns into a bias.
So: same precision filter, same thinning distance, same order of operations -
extent first, then precision, then thinning. Thinning before splitting by
extent would let a 1 km cell straddling a fylke border be resolved differently
than it is for the presences.

Records keep their fylke so that the training extent (fourteen fylker) and the
held-out extent (Nordland) can both be cut from one cached file, rather than
being two separate hour-long passes that could silently drift apart.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

BG = ROOT / "data" / "raw" / "artskart" / "target_group_sopper_norge.jsonl"
OUT = ROOT / "data" / "interim" / "target_group_pool_norge.json"
FYLKER = ROOT / "data" / "interim" / "fylker"


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import shape

    from src.features.thin import thin
    from src.ingest import artskart

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    excluded = str(area["national_training_extent"]["exclude_code"])
    holdout = area["national_holdout_extent"]["name"].split()[0].lower()

    if not BG.exists():
        raise SystemExit(f"missing {BG} - run scripts/fetch_national_background.py")

    t0 = time.time()
    pts = artskart.usable_points(artskart.iter_jsonl(BG))
    print(f"{len(pts):,} usable target-group points in {time.time()-t0:.0f}s")

    df = pd.DataFrame({
        "east": [p["east"] for p in pts],
        "north": [p["north"] for p in pts],
        "precision_m": [p["precision_m"] for p in pts],
        "id": [p.get("id") for p in pts],
    })
    g = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df.east, df.north),
                         crs="EPSG:25833")

    frames = []
    for f in sorted(FYLKER.glob("boundary_*_25833.geojson")):
        fc = json.loads(f.read_text(encoding="utf-8"))
        gg = gpd.GeoDataFrame(geometry=[shape(fc["features"][0]["geometry"])],
                              crs="EPSG:25833")
        gg["fylke"] = f.stem.replace("boundary_", "").replace("_25833", "")
        frames.append(gg)
    hit = gpd.sjoin(g, pd.concat(frames, ignore_index=True), how="inner",
                    predicate="within").drop_duplicates(subset=["east", "north", "id"])
    print(f"placed in a fylke: {len(hit):,}")

    out = {"source": str(BG.relative_to(ROOT)).replace(chr(92), "/"),
           "max_precision_m": max_prec, "thin_m": thin_m,
           "usable_points": len(pts), "placed": int(len(hit)), "pools": {}}

    for label, keep in (("training", lambda s: s != holdout),
                        ("holdout", lambda s: s == holdout)):
        sub = hit[hit["fylke"].map(keep)]
        # Precision AFTER extent and BEFORE thinning, matching how the
        # presences are treated in run_models.
        prec = sub[sub["precision_m"].notna() & (sub["precision_m"] <= max_prec)]
        pool = thin([{"east": r.east, "north": r.north,
                      "precision_m": r.precision_m, "id": r.id}
                     for r in prec.itertuples()], thin_m)
        out["pools"][label] = {
            "fylker": sorted(sub["fylke"].unique().tolist()),
            "in_extent": int(len(sub)),
            "precise": int(len(prec)),
            "thinned": len(pool),
            "points": [[round(p["east"], 1), round(p["north"], 1)] for p in pool],
        }
        print(f"{label:9s} {len(sub):>9,} in extent  {len(prec):>9,} precise  "
              f"{len(pool):>8,} thinned to {thin_m:.0f} m")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding="utf-8")
    print(f"\n-> {OUT.relative_to(ROOT)}  "
          f"({OUT.stat().st_size/1e6:.1f} MB, {time.time()-t0:.0f}s total)")
    print(f"excluded fylke code {excluded} from training: "
          f"{out['pools']['holdout']['fylker']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
