"""Phase 0 artefact 1: blocked AUC and Boyce for three species plus the null.

Everything this script needs was decided and written down before it existed -
the predicted ordering in FINDINGS, the climate window in `config/climate.yaml`,
the block size and buffer in `config/study_area.yaml`. It reads those and
reports what comes out. It does not choose anything, and it must never be
edited to make a number look better.

The null control runs on every invocation, alongside the real species and
through exactly the same pipeline. CLAUDE.md hard constraint 5: if the null
scores meaningfully above 0.5 on blocked CV, the background sampling is broken
and nothing else in the run means anything. That is checked here and stated in
the output rather than left for a reader to notice.

    python scripts/run_models.py
    python scripts/run_models.py --national
    python scripts/run_models.py --tag terrain-only
    python scripts/run_models.py --no-vol-bounds
    python scripts/run_models.py --no-elevation
    python scripts/run_models.py --no-twi

Two extents, and they never share a file. The default is Agder, which is what
every published Phase 0 number came from, and it writes `model_results.md`.
`--national` fits on fourteen fylker with Nordland held out and writes
`model_results_norge.md`. Which rasters, which background pool, which boundary
and which output file are all read off one `Extent` record rather than being
decided in eight separate places.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import yaml

from src.features.background import null_and_background
from src.features.stack import RasterLayer, SampleCache, build
from src.features.thin import thin
from src.geometry import Boundaries, Boundary
from src.ingest import artskart
from src.ingest.ar50 import CATEGORIES as AR50_CATEGORIES
from src.ingest.ar50 import LABELS as AR50_LABELS
from src.ingest.losmasser import CATEGORIES as LOSMASSE_CATEGORIES
from src.ingest.losmasser import LABELS as LOSMASSE_LABELS
from src import species as species_cfg
from src.model.models import Constant, LightGBM, MaxentStyle, permutation_importance
from src.validate.harness import _excluded_by_buffer, spatial_block_cv
from src.validate.metrics import roc_auc

ROOT = pathlib.Path(__file__).resolve().parents[1]
INTERIM = ROOT / "data" / "interim"
RAW = ROOT / "data" / "raw" / "artskart"
PROCESSED = ROOT / "data" / "processed"

AGDER_NAMES = {"Agder", "Aust-Agder", "Vest-Agder"}


@dataclass(frozen=True)
class Extent:
    """Everything that differs between the Agder run and the national one.

    CLAUDE.md's study-area section is explicit that the extents must never
    silently merge, and the same discipline now has to survive a second pair.
    Holding every path in one table makes the difference between them readable
    in one place rather than spread across nine string literals, and makes it
    structurally impossible for a national run to write over Phase 0's
    published results - which is the failure this project has hit three times
    now, on a download, on a climate report and on a tagged model run.
    """

    key: str
    label: str
    terrain: str
    terrain_bands: tuple[tuple[int, str], ...]
    climate: str
    sr16: str                          # `{code}` is the SR16 variable code
    losmasser: str
    ar50: str
    pool: str                          # target-group pool cache, in interim
    report: str                        # in data/processed
    transfer_report: str               # in data/processed, written by run_transfer
    holdout: str                       # study_area.yaml entry for the held-out region
    counties: frozenset | None         # None: use the boundary, not the label
    cv_extent: str | None              # which study_area.yaml entry bounds CV


AGDER = Extent(
    key="agder",
    label="Agder fylke",
    terrain="terrain_agder_32m.tif",
    terrain_bands=((1, "elevation"), (2, "slope_deg"), (3, "northness"),
                   (4, "eastness"), (5, "curvature"), (6, "twi")),
    climate="autumn_rain_agder_1km.tif",
    sr16="sr16/sr16_42_SRR{code}.tif",
    losmasser="losmasser_agder_32m.tif",
    ar50="ar50_agder_32m.tif",
    pool="target_group_pool_agder.json",
    report="model_results.md",
    transfer_report="transferability.md",
    holdout="prediction_extent",
    counties=frozenset(AGDER_NAMES),
    # None: take it from `spatial_cv.cv_extent` in config/study_area.yaml,
    # which is where Phase 0 reads it and where it stays configurable.
    cv_extent=None,
)

NORGE = Extent(
    key="norge",
    label="Norway, mainland, minus the held-out region",
    terrain="terrain_norge_32m.tif",
    # Five bands, not six, and that is a property of the raster rather than a
    # flag. TWI needs flow accumulation over a whole connected surface, and
    # 1.83 billion cells will not take a priority-flood in 15.7 GB at any
    # speed; FINDINGS 18 measured what dropping it costs before the decision
    # was taken. A sixth entry here would be read off a five-band raster as
    # whatever band 6 happened to be, which is nothing at all.
    terrain_bands=((1, "elevation"), (2, "slope_deg"), (3, "northness"),
                   (4, "eastness"), (5, "curvature")),
    climate="autumn_rain_norge_1km.tif",
    sr16="sr16_norge/SRR{code}.tif",
    losmasser="losmasser_norge_32m.tif",
    ar50="ar50_norge_32m.tif",
    pool="target_group_pool_norge.json",
    report="model_results_norge.md",
    transfer_report="transferability_norge.md",
    holdout="national_holdout_extent",
    # No county filter nationally. The extent is fourteen polygons rather than
    # three names, and Artskart's County field is a label attached once per
    # administrative unit a record intersects - fine for picking one fylke out
    # of a national download, wrong for defining the region a model is fitted
    # on.
    counties=None,
    cv_extent="national_training_extent",
)

EXTENTS = {AGDER.key: AGDER, NORGE.key: NORGE}
EXTENT = AGDER.key          # rebound by --national in main()


def extent() -> Extent:
    return EXTENTS[EXTENT]


def out_path() -> pathlib.Path:
    """Where an untagged run writes. Never the same file for two extents."""
    return PROCESSED / extent().report

# The null must sit near 0.5. This is the threshold above which the run is
# declared broken rather than interpreted - CLAUDE.md's "meaningfully above
# 0.5", made a number so it cannot be argued with after the fact.
NULL_CEILING = 0.60

# FINDINGS section 8 carried forward: vol_lower and vol_upper are strongly
# correlated with vol_m3ha by construction, and permutation importance under
# correlated features assigns credit arbitrarily. Refitting without them is the
# registered check on whether the volume block or its uncertainty bounds
# carried C. tubaeformis to 0.704. Set by --no-vol-bounds; the default keeps
# them, so the default run stays comparable with everything already reported.
INCLUDE_VOL_BOUNDS = True


# SR16 forest variables taken as the band-1 estimate. Absence means "not
# productive forest" - water, farmland, bog, bare rock, ground above the forest
# line - which is true of 19% of the target-group pool, so it is encoded as
# zero rather than dropped. See RasterLayer's docstring for why that is an
# encoding of a fact and not an invention of data.
SR16_NUMERIC = {
    "VOLMB": "vol_m3ha",            # standing volume
    "GRFLATE": "basal_area_m2ha",
    "MHOYDE": "lorey_height_dm",
    "LAI": "lai",
    "TREALDER": "stand_age_yr",
    "KRONEDEK": "crown_cover_pct",
    "BONITET": "site_index",
}


INCLUDE_ELEVATION = True
INCLUDE_TWI = True


def layers() -> list[RasterLayer]:
    """The feature stack, in the order it will be reported.

    AR5 is absent: it needs a Geonorge login with the FKB role. Whatever is
    present is listed in the output, so a run can never quietly claim more
    features than it used.
    """
    # `--no-elevation` refits everything on the same stack minus one band, to
    # answer whether elevation is measuring terrain or measuring how easy a
    # place is to reach. The prediction is registered in FINDINGS section 15,
    # written before the first run of it. Bands are 1-indexed and elevation is
    # band 1; the other five terrain derivatives stay.
    #
    # `--no-twi` asks a different question, and a practical one. TWI is the only
    # terrain derivative that is not a local window operation: it needs flow
    # accumulation over a whole connected surface, which is why the Agder raster
    # had to be built in one piece. Nationally that surface is 1.83 billion cells
    # at 32 m, and a priority-flood over it will not run in 15.7 GB of RAM at any
    # speed. So before choosing a way around that, measure what TWI is worth.
    # Permutation importance says essentially nothing (+0.005 +/- 0.008 at best,
    # negative on four of eight species) - but permutation importance understates
    # every member of a correlated group, and TWI correlates with slope and
    # curvature. An ablation does not have that problem.
    #
    # Which terrain bands exist is a property of the extent, not of a flag:
    # the national raster has five and Agder's has six. Asking for band 6 of a
    # five-band file returns nothing useful and raises nothing either.
    ext = extent()
    switch = {"elevation": INCLUDE_ELEVATION, "twi": INCLUDE_TWI}
    keep = [(b, n) for b, n in ext.terrain_bands if switch.get(n, True)]
    terrain = str(INTERIM / ext.terrain)
    if len(keep) == len(ext.terrain_bands):
        out = [RasterLayer(terrain, method="nearest")]
    else:
        out = [RasterLayer(terrain, method="nearest",
                           bands=[b for b, _ in keep],
                           names=[n for _, n in keep])]

    climate = INTERIM / ext.climate
    if climate.exists():
        # Bilinear: sampled nearest, a 1 km field gives neighbouring points a
        # few hundred metres apart identical values for no physical reason.
        out.append(RasterLayer(str(climate), bands=[1], names=["autumn_rain_mm"],
                               method="bilinear"))

    def sr16_path(code: str) -> pathlib.Path:
        return INTERIM / ext.sr16.format(code=code)

    treslag = sr16_path("TRESLAG")
    if treslag.exists():
        # Tree species one-hot: 1 spruce, 2 pine, 3 deciduous. This layer also
        # carries the single shared "SR16 absent" indicator for the whole SR16
        # block - every SR16 variable is missing on exactly the same cells, so
        # one indicator per variable would be seven identical columns and would
        # split that signal seven ways in the permutation importance.
        out.append(RasterLayer(str(treslag), bands=[1], names=["treslag"],
                               categories=[1, 2, 3], missing_as=0.0,
                               missing_indicator=True))
    for code, name in SR16_NUMERIC.items():
        path = sr16_path(code)
        if path.exists():
            out.append(RasterLayer(str(path), bands=[1], names=[name],
                                   missing_as=0.0))

    # Uncertainty, propagated rather than hidden, as CLAUDE.md asks. SR16 ships
    # every variable with lower and upper bounds; published validation puts
    # volume error at 30-60% of the mean. Carrying the bounds for volume lets
    # the model see where the forest estimate is weak instead of treating a
    # wide interval and a tight one as the same number.
    vol = sr16_path("VOLMB")
    if vol.exists() and INCLUDE_VOL_BOUNDS:
        out.append(RasterLayer(str(vol), bands=[2, 3],
                               names=["vol_lower", "vol_upper"], missing_as=0.0))

    # NGU superficial deposits, collapsed to ten depositional groups. The
    # grouping is fixed in src/ingest/losmasser.py and was registered, with a
    # prediction for what this layer would do, in FINDINGS section 8 before it
    # was ever fitted. Categorical, so nearest sampling and one-hot: halfway
    # between peat and bare rock is not a substrate.
    losmasser = INTERIM / ext.losmasser
    if losmasser.exists():
        out.append(RasterLayer(str(losmasser), bands=[1], names=["losmasse"],
                               categories=LOSMASSE_CATEGORIES, missing_as=0.0,
                               missing_indicator=True,
                               category_labels=[LOSMASSE_LABELS[c]
                                                for c in LOSMASSE_CATEGORIES]))

    # AR50 land cover. The open stand-in for FKB-AR5, which is Norge digitalt
    # restricted - see src/ingest/ar50.py. Its job is to split SR16's single
    # "not productive forest" flag into bog, farmland, open ground, water and
    # built-up. Predictions for what it would do were registered in FINDINGS
    # section 10 before it was fitted.
    ar50 = INTERIM / ext.ar50
    if ar50.exists():
        out.append(RasterLayer(str(ar50), bands=[1], names=["ar50"],
                               categories=AR50_CATEGORIES, missing_as=0.0,
                               missing_indicator=True,
                               category_labels=[AR50_LABELS[c]
                                                for c in AR50_CATEGORIES]))

    return [layer for layer in out if pathlib.Path(layer.path).exists()]


_FROM_EXTENT = object()


def presences(sp: dict, boundary, max_prec: float, thin_m: float,
              counties=_FROM_EXTENT):
    """Thinned presence coordinates inside `boundary`.

    Extent, then precision, then thinning - the same order the target-group
    background gets, because thinning before the extent cut would let a 1 km
    cell straddling a border be resolved differently on the two sides and turn
    the bias correction into a bias.

    `counties` is a cheap prefilter on Artskart's County label, used only by
    the Agder run, where it removes 90% of a national download before any
    point-in-polygon work. It is not the extent definition - the boundary is -
    and nationally it is None, because a record on a fylke border is published
    once per fylke it touches and picking by label would then depend on which
    copy arrived first.
    """
    if counties is _FROM_EXTENT:
        counties = extent().counties
    obs = artskart.load(RAW / f"{sp['key']}.json")
    if counties is not None:
        obs = [o for o in obs if o.get("County") in counties]
    pts = artskart.usable_points(obs)
    pts = [p for p in pts if (p["east"], p["north"]) in boundary]
    pts = [p for p in pts if p["precision_m"] is not None and p["precision_m"] <= max_prec]
    return [(p["east"], p["north"]) for p in thin(pts, thin_m)]


def cv_boundary(area: dict):
    """The region blocked cross-validation runs inside.

    Agder is one polygon from one file. The national training extent is
    fourteen, because Kartverket publishes no single national outline through
    the kommuneinfo API, and the fifteenth - Nordland - is excluded here rather
    than anywhere downstream. Excluding it by fylke code at the point the
    region is built is the only place a reader can check the held-out region
    really is held out.
    """
    ext = extent()
    cfg = area[ext.cv_extent or area["spatial_cv"]["cv_extent"]]
    if "boundary_file" in cfg:
        fc = json.loads((ROOT / cfg["boundary_file"]).read_text(encoding="utf-8"))
        return Boundary(fc["features"][0]["geometry"], name=cfg["name"])

    excluded = str(cfg["exclude_code"])
    parts, left_out = [], []
    for f in sorted((ROOT / cfg["boundary_dir"]).glob("boundary_*_25833.geojson")):
        fc = json.loads(f.read_text(encoding="utf-8"))
        declared = fc.get("crs_declared_by_source")
        if declared and "25833" not in str(declared):
            raise SystemExit(f"{f.name} declares {declared}, expected EPSG:25833")
        props = fc["features"][0].get("properties", {})
        code = str(fc.get("fylke_code") or props.get("fylkesnummer") or "")
        name = f.stem.replace("boundary_", "").replace("_25833", "")
        if code == excluded:
            left_out.append(name)
            continue
        parts.append(Boundary(fc["features"][0]["geometry"], name=name))
    if not left_out:
        raise SystemExit(
            f"no fylke with code {excluded} under {cfg['boundary_dir']}; the "
            f"held-out region would end up inside the training extent")
    print(f"training extent: {len(parts)} fylker, holding out "
          f"{', '.join(left_out)} (code {excluded})", flush=True)
    return Boundaries(parts, name=cfg["name"])


def transfer_regions(area: dict):
    """`(region the presences are drawn from, the region held out of fitting)`.

    The two extents put the held-out region in opposite places and the
    difference is easy to get wrong.

    Kristiansand sits **inside** Agder, so Phase 0 draws one presence set over
    Agder and flags the subset that falls in the kommune. Nordland sits
    **outside** the national training extent by construction, so the draw
    region has to be the training fylker plus Nordland or the held-out
    presences would never be pulled at all - and the test would silently
    report on zero points.

    Splitting one set is what makes the two sides comparable: identical
    filtering, identical thinning, identical everything except which side of
    the line a point fell on.
    """
    ext = extent()
    train = cv_boundary(area)
    cfg = area[ext.holdout]
    fc = json.loads((ROOT / cfg["boundary_file"]).read_text(encoding="utf-8"))
    held = Boundary(fc["features"][0]["geometry"], name=cfg["name"])
    if isinstance(train, Boundaries):
        return Boundaries(list(train.parts) + [held],
                          name="Norway, mainland, all fylker"), held
    return train, held


def transfer_pool() -> list[dict]:
    """Background for the transfer test, covering both sides of the split.

    The national pool is cached as two lists, cut from one pass over 1.3
    million records so they cannot drift apart. The transfer test needs both:
    background inside Nordland is what the held-out AUC is measured against,
    and without it there is nothing for the held-out presences to be ranked
    above.
    """
    if extent().key == AGDER.key:
        return target_group_pool()
    return target_group_pool("training") + target_group_pool("holdout")


def target_group_pool(role: str = "training") -> list[dict]:
    """The cached target-group background pool for the current extent.

    Two file shapes, because they were built by different scripts for
    different jobs. Agder's is one flat list carrying each record's precision
    and Artskart id; the national one is split into a `training` and a
    `holdout` pool so the fourteen fylker and Nordland are cut from a single
    pass and cannot drift apart. Both have already had the extent, precision
    and thinning filters applied - in that order, matching the presences - so
    nothing here filters again.

    Only the coordinates are used downstream; `null_and_background` draws the
    null and the background disjointly from this list and reads nothing else.
    The national file stores them rounded to 0.1 m, which is four orders of
    magnitude below the 32 m cell being sampled.
    """
    ext = extent()
    cache = INTERIM / ext.pool
    if not cache.exists():
        script = ("scripts/build_background_pool.py" if ext.key == NORGE.key
                  else "scripts/report_background.py")
        raise SystemExit(f"missing {cache} - run {script} first")
    blob = json.loads(cache.read_text(encoding="utf-8"))
    if "pool" in blob:
        return [{"east": e, "north": n, "precision_m": pr, "id": i}
                for e, n, pr, i in blob["pool"]]
    if role not in blob.get("pools", {}):
        raise SystemExit(f"{cache.name} has no '{role}' pool; it holds "
                         f"{sorted(blob.get('pools', {}))}")
    return [{"east": e, "north": n, "precision_m": None, "id": None}
            for e, n in blob["pools"][role]["points"]]


def main() -> int:
    global INCLUDE_VOL_BOUNDS, INCLUDE_ELEVATION, INCLUDE_TWI, EXTENT
    tag = None
    if "--national" in sys.argv:
        EXTENT = NORGE.key
        # Not a flag the user may countermand: the national terrain raster has
        # five bands and no sixth to include.
        INCLUDE_TWI = False
    if "--no-elevation" in sys.argv:
        INCLUDE_ELEVATION = False
        tag = "no-elevation"
    if "--no-twi" in sys.argv:
        if EXTENT == NORGE.key:
            raise SystemExit("--no-twi is meaningless with --national: the "
                             "national terrain raster has no TWI band. See "
                             "FINDINGS section 18.")
        INCLUDE_TWI = False
        tag = "no-twi"
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    if "--no-vol-bounds" in sys.argv:
        INCLUDE_VOL_BOUNDS = False
        tag = tag or "no-vol-bounds"

    ext = extent()
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    cv = area["spatial_cv"]
    block_m, n_folds, buffer_m = cv["block_size_m"], cv["n_folds"], cv["buffer_m"]
    print(f"extent: {ext.label}  ->  data/processed/{ext.report}", flush=True)
    boundary = cv_boundary(area)

    stack = layers()
    if not stack:
        raise SystemExit("no feature rasters found - run scripts/build_terrain.py first")

    pool = target_group_pool()

    sets = {sp["scientific_name"]: presences(sp, boundary, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    models = [MaxentStyle(), LightGBM(), Constant()]

    print(f"features: {sum(len(l.resolve()[0]) for l in stack)} bands from "
          f"{len(stack)} raster(s)", flush=True)
    print(f"background: {len(bg_set.coords)} target-group points\n", flush=True)

    # Nationally the same 52,249 background points are passed to `build` nine
    # times, against rasters that cost milliseconds per point to read. Cached,
    # they are read once. Agder stays uncached: the cache cannot change a value
    # - tests/test_stack_cache.py pins that - but Phase 0's published numbers
    # have no reason to depend on a mechanism added for a different extent, and
    # the Agder run takes minutes either way.
    sample_cache = SampleCache() if ext.key == NORGE.key else None

    rows, importances, designs = [], {}, {}
    for name, coords in sets.items():
        d = build(coords, bg_set.coords, stack, cache=sample_cache)
        designs[name] = d
        print(f"{name}: {d.n_presence} presences, {d.n_background} background, "
              f"dropped {d.dropped['presence']} / {d.dropped['background']}", flush=True)
        X = [list(r) for r in d.X]
        y = list(d.y)
        pts = [tuple(c) for c in d.coords]

        for model in models:
            t0 = time.time()
            res = spatial_block_cv(pts, y, X, model, block_size_m=block_m,
                                   n_folds=n_folds, buffer_m=buffer_m)
            rows.append({"species": name, "model": model.name,
                         "auc": res.mean_auc, "auc_sd": res.sd_auc,
                         "boyce": res.mean_boyce, "boyce_sd": res.sd_boyce,
                         "boyce_q": res.mean_boyce_q, "boyce_q_sd": res.sd_boyce_q,
                         "warnings": res.warnings})
            print(f"  {model.name:48s} AUC {res.mean_auc:.3f} "
                  f"Boyce {res.mean_boyce:+.3f}/{res.mean_boyce_q:+.3f}  "
                  f"({time.time() - t0:.0f}s)", flush=True)

        # Importance across ALL folds, not one.
        #
        # This used to run on fold 0 alone, described as a qualitative
        # diagnostic. FINDINGS section 9d showed that was too weak to carry the
        # weight the writeup was putting on it: treslag_3 moved from +0.078 to
        # +0.036 between two runs whose AUCs agreed to three decimal places.
        # A number that unstable cannot rank features, and it was being read as
        # though it could. Averaging the five folds and reporting the spread
        # across them makes the instability visible instead of invisible.
        from src.validate.blocks import assign_blocks, make_folds
        fa = make_folds(assign_blocks(pts, block_m), y, n_folds=n_folds,
                        block_size_m=block_m, seed=0)
        # Importance is computed for BOTH models, not just the flexible one.
        # They can disagree completely - a linear model's reliance on a feature
        # says nothing about a tree ensemble's - and reporting only one model's
        # importances next to both models' AUCs would be explaining one number
        # with evidence drawn from another.
        for model in (MaxentStyle(), LightGBM()):
            per_fold: dict[str, list[float]] = {n: [] for n in d.names}
            for f in range(n_folds):
                tr = [i for i, g in enumerate(fa.fold_of_point) if g != f]
                te = [i for i, g in enumerate(fa.fold_of_point) if g == f]
                if buffer_m > 0:
                    dropped = _excluded_by_buffer(pts, tr, te, buffer_m)
                    tr = [i for i in tr if i not in dropped]
                if not tr or not te or sum(y[i] for i in te) == 0:
                    continue
                got = permutation_importance(
                    model, [X[i] for i in tr], [y[i] for i in tr],
                    [X[i] for i in te], [y[i] for i in te], d.names,
                    roc_auc, n_repeats=5)
                for feat, stats in got.items():
                    per_fold[feat].append(stats["mean_drop"])
            importances.setdefault(model.name, {})[name] = {
                feat: {"mean_drop": float(np.mean(vals)) if vals else float("nan"),
                       "sd": float(np.std(vals)) if len(vals) > 1 else float("nan"),
                       "n_folds": len(vals)}
                for feat, vals in per_fold.items()
            }

    write_report(rows, importances, designs, spp, stack, block_m, n_folds, buffer_m, tag)
    return 0


def write_report(rows, importances, designs, spp, stack, block_m, n_folds,
                 buffer_m, tag) -> None:
    from datetime import date

    feature_names = designs[next(iter(designs))].names
    null_label = spp["null_control"]["label"]
    # The ordering test judges the three pre-registered species and no others.
    # Species added later have no advance prediction to be scored against, so
    # including them here would be inventing one after seeing the result.
    order = [sp["scientific_name"] for sp in species_cfg.registered(spp)]
    extra = [sp["scientific_name"] for sp in species_cfg.additional(spp)]

    w = []
    a = w.append
    a("# Model results - spatially blocked\n")
    a(f"Generated by `scripts/run_models.py` on {date.today().isoformat()}"
      + (f", tagged `{tag}`" if tag else "") + ".\n")
    a(f"**Extent:** {extent().label}.\n")
    a(f"{block_m:.0f} m blocks, {n_folds} folds, {buffer_m:.0f} m exclusion buffer, "
      "target-group background.\n")
    a(f"**Features ({len(feature_names)}):** {', '.join(feature_names)}.\n")

    a("## Blocked AUC and continuous Boyce index\n")
    a("Two Boyce columns, deliberately. The published equal-width index is")
    a("biased downward on scores as skewed as these - measured at real fold")
    a("sizes it returns about -0.42 for a model with **no skill at all**, where")
    a("zero is the right answer - so its sign cannot be read on its own. The")
    a("quantile-window version returns about -0.01 on the same synthetic case.")
    a("Both are shown so neither is taken on trust; the diagnosis is in")
    a("[boyce_diagnosis.md](boyce_diagnosis.md).\n")
    a("| species | model | AUC | Boyce (published) | Boyce (quantile) |")
    a("|---|---|---|---|---|")
    for r in rows:
        a(f"| {r['species']} | {r['model']} | {r['auc']:.3f} +/- {r['auc_sd']:.3f} "
          f"| {r['boyce']:+.3f} +/- {r['boyce_sd']:.3f} "
          f"| {r['boyce_q']:+.3f} +/- {r['boyce_q_sd']:.3f} |")
    a("")
    a("Every number here is from spatial block cross-validation. No random-split")
    a("figure appears in this table; CLAUDE.md forbids reporting one without its")
    a("blocked counterpart, and the blocked figure is the only one that means")
    a("anything on autocorrelated occurrence data.\n")

    # --- the null control, first, because nothing else counts if it fails ---
    a("## Null control\n")
    best_null = max((r for r in rows if r["species"] == null_label
                     and "constant" not in r["model"]),
                    key=lambda r: r["auc"], default=None)
    if best_null is None:
        a("**The null did not run.** Nothing in this table may be interpreted.\n")
    elif best_null["auc"] > NULL_CEILING:
        a(f"**FAIL - the synthetic null scored {best_null['auc']:.3f}, above the "
          f"{NULL_CEILING:.2f} ceiling.**\n")
        a("The null has no ecology in it at all: its presences are drawn from the")
        a("sampling-effort surface. Scoring above chance means the background")
        a("sampling is broken, and CLAUDE.md is explicit that nothing else in this")
        a("run means anything until that is fixed. Do not interpret the species")
        a("rows above.\n")
    else:
        a(f"The synthetic null scored **{best_null['auc']:.3f}** against a "
          f"{NULL_CEILING:.2f} ceiling, so the background sampling is not")
        a("visibly broken. This is a necessary condition for reading the rest of")
        a("the table, not evidence that the species results are real.\n")

    # --- the ordering test ---
    a("## Predicted ordering\n")
    a("Registered in FINDINGS before any model existed: *C. tubaeformis* best,")
    a("*C. cibarius* middle, *A. muscaria* worst.\n")
    for model_name in {r["model"] for r in rows} - {Constant().name}:
        got = [(r["species"], r["auc"]) for r in rows
               if r["model"] == model_name and r["species"] in order]
        ranked = [s for s, _ in sorted(got, key=lambda t: -t[1])]
        verdict = "**PASS**" if ranked == order else "**FAIL**"
        a(f"* {model_name}: {' > '.join(ranked)} - {verdict}")
    a("")
    if extra:
        a("")
        a(f"{len(extra)} further species are fitted and reported above "
          f"({', '.join(f'*{e}*' for e in extra)}) but take no part in this")
        a("verdict. No ordering was registered for them, so there is nothing to")
        a("score them against, and reading their results back into a prediction")
        a("made before they existed would defeat the point of registering it.\n")
    a("A failed ordering is a result, not a bug. CLAUDE.md: if all three score")
    a("similarly we are modelling recording effort, and the bias correction has")
    a("to be fixed before anything else is believed.\n")

    if importances:
        a("## Permutation importance, averaged across all folds\n")
        a("Drop in AUC when each feature is shuffled in the held-out fold,")
        a(f"against a model fitted on the others, meaned over all {n_folds} folds")
        a("with the fold-to-fold spread beside it. Reported per model, because")
        a("the two need not agree and the AUC table above reports both.\n")
        a("**Read the spread before the mean.** An earlier version of this table")
        a("used one fold, and FINDINGS section 9d showed that was far too noisy")
        a("to rank anything below the top two features - a value moved from")
        a("+0.078 to +0.036 between two runs whose AUCs agreed to three decimal")
        a("places. Where the spread is comparable to the mean, the feature has")
        a("no established rank and should not be given one in prose.\n")
        a("This is also the check FINDINGS section 6 committed to: autumn")
        a("rainfall follows the same coast-inland gradient as the population, so")
        a("a model leaning on it may be reading accessibility rather than")
        a("ecology.\n")
        a("A caution about the null column. Its importances are near zero for")
        a("every feature, which is *not* evidence that the features are free of")
        a("effort bias. The null's presences are drawn from the same pool as the")
        a("background, so the two are environmentally identical by construction")
        a("and no feature can separate them. That column checks that the")
        a("pipeline does not leak, and nothing more.\n")
        for model_name, per_species in importances.items():
            a(f"### {model_name}\n")
            a("| feature | " + " | ".join(per_species) + " |")
            a("|---" * (len(per_species) + 1) + "|")
            for feat in feature_names:
                cells = []
                for s in per_species:
                    st = per_species[s].get(feat, {})
                    m = st.get("mean_drop", float("nan"))
                    sd = st.get("sd", float("nan"))
                    cells.append(f"{m:+.3f} ±{sd:.3f}" if sd == sd else f"{m:+.3f}")
                a(f"| {feat} | " + " | ".join(cells) + " |")
            a("")

    a("## Points dropped for missing features\n")
    a("| set | presences | background | presences dropped | background dropped |")
    a("|---|---|---|---|---|")
    for name, d in designs.items():
        a(f"| {name} | {d.n_presence} | {d.n_background} | "
          f"{d.dropped['presence']} ({d.dropped['presence_pct']:.1f}%) | "
          f"{d.dropped['background']} ({d.dropped['background_pct']:.1f}%) |")
    a("")

    warnings = {wn for r in rows for wn in r["warnings"]}
    if warnings:
        a("## Warnings raised by the harness\n")
        for wn in sorted(warnings):
            a(f"* {wn}")
        a("")

    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(w)
    # A tagged run is a variant - one feature dropped, one bound relaxed - and
    # must not overwrite the canonical results. It used to, so running
    # `--no-elevation` to compare against the baseline silently replaced the
    # baseline with the very thing being compared, and the comparison then
    # reported a change of exactly 0.000 for every species. That reads like a
    # broken experiment rather than a clobbered file, and the only reason the
    # real numbers survived is that model_results.md had been committed.
    #
    # Tagged runs now write only their archive below. FINDINGS links to both.
    #
    # The extent picks the filename, so a national run cannot land on Phase 0's
    # published results however it is invoked. That is deliberately structural
    # rather than a check: the same mistake has already been made three times
    # in this project, and each time the file that was overwritten looked
    # entirely normal afterwards.
    if not tag:
        out.write_text(body, encoding="utf-8")
    print("\n" + body)
    if not tag:
        print(f"written: {out.relative_to(ROOT)}")

    # Also keep a per-tag copy. `model_results.md` is the latest run and every
    # run overwrites it, which quietly left FINDINGS section 8 linking to a file
    # that no longer held the numbers section 8 quotes. A results file that
    # cannot be got back is not a record.
    if tag:
        archive = out.parent / "runs" / f"{out.stem}_{tag}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        # The body is written for `data/processed/`, so its sibling links have
        # to be lifted a directory on the way into `runs/`. Harmless while these
        # were local files; a dead link the moment the repository is published.
        archive.write_text(body.replace("](boyce_diagnosis.md)",
                                        "](../boyce_diagnosis.md)"),
                           encoding="utf-8")
        print(f"written: {archive.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
