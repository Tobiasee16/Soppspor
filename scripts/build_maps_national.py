"""National suitability surfaces at 32 m, one GeoTIFF per species.

    python scripts/build_maps_national.py
    python scripts/build_maps_national.py --model lightgbm
    python scripts/build_maps_national.py --only craterellus_tubaeformis

The national sibling of `build_maps.py`, separate rather than a `--national`
flag because almost none of the body is shared - that script clips the stack to
the map window, holds the whole surface in memory, runs suppression over it and
draws a PNG of the result, and none of those survive at 1.83 billion cells.
`build_terrain_national.py` and `build_vector_layers_national.py` set the same
precedent: `run_*` scripts take a flag, `build_*` scripts get a sibling.

Four decisions, taken deliberately and recorded here so they are not reversed
by accident.

**32 m, not 16.** The national feature stack is 32 m natively - terrain, AR50
and losmasser are all built on that grid - and the app quantises to 128 m for
display, so 32 m is already four times finer than anything reaches a screen.
16 m would cost about 22 hours against 6 and would read SR16 at its native
detail, which nothing downstream would ever show. Measured costs are in
EXPANSION.md section 3.

**The grid comes from `terrain_norge_32m.tif` rather than being computed.**
Recomputing an equivalent grid puts alignment at the mercy of two
floating-point transforms agreeing, and a half-cell shift produces plausible
numbers and no error at all. This is the same rule `build_vector_layers_
national.py` follows.

**Fitted on fourteen fylker, mapped over all fifteen.** The model is the exact
one `run_transfer.py --national` reports on: everything outside Nordland, minus
a 4 km buffer around the held-out points. So the quoted transfer AUC describes
the model actually shipped. Nordland's map is a prediction onto ground the
model never saw; the other fourteen fylker are in-sample, and the writeup says
so rather than implying the whole surface is held-out.

**No suppression pass.** CLAUDE.md once required blur/mask capability near
sensitive habitat in the tile pipeline. That requirement was removed from
CLAUDE.md by the team in September 2026 and the in-app blur option was dropped
with it, so this is not a live constraint. `src/features/suppress.py` stays in
the tree, unused. It could not run here in its present form anyway - it takes
the whole surface as an array.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import rasterio
import yaml

import run_models as rm
from src import species as species_cfg
from src.features.background import null_and_background
from src.features.stack import SampleCache, build
from src.model.models import LightGBM, MaxentStyle
from src.model.predict import predict_grid_to_raster

INTERIM = ROOT / "data" / "interim"
MAPS = ROOT / "data" / "processed" / "maps_norge"
OUT_MD = ROOT / "data" / "processed" / "suitability_maps_norge.md"
REFERENCE = INTERIM / "terrain_norge_32m.tif"
AR50 = INTERIM / "ar50_norge_32m.tif"
MASK = INTERIM / "norge_mask_32m.tif"

# AR50's sea class. The codes live in src/ingest/ar50.py; imported rather than
# written as a literal so a change to the grouping cannot leave this behind.
from src.ingest.ar50 import LABELS as _AR50_LABELS

AR50_SEA = next(c for c, n in _AR50_LABELS.items() if n == "sea")

ROWS_PER_BLOCK = 512
# The preview is a view of the GeoTIFF, not a second product. 512 m keeps a
# national sheet around 2,400 x 3,000 - large enough to see the pattern, small
# enough to open.
PREVIEW_CELL_M = 512.0


def country_mask(area: dict) -> pathlib.Path:
    """Where the map is allowed to have a value: inside a fylke, and not sea.

    Testing point-in-polygon per cell is out of the question - the fylke
    outlines carry 175,000 vertices between them and the grid is 1.83 billion
    cells. Holding the mask in memory is also out: 1.83 GB as uint8, on top of
    the sampling working set. So it is rasterised once to a tiled, deflated
    GeoTIFF, which compresses to almost nothing because it is two values, and
    read back a block at a time.

    **The sea cut is not optional, and it is not the fylke boundary's job.**
    Kartverket publishes fylke areas out to the territorial baseline rather
    than to the coastline, so a fylke polygon contains thousands of square
    kilometres of open water - 20 km south of Kristiansand is inside Agder by
    this test. The Agder build got sea removal for free because its DTM was
    sea-masked and every sea cell therefore had a non-finite feature;
    `build_terrain_national.py` deliberately dropped that mask when TWI was
    dropped, and says in its own docstring that *prediction is masked to land
    regardless*. This is where that happens.

    Sea is taken from AR50's explicit sea class rather than from an elevation
    threshold, so an inland lake at sea level stays land - the same outcome the
    Agder flood fill was written to produce. AR50 freshwater is kept: a lake is
    not habitat, but the model carries an explicit freshwater column and Phase
    0 scored those cells too, so excluding them here would make the two maps
    answer subtly different questions.
    """
    if MASK.exists():
        print(f"mask: using cached {MASK.name}")
        return MASK

    from rasterio.features import rasterize

    cfg = area["national_training_extent"]
    files = sorted((ROOT / cfg["boundary_dir"]).glob("boundary_*_25833.geojson"))
    if len(files) != 15:
        raise SystemExit(f"expected 15 fylke boundaries in {cfg['boundary_dir']}, "
                         f"found {len(files)}")
    shapes = []
    for f in files:
        fc = json.loads(f.read_text(encoding="utf-8"))
        declared = fc.get("crs_declared_by_source")
        if declared and "25833" not in str(declared):
            raise SystemExit(f"{f.name} declares {declared}, expected EPSG:25833")
        shapes.append((fc["features"][0]["geometry"], 1))

    with rasterio.open(REFERENCE) as ref:
        profile = dict(driver="GTiff", height=ref.height, width=ref.width,
                       count=1, dtype="uint8", crs=ref.crs,
                       transform=ref.transform, nodata=0, compress="deflate",
                       tiled=True, blockxsize=256, blockysize=256,
                       BIGTIFF="IF_SAFER")
        t0 = time.time()
        print(f"mask: rasterising {len(shapes)} fylker onto "
              f"{ref.width:,} x {ref.height:,}, minus AR50 sea ...", flush=True)
        kept = dropped_sea = 0
        try:
            with rasterio.open(MASK, "w", **profile) as dst, \
                 rasterio.open(AR50) as ar:
                if ar.transform != ref.transform or ar.shape != ref.shape:
                    raise SystemExit(
                        f"{AR50.name} is not on the terrain grid; the sea cut "
                        f"would be offset from the cells it is cutting")
                for start in range(0, ref.height, 2048):
                    stop = min(start + 2048, ref.height)
                    win = rasterio.windows.Window(0, start, ref.width, stop - start)
                    block = rasterize(
                        shapes, out_shape=(stop - start, ref.width),
                        transform=ref.window_transform(win), fill=0,
                        dtype="uint8", all_touched=False) > 0
                    sea = ar.read(1, window=win) == AR50_SEA
                    dropped_sea += int((block & sea).sum())
                    block &= ~sea
                    kept += int(block.sum())
                    dst.write(block.astype("uint8"), 1, window=win)
                    print(f"  rows {stop:,}/{ref.height:,}", flush=True)
        except BaseException:
            MASK.unlink(missing_ok=True)
            raise
    cell_km2 = 32.0 ** 2 / 1e6
    print(f"mask: {kept:,} cells kept = {kept*cell_km2:,.0f} km2; "
          f"{dropped_sea:,} sea cells removed = {dropped_sea*cell_km2:,.0f} km2")
    print(f"mask: {MASK.stat().st_size/1e6:.0f} MB in {time.time()-t0:.0f}s")
    return MASK


def fitting_rows(pts, holdout, buffer_m: float) -> tuple[list[int], int]:
    """Rows to fit on: outside the held-out region, minus a buffer ring.

    A function rather than three lines inline, because inline it was written as

        tr = [i for i in tr if i not in _excluded_by_buffer(pts, tr, te, ...)]

    which re-runs the entire exclusion for **every one of 52,000 rows**. The
    result is identical - the call is deterministic - so nothing was wrong with
    the maps it produced, and nothing failed. It just went quadratic:
    *A. muscaria* sat at 100% CPU with zero disk I/O for four and a half hours
    before anyone looked at why the log had gone quiet.

    `build_maps.py` had it right and this was a transcription error, which is
    the argument for the helper: there is now one place to get it wrong.
    """
    from src.validate.harness import _excluded_by_buffer

    in_hold = [(e, n) in holdout for e, n in pts]
    te = [i for i, flag in enumerate(in_hold) if flag]
    tr = [i for i, flag in enumerate(in_hold) if not flag]
    dropped = _excluded_by_buffer(pts, tr, te, buffer_m)
    return [i for i in tr if i not in dropped], len(te)


def decimate(tif: pathlib.Path, cell_m: float) -> np.ndarray:
    """The raster read down to roughly `cell_m`, averaging over nodata.

    `read(band, ...)` with a scalar band index already returns a 2-D array.
    The first version of this indexed `[0]` into it, which takes the top row
    of the raster - a 1,192-cell sliver of Arctic sea, entirely NaN - and the
    caller then reported the whole national surface as empty. It cost a
    completed 36-minute prediction, because the check fired after the GeoTIFF
    was already written.

    `Resampling.average` rather than nearest, because a preview cell covers 256
    source cells and showing one of them is a lie about the other 255. GDAL
    skips nodata when averaging, so a coastal cell reads as the mean of its
    land part rather than as NaN.
    """
    with rasterio.open(tif) as src:
        step = max(1, int(round(cell_m / abs(src.transform.a))))
        return src.read(1, out_shape=(src.height // step, src.width // step),
                        resampling=rasterio.enums.Resampling.average)


def preview_png(tif: pathlib.Path, png: pathlib.Path, title: str, subtitle: str):
    """A decimated sheet of the GeoTIFF. The raster is the artefact."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    import build_maps as bm

    arr = decimate(tif, PREVIEW_CELL_M)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        raise SystemExit(f"{tif.name} has no finite cells")
    vmin, vmax = float(np.percentile(finite, 2)), float(np.percentile(finite, 98))

    cmap = LinearSegmentedColormap.from_list("sopp_blue", bm.BLUE_RAMP)
    cmap.set_bad("#f4f4f2")
    h, w = arr.shape
    fig_w = 7.0
    map_in = fig_w * h / w
    head_in, foot_in = 0.9, 1.3
    fig = plt.figure(figsize=(fig_w, map_in + head_in + foot_in), dpi=170)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_axes([0.02, foot_in / (map_in + head_in + foot_in), 0.96,
                       map_in / (map_in + head_in + foot_in)])
    ax.set_facecolor("#f4f4f2")
    im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap, vmin=vmin, vmax=vmax,
                   interpolation="nearest")
    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    fig.text(0.02, 1 - 0.30 / (map_in + head_in + foot_in), title,
             fontsize=12, weight="bold", va="top")
    fig.text(0.02, 1 - 0.58 / (map_in + head_in + foot_in), subtitle,
             fontsize=7.5, va="top", color="#444444")
    cax = fig.add_axes([0.10, 0.42 / (map_in + head_in + foot_in), 0.5, 0.018])
    fig.colorbar(im, cax=cax, orientation="horizontal").set_label(
        "lower  ←  habitat plausibility  →  higher", fontsize=7)
    cax.tick_params(labelsize=0, length=0)
    fig.text(0.02, 0.16 / (map_in + head_in + foot_in),
             "Not an edibility assessment and not a species identification. An "
             "estimate from public geodata,\nnever validated against anybody's "
             "actual walks.", fontsize=6.5, color="#666666", va="bottom")
    fig.savefig(png, facecolor=fig.get_facecolor())
    plt.close(fig)


