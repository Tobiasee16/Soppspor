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
    python scripts/run_models.py --tag terrain-only
    python scripts/run_models.py --no-vol-bounds
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
import yaml

from src.features.background import null_and_background
from src.features.stack import RasterLayer, build
from src.features.thin import thin
from src.geometry import Boundary
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
OUT = ROOT / "data" / "processed" / "model_results.md"

AGDER_NAMES = {"Agder", "Aust-Agder", "Vest-Agder"}

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


def layers() -> list[RasterLayer]:
    """The feature stack, in the order it will be reported.

    AR5 is absent: it needs a Geonorge login with the FKB role. Whatever is
    present is listed in the output, so a run can never quietly claim more
    features than it used.
    """
    out = [RasterLayer(str(INTERIM / "terrain_agder_32m.tif"), method="nearest")]

    climate = INTERIM / "autumn_rain_agder_1km.tif"
    if climate.exists():
        # Bilinear: sampled nearest, a 1 km field gives neighbouring points a
        # few hundred metres apart identical values for no physical reason.
        out.append(RasterLayer(str(climate), bands=[1], names=["autumn_rain_mm"],
                               method="bilinear"))

    sr16 = INTERIM / "sr16"
    treslag = sr16 / "sr16_42_SRRTRESLAG.tif"
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
        path = sr16 / f"sr16_42_SRR{code}.tif"
        if path.exists():
            out.append(RasterLayer(str(path), bands=[1], names=[name],
                                   missing_as=0.0))

    # Uncertainty, propagated rather than hidden, as CLAUDE.md asks. SR16 ships
    # every variable with lower and upper bounds; published validation puts
    # volume error at 30-60% of the mean. Carrying the bounds for volume lets
    # the model see where the forest estimate is weak instead of treating a
    # wide interval and a tight one as the same number.
    vol = sr16 / "sr16_42_SRRVOLMB.tif"
    if vol.exists() and INCLUDE_VOL_BOUNDS:
        out.append(RasterLayer(str(vol), bands=[2, 3],
                               names=["vol_lower", "vol_upper"], missing_as=0.0))

    # NGU superficial deposits, collapsed to ten depositional groups. The
    # grouping is fixed in src/ingest/losmasser.py and was registered, with a
    # prediction for what this layer would do, in FINDINGS section 8 before it
    # was ever fitted. Categorical, so nearest sampling and one-hot: halfway
    # between peat and bare rock is not a substrate.
    losmasser = INTERIM / "losmasser_agder_32m.tif"
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
    ar50 = INTERIM / "ar50_agder_32m.tif"
    if ar50.exists():
        out.append(RasterLayer(str(ar50), bands=[1], names=["ar50"],
                               categories=AR50_CATEGORIES, missing_as=0.0,
                               missing_indicator=True,
                               category_labels=[AR50_LABELS[c]
                                                for c in AR50_CATEGORIES]))

    return [layer for layer in out if pathlib.Path(layer.path).exists()]


def presences(sp: dict, boundary: Boundary, max_prec: float, thin_m: float):
    obs = [o for o in artskart.load(RAW / f"{sp['key']}.json")
           if o.get("County") in AGDER_NAMES]
    pts = artskart.usable_points(obs)
    pts = [p for p in pts if (p["east"], p["north"]) in boundary]
    pts = [p for p in pts if p["precision_m"] is not None and p["precision_m"] <= max_prec]
    return [(p["east"], p["north"]) for p in thin(pts, thin_m)]


def main() -> int:
    global INCLUDE_VOL_BOUNDS
    tag = None
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    if "--no-vol-bounds" in sys.argv:
        INCLUDE_VOL_BOUNDS = False
        tag = tag or "no-vol-bounds"

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    cv = area["spatial_cv"]
    block_m, n_folds, buffer_m = cv["block_size_m"], cv["n_folds"], cv["buffer_m"]
    fc = json.loads((ROOT / area[cv["cv_extent"]]["boundary_file"]).read_text(encoding="utf-8"))
    boundary = Boundary(fc["features"][0]["geometry"], name=area[cv["cv_extent"]]["name"])

    stack = layers()
    if not stack:
        raise SystemExit("no feature rasters found - run scripts/build_terrain.py first")

    cache = INTERIM / "target_group_pool_agder.json"
    if not cache.exists():
        raise SystemExit(f"missing {cache} - run scripts/report_background.py first")
    pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
            for e, n, pr, i in json.loads(cache.read_text(encoding="utf-8"))["pool"]]

    sets = {sp["scientific_name"]: presences(sp, boundary, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    models = [MaxentStyle(), LightGBM(), Constant()]

    print(f"features: {sum(len(l.resolve()[0]) for l in stack)} bands from "
          f"{len(stack)} raster(s)", flush=True)
    print(f"background: {len(bg_set.coords)} target-group points\n", flush=True)

    rows, importances, designs = [], {}, {}
    for name, coords in sets.items():
        d = build(coords, bg_set.coords, stack)
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

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(w)
    OUT.write_text(body, encoding="utf-8")
    print("\n" + body)
    print(f"written: {OUT.relative_to(ROOT)}")

    # Also keep a per-tag copy. `model_results.md` is the latest run and every
    # run overwrites it, which quietly left FINDINGS section 8 linking to a file
    # that no longer held the numbers section 8 quotes. A results file that
    # cannot be got back is not a record.
    if tag:
        archive = OUT.parent / "runs" / f"model_results_{tag}.md"
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
