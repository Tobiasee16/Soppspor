"""Phase 0 artefact 3: static suitability maps of Kristiansand at 16 m.

    python scripts/build_maps.py
    python scripts/build_maps.py --model lightgbm
    python scripts/build_maps.py --png-only   # redraw the views, no refit

One GeoTIFF and one PNG per species, plus the null control, which is mapped
alongside the real species for the same reason it is scored alongside them: a
null map that shows structure means the structure is not ecology.

Four things here are deliberate and should not be quietly changed.

**Fitted on Agder minus Kristiansand.** The maps are predictions onto held-out
ground, not a fit redrawn over its own training data. That costs a little
accuracy and buys the map the same status as the transferability number in
`data/processed/transferability.md`.

**Terrain is resampled from the 32 m stack, not recomputed at 16 m.** See
`src/model/predict.py` - TWI at 16 m is a different variable from TWI at 32 m,
and a model must not be handed a feature computed at a resolution it was never
fitted on. The map is 16 m because SR16 and the substrate layer carry real
detail there.

**Every raster passes through suppression before export.** CLAUDE.md requires
the capability from the start rather than as a later policy layer, so there is
no code path here that writes a file without it. With no sensitive sites
configured it is a no-op and says so, loudly, in the report and in the GeoTIFF
metadata.

**Single-hue sequential ramp, blue, light to dark.** CLAUDE.md hard constraint
7 forbids red-to-green anywhere: red/green reads as danger/safe, which is
exactly the wrong signal for a mushroom product. Blue also keeps the map
looking like a topographic sheet rather than a weather warning.
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
from rasterio.windows import from_bounds

import run_models as rm
from src import species as species_cfg
from src.features.background import null_and_background
from src.features.stack import build
from src.features.suppress import suppress
from src.geometry import Boundary
from src.model.models import LightGBM, MaxentStyle
from src.model.predict import predict_grid, snap_grid

INTERIM = ROOT / "data" / "interim"
CLIPPED = INTERIM / "kristiansand_stack"
MAPS = ROOT / "data" / "processed" / "maps"
OUT_MD = ROOT / "data" / "processed" / "suitability_maps.md"

CELL_M = 16.0
# The SR16 grid origin. Snapping to it makes each 16 m output cell coincide
# with an SR16 cell instead of straddling two.
SR16_ORIGIN = (-21824.0, 6640704.0)

# Single-hue sequential, light -> dark. Muted at the low end so that "no
# information" does not read as "nothing here".
BLUE_RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
             "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
             "#0d366b"]

# Sensitive habitat for suppression. Populated from Miljodirektoratet Naturbase
# by `scripts/fetch_naturbase.py`; the rule for what counts as sensitive lives
# in `src/ingest/naturbase.py:is_sensitive` so it is one decision in one place.
NATURBASE_FILE = INTERIM / "naturbase_kristiansand_25833.geojson"
SENSITIVE_SITES: list[tuple[float, float]] = []
SUPPRESSION_RADIUS_M = 500.0
SUPPRESSION_MODE = "blur"


def sensitive_polygons() -> list:
    """Sensitive naturtype rings, or an empty list if the pull has not run.

    Returning empty rather than raising is deliberate: the export must still
    work before Naturbase has been fetched, and the report then says loudly
    that the result is unscreened. Failing hard here would tempt someone to
    comment out the suppression call to get a map out, which is the one outcome
    this design exists to prevent.
    """
    if not NATURBASE_FILE.exists():
        return []
    from src.ingest import naturbase
    polys, summary = naturbase.sensitive_polygons(naturbase.load(NATURBASE_FILE))
    total = summary["sensitive"] + summary["not_sensitive"]
    print(f"sensitive habitat: {summary['sensitive']:,} of {total:,} naturtype "
          f"localities, {summary['rings']:,} rings", flush=True)
    return polys


def clip_stack(bbox, margin_m=3000.0) -> pathlib.Path:
    """Clip every raster in the training stack to the map window.

    Not an optimisation for its own sake: `sample_layers` reads a whole band
    per call, and the SR16 rasters are 13416 x 13265, so predicting in blocks
    against the full-county files would re-read well over a gigabyte per block.
    Clipping once makes the same reads trivial.

    The clipped files keep their band descriptions, nodata and dtype, so
    `rm.layers()` pointed at this directory produces byte-identical feature
    columns to the training run. The margin exists for the bilinear climate
    layer, which needs neighbouring cells beyond the window edge.
    """
    minx, miny, maxx, maxy = bbox
    window_bounds = (minx - margin_m, miny - margin_m, maxx + margin_m, maxy + margin_m)
    CLIPPED.mkdir(parents=True, exist_ok=True)

    for layer in rm.layers():
        src_path = pathlib.Path(layer.path)
        rel = src_path.relative_to(INTERIM)
        dst_path = CLIPPED / rel
        if dst_path.exists():
            continue
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(src_path) as src:
            win = from_bounds(*window_bounds, transform=src.transform)
            win = win.intersection(
                rasterio.windows.Window(0, 0, src.width, src.height))
            data = src.read(window=win)
            profile = src.profile.copy()
            profile.update(height=int(win.height), width=int(win.width),
                           transform=src.window_transform(win),
                           compress="deflate")
            profile.pop("photometric", None)
            with rasterio.open(dst_path, "w", **profile) as dst:
                dst.write(data)
                for b in range(src.count):
                    if src.descriptions[b]:
                        dst.set_band_description(b + 1, src.descriptions[b])
        print(f"  clipped {rel}", flush=True)
    return CLIPPED


def colormap():
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("soppkart_blue", BLUE_RAMP)
    cmap.set_bad("#00000000")       # transparent where there is no value
    return cmap


def crop_to_data(arr, margin_cells: int = 12):
    """Trim rows and columns that hold no finite value.

    Kristiansand's kommune boundary runs out to sea, so roughly the lower
    third of the prediction grid is inside the boundary and has no land under
    it. Left uncropped the PNG spends a third of its area on blank canvas.
    Only the PNG is cropped; the GeoTIFF keeps the full grid so that it stays
    aligned with everything else at 16 m.
    """
    finite = np.isfinite(arr)
    if not finite.any():
        return arr
    rows = np.flatnonzero(finite.any(axis=1))
    cols = np.flatnonzero(finite.any(axis=0))
    r0 = max(0, rows[0] - margin_cells)
    r1 = min(arr.shape[0], rows[-1] + 1 + margin_cells)
    c0 = max(0, cols[0] - margin_cells)
    c1 = min(arr.shape[1], cols[-1] + 1 + margin_cells)
    return arr[r0:r1, c0:c1]


def write_png(arr, path, title, subtitle, vmin, vmax):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arr = crop_to_data(arr)
    h, w = arr.shape
    fig_w = 7.5
    # Headroom for a two-line header, footroom for the colourbar and the
    # disclaimer. Fixed in inches rather than as a fraction, so the header does
    # not grow into the map when the map is tall.
    head_in, foot_in = 0.95, 1.45
    map_in = fig_w * h / w
    fig_h = map_in + head_in + foot_in

    fig = plt.figure(figsize=(fig_w, fig_h), dpi=170)
    fig.patch.set_facecolor("#ffffff")
    ax = fig.add_axes([0.02, foot_in / fig_h, 0.96, map_in / fig_h])
    ax.set_facecolor("#f4f4f2")

    im = ax.imshow(np.ma.masked_invalid(arr), cmap=colormap(), vmin=vmin,
                   vmax=vmax, interpolation="nearest")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.text(0.02, 1 - 0.34 / fig_h, title, fontsize=12, va="top", ha="left",
             color="#1a1a1a")
    fig.text(0.02, 1 - 0.60 / fig_h, subtitle, fontsize=7.5, va="top",
             ha="left", color="#5a5a5a", linespacing=1.35)

    cax = fig.add_axes([0.28, 0.78 / fig_h, 0.44, 0.16 / fig_h])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_label("relative habitat plausibility  ·  lav → høy / low → high",
                   fontsize=7.5, color="#3a3a3a", labelpad=4)
    cbar.ax.tick_params(labelsize=6.5, length=2, colors="#5a5a5a")
    cbar.outline.set_visible(False)

    fig.text(0.5, 0.16 / fig_h,
             "Ikke en spiselighetsvurdering. Not an edibility assessment.",
             ha="center", fontsize=7, color="#6a6a6a")
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def redraw_pngs(model_name: str) -> int:
    """Re-render the PNGs from the GeoTIFFs already on disk.

    Presentation is not a result, so it should not cost a refit. The GeoTIFF is
    the artefact; the PNG is a view of it, and this regenerates the view only.
    Nothing here can change a number - it reads the exported raster and draws
    it, and it refuses to invent one if the raster is missing.
    """
    tifs = sorted(MAPS.glob(f"suitability_*_{model_name}_16m.tif"))
    if not tifs:
        raise SystemExit(f"no GeoTIFFs for model {model_name!r} in {MAPS}")
    rows = []
    for tif in tifs:
        with rasterio.open(tif) as src:
            arr = src.read(1, masked=True).astype("float64").filled(np.nan)
            tags = src.tags()
        finite = arr[np.isfinite(arr)]
        vmin, vmax = (float(np.percentile(finite, 2)),
                      float(np.percentile(finite, 98))) if finite.size else (0.0, 1.0)
        rows.append({
            "species": tags.get("species", tif.stem),
            "tif": tif.name, "png": tif.with_suffix(".png").name,
            "cells": int(np.isfinite(arr).sum()),
            "min": float(finite.min()) if finite.size else float("nan"),
            "median": float(np.median(finite)) if finite.size else float("nan"),
            "p99": float(np.percentile(finite, 99)) if finite.size else float("nan"),
            "max": float(finite.max()) if finite.size else float("nan"),
            "suppression": tags.get("suppression", "unknown"),
        })
        png = tif.with_suffix(".png")
        write_png(arr, png,
                  f"{tags.get('species', tif.stem)} — habitat plausibility",
                  f"Kristiansand kommune (post-2020 merge) · {CELL_M:.0f} m\n"
                  f"{tags.get('model', model_name)}\n"
                  f"{tags.get('fitted_on', '')}",
                  vmin, vmax)
        print(f"redrew {png.name}", flush=True)

    with rasterio.open(tifs[0]) as src:
        h, w, model_label = src.height, src.width, src.tags().get("model", model_name)
    rebuild_report(rows, model_label, h, w)
    return 0


def main() -> int:
    model_name = "maxent"
    if "--model" in sys.argv:
        model_name = sys.argv[sys.argv.index("--model") + 1]
    if "--png-only" in sys.argv:
        return redraw_pngs(model_name)
    model = {"maxent": MaxentStyle, "lightgbm": LightGBM}[model_name]()

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    buffer_m = area["spatial_cv"]["buffer_m"]

    train_cfg, pred_cfg = area["training_extent"], area["prediction_extent"]
    training = Boundary(json.loads((ROOT / train_cfg["boundary_file"]).read_text(
        encoding="utf-8"))["features"][0]["geometry"], name=train_cfg["name"])
    prediction = Boundary(json.loads((ROOT / pred_cfg["boundary_file"]).read_text(
        encoding="utf-8"))["features"][0]["geometry"], name=pred_cfg["name"])

    stack = rm.layers()
    if not stack:
        raise SystemExit("no feature rasters found - run scripts/build_terrain.py first")

    pool_file = INTERIM / "target_group_pool_agder.json"
    pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
            for e, n, pr, i in json.loads(pool_file.read_text(encoding="utf-8"))["pool"]]

    sets = {sp["scientific_name"]: rm.presences(sp, training, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    slugs = {sp["scientific_name"]: sp["key"] for sp in species_cfg.modelled(spp)}
    slugs[spp["null_control"]["label"]] = spp["null_control"]["key"]

    print("clipping the feature stack to the map window ...", flush=True)
    clip_stack(prediction.bbox)
    original_interim = rm.INTERIM
    rm.INTERIM = CLIPPED
    map_stack = rm.layers()
    rm.INTERIM = original_interim
    if len(map_stack) != len(stack):
        raise SystemExit("the clipped stack has a different number of layers")

    transform, height, width = snap_grid(prediction.bbox, CELL_M, SR16_ORIGIN)
    print(f"grid: {height} x {width} at {CELL_M:.0f} m "
          f"({height * width:,} cells)\n", flush=True)

    # The boundary is rasterised once rather than tested per cell. Kristiansand
    # is 11.4M cells at 16 m and its coastline carries thousands of vertices, so
    # a Python point-in-polygon call per cell would dominate the whole run.
    from rasterio.features import rasterize as _rasterize
    pred_geom = json.loads((ROOT / pred_cfg["boundary_file"]).read_text(
        encoding="utf-8"))["features"][0]["geometry"]
    inside_mask = _rasterize([(pred_geom, 1)], out_shape=(height, width),
                             transform=transform, fill=0,
                             dtype="uint8").astype(bool)
    print(f"inside the kommune: {inside_mask.sum():,} of "
          f"{inside_mask.size:,} cells\n", flush=True)

    MAPS.mkdir(parents=True, exist_ok=True)
    sensitive = sensitive_polygons()
    rows = []
    for name, coords in sets.items():
        t0 = time.time()
        d = build(coords, bg_set.coords, stack)
        pts = [tuple(c) for c in d.coords]
        y = list(d.y)
        X = [list(r) for r in d.X]

        # Fit on Agder minus Kristiansand and its buffer, so the map is a
        # prediction onto ground the model never saw.
        in_pred = [(e, n) in prediction for e, n in pts]
        te = [i for i, f in enumerate(in_pred) if f]
        tr = [i for i, f in enumerate(in_pred) if not f]
        from src.validate.harness import _excluded_by_buffer
        dropped = _excluded_by_buffer(pts, tr, te, buffer_m)
        tr = [i for i in tr if i not in dropped]
        predict = model.fit([X[i] for i in tr], [y[i] for i in tr])

        print(f"{name}: fitted on {len(tr)} rows "
              f"({sum(y[i] for i in tr)} presences), predicting ...", flush=True)

        def progress(done, total, _n=name):
            print(f"    {_n}: {done}/{total} rows", flush=True)

        arr = predict_grid(predict, map_stack, d.names, transform, height, width,
                           inside_mask=inside_mask, rows_per_block=512,
                           progress=progress)

        treated, report = suppress(arr, transform, SENSITIVE_SITES,
                                   polygons=sensitive,
                                   radius_m=SUPPRESSION_RADIUS_M,
                                   mode=SUPPRESSION_MODE)
        print(f"    {report.describe()}", flush=True)

        # The config key, not the genus. Slugging on the first word collided the
        # moment a second Craterellus was added: C. tubaeformis and
        # C. cornucopioides would both have written suitability_craterellus_*,
        # and the second run would have silently overwritten the first.
        slug = slugs[name]
        tif = MAPS / f"suitability_{slug}_{model_name}_16m.tif"
        png = MAPS / f"suitability_{slug}_{model_name}_16m.png"

        profile = dict(driver="GTiff", height=height, width=width, count=1,
                       dtype="float32", crs="EPSG:25833", transform=transform,
                       nodata=np.nan, compress="deflate")
        with rasterio.open(tif, "w", **profile) as dst:
            dst.write(treated.astype("float32"), 1)
            dst.set_band_description(1, "habitat plausibility (relative)")
            dst.update_tags(
                species=name, model=model.name,
                fitted_on=f"{train_cfg['name']} minus {pred_cfg['name']} "
                          f"and a {buffer_m:.0f} m buffer",
                suppression=report.describe(),
                not_an_edibility_assessment="true",
                terrain_resolution="32 m, resampled to the 16 m grid",
            )

        finite = treated[np.isfinite(treated)]
        vmin, vmax = (float(np.percentile(finite, 2)),
                      float(np.percentile(finite, 98))) if finite.size else (0.0, 1.0)
        write_png(treated, png,
                  f"{name} — habitat plausibility",
                  f"{pred_cfg['name']} · {CELL_M:.0f} m\n"
                  f"{model.name}\n"
                  f"fitted on {train_cfg['name']} minus this area",
                  vmin, vmax)

        rows.append({"species": name, "tif": tif.name, "png": png.name,
                     "cells": int(np.isfinite(treated).sum()),
                     "min": float(finite.min()) if finite.size else float("nan"),
                     "median": float(np.median(finite)) if finite.size else float("nan"),
                     "p99": float(np.percentile(finite, 99)) if finite.size else float("nan"),
                     "max": float(finite.max()) if finite.size else float("nan"),
                     "suppression": report.describe(),
                     "n_train": len(tr)})
        print(f"    written in {time.time() - t0:.0f}s: {tif.name}, {png.name}\n",
              flush=True)

    write_report(rows, model, spp, train_cfg, pred_cfg, buffer_m, height, width)
    return 0


def rebuild_report(rows, model_label, height, width):
    """Regenerate the report from the exported rasters, without refitting.

    The GeoTIFF is the artefact; the report and the PNGs are views of it. This
    keeps them derivable, so a presentation fix never costs a refit and never
    tempts anyone to hand-edit a generated file.
    """
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))

    class _NamedModel:
        name = model_label

    write_report(rows, _NamedModel(), spp, area["training_extent"],
                 area["prediction_extent"], area["spatial_cv"]["buffer_m"],
                 height, width)


def write_report(rows, model, spp, train_cfg, pred_cfg, buffer_m, height, width):
    from datetime import date

    null_label = spp["null_control"]["label"]
    w = []
    a = w.append
    a("# Suitability maps - Kristiansand, 16 m\n")
    a(f"Generated by `scripts/build_maps.py` on {date.today().isoformat()}.\n")
    a(f"Phase 0 artefact 3. {height} x {width} at 16 m, EPSG:25833, "
      f"model `{model.name}`.\n")
    a(f"**Fitted on {train_cfg['name']} minus {pred_cfg['name']} and a "
      f"{buffer_m:.0f} m buffer.** These are predictions onto ground the model "
      "never saw, not a fit redrawn over its own training data.\n")
    a("**These maps are not an edibility assessment and not a species "
      "identification.** They shade terrain by habitat plausibility from public "
      "geodata. A high cell means the terrain resembles places where the "
      "species has been recorded. It does not mean anything is there.\n")

    a("## Files\n")
    a("| species | GeoTIFF | PNG | scored cells | min | median | p99 | max |")
    a("|---|---|---|---|---|---|---|---|")
    for r in rows:
        a(f"| {r['species']} | `{r['tif']}` | `{r['png']}` | {r['cells']:,} | "
          f"{r['min']:.3f} | {r['median']:.3f} | {r.get('p99', float('nan')):.3f} "
          f"| {r['max']:.3f} |")
    a("")

    a("## Red-list suppression\n")
    a("CLAUDE.md requires this capability in the tile pipeline from the start, "
      "not as a later policy layer. It is wired in: every raster above passed "
      "through `src/features/suppress.py` before export, and the result is "
      "recorded in each GeoTIFF's metadata.\n")
    for r in rows:
        a(f"* {r['species']}: {r['suppression']}")
    a("")
    if rows and all(r["suppression"].startswith("suppression") for r in rows):
        a("The sensitive set comes from Miljodirektoratet Naturbase NiN "
          "naturtyper - see [naturbase.md](naturbase.md) for the pull and for "
          "the rule deciding what counts as sensitive. The mode is `blur`, not "
          "`mask`, because a hole in a map is itself a signal: a sharp nodata "
          "disc says 'something is here' as loudly as a high score would.\n")
        a("**This is not a security control.** Naturbase is public, so a "
          "determined reader can recover these locations from the source. "
          "Suppression removes the fine structure that would let someone walk "
          "to a specific stand; it raises the effort, it does not prevent.\n")
        a("**It covers only what has been surveyed.** Most of Agder has never "
          "been mapped for naturtyper, so these polygons mark where somebody "
          "looked, not where the sensitive habitat is. The unmapped ground is "
          "the larger gap and suppression cannot close it.\n")
    else:
        a("**These maps are not screened.** The sensitive set is empty because "
          "Miljodirektoratet Naturbase (NiN naturtyper) has not been fetched - "
          "run `scripts/fetch_naturbase.py`. The suppression step ran and did "
          "nothing. Do not publish these rasters or hand them to anyone outside "
          "the project until the sensitive set is populated: the habitats "
          "richest in interesting fungi are disproportionately the ones holding "
          "threatened species.\n")

    a("## What the colours mean, and what they do not\n")
    a("Single-hue sequential ramp, blue, light to dark. CLAUDE.md hard "
      "constraint 7 forbids red-to-green anywhere in this project: red/green "
      "reads as danger/safe, which is the wrong signal for a mushroom product. "
      "The ramp is stretched between the 2nd and 98th percentile of each map's "
      "own scores, so **colours are not comparable between species.** A dark "
      "cell on the *A. muscaria* map and a dark cell on the *C. tubaeformis* "
      "map do not represent the same score.\n")
    a("The values are relative habitat plausibility, not probability of "
      "occurrence and not yield. They are uncalibrated model scores.\n")

    a("## The null map is the control, and it needs reading carefully\n")
    a(f"`{null_label}` is mapped on the same grid, from presences drawn from "
      "the sampling-effort surface with no ecology in them at all.\n")
    a("**It shows terrain structure too, and that is not the scandal it looks "
      "like.** Every map here is stretched between its own 2nd and 98th "
      "percentile, so any variation at all is expanded to fill the whole ramp. "
      "The null has a transfer AUC of 0.486 - no discriminative power - but its "
      "fitted coefficients are not zero, so it still paints valleys and ridges, "
      "and the stretch then makes that look as emphatic as a real signal.\n")
    a("What separates them is the *range*, which is why the table above "
      "reports it:\n")
    null_row = next((r for r in rows if r["species"] == null_label), None)
    if null_row and null_row["max"]:
        for r in rows:
            if r["species"] == null_label:
                continue
            a(f"* {r['species']}: max {r['max']:.3f}, "
              f"{r['max'] / null_row['max']:.1f}x the null's "
              f"{null_row['max']:.3f}")
        a("")
        a("The species models spread their scores over a genuinely wider range "
          "than the null does; the null sits in a narrow band that the colour "
          "stretch inflates. So compare *ranges* between these maps, never "
          "colours - the ramps are not on a common scale.\n")
    a("The honest summary: the shapes on the species maps are largely terrain, "
      "because terrain is what these features mostly encode. That was the "
      "reading in FINDINGS section 7 and nothing here overturns it.\n")

    a("## Known limits carried into this artefact\n")
    a("* Terrain is 32 m resampled to the 16 m grid, not recomputed at 16 m. "
      "TWI at 16 m is a different variable and the model was not fitted on it. "
      "The 16 m detail in these maps comes from SR16 and the substrate layer.")
    a("* SR16 is a model, not ground truth: tree species correct in roughly "
      "two-thirds of pixels, volume error 30-60% of the mean.")
    a("* These maps are fitted on the transfer split, so the accuracy behind "
      "them is the **transferability** figure, not the blocked-CV one: "
      "0.605-0.730 AUC, with the null at 0.486. See "
      "[transferability.md](transferability.md). That is weak-to-moderate. A "
      "map drawn from it will be wrong in specific places and should be read "
      "as a hypothesis about terrain, not a prediction about mushrooms.")
    a("* The registered species ordering does **not** hold on this split. "
      "*C. tubaeformis*, predicted to be the most predictable of the three, "
      "scores lowest here. Do not read the tubaeformis map as the most "
      "trustworthy of the set.")
    a("* The Boyce index asks whether predicted suitability rises monotonically "
      "with observed presence density, which is the question that matters for "
      "a *surface* rather than for a ranking. On the corrected quantile-window "
      "implementation it is +0.70 to +0.92 for the three species and about "
      "zero for the null, so these surfaces are coherent even where they are "
      "not accurate. The published equal-width implementation reports the "
      "opposite and is biased; see "
      "[boyce_diagnosis.md](boyce_diagnosis.md).\n")

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(w), encoding="utf-8")
    print("\n".join(w))
    print(f"written: {OUT_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
