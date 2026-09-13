"""Can the held-out region actually test a national model? Counts only.

    python scripts/check_holdout_power.py

The national design fits on fourteen fylker and tests on one it has never seen.
That is only a test if the held-out region contains enough records of each
species to produce a number worth reading - and a region can be short for two
very different reasons:

* **Nobody looked there.** A sampling gap. The species is present, the records
  are not, and a low score says more about recorders than about the model.
* **The species is not there.** Real ecology. A southern calcareous-deciduous
  species genuinely thins out toward the Arctic, and the correct model output
  for Nordland is "almost nowhere", which no held-out AUC can reward.

The two look identical in a count, and the difference decides whether a weak
result is a finding or an artefact. So this script reports the count and the
writeup states the reason per species; it does not try to infer one from the
other.

Run BEFORE committing to a held-out region, so the choice cannot be made after
seeing which region flatters the model. This does not fit anything and must
never be made to.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

NL_OUT = chr(10)

from src import species as species_cfg
from src.features.thin import thin
from src.geometry import Boundary
from src.ingest import artskart

RAW = ROOT / "data" / "raw" / "artskart"
EFFORT = ROOT / "data" / "processed" / "effort_by_fylke.json"

# The floor below which a held-out score is not worth quoting. Lower than the
# 50 that report_counts.py uses to judge whether a species can be *fitted* at
# all, because this is a test set rather than a training set: 30 gives an AUC
# standard error of roughly 0.09, which is wide but still distinguishes 0.75
# from 0.50. Below 30 the interval covers most of the range the model could
# occupy and the number stops carrying information.
MIN_HOLDOUT = 30


def load(path: pathlib.Path) -> Boundary:
    fc = json.loads(path.read_text(encoding="utf-8"))
    declared = fc.get("crs_declared_by_source")
    if declared and "25833" not in str(declared):
        raise RuntimeError(f"{path.name} declares {declared}, expected EPSG:25833")
    return Boundary(fc["features"][0]["geometry"], name=path.stem)


def main() -> int:
    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))

    hold_cfg = area["national_holdout_extent"]
    train_cfg = area["national_training_extent"]
    thin_m = area["reporting_thinning_m"]
    max_prec = area["max_coordinate_precision_m"]

    holdout = load(ROOT / hold_cfg["boundary_file"])
    excluded = str(train_cfg["exclude_code"])
    train_dir = ROOT / train_cfg["boundary_dir"]

    # The training extent is every fylke except the held-out one. Loading them
    # all and testing membership is slower than a bbox, and it is the only way
    # to be sure the two sets do not overlap - which is the entire premise.
    train = []
    for f in sorted(train_dir.glob("boundary_*_25833.geojson")):
        fc = json.loads(f.read_text(encoding="utf-8"))
        code = str(fc.get("fylke_code") or fc["features"][0].get("properties", {}).get("fylkesnummer") or "")
        if code == excluded or f.stem == holdout.name:
            continue
        train.append(Boundary(fc["features"][0]["geometry"], name=f.stem))

    print(f"held out : {hold_cfg['name']}")
    print(f"training : {len(train)} fylker, {train_cfg['name']}")
    print(f"filters  : precision <= {max_prec} m, thinned to {thin_m} m\n")
    print(f"{'species':34s}{'train':>9s}{'holdout':>9s}{'share':>8s}   verdict")

    rows = []
    for sp in species_cfg.modelled(spp):
        dest = RAW / f"{sp['key']}.json"
        if not dest.exists():
            print(f"{sp['scientific_name']:34s}   MISSING {dest.name}")
            continue
        pts = artskart.usable_points(artskart.load(dest))
        pts = [p for p in pts
               if p["precision_m"] is not None and p["precision_m"] <= max_prec]

        in_hold = [p for p in pts if (p["east"], p["north"]) in holdout]
        in_train = [p for p in pts
                    if (p["east"], p["north"]) not in holdout
                    and any((p["east"], p["north"]) in b for b in train)]

        n_hold = len(thin(in_hold, thin_m))
        n_train = len(thin(in_train, thin_m))
        ok = n_hold >= MIN_HOLDOUT
        # The share is what separates the two ways of being short. A species
        # merely uncommon everywhere has a NORMAL share of its records in the
        # held-out region and simply has few of everything; a species at its
        # northern range limit has an abnormally LOW share. The raw count
        # cannot tell those apart and this largely can.
        share = n_hold / (n_hold + n_train) if (n_hold + n_train) else 0.0
        print(f"{sp['scientific_name']:34s}{n_train:9,d}{n_hold:9,d}{100*share:7.1f}%   "
              f"{'ok' if ok else 'BELOW ' + str(MIN_HOLDOUT)}")
        rows.append((sp["scientific_name"], sp.get("vernacular_no", ""),
                     n_train, n_hold, ok, share))

    short = [r for r in rows if not r[4]]
    shares = sorted(r[5] for r in rows)
    median = shares[len(shares) // 2]

    # The honest baseline is the share of ALL fungal recording effort falling
    # in the held-out region, which is exactly what the target-group background
    # measures. The median across our own eight species is only a fallback, and
    # it is circular: it assumes these species are collectively typical of
    # fungal recording, which is the thing being tested.
    baseline, source = median, "median across these eight species (CIRCULAR)"
    if EFFORT.exists():
        eff = json.loads(EFFORT.read_text(encoding="utf-8"))
        key = hold_cfg["name"].split()[0].lower()
        if key in eff["share"]:
            baseline = eff["share"][key]
            source = (f"{eff['assigned_to_a_fylke']:,} target-group records "
                      f"(scripts/effort_by_fylke.py)")
    print(f"\n{sum(r[3] for r in rows):,} thinned records in the held-out region, "
          f"{len(rows) - len(short)}/{len(rows)} species at or above {MIN_HOLDOUT}.")
    print(f"median share across these eight species: {100*median:.1f}%")
    print(f"RECORDING-EFFORT BASELINE: {100*baseline:.2f}%  - {source}")
    if short:
        print("\nBelow the floor. The share column is the evidence for which kind of")
        print("short it is - a normal share means the species is thin everywhere, a low")
        print("one means it is thinning out toward the north:")
        for name, common, _, n, _, sh in short:
            kind = ("uncommon everywhere, not specifically absent here"
                    if sh >= 0.75 * baseline else
                    "under-represented here even for its own rarity")
            print(f"  {name} ({common}): {n} at {100*sh:.1f}% "
                  f"({sh/baseline:.2f}x effort) - {kind}")
    print(NL_OUT + "share against recording effort, all eight:")
    for name, common, _, n, ok, sh in sorted(rows, key=lambda r: r[5]):
        print(f"  {name:34s}{100*sh:6.1f}%  {sh/baseline:5.2f}x effort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
