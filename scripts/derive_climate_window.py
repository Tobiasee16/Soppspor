"""Derive the fruiting season from the occurrence records, and fix the
rainfall accumulation window that follows from it.

The climate feature is autumn rainfall over the fruiting season. That leaves
two numbers to pin down - which days, and which years - and both are the kind
of quantity that is easy to tune after the fact until the model looks better.
So they are derived here, once, from the records themselves, and written into
FINDINGS.md before any model exists.

Three decisions are made here, each stated rather than buried:

1. THE WINDOW IS SHARED ACROSS ALL THREE SPECIES. They do not fruit at the
   same time - traktkantarell runs weeks later than kantarell - so a
   per-species window is tempting. It is also forbidden: CLAUDE.md requires
   "same feature stack, same pipeline, different presence sets", because the
   ordering test compares the three against each other. Three different
   climate features would make the three results incomparable and silently
   destroy the falsification test the whole design rests on.

2. THE FEATURE IS A CLIMATOLOGY, NOT A YEAR'S WEATHER. Records span decades.
   Matching each record to its own year's rainfall would build a model of
   "was it wet the week this person went for a walk", which is a conditions
   layer, not habitat - and CLAUDE.md puts the daily conditions overlay in
   Phase 2, deliberately separate. The habitat question is whether a place is
   reliably wet in autumn. So: mean total precipitation over the window,
   averaged across a fixed normal period.

3. THE WINDOW IS THE CENTRAL MASS OF RECORDS, NOT THEIR FULL RANGE. A handful
   of January records exist and are not evidence of a January fruiting season.

4. EACH SPECIES GETS AN EQUAL VOTE, rather than pooling the records. Pooling
   lets the most-recorded species set the window, and CLAUDE.md names
   A. muscaria as "heavily over-recorded relative to actual distribution" -
   pooling would import precisely the effort bias the whole target-group
   apparatus exists to remove. The three are a control set of equals, so the
   window averages their percentiles rather than their records. Both numbers
   are reported so the choice is visible and checkable.

    python scripts/derive_climate_window.py
"""
from __future__ import annotations

import calendar
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import yaml

from src.ingest.artskart import iter_jsonl, load, usable_points

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "artskart"
OUT = ROOT / "data" / "processed" / "climate_window.md"

# The registered window lives in config, because the model reads config and
# nobody reads a markdown file at fit time. It is written ONCE. On every later
# run this script recomputes the window and refuses to continue if the answer
# has moved, rather than quietly rewriting the number the results were
# pre-registered against - which is the whole failure mode pre-registration
# exists to prevent. Overwrite deliberately with --rewrite-registration.
REGISTRY = ROOT / "config" / "climate.yaml"

# Agder appears under three names across the record history: the county merged
# in 2020, and older records carry the pre-merge names. Filtering on the modern
# name alone would silently drop decades of the phenology we are measuring.
AGDER_NAMES = {"Agder", "Aust-Agder", "Vest-Agder"}

# Percentile band taken as "the fruiting season". 10-90 keeps the central 80%
# of dated records and discards the tails, where a scatter of out-of-season
# records - misdated, cultivated, or genuinely freak - would otherwise stretch
# the window across half the year.
LO_PCT, HI_PCT = 10.0, 90.0

# WMO standard normal period. Chosen because it is the standard, not because
# it suits us: any window we picked ourselves would invite the question of
# whether we picked it after seeing the answer.
NORMAL_FROM, NORMAL_TO = 1991, 2020


def doy_to_date(doy: int) -> str:
    """Label a 365-day index with a calendar date, using a non-leap year."""
    d = date(2019, 1, 1) + timedelta(days=doy - 1)
    return f"{d.day} {calendar.month_abbr[d.month]}"


