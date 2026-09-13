"""How much of Norway's fungal recording effort falls in each fylke.

    python scripts/effort_by_fylke.py

Writes `data/processed/effort_by_fylke.json`.

This is the honest baseline for judging whether a species is scarce in a region
because nobody looked there or because it does not grow there. The target-group
background IS recording effort - that is the whole reason it corrects the
sampling bias in the model - so the share of it falling in a region is the
share of looking that happened there.

Without this, the only available baseline is the median across our own eight
species, which is circular: it assumes our species are collectively typical of
fungal recording, which is the thing being tested.

Takes a couple of minutes over 1.3 million records, so the answer is cached.
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

BG = ROOT / "data" / "raw" / "artskart" / "target_group_sopper_norge.jsonl"
OUT = ROOT / "data" / "processed" / "effort_by_fylke.json"
FYLKER = ROOT / "data" / "interim" / "fylker"


def main() -> int:
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import shape
    from src.ingest import artskart

    if not BG.exists():
        raise SystemExit(f"missing {BG} - run scripts/fetch_national_background.py")

    t0 = time.time()
    pts = artskart.usable_points(artskart.iter_jsonl(BG))
    print(f"{len(pts):,} usable background points in {time.time()-t0:.0f}s")

    gdf = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy([p["east"] for p in pts],
                                    [p["north"] for p in pts]),
        crs="EPSG:25833")

    frames = []
    for f in sorted(FYLKER.glob("boundary_*_25833.geojson")):
        fc = json.loads(f.read_text(encoding="utf-8"))
        g = gpd.GeoDataFrame(geometry=[shape(fc["features"][0]["geometry"])],
                             crs="EPSG:25833")
        g["fylke"] = f.stem.replace("boundary_", "").replace("_25833", "")
        frames.append(g)
    fylker = pd.concat(frames, ignore_index=True)

    hit = gpd.sjoin(gdf, fylker, how="inner", predicate="within")
    counts = hit["fylke"].value_counts().to_dict()
    total = int(sum(counts.values()))

    payload = {
        "source": str(BG.relative_to(ROOT)).replace(chr(92), "/"),
        "usable_points": len(pts),
        "assigned_to_a_fylke": total,
        # Points outside every mainland fylke: Svalbard, Jan Mayen and a little
        # offshore. Reported rather than silently dropped.
        "outside_mainland": len(pts) - total,
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "share": {k: v / total for k, v in
                  sorted(counts.items(), key=lambda kv: -kv[1])},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    print(f"{'fylke':22s}{'records':>12s}{'share':>9s}")
    for k, v in payload["counts"].items():
        print(f"{k:22s}{v:>12,}{100*v/total:>8.2f}%")
    print(f"\noutside every mainland fylke: {payload['outside_mainland']:,}")
    print(f"-> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
