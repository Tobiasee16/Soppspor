"""Download the Agder DTM and derive slope, aspect, curvature and TWI.

One raster covers both extents. Kristiansand sits inside Agder, so a single
Agder-wide surface serves training and prediction, and - the part that actually
matters - catchments draining across the kommune boundary are routed correctly
instead of being cut off at it. A TWI computed on a Kristiansand-shaped tile
would invent a watershed divide along an administrative line.

Resolution is a deliberate compromise, stated rather than buried:

* Terrain derivatives are computed at 32 m over Agder. At 16 m the extent is
  178 million cells, and the priority-flood heap alone would not fit
  comfortably in memory.
* 32 m is a normal resolution for TWI in ecology. TWI is a catchment-scale
  index; it is not a 16 m quantity in the first place.
* **The same raster is used for training and prediction**, so there is no
  resolution mismatch between the two - which would have quietly broken the
  transferability test.

Sea is masked in two steps, because one is not enough. Clipping to the
administrative polygon is not sufficient: a fylke boundary runs to the
territorial baseline, not the coastline, and leaves 6,576 km2 of sea inside the
mask - the remainder after removing it is 16,481 km2 against Agder's true land
area of 16,434 km2. Nor is elevation sufficient on its own, since a coastal
meadow is also near zero. Sea is therefore defined as near sea level AND
connected to the map edge, which keeps an inland lake at sea level as land.

Getting this wrong is not subtle in effect but is invisible in the code: left
in, the sea is one flat basin with an enormous upslope area and no slope, so
TWI reads maximal across all of it - median 11.07 there against 6.19 on real
terrain. The suitability map would have shown the sea as the wettest habitat in
the county.

    python scripts/build_terrain.py            # skips work already done
    python scripts/build_terrain.py --refresh  # re-downloads and recomputes
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import rasterio
import yaml
from rasterio.features import geometry_mask

from src.features.terrain import (
    curvature,
    eastness,
    fill_depressions,
    flow_accumulation,
    northness,
    sea_mask,
    slope_aspect,
)
from src.geometry import Boundary
from src.ingest.dtm import download_dtm

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
RES_M = 32.0
DTM_PATH = INTERIM / "dtm_agder_32m.tif"
OUT_PATH = INTERIM / "terrain_agder_32m.tif"
BANDS = ["elevation", "slope_deg", "northness", "eastness", "curvature", "twi"]


def main():
    refresh = "--refresh" in sys.argv
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    slot = area["spatial_cv"]["cv_extent"]
    fc = json.loads((ROOT / area[slot]["boundary_file"]).read_text(encoding="utf-8"))
    geom = fc["features"][0]["geometry"]
    boundary = Boundary(geom, name=area[slot]["name"])

    if refresh or not DTM_PATH.exists():
        print(f"downloading {boundary.name} DTM at {RES_M:.0f} m ...", flush=True)
        t0 = time.time()

        def prog(i, n, bb):
            print(f"  tile {i}/{n}  {time.time() - t0:.0f}s", flush=True)

        meta = download_dtm(boundary.bbox, DTM_PATH, resolution_m=RES_M, progress=prog)
        print(f"  {meta['width']}x{meta['height']} in {time.time() - t0:.0f}s", flush=True)
    else:
        print(f"using cached {DTM_PATH.relative_to(ROOT)} (--refresh to redo)")

    with rasterio.open(DTM_PATH) as ds:
        dem = ds.read(1).astype("float64")
        profile = ds.profile
        transform = ds.transform
        shape = ds.shape

    print(f"masking everything outside {boundary.name} ...", flush=True)
    outside = geometry_mask([geom], out_shape=shape, transform=transform, invert=False)
    dem[outside] = np.nan
    inside = int(np.isfinite(dem).sum())

    # The administrative polygon runs to the territorial baseline, not the
    # coastline, so clipping to it leaves thousands of square kilometres of sea
    # inside the mask. Left in, that becomes one flat basin with a vast upslope
    # area and no slope, and TWI reads as maximal across the whole of it.
    print("masking sea (near sea level and connected to the edge) ...", flush=True)
    sea = sea_mask(dem)
    dem[sea] = np.nan
    land = int(np.isfinite(dem).sum())
    print(f"  inside polygon {inside:,} cells = {inside * RES_M ** 2 / 1e6:,.0f} km2",
          flush=True)
    print(f"  sea removed    {int(sea.sum()):,} cells = {sea.sum() * RES_M ** 2 / 1e6:,.0f} km2",
          flush=True)
    print(f"  land           {land:,} cells = {land * RES_M ** 2 / 1e6:,.0f} km2 "
          f"(Agder land area is about 16,434 km2)", flush=True)
    if land == 0:
        raise SystemExit("no land cells inside the boundary - check the mask orientation")

    t0 = time.time()
    print("slope, aspect, curvature ...", flush=True)
    slope, aspect = slope_aspect(dem, RES_M)
    curv = curvature(dem, RES_M)
    north = northness(aspect, slope)
    east = eastness(aspect, slope)

    print("filling depressions (priority-flood) ...", flush=True)
    filled = fill_depressions(dem)
    print(f"  done in {time.time() - t0:.0f}s; mean raise "
          f"{np.nansum(filled - dem) / max(land, 1):.3f} m", flush=True)

    print("flow accumulation ...", flush=True)
    t1 = time.time()
    acc = flow_accumulation(filled, RES_M)
    print(f"  done in {time.time() - t1:.0f}s; max upslope cells "
          f"{np.nanmax(acc):,.0f} = {np.nanmax(acc) * RES_M ** 2 / 1e6:,.0f} km2", flush=True)

    # TWI from the FILLED surface's slope, not the raw one: routing and slope
    # must describe the same surface or the index is internally inconsistent.
    fslope, _ = slope_aspect(filled, RES_M)
    tan_beta = np.tan(np.maximum(fslope, 0.001))
    with np.errstate(invalid="ignore", divide="ignore"):
        twi_arr = np.log(acc * RES_M / tan_beta)
    twi_arr[~np.isfinite(dem)] = np.nan

    stack = np.stack([
        dem,
        np.degrees(slope),
        north,
        east,
        curv,
        twi_arr,
    ]).astype("float32")

    profile.update(count=len(BANDS), dtype="float32", nodata=np.nan,
                   compress="deflate", predictor=2, tiled=True,
                   blockxsize=256, blockysize=256)
    with rasterio.open(OUT_PATH, "w", **profile) as out:
        out.write(stack)
        for i, name in enumerate(BANDS, start=1):
            out.set_band_description(i, name)

    print()
    print(f"written: {OUT_PATH.relative_to(ROOT)}  ({OUT_PATH.stat().st_size / 1e6:.0f} MB)")
    print(f"{len(BANDS)} bands at {RES_M:.0f} m: {', '.join(BANDS)}")
    print()
    print("| band | min | median | max |")
    print("|---|---|---|---|")
    for name, band in zip(BANDS, stack):
        finite = band[np.isfinite(band)]
        if finite.size:
            print(f"| {name} | {finite.min():.2f} | {np.median(finite):.2f} | {finite.max():.2f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
