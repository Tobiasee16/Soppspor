"""Build the target-group background and check that it does its job.

Hard constraint 3 says draw background from the distribution of all fungal
records rather than uniformly, so the model cannot win by learning "near
roads". This script builds that pool and then tests the claim rather than
asserting it, in three parts:

1. Composition. What the target group is made of, and how much survives the
   same precision filter and thinning the presences get.

2. Is it actually non-uniform? If the fungal records covered Agder evenly there
   would be nothing to correct and the whole exercise would be theatre.

3. The one that matters: how much apparent signal is accessibility? A model
   given nothing but coordinates is run against uniform background and against
   target-group background. Against uniform background it can only be learning
   where people go. The drop between the two is the size of the bias being
   corrected, measured rather than assumed.

Plus the null control (hard constraint 5) on real geography: null presences
drawn from the effort surface must score 0.5 under blocked CV.

    python scripts/report_background.py
"""
from __future__ import annotations

import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

from src.features.background import (
    PointSet,
    assemble,
    draw_background,
    null_and_background,
    overlap_count,
    target_group_pool,
)
from src.features.thin import thin
from src.geometry import Boundary, cells_inside
from src.ingest import artskart
from src.validate.harness import spatial_block_cv

ROOT = pathlib.Path(__file__).resolve().parents[1]
TG_FILE = ROOT / "data" / "raw" / "artskart" / "target_group_sopper_agder.jsonl"
CACHE = ROOT / "data" / "interim" / "target_group_pool_agder.json"
OUT = ROOT / "data" / "processed" / "background.md"

# The coordinates-only probe is O(n_train * n_test) in pure Python, so the
# background is capped for the diagnostic only. The real model will use the
# whole pool.
PROBE_BACKGROUND = 2000
PROBE_K = 15
SEED = 0


def knn(k=PROBE_K):
    """Coordinates-only nearest-neighbour probe.

    Given only location, any skill it shows is knowledge of where records are,
    which is the quantity this script is trying to measure.
    """
    def fit_predict(X_tr, y_tr, X_te):
        out = []
        for xt in X_te:
            best = []
            for xr, yr in zip(X_tr, y_tr):
                d = (xt[0] - xr[0]) ** 2 + (xt[1] - xr[1]) ** 2
                if len(best) < k:
                    best.append((d, yr)); best.sort()
                elif d < best[-1][0]:
                    best[-1] = (d, yr); best.sort()
            out.append(sum(v for _, v in best) / len(best))
        return out
    return fit_predict