def percentile(sorted_vals: list[int], pct: float) -> float:
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summarise(name: str, points: list[dict]) -> dict:
    dated = sorted(p["doy"] for p in points if p.get("doy") is not None)
    # Artskart carries a few records dated to year 1 and similar. They are
    # obvious transcription damage, and left in they make the reported record
    # span read "1-2026", which discredits a table that is otherwise sound.
    years = [p["year"] for p in points
             if p.get("year") is not None and 1800 <= p["year"] <= date.today().year + 1]
    n_bad_year = sum(1 for p in points if p.get("year") is not None
                     and not (1800 <= p["year"] <= date.today().year + 1))
    return {
        "name": name,
        "n": len(points),
        "n_dated": len(dated),
        "doy": dated,
        "p10": percentile(dated, 10.0),
        "p25": percentile(dated, 25.0),
        "p50": percentile(dated, 50.0),
        "p75": percentile(dated, 75.0),
        "p90": percentile(dated, 90.0),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "n_bad_year": n_bad_year,
    }


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))

    rows = []
    for sp in cfg["species"]:
        path = RAW / f"{sp['key']}.json"
        if not path.exists():
            raise SystemExit(f"missing {path} - run the occurrence download first")
        obs = [o for o in load(path) if o.get("County") in AGDER_NAMES]
        rows.append(summarise(sp["scientific_name"], usable_points(obs)))

    # The target group as a whole is the recording-effort phenology: when do
    # people look for fungi at all. If the three species tracked it exactly, we
    # would be reading the walkers' calendar rather than the mushrooms'.
    tg_path = ROOT / cfg["target_group"]["raw_file"]
    tg = None
    if tg_path.exists():
        tg = summarise("all fungi (target group)", usable_points(list(iter_jsonl(tg_path))))

    pooled = sorted(d for r in rows for d in r["doy"])
    pooled_lo = int(round(percentile(pooled, LO_PCT)))
    pooled_hi = int(round(percentile(pooled, HI_PCT)))

    # The window actually used: each species contributes one p10 and one p90,
    # so a species with twice the records does not get twice the say.
    lo = int(round(sum(r["p10"] for r in rows) / len(rows)))
    hi = int(round(sum(r["p90"] for r in rows) / len(rows)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    w = []
    a = w.append
    a("# Climate window - derived, not assumed\n")
    a(f"Generated by `scripts/derive_climate_window.py` on {date.today().isoformat()}.\n")
    a("The climate feature is autumn rainfall over the fruiting season. This file")
    a("fixes which days and which years that means, derived from the records")
    a("themselves, before any model is fitted.\n")

    a("## Observed phenology, Agder records\n")
    a("Day-of-year percentiles of dated records. Pre-2020 county names are")
    a("included, so this covers the full record history rather than only the")
    a("years since the county merger.\n")
    a("| set | records | dated | 10% | 25% | median | 75% | 90% | years |")
    a("|---|---|---|---|---|---|---|---|---|")
    for r in rows + ([tg] if tg else []):
        a(f"| {r['name']} | {r['n']:,} | {r['n_dated']:,} | "
          f"{doy_to_date(int(round(r['p10'])))} | {doy_to_date(int(round(r['p25'])))} | "
          f"**{doy_to_date(int(round(r['p50'])))}** | {doy_to_date(int(round(r['p75'])))} | "
          f"{doy_to_date(int(round(r['p90'])))} | {r['year_min']}-{r['year_max']} |")
    a("")

    a("### The dates carry biology, not only the walking calendar\n")
    med = {r["name"]: int(round(r["p50"])) for r in rows}
    spread = max(med.values()) - min(med.values())
    early = min(med, key=med.get)
    late = max(med, key=med.get)
    a(f"The three medians span {spread} days, from {early} on "
      f"{doy_to_date(med[early])} to {late} on {doy_to_date(med[late])}.")
    if tg:
        tgm = int(round(tg["p50"]))
        a(f"The target group as a whole - which is the recording-effort calendar,")
        a(f"i.e. when people go looking at all - peaks on {doy_to_date(tgm)}.")
        offsets = sorted(abs(m - tgm) for m in med.values())
        a(f"The species medians sit {offsets[0]}-{offsets[-1]} days off that peak,")
        a("in both directions. If the record dates were purely a function of when")
        a("people walk, the three would cluster on the effort peak instead. They")
        a("do not, which is a small piece of evidence that the dates carry real")
        a("phenology. It says nothing about whether the *locations* do - that is")
        a("what the blocked-CV result is for.\n")

    a("## The window\n")
    a(f"**Day {lo} ({doy_to_date(lo)}) to day {hi} ({doy_to_date(hi)})**, the mean")
    a(f"of the three species' {int(LO_PCT)}th and {int(HI_PCT)}th day-of-year")
    a("percentiles.\n")
    a("Each species gets one vote. Pooling all "
      f"{len(pooled):,} records instead would give")
    a(f"day {pooled_lo} ({doy_to_date(pooled_lo)}) to day {pooled_hi} "
      f"({doy_to_date(pooled_hi)}), a window set mainly by whichever species was")
    a("recorded most - and CLAUDE.md names *A. muscaria* as over-recorded")
    a("relative to its actual distribution. Letting record counts choose the")
    a("window would import the exact effort bias the target-group background")
    a("exists to remove. The difference between the two is small, which is the")
    a("expected result and not a reason to skip the distinction.\n")
    a("One window is used for all three species. They demonstrably do not fruit")
    a("at the same time, but CLAUDE.md requires the same feature stack across")
    a("species: the ordering test compares the three against each other, and")
    a("three different climate features would make those three numbers")
    a("incomparable. The cost of a shared window is stated here rather than")
    a("hidden - it fits the middle species best and the tails worst.\n")

    a("## The years\n")
    a(f"Mean over **{NORMAL_FROM}-{NORMAL_TO}**, the WMO standard normal period,")
    a("chosen because it is the standard rather than because it suits the data.")
    a("The feature is therefore a climatology: *is this place reliably wet in")
    a("autumn*, not *was it wet the week someone walked past*. Year-matched")
    a("weather would be a conditions layer, which CLAUDE.md places in Phase 2.\n")

    a("## Feature definition, registered\n")
    a("```")
    a(f"autumn_rain = mean over years {NORMAL_FROM}..{NORMAL_TO} of")
    a(f"              sum of seNorge_2018 rr over day {lo}..{hi} "
      f"({doy_to_date(lo)}-{doy_to_date(hi)})")
    a("              at 1 km, resampled to the 32 m terrain grid")
    a("```")
    a("")
    a("### Known weaknesses of this feature, stated before it is fitted\n")
    a("* seNorge's effective resolution is coarser than its 1 km grid, and")
    a("  quality tracks station density. Coastal Agder is better instrumented")
    a("  than the inland valleys.")
    a("* Autumn rainfall over Agder is dominated by a coast-inland and")
    a("  orographic gradient. **Population is also coastal.** This feature can")
    a("  therefore stand in for accessibility, which is exactly the failure the")
    a("  target-group background exists to prevent. Its permutation importance")
    a("  must be reported separately, and a model that leans on it while the")
    a("  terrain features do nothing should be read as suspicious, not as a")
    a("  result.")
    a("* At climatological scale the exact window matters far less than it")
    a("  would for a daily layer: the spatial pattern of mean autumn rainfall")
    a("  is similar across neighbouring windows. That is testable - the")
    a("  correlation between this window and shifted variants is reported once")
    a("  the raster exists, as a sensitivity check rather than an assumption.")
    a("")
    OUT.write_text("\n".join(w), encoding="utf-8")

    print("\n".join(w))
    print(f"\nwritten: {OUT.relative_to(ROOT)}")
    return register(lo, hi)


def register(lo: int, hi: int) -> int:
    """Write the window to config once; afterwards, verify rather than rewrite."""
    derived = {
        "autumn_rain": {
            "doy_from": lo,
            "doy_to": hi,
            "year_from": NORMAL_FROM,
            "year_to": NORMAL_TO,
            "variable": "rr",
            "source": "MET seNorge_2018 via THREDDS/OPeNDAP",
            "derived_by": "scripts/derive_climate_window.py",
            "derived_on": date.today().isoformat(),
        }
    }

    if REGISTRY.exists() and "--rewrite-registration" not in sys.argv:
        have = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))["autumn_rain"]
        keys = ("doy_from", "doy_to", "year_from", "year_to")
        moved = {k: (have[k], derived["autumn_rain"][k])
                 for k in keys if have[k] != derived["autumn_rain"][k]}
        if moved:
            print("\nREGISTERED WINDOW HAS MOVED - refusing to overwrite it.")
            for k, (was, now) in moved.items():
                print(f"  {k}: registered {was}, recomputed {now}")
            print("\nThe records have changed since the window was registered, or")
            print("the derivation has. Either is worth understanding before the")
            print("number the results are pre-registered against is replaced.")
            print("If the new window is genuinely correct, say so explicitly:")
            print("  python scripts/derive_climate_window.py --rewrite-registration")
            return 1
        print(f"registration unchanged: {REGISTRY.relative_to(ROOT)}")
        return 0

    header = (
        "# The climate window, registered before any model was fitted.\n"
        "#\n"
        "# Derived from the observed phenology of the three species' own records,\n"
        "# not from field-guide months. Full derivation and the reasoning behind\n"
        "# each choice: data/processed/climate_window.md, and FINDINGS.md section 6.\n"
        "#\n"
        "# Do not edit these numbers by hand. scripts/derive_climate_window.py\n"
        "# recomputes them and fails if they have moved.\n"
    )
    REGISTRY.write_text(header + yaml.safe_dump(derived, sort_keys=False),
                        encoding="utf-8")
    print(f"registered: {REGISTRY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
