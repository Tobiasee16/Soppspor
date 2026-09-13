"""The transferability test: fit on one extent, evaluate once on a held-out one.

CLAUDE.md's study-area section is explicit that the two extents exist for this
reason - "we are asking whether a model fitted regionally holds up in a smaller
held-out area" - and that it must be said so explicitly in the writeup. Every
number produced before this script was internal to one region: spatially
blocked, but trained and tested inside it.

    python scripts/run_transfer.py              # Agder    -> Kristiansand
    python scripts/run_transfer.py --national   # 14 fylker -> Nordland
    python scripts/run_transfer.py --national --no-climate
    python scripts/run_transfer.py --tag no-vol-bounds

The two pairs sit differently and `rm.transfer_regions` handles the difference.
Kristiansand is *inside* Agder, so Phase 0 draws one presence set and flags the
subset that falls in the kommune. Nordland is *outside* the national training
extent by construction, so the draw region there is the fourteen training
fylker plus Nordland - otherwise no held-out presence would be pulled at all
and the test would report confidently on nothing.

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
from src.features.stack import SampleCache, build
from src.geometry import Boundary
from src.model.models import Constant, LightGBM, MaxentStyle
from src.validate.harness import held_out_extent

INTERIM = ROOT / "data" / "interim"


def out_path():
    """Where this run writes. Extent-derived, so the two can never collide."""
    return ROOT / "data" / "processed" / rm.extent().transfer_report


def main() -> int:
    tag = None
    if "--national" in sys.argv:
        rm.EXTENT = rm.NORGE.key
        rm.INCLUDE_TWI = False
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
    if "--no-climate" in sys.argv:
        # Run this against the blocked-CV ablation, not on its own. The two
        # answer different halves of one question, and it is the CONTRAST that
        # is informative:
        #
        #   ecology       - removing rainfall hurts the moisture-dependent
        #                   species in BOTH, because the thing being removed is
        #                   real habitat information wherever you stand.
        #   accessibility - removing it hurts blocked CV, where effort bias is
        #                   shared within a region, but does little to transfer,
        #                   because a coast-inland proxy fitted on southern
        #                   Norway has no reason to hold in Nordland.
        #
        # See FINDINGS section 6 for the registration and section 22 for what
        # came out.
        rm.INCLUDE_CLIMATE = False
        tag = tag or "no-climate"
    if "--no-vol-bounds" in sys.argv:
        rm.INCLUDE_VOL_BOUNDS = False
        tag = tag or "no-vol-bounds"

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    buffer_m = area["spatial_cv"]["buffer_m"]

    ext = rm.extent()
    stem = pathlib.Path(ext.transfer_report).stem
    dest = (f"data/processed/runs/{stem}_{tag}.md" if tag
            else f"data/processed/{ext.transfer_report}")
    print(f"extent: {ext.label}  ->  {dest}", flush=True)
    train_cfg = area[ext.cv_extent or area["spatial_cv"]["cv_extent"]]
    pred_cfg = area[ext.holdout]
    training, prediction = rm.transfer_regions(area)

    stack = rm.layers()
    if not stack:
        raise SystemExit("no feature rasters found - run scripts/build_terrain.py first")

    pool = rm.transfer_pool()

    # Presence sets over the whole draw region, flagged afterwards rather than
    # pulled twice. Kristiansand sits inside Agder and Nordland sits outside
    # the national training extent, so `transfer_regions` returns a region that
    # covers both sides in either case - splitting one set is the only way to
    # be certain the same filtering and thinning applied to both.
    sets = {sp["scientific_name"]: rm.presences(sp, training, max_prec, thin_m)
            for sp in species_cfg.modelled(spp)}
    n_null = max(len(v) for v in sets.values())
    null_set, bg_set = null_and_background(pool, n_null=n_null, seed=0)
    sets[spp["null_control"]["label"]] = null_set.coords

    models = [MaxentStyle(), LightGBM(), Constant()]
    print(f"features: {sum(len(l.resolve()[0]) for l in stack)} bands from "
          f"{len(stack)} raster(s)")
    print(f"holding out: {prediction.name}\n", flush=True)

    sample_cache = SampleCache() if ext.key == rm.NORGE.key else None

    rows, counts = [], {}
    for name, coords in sets.items():
        d = build(coords, bg_set.coords, stack, cache=sample_cache)
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
    train_name = train_cfg["name"]
    held_name = pred_cfg["name"]
    # Short labels for table headers, where the full config names do not fit.
    held_short = held_name.split(" fylke")[0].split(" kommune")[0]
    train_short = train_name.split(",")[0].split(" fylke")[0]
    if "exclude_code" in train_cfg:
        # The national training extent is defined by what it leaves out, and
        # a title reading "fitted on Norway, evaluated on Nordland" would
        # describe a leak rather than a test.
        train_short = f"{train_short} minus {held_short}"

    a(f"# Transferability - fitted on {train_short}, evaluated once on {held_short}\n")
    a(f"Generated by `scripts/run_transfer.py` on {date.today().isoformat()}"
      + (f", tagged `{tag}`" if tag else "") + ".\n")
    a(f"**Training extent:** {train_name}, minus the held-out extent and "
      f"everything within {buffer_m:.0f} m of it.")
    a(f"**Held-out extent:** {held_name}. Never seen during fitting.\n")
    a("This is the question CLAUDE.md's study-area section poses: does a model")
    a("fitted regionally hold up in an area it was not trained on? Every other")
    a("number in this project is internal to its own training region -")
    a("spatially blocked, but trained and tested inside one extent.\n")
    a("**It is not cross-validation.** There is one test set and one number per")
    a("model. The interval is a percentile bootstrap over the held-out points,")
    a("which covers sampling variation in those points and nothing else - in")
    a(f"particular it says nothing about whether {held_short}'s records are a")
    a(f"fair sample of {held_short}, which on effort-biased data is the larger")
    a("worry.\n")

    a("## Held-out record counts\n")
    a(f"| set | presences in {train_short} | held out in {held_short} "
      f"| held-out background |")
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

    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(w)
    # A tagged run is a variant and must not land on the canonical file. This
    # script used to write it unconditionally, which is exactly the bug
    # run_models.py has a paragraph about: `--tag no-vol-bounds` replaced the
    # baseline with the thing being compared against it, and the comparison
    # then reported no difference. Found while giving this script a second
    # extent, in code that had to be touched anyway.
    if not tag:
        out.write_text(body, encoding="utf-8")
    print("\n" + body)
    if not tag:
        print(f"written: {out.relative_to(ROOT)}")
    else:
        archive = out.parent / "runs" / f"{out.stem}_{tag}.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(body.replace("](boyce_diagnosis.md)",
                                        "](../boyce_diagnosis.md)"),
                           encoding="utf-8")
        print(f"written: {archive.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
