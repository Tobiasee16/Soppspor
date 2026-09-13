"""Build the autumn-rainfall climatology from seNorge_2018.

The window is not chosen here. It is read from `config/climate.yaml`, which
`scripts/derive_climate_window.py` wrote once from the observed phenology of
the three species' own records and now refuses to silently rewrite. This
script only fetches what that registration says to fetch.

Output is at the native 1 km, not resampled to the 32 m terrain grid. Storing
a 1 km field on a 32 m grid would make a raster thirty times larger that
implies a precision the data does not have; resampling happens at the point of
use instead, where it is visible.

Two extra things come out of the same download:

* **Interannual spread.** The standard deviation of the 30 seasonal totals is
  kept alongside the mean. CLAUDE.md asks for uncertainty to be propagated
  rather than hidden, and "700 mm every year" is a much stronger statement
  about a place than "700 mm on average, plus or minus 250".
* **A sensitivity check.** FINDINGS section 6 claims the exact window matters
  little at climatological scale. That is a testable claim, so it is tested:
  windows shifted two weeks earlier and later are accumulated from the same
  daily data and correlated against the registered one. The extra cost is the
  wider span, not a second download.

    python scripts/build_climate.py            # Agder; skips work already done
    python scripts/build_climate.py --refresh  # re-downloads
    python scripts/build_climate.py --national # all of Norway, about 14 min

The window registered in config/climate.yaml is used unchanged nationally. It
was derived from the phenology of the three species' own records in Agder, and
re-deriving it per region would make the climate feature mean something
different in each place - which is the opposite of what a transferability test
needs. Whether an Agder-derived fruiting window is right for Finnmark is a real
question, and it is one for the writeup rather than something to paper over by
quietly fitting a second window.
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
from rasterio.transform import from_origin

from src.ingest import senorge

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
OUT_PATHS = {False: INTERIM / "autumn_rain_agder_1km.tif",
             True: INTERIM / "autumn_rain_norge_1km.tif"}
OUT_PATH = OUT_PATHS[False]      # rebound in main() when --national is given
REPORT = ROOT / "data" / "processed" / "climate_raster.md"

# Two weeks either side. Wide enough that a real sensitivity would show, narrow
# enough to stay inside a plausible reading of "the fruiting season".
SHIFT_DAYS = 14


def main() -> int:
    global OUT_PATH
    national = "--national" in sys.argv
    OUT_PATH = OUT_PATHS[national]
    reg = yaml.safe_load((ROOT / "config" / "climate.yaml").read_text(encoding="utf-8"))
    ar = reg["autumn_rain"]
    lo, hi = int(ar["doy_from"]), int(ar["doy_to"])
    years = range(int(ar["year_from"]), int(ar["year_to"]) + 1)

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    if national:
        # The FULL national bbox, all fifteen fylker including the held-out
        # one. Climate is a predictor, not a training set: the model has to be
        # able to score Nordland even though it never trains there, and a
        # climate grid stopping at the training boundary would leave the
        # transfer test with no rainfall to read.
        bbox = tuple(area["national_training_extent"]["bbox_25833"])
    else:
        slot = area["spatial_cv"]["cv_extent"]
        fc = json.loads((ROOT / area[slot]["boundary_file"]).read_text(encoding="utf-8"))
        geom = fc["features"][0]["geometry"]
        coords = ([c for r in geom["coordinates"] for c in r]
                  if geom["type"] == "Polygon"
                  else [c for p in geom["coordinates"] for r in p for c in r])
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    w = senorge.window_for(bbox)

    variants = {
        "registered": (lo, hi),
        "earlier": (lo - SHIFT_DAYS, hi - SHIFT_DAYS),
        "later": (lo + SHIFT_DAYS, hi + SHIFT_DAYS),
    }

    print(f"window {w.width} x {w.height} cells at 1 km, "
          f"NW corner ({w.west:,.0f}, {w.north:,.0f})")
    print(f"registered window: day {lo}-{hi}, years {years.start}-{years.stop - 1}")
    print(f"fetching day {min(v[0] for v in variants.values())}-"
          f"{max(v[1] for v in variants.values())} to cover the "
          f"+/-{SHIFT_DAYS} day sensitivity variants too\n", flush=True)

    if OUT_PATH.exists() and "--refresh" not in sys.argv:
        print(f"using cached {OUT_PATH.relative_to(ROOT)} (--refresh to redo)")
        with rasterio.open(OUT_PATH) as ds:
            mean = ds.read(1)
            sd = ds.read(2)
        results = None
    else:
        t0 = time.time()

        def prog(i, n, year):
            el = time.time() - t0
            print(f"  {year}  {i}/{n}  {el:.0f}s elapsed, "
                  f"~{el / i * (n - i):.0f}s left", flush=True)

        results = senorge.climatology_variants(years, variants, w, "rr", progress=prog)
        mean = results["registered"]["mean"]
        sd = results["registered"]["sd"]

        transform = from_origin(w.west, w.north, senorge.STEP, senorge.STEP)
        profile = {
            "driver": "GTiff", "width": w.width, "height": w.height, "count": 2,
            "dtype": "float32", "crs": "EPSG:25833", "transform": transform,
            "nodata": np.nan, "compress": "deflate", "predictor": 2, "tiled": True,
        }
        # A partial write must not be left behind looking like a finished
        # raster: CLAUDE.md forbids anything that could silently reach a model.
        tmp = OUT_PATH.with_suffix(".partial.tif")
        try:
            with rasterio.open(tmp, "w", **profile) as out:
                out.write(mean.astype("float32"), 1)
                out.write(sd.astype("float32"), 2)
                out.set_band_description(1, "autumn_rain_mm")
                out.set_band_description(2, "autumn_rain_sd_mm")
            tmp.replace(OUT_PATH)
        finally:
            tmp.unlink(missing_ok=True)
        print(f"\nwritten: {OUT_PATH.relative_to(ROOT)} in {time.time() - t0:.0f}s")

    finite = np.isfinite(mean)
    lines = []
    a = lines.append
    a("# Autumn rainfall climatology\n")
    a("Generated by `scripts/build_climate.py`.\n")
    a(f"`{OUT_PATH.relative_to(ROOT)}` - {w.width} x {w.height} at 1 km, EPSG:25833.\n")
    a(f"Sum of seNorge_2018 `rr` over day {lo}-{hi}, meaned across "
      f"{years.start}-{years.stop - 1}.\n")
    a("| quantity | min | median | max |")
    a("|---|---|---|---|")
    a(f"| mean seasonal total (mm) | {np.nanmin(mean):.0f} | "
      f"{np.nanmedian(mean[finite]):.0f} | {np.nanmax(mean):.0f} |")
    a(f"| interannual sd (mm) | {np.nanmin(sd):.0f} | "
      f"{np.nanmedian(sd[finite]):.0f} | {np.nanmax(sd):.0f} |")
    a("")
    a(f"{int(finite.sum()):,} of {mean.size:,} cells carry data; the rest are "
      "outside Norway.\n")
    cv = np.nanmedian(sd[finite] / np.maximum(mean[finite], 1e-9))
    a(f"Median interannual coefficient of variation is {cv:.2f}. A place with a")
    a(f"{np.nanmedian(mean[finite]):.0f} mm mean therefore varies by roughly")
    a(f"+/-{np.nanmedian(sd[finite]):.0f} mm from year to year. The mean is a")
    a("statement about the long-run climate of a cell, not about any autumn a")
    a("mushroom picker will actually walk into.\n")

    if results is not None:
        a("## Sensitivity to the window\n")
        a(f"FINDINGS section 6 claims the exact window matters little at")
        a("climatological scale. Testing it, against windows shifted "
          f"{SHIFT_DAYS} days:\n")
        a("| window | days | median (mm) | correlation with registered |")
        a("|---|---|---|---|")
        base = results["registered"]["mean"][finite]
        for key, (d0, d1) in variants.items():
            m = results[key]["mean"]
            r = float(np.corrcoef(base, m[finite])[0, 1])
            a(f"| {key} | {d0}-{d1} | {np.nanmedian(m[finite]):.0f} | {r:.4f} |")
        a("")
        worst = min(float(np.corrcoef(base, results[k]["mean"][finite])[0, 1])
                    for k in variants)
        if worst > 0.98:
            a(f"Lowest correlation {worst:.4f}. The claim holds: the *spatial")
            a("pattern* is essentially the same whichever of these windows is")
            a("used, so the choice of window is not quietly doing the work. Note")
            a("what this does and does not say - the totals differ, but a model")
            a("reads the pattern, and the pattern is stable.\n")
        else:
            a(f"**Lowest correlation {worst:.4f} - the claim in FINDINGS section 6")
            a("does not hold.** The window choice materially changes the spatial")
            a("pattern, which makes it a tuning knob. Say so plainly in the")
            a("writeup rather than picking the window that scores best.\n")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print()
    print("\n".join(lines))
    print(f"written: {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
