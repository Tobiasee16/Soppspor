"""The transferability test: fit on Agder, evaluate once on Kristiansand.

CLAUDE.md's study-area section is explicit that the two extents exist for this
reason - "we are asking whether a model fitted regionally holds up in a smaller
held-out area" - and that it must be said so explicitly in the writeup. Every
number produced before this script was Agder-internal: spatially blocked, but
still trained and tested inside one region.

    python scripts/run_transfer.py
    python scripts/run_transfer.py --tag no-vol-bounds

The feature stack, the presence filtering, the thinning and the background pool
are imported from `run_models.py` rather than restated, so the two cannot drift
apart. If the transfer test ever used a different stack from the blocked CV,
comparing their numbers would be meaningless.

This is not cross-validation. There is one test set and one number per model,
with a bootstrap interval over the test points. It is reported that way.
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

import yaml

import run_models as rm
from src import species as species_cfg
from src.features.background import null_and_background
from src.features.stack import build
from src.geometry import Boundary
from src.model.models import Constant, LightGBM, MaxentStyle
from src.validate.harness import held_out_extent

INTERIM = ROOT / "data" / "interim"
OUT = ROOT / "data" / "processed" / "transferability.md"


def main() -> int:
    tag = None
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    if "--no-vol-bounds" in sys.argv:
        rm.INCLUDE_VOL_BOUNDS = False
        tag = tag or "no-vol-bounds"

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    buffer_m = area["spatial_cv"]["buffer_m"]

    train_cfg = area["training_extent"]
    pred_cfg = area["prediction_extent"]
    train_fc = json.loads((ROOT / train_cfg["boundary_file"]).read_text(encoding="utf-8"))
    pred_fc = json.loads((ROOT / pred_cfg["boundary_file"]).read_text(encoding="utf-8"))
    training = Boundary(train_fc["features"][0]["geometry"], name=train_cfg["name"])
    prediction = Boundary(pred_fc["features"][0]["geometry"], name=pred_cfg["name"])

    stack = rm.layers()
    if not stack:
        raise SystemExit("no feature rasters found - run scripts/build_terrain.py first")

    cache = INTERIM / "target_group_pool_agder.json"
    if not cache.exists():
        raise SystemExit(f"missing {cache} - run scripts/report_background.py first")
    pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
            for e, n, pr, i in json.loads(cache.read_text(encoding="utf-8"))["pool"]]

    # Presence sets over the whole training extent. Kristiansand sits inside
    # Agder, so the held-out points are a subset of these rather than a
    # separate pull - splitting one set is the only way to be certain the same
    # filtering and thinning applied to both sides.
    sets = {sp["scientific_name"]: rm.presences(sp, training, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    models = [MaxentStyle(), LightGBM(), Constant()]
    print(f"features: {sum(len(l.resolve()[0]) for l in stack)} bands from "
          f"{len(stack)} raster(s)")
    print(f"holding out: {prediction.name}\n", flush=True)

    rows, counts = [], {}
    for name, coords in sets.items():
        d = build(coords, bg_set.coords, stack)
        pts = [tuple(c) for c in d.coords]
        X = [list(r) for r in d.X]
        y = list(d.y)
        # Membership is tested AFTER `build`, on the surviving rows, so the
        # flags line up with X and y even though build drops rows for missing
        # features.
        in_test = [(e, n) in prediction for e, n in pts]

        n_test_pres = sum(1 for flag, label in zip(in_test, y) if flag and label == 1)
        counts[name] = {
            "presences_total": d.n_presence,
            "presences_held_out": n_test_pres,
            "background_held_out": sum(1 for flag, label in zip(in_test, y)
                                       if flag and label != 1),
        }
        print(f"{name}: {d.n_presence} presences, {n_test_pres} of them in "
              f"{prediction.name}", flush=True)
        if n_test_pres == 0:
            print("  no held-out presences; skipping", flush=True)
            continue

        for model in models:
            t0 = time.time()
            res = held_out_extent(pts, y, X, model, in_test, buffer_m=buffer_m)
            rows.append({"species": name, "model": model.name, "res": res})
            print(f"  {model.name:48s} AUC {res.auc:.3f} "
                  f"[{res.auc_lo:.3f}-{res.auc_hi:.3f}]  "
                  f"Boyce {res.boyce:+.3f}/{res.boyce_q:+.3f}  "
                  f"({time.time() - t0:.0f}s)", flush=True)

    write_report(rows, counts, spp, stack, train_cfg, pred_cfg, buffer_m, tag)
    return 0


def write_report(rows, counts, spp, stack, train_cfg, pred_cfg, buffer_m, tag) -> None:
    from datetime import date

    null_label = spp["null_control"]["label"]
    # Only the pre-registered three are judged against the registered ordering.
    order = [sp["scientific_name"] for sp in species_cfg.registered(spp)]
    extra = [sp["scientific_name"] for sp in species_cfg.additional(spp)]
    names = []
    for layer in stack:
        names.extend(layer.resolve()[1] if layer.categories is None
                     else [f"{n}_{s}" for n in layer.resolve()[1]
                           for s in layer.suffixes() + (["missing"] if layer.missing_indicator else [])])

    w = []
    a = w.append
    a("# Transferability - fitted on Agder, evaluated once on Kristiansand\n")
    a(f"Generated by `scripts/run_transfer.py` on {date.today().isoformat()}"
      + (f", tagged `{tag}`" if tag else "") + ".\n")
    a(f"**Training extent:** {train_cfg['name']}, minus the held-out extent and "
      f"everything within {buffer_m:.0f} m of it.")
    a(f"**Held-out extent:** {pred_cfg['name']}. Never seen during fitting.\n")
    a("This is the question CLAUDE.md's study-area section poses: does a model")
    a("fitted regionally hold up in a smaller area it was not trained on? Every")
    a("other number in this project is Agder-internal - spatially blocked, but")
    a("trained and tested inside one region.\n")
    a("**It is not cross-validation.** There is one test set and one number per")
    a("model. The interval is a percentile bootstrap over the held-out points,")
    a("which covers sampling variation in those points and nothing else - in")
    a("particular it says nothing about whether Kristiansand's records are a")
    a("fair sample of Kristiansand, which on effort-biased data is the larger")
    a("worry.\n")

    a("## Held-out record counts\n")
    a("| set | presences in Agder | held out in Kristiansand | held-out background |")
    a("|---|---|---|---|")
    for name in list(order) + extra + [null_label]:
        c = counts.get(name)
        if c:
            a(f"| {name} | {c['presences_total']} | {c['presences_held_out']} | "
              f"{c['background_held_out']} |")
    a("")

    a("## Result\n")
    a("| species | model | AUC | bootstrap 95% CI | Boyce (published) | Boyce (quantile) |")
    a("|---|---|---|---|---|---|")
    for r in rows:
        res = r["res"]
        a(f"| {r['species']} | {r['model']} | {res.auc:.3f} | "
          f"{res.auc_lo:.3f}-{res.auc_hi:.3f} | {res.boyce:+.3f} | "
          f"{res.boyce_q:+.3f} |")
    a("")
    a("The published Boyce column has a zero point near -0.42 on scores this")
    a("skewed and cannot be read on its own; see")
    a("[boyce_diagnosis.md](boyce_diagnosis.md). Both are shown for the same")
    a("reason as in the blocked-CV table.\n")

    a("## Null control\n")
    null_rows = [r for r in rows if r["species"] == null_label
                 and "constant" not in r["model"]]
    if not null_rows:
        a("**The null did not run.** Nothing in this table may be interpreted.\n")
    else:
        best = max(null_rows, key=lambda r: r["res"].auc)
        if best["res"].auc > rm.NULL_CEILING:
            a(f"**FAIL - the synthetic null scored {best['res'].auc:.3f} on the "
              f"held-out extent, above the {rm.NULL_CEILING:.2f} ceiling.**\n")
            a("The null carries no ecology. Scoring above chance here means the")
            a("background sampling or the extent split is broken, and nothing")
            a("else in this table means anything until that is fixed.\n")
        else:
            a(f"The synthetic null scored **{best['res'].auc:.3f}** on the held-out")
            a(f"extent, against a {rm.NULL_CEILING:.2f} ceiling. The transfer split")
            a("does not by itself manufacture signal.\n")

    a("## Predicted ordering, on the held-out extent\n")
    a("The same registered prediction, judged on transfer rather than on")
    a("blocked CV: *C. tubaeformis* best, *C. cibarius* middle, *A. muscaria*")
    a("worst.\n")
    for model_name in sorted({r["model"] for r in rows} - {Constant().name}):
        got = [(r["species"], r["res"].auc) for r in rows
               if r["model"] == model_name and r["species"] in order]
        if len(got) != len(order):
            continue
        ranked = [s for s, _ in sorted(got, key=lambda t: -t[1])]
        a(f"* {model_name}: {' > '.join(ranked)} - "
          + ("**PASS**" if ranked == order else "**FAIL**"))
    a("")
    a("Read this against the blocked-CV ordering in")
    a("[model_results.md](model_results.md), not instead of it. Agreement")
    a("between the two is the interesting case; disagreement means the blocked")
    a("figure did not survive being moved.\n")

    a("## Warnings raised by the harness\n")
    seen = set()
    for r in rows:
        for warn in r["res"].warnings:
            if warn not in seen:
                seen.add(warn)
                a(f"* {warn}")
    if not seen:
        a("* none")
    a("")
    a("## Training rows removed by the boundary buffer\n")
    a("| species | model | training rows kept | removed by buffer |")
    a("|---|---|---|---|")
    for r in rows:
        if "constant" in r["model"]:
            continue
        a(f"| {r['species']} | {r['model']} | {r['res'].n_train} | "
          f"{r['res'].n_buffered} |")
    a("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(w), encoding="utf-8")
    print("\n" + "\n".join(w))
    print(f"written: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