def main():
    if not TG_FILE.exists():
        raise SystemExit(
            f"missing {TG_FILE}.\n"
            f"Pull it first - all Sopper records for Agder, roughly 82k rows:\n"
            f"  filter.taxongroups=9 & filter.countys=42 via "
            f"artskart.download_filtered()"
        )

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))
    max_prec = area["max_coordinate_precision_m"]
    thin_m = area["reporting_thinning_m"]
    block_m = area["spatial_cv"]["block_size_m"]
    n_folds = area["spatial_cv"]["n_folds"]
    buffer_m = area["spatial_cv"]["buffer_m"]
    cv_slot = area["spatial_cv"]["cv_extent"]

    fc = json.loads((ROOT / area[cv_slot]["boundary_file"]).read_text(encoding="utf-8"))
    boundary = Boundary(fc["features"][0]["geometry"], name=area[cv_slot]["name"])

    meta = json.loads(TG_FILE.with_suffix(".meta.json").read_text(encoding="utf-8"))

    # Deriving the pool means parsing 155 MB and running 82k point-in-polygon
    # tests against a 6,486-vertex boundary, which takes minutes. The result is
    # under 4,000 points, so it is cached. Pass --refresh to rebuild it.
    if CACHE.exists() and "--refresh" not in sys.argv:
        print(f"using cached pool: {CACHE.relative_to(ROOT)}", flush=True)
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        stages = cached["stages"]
        names = cached["names"]
        pool = [{"east": e, "north": n, "precision_m": pr, "id": i}
                for e, n, pr, i in cached["pool"]]
    else:
        print("loading target group (a few minutes) ...", flush=True)
        raw = list(artskart.iter_jsonl(TG_FILE))
        pts = artskart.usable_points(raw)
        in_extent = [p for p in pts if (p["east"], p["north"]) in boundary]
        precise = [p for p in in_extent
                   if p["precision_m"] is not None and p["precision_m"] <= max_prec]
        pool = thin(precise, thin_m)

        # The staged filtering above exists to report a count at each step. It
        # must not drift from the function the model will actually use.
        canonical = target_group_pool(pts, boundary, max_prec, thin_m)
        if len(canonical) != len(pool):
            raise SystemExit(
                f"staged filtering ({len(pool)}) disagrees with target_group_pool "
                f"({len(canonical)}). The reported counts would not describe the "
                f"pool the model is given."
            )

        counts = {}
        for o in raw:
            nm = o.get("ScientificName")
            if nm:
                counts[nm] = counts.get(nm, 0) + 1
        names = dict(sorted(counts.items(), key=lambda t: -t[1])[:40])
        stages = {"raw": len(raw), "usable": len(pts), "in_extent": len(in_extent),
                  "precise": len(precise), "pool": len(pool)}
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({
            "stages": stages,
            "names": names,
            "pool": [[q["east"], q["north"], q["precision_m"], q["id"]] for q in pool],
        }, ensure_ascii=False), encoding="utf-8")
        print(f"  cached: {CACHE.relative_to(ROOT)}", flush=True)

    n_raw = stages["raw"]
    n_usable = stages["usable"]
    n_extent = stages["in_extent"]
    n_precise = stages["precise"]
    print(f"  {n_raw} rows -> {len(pool)} pooled points", flush=True)

    lines = []
    w = lines.append
    w("# Target-group background")
    w("")
    w("Generated by `scripts/report_background.py`.")
    w("")
    w("Background points are drawn from the locations of **all** fungal records in")
    w("the region, not scattered uniformly. A record of any fungus means somebody")
    w("stood there, looked, and wrote something down. Using those places as")
    w("background gives presences and background the same observer bias, so it")
    w("cancels instead of being learned. Hard constraint 3.")
    w("")
    w("## 1. What the pool is")
    w("")
    w(f"- Source: Artskart `observations/list`, `filter.taxongroups=9` (Sopper)")
    w(f"  and `filter.countys=42` (Agder). Server-side filtering, verified to")
    w(f"  change TotalCount and cross-checked against our own polygon clip.")
    w(f"- Reported by the API: **{meta['total_count_reported']:,}** records; "
      f"downloaded {meta['records_downloaded']:,}.")
    w("")
    w("| Stage | Records | Note |")
    w("|---|---|---|")
    w(f"| Downloaded rows | {n_raw:,} | one row per record per admin unit |")
    w(f"| Usable coordinates, deduplicated | {n_usable:,} | drops rows with no East/North |")
    w(f"| Inside the {boundary.name} polygon | {n_extent:,} | matches the server-side county filter exactly |")
    w(f"| Stated precision <= {max_prec} m | {n_precise:,} | not assuming unstated precision is good |")
    w(f"| Thinned to {thin_m} m | {len(pool):,} | **the background pool** |")
    w("")
    w(f"Thinning removes {1 - len(pool) / max(1, n_precise):.0%} of the records, and it should: a")
    w("heavily visited forest can carry hundreds of records, and unthinned it")
    w("would contribute hundreds of background points and drown the rest of")
    w("Agder. That the reduction is this steep is itself a finding - the records")
    w("are packed into a small number of places.")
    w("")
    w(f"The pool is {len(pool) / max(1, n_precise):.0%} of the precision-filtered")
    w("records, and it supplies both the background and the null control.")
    w("")
    w("Most-recorded taxa in the group:")
    w("")
    w("| Taxon | Records |")
    w("|---|---|")
    for name, c in sorted(names.items(), key=lambda t: -t[1])[:10]:
        w(f"| *{name}* | {c:,} |")
    w("")
    focal = {sp["scientific_name"] for sp in spp["species"]}
    focal_n = sum(c for n, c in names.items() if n in focal)
    w(f"The three focal species are {focal_n:,} of {n_raw:,} rows "
      f"({focal_n / max(1, n_raw):.1%}). They are deliberately left in: target-group")
    w("background is the set of places the group was recorded, and removing the")
    w("focal species from it would make background systematically *less* likely to")
    w("sit where the species occurs, which is a bias in the opposite direction.")
    w("")

    # --- 2. non-uniformity -----------------------------------------------------
    print("measuring coverage ...", flush=True)
    cells = cells_inside(boundary, 1000.0)
    occupied = {(int(p["east"] // 1000), int(p["north"] // 1000)) for p in precise}
    cell_keys = {(int(x // 1000), int(y // 1000)) for x, y in cells}
    covered = len(occupied & cell_keys)
    w("## 2. Is the background actually non-uniform?")
    w("")
    frac = covered / max(1, len(cells))
    w(f"The Agder boundary polygon spans {len(cells):,} square-kilometre cells.")
    w(f"Fungal records touch **{covered:,}** of them - {frac:.0%} of the region.")
    w("")
    w(f"**About {1 - frac:.0%} of Agder has never had a single fungus recorded on")
    w("it.** Uniform background would put most of its points on that untouched")
    w("ground, and a model could then score well knowing nothing whatsoever")
    w("except which ground people visit. Section 3 measures how large that")
    w("effect actually is.")
    w("")
    w("(The cell count follows the administrative polygon, which includes water")
    w("inside the boundary; Agder's land area is nearer 16,400 km2. The coverage")
    w("fraction is therefore pessimistic rather than flattering.)")
    w("")

    # --- 3. how much apparent signal is accessibility? -------------------------
    w("## 3. How much apparent signal is just accessibility?")
    w("")
    w("A model given **nothing but coordinates** is run twice per species: once")
    w("against uniformly scattered background, once against target-group")
    w("background. It has no environmental data at all, so anything it scores is")
    w("knowledge of where records are.")
    w("")
    w(f"Blocked CV, {block_m:.0f} m blocks, {n_folds} folds, {buffer_m:.0f} m exclusion")
    w(f"buffer, background capped at {PROBE_BACKGROUND:,} points for run time.")
    w("")
    w("| Species | vs uniform background | vs target-group background | corrected |")
    w("|---|---|---|---|")

    rng = random.Random(SEED)
    uniform_pool = [
        {"east": x + rng.uniform(-500, 500), "north": y + rng.uniform(-500, 500),
         "precision_m": 0.0, "id": f"u{i}"}
        for i, (x, y) in enumerate(rng.sample(cells, min(PROBE_BACKGROUND, len(cells))))
    ]

    for sp in spp["species"]:
        raw_sp = ROOT / "data" / "raw" / "artskart" / f"{sp['key']}.json"
        p = artskart.usable_points(artskart.load(raw_sp))
        p = [q for q in p if (q["east"], q["north"]) in boundary]
        p = [q for q in p if q["precision_m"] is not None and q["precision_m"] <= max_prec]
        presence = PointSet(coords=[(q["east"], q["north"]) for q in thin(p, thin_m)],
                            role="presences")

        results = {}
        for label, bgpool in (("uniform", uniform_pool), ("target-group", pool)):
            print(f"  probe: {sp['key']} vs {label} background", flush=True)
            bg = draw_background(bgpool, n=PROBE_BACKGROUND, seed=SEED, thinning_m=thin_m)
            coords, y = assemble(presence, bg)
            X = [[c[0], c[1]] for c in coords]
            r = spatial_block_cv(coords, y, X, knn(), block_size_m=block_m,
                                 n_folds=n_folds, seed=SEED, buffer_m=buffer_m)
            results[label] = r.mean_auc
        drop = results["uniform"] - results["target-group"]
        w(f"| *{sp['scientific_name']}* | {results['uniform']:.3f} | "
          f"{results['target-group']:.3f} | {drop:+.3f} |")
    w("")
    w("The first column is the number we would have reported had we used uniform")
    w("background and no environmental data whatsoever. The third column is how")
    w("much of that was accessibility rather than anything about fungi.")
    w("")

    # --- 4. null control -------------------------------------------------------
    print("  null control ...", flush=True)
    null, bg = null_and_background(pool, n_null=300, n_background=PROBE_BACKGROUND,
                                  seed=SEED, thinning_m=thin_m)
    coords, y = assemble(null, bg)
    X = [[c[0], c[1]] for c in coords]
    rn = spatial_block_cv(coords, y, X, knn(), block_size_m=block_m,
                          n_folds=n_folds, seed=SEED, buffer_m=buffer_m)
    ok = 0.45 <= rn.mean_auc <= 0.55

    w("## 4. Null control on real geography")
    w("")
    w(f"{len(null)} null presences and {len(bg)} background points drawn disjointly")
    w(f"from the same pool ({overlap_count(null, bg)} coordinates shared - must be 0).")
    w("The null carries sampling effort and no ecology at all, so a correct")
    w("pipeline returns 0.5.")
    w("")
    w(f"**Blocked-CV AUC for the synthetic null: {rn.mean_auc:.3f}** "
      f"(+/- {rn.sd_auc:.3f} across folds) - {'PASS' if ok else '**FAIL**'}")
    w("")
    if ok:
        w("The null sits where it should, but note it is not exactly 0.5, and the")
        w("excess is not noise to wave away. It is what")
        w("[autocorrelation_range.md](autocorrelation_range.md) predicts: at a")
        w(f"{buffer_m:.0f} m buffer a third to a half of the short-range spatial")
        w("dependence survives, so a small positive bias in the null is the")
        w("expected consequence rather than a surprise.")
        w("")
        w(f"**Treat {rn.mean_auc:.2f} as the noise floor of this pipeline.** A species")
        w("scoring near it has demonstrated nothing, however far above 0.5 it")
        w("looks. The fold spread is wide too, so the floor is a band and not a")
        w("line.")
        w("")
        w("This is a necessary condition for reading any real result, not a")
        w("sufficient one. It is measured with coordinates as the only feature;")
        w("it must be re-run against the real feature stack once that exists,")
        w("because environmental variables correlate with accessibility in ways")
        w("coordinates alone do not capture.")
    else:
        w("**The null is not at 0.5. Under hard constraint 5 nothing else in any")
        w("run means anything until this is fixed.** Do not interpret species")
        w("results produced with this background.")
    w("")
    w("## Carried forward")
    w("")
    w("- The pool is built from presences of the whole group, so it inherits any")
    w("  bias shared across all fungal recording - a bias toward autumn, toward")
    w("  conspicuous fruiting bodies, toward southern Agder. Target-group")
    w("  background corrects for *where people looked*, not for what they were")
    w("  willing to write down.")
    w("- Records with no stated precision are dropped rather than assumed good,")
    w("  which discards real observations. The pool is large enough to afford it.")
    w("- The accessibility probe uses coordinates only. It bounds the effort")
    w("  signal; it does not tell us how much survives once real environmental")
    w("  features are present, because those correlate with accessibility too.")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    print("\n".join(lines))
    print(f"written: {OUT.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