def main() -> int:
    model_name = "maxent"
    if "--model" in sys.argv:
        model_name = sys.argv[sys.argv.index("--model") + 1]
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    model = {"maxent": MaxentStyle, "lightgbm": LightGBM}[model_name]()

    rm.EXTENT = rm.NORGE.key
    rm.INCLUDE_TWI = False

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    buffer_m = area["spatial_cv"]["buffer_m"]

    if not REFERENCE.exists():
        raise SystemExit(f"missing {REFERENCE} - run scripts/build_terrain_national.py")

    stack = rm.layers()
    if not stack:
        raise SystemExit("no national feature rasters found")

    # The same regions the transfer test used, so the model fitted below is the
    # one transferability_norge.md reports on.
    draw, holdout = rm.transfer_regions(area)
    pool = rm.transfer_pool()
    sets = {sp["scientific_name"]: rm.presences(sp, draw, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords
    slugs = {sp["scientific_name"]: sp["key"] for sp in species_cfg.modelled(spp)}
    slugs[spp["null_control"]["label"]] = spp["null_control"]["key"]

    with rasterio.open(REFERENCE) as ref:
        transform, height, width = ref.transform, ref.height, ref.width
        crs = ref.crs
    cell_m = abs(transform.a)
    print(f"grid: {width:,} x {height:,} at {cell_m:.0f} m = "
          f"{width*height/1e9:.2f} billion cells, {crs}")
    print(f"model: {model.name}")
    print(f"fitted on: everything outside {holdout.name}, minus a "
          f"{buffer_m:.0f} m buffer\n", flush=True)

    mask_path = country_mask(area)
    mask_src = rasterio.open(mask_path)

    def inside_block(start, stop):
        win = rasterio.windows.Window(0, start, width, stop - start)
        return mask_src.read(1, window=win) > 0

    MAPS.mkdir(parents=True, exist_ok=True)
    sample_cache = SampleCache()
    rows = []
    refresh = "--refresh" in sys.argv
    for name, coords in sets.items():
        slug = slugs[name]
        if only and slug != only:
            continue
        tif = MAPS / f"suitability_{slug}_{model_name}_32m.tif"
        png = MAPS / f"suitability_{slug}_{model_name}_32m.png"
        meta = tif.with_suffix(".meta.json")

        # Resume. Nine sets at 36 minutes each is a five-hour run, and the
        # first attempt lost a completed prediction to a bug in the *preview*
        # that fired after the GeoTIFF was written. Anything that finished
        # stays finished.
        if tif.exists() and meta.exists() and not refresh:
            stats = json.loads(meta.read_text(encoding="utf-8"))
            print(f"{name}: using cached {tif.name} "
                  f"({stats['scored']:,} cells scored)", flush=True)
            if not png.exists():
                preview_png(tif, png, f"{name} — habitat plausibility",
                            f"Norway · {cell_m:.0f} m, previewed at "
                            f"{PREVIEW_CELL_M:.0f} m\n{model.name}\n"
                            f"fitted on 14 fylker, {holdout.name} held out")
            rows.append({"species": name, "tif": tif.name, "png": png.name,
                         **stats})
            continue

        t0 = time.time()
        d = build(coords, bg_set.coords, stack, cache=sample_cache)
        pts = [tuple(c) for c in d.coords]
        y, X = list(d.y), [list(r) for r in d.X]

        tr, n_held = fitting_rows(pts, holdout, buffer_m)
        predict = model.fit([X[i] for i in tr], [y[i] for i in tr])
        print(f"{name}: fitted on {len(tr):,} rows "
              f"({sum(y[i] for i in tr):,} presences), predicting ...", flush=True)

        last = [time.time()]

        def progress(done, total, _t0=t0, _last=last):
            now = time.time()
            if now - _last[0] < 60 and done < total:
                return
            _last[0] = now
            el = now - _t0
            print(f"    rows {done:,}/{total:,}  {100*done/total:5.1f}%  "
                  f"~{el/max(done,1)*(total-done)/60:.0f} min left", flush=True)

        stats = predict_grid_to_raster(
            predict, stack, d.names, transform, height, width, tif, crs,
            inside_block=inside_block, rows_per_block=ROWS_PER_BLOCK,
            progress=progress)

        with rasterio.open(tif, "r+") as dst:
            dst.set_band_description(1, "habitat plausibility (relative)")
            dst.update_tags(
                species=name, model=model.name,
                fitted_on=f"14 fylker, everything outside {holdout.name} minus a "
                          f"{buffer_m:.0f} m buffer",
                held_out=f"{holdout.name} is a prediction onto unseen ground; "
                         f"the other fourteen fylker are in-sample",
                suppression="none - not a live constraint, see the module docstring",
                not_an_edibility_assessment="true",
                terrain_resolution="32 m, native")

        stats = {**stats, "n_train": len(tr), "n_held_out": n_held,
                 "seconds": round(time.time() - t0)}
        # Written before the preview, so a preview bug can never cost a
        # completed prediction again.
        meta.write_text(json.dumps(stats, indent=1), encoding="utf-8")

        preview_png(tif, png, f"{name} — habitat plausibility",
                    f"Norway · {cell_m:.0f} m, previewed at {PREVIEW_CELL_M:.0f} m\n"
                    f"{model.name}\nfitted on 14 fylker, {holdout.name} held out")

        rows.append({"species": name, "tif": tif.name, "png": png.name, **stats})
        print(f"    {stats['scored']:,} cells scored, "
              f"{stats['no_features']:,} without features, "
              f"{tif.stat().st_size/1e9:.2f} GB in "
              f"{(time.time()-t0)/60:.0f} min\n", flush=True)

    mask_src.close()
    write_report(rows, model, holdout, buffer_m, height, width, cell_m)
    return 0


def write_report(rows, model, holdout, buffer_m, height, width, cell_m):
    from datetime import date

    w = []
    a = w.append
    a(f"# Suitability maps - Norway, {cell_m:.0f} m\n")
    a(f"Generated by `scripts/build_maps_national.py` on "
      f"{date.today().isoformat()}. {width:,} x {height:,} at {cell_m:.0f} m, "
      f"EPSG:25833, model `{model.name}`.\n")
    a("**These maps are not an edibility assessment and not a species "
      "identification.** They shade terrain by habitat plausibility from public "
      "geodata. A high cell means the terrain resembles places where the species "
      "has been recorded. It does not mean anything is there.\n")
    a(f"**Fitted on fourteen fylker** - everything outside {holdout.name}, minus "
      f"a {buffer_m:.0f} m buffer around the held-out points. That is the same "
      f"model `transferability_norge.md` reports on, so the AUC quoted there "
      f"describes the surface shipped here.\n")
    a(f"**{holdout.name} is the only part of this map that is a prediction onto "
      f"ground the model never saw.** The other fourteen fylker are in-sample. "
      f"Phase 0's Kristiansand map was held-out over its whole extent; this one "
      f"is not, and the honest read is that the held-out number from Nordland is "
      f"what bounds trust in the rest.\n")
    a("| species | GeoTIFF | preview | cells scored | no features | fitted rows | min |")
    a("|---|---|---|---|---|---|---|")
    for r in rows:
        a(f"| {r['species']} | `{r['tif']}` | `{r['png']}` | {r['scored']:,} | "
          f"{r['no_features']:,} | {r['n_train']:,} | {r['seconds']/60:.0f} |")
    a("")
    a("`no features` counts cells inside Norway where some layer had no value - "
      "chiefly SR16 outside productive forest, which is a fact about the place "
      "rather than a gap, but which still leaves the cell unscored here because "
      "`build` drops any row with a non-finite feature.\n")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(w), encoding="utf-8")
    print("\n".join(w))
    print(f"written: {OUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
