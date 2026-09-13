"""National terrain: elevation, slope, aspect and curvature at 32 m.

    python scripts/build_terrain_national.py            # resumes
    python scripts/build_terrain_national.py --refresh

Three things differ from `build_terrain.py`, and all three are consequences of
scale rather than preferences.

**The grid is aligned to SR16, not snapped to 32 m.** SR16's landsdekkende
mosaic starts at x = -99513.02, which is not a multiple of 16 let alone 32
(FINDINGS 17d). Snapping would leave terrain about 7 m out of step with the
forest data it is stacked against, on every cell of the country. Aligned, each
32 m terrain cell is exactly the 2x2 block of SR16 cells beneath it.

**Derivatives are computed in windows.** Norway at 32 m is 1.83 billion cells;
one float32 band is 7.3 GB against 15.7 GB of RAM. Each window is read with a
one-cell halo of real neighbouring data, so the result equals the whole-array
answer - asserted in tests/test_terrain_windowed.py.

**There is no sea mask, and TWI is absent.** These are the same decision. The
Agder build masks sea because Kartverket's DTM returns 0.0 over water rather
than nodata, so the sea is one flat basin with an enormous upslope area and TWI
reads maximal across all of it - it would have shown the sea as the wettest
habitat in the county. That argument is entirely about flow accumulation. With
TWI gone the mask has no job left: slope and curvature at a coastal cell are
more accurate computed against real sea-level neighbours than against a
locally-flat fill, prediction is masked to land regardless, and AR50 carries an
explicit sea class the model can use. The flood fill that built the mask would
also have needed the whole elevation array in memory, which is the same wall.

Whether TWI can be dropped at all is the ablation registered in FINDINGS
section 18. This script refuses to run until that question has an answer on
disk, because building a national stack that silently lacks a feature the Agder
stack has is exactly the kind of difference that survives into a result.
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

INTERIM = ROOT / "data" / "interim"
DTM_PATH = INTERIM / "dtm_norge_32m.tif"
OUT_PATH = INTERIM / "terrain_norge_32m.tif"
META_PATH = INTERIM / "terrain_norge_32m.meta.json"
ABLATION = ROOT / "data" / "processed" / "runs" / "model_results_no-twi.md"

RES_M = 32.0
# SR16's national mosaic origin, so a terrain cell is a whole block of SR16
# cells. Read from the raster rather than retyped, when it is present.
SR16_NATIONAL = INTERIM / "sr16_norge" / "SRRVOLMB.tif"
SR16_ORIGIN_FALLBACK = (-99513.02, 8000000.0)


def sr16_origin() -> tuple[float, float]:
    if SR16_NATIONAL.exists():
        import rasterio
        with rasterio.open(SR16_NATIONAL) as r:
            return (r.transform.c, r.transform.f)
    print(f"  WARNING: {SR16_NATIONAL.name} missing, using the recorded origin")
    return SR16_ORIGIN_FALLBACK


def main() -> int:
    import rasterio
    from src.ingest.dtm import download_dtm, grid_for
    from src.features.terrain import derivatives_windowed

    refresh = "--refresh" in sys.argv
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    bbox = tuple(area["national_training_extent"]["bbox_25833"])

    origin = sr16_origin()
    (minx, miny, maxx, maxy), w, h = grid_for(bbox, RES_M, origin=origin)
    print(f"grid   {w:,} x {h:,} at {RES_M:.0f} m = {w*h/1e9:.2f} billion cells")
    print(f"origin ({minx:,.2f}, {maxy:,.2f}) aligned to SR16 "
          f"({origin[0]:,.2f}, {origin[1]:,.2f})")
    off = ((minx - origin[0]) % 16.0, (origin[1] - maxy) % 16.0)
    assert off == (0.0, 0.0), f"terrain grid is {off} off the SR16 grid"
    print(f"       offset from the SR16 16 m grid: {off[0]:.2f} m, {off[1]:.2f} m\n")

    if not DTM_PATH.exists() or refresh:
        t0 = time.time()
        last = [0.0]

        def prog(done, total, box):
            now = time.time()
            if now - last[0] < 20 and done < total:
                return
            last[0] = now
            el = now - t0
            print(f"  tile {done}/{total}  {100*done/total:5.1f}%  "
                  f"{el/60:.0f} min elapsed, ~{el/done*(total-done)/60:.0f} min left",
                  flush=True)

        print("downloading the national DTM from Kartverket WCS ...", flush=True)
        meta = download_dtm(bbox, DTM_PATH, resolution_m=RES_M, progress=prog,
                            origin=origin)
        print(f"  DTM: {DTM_PATH.stat().st_size/1e9:.2f} GB in "
              f"{(time.time()-t0)/60:.0f} min")
    else:
        print(f"using cached {DTM_PATH.name} "
              f"({DTM_PATH.stat().st_size/1e9:.2f} GB)")

    if not ABLATION.exists():
        print(f"\nSTOPPING before derivatives: {ABLATION.relative_to(ROOT)} does "
              f"not exist yet.\nThe national stack drops TWI, and FINDINGS "
              f"section 18 registered an ablation to\nmeasure what that costs "
              f"before it is done rather than after. Run:\n"
              f"    python scripts/run_models.py --no-twi")
        return 2

    if OUT_PATH.exists() and not refresh:
        print(f"\nusing cached {OUT_PATH.name}")
        return 0

    t0 = time.time()
    last = [0.0]

    def dprog(done, total):
        now = time.time()
        if now - last[0] < 20 and done < total:
            return
        last[0] = now
        el = now - t0
        print(f"  block {done}/{total}  {100*done/total:5.1f}%  "
              f"~{el/done*(total-done)/60:.0f} min left", flush=True)

    print("\ncomputing slope, aspect and curvature in windows ...", flush=True)
    with rasterio.open(DTM_PATH) as src:
        info = derivatives_windowed(src, OUT_PATH, RES_M, block=4096, progress=dprog)
    info.update({"resolution_m": RES_M, "origin": list(origin),
                 "bbox": [minx, miny, maxx, maxy],
                 "sea_masked": False,
                 "twi": False,
                 "why_no_twi": "FINDINGS section 18; flow accumulation needs the "
                               "whole surface and Norway at 32 m does not fit",
                 "seconds": round(time.time() - t0)})
    META_PATH.write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(f"\n-> {OUT_PATH.relative_to(ROOT)} "
          f"({OUT_PATH.stat().st_size/1e9:.2f} GB in {(time.time()-t0)/60:.0f} min)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
