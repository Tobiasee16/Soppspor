"""Recent rainfall over Kristiansand, against what is normal for the date.

    python scripts/fetch_recent_weather.py

The model's rainfall feature is a **1991-2020 climatological mean** for the
fruiting window. That is the right thing for a model: it describes what a place
is usually like, which is a property of the place. It is the wrong thing for
deciding whether to go out this weekend, which is a property of the fortnight.

So this is a second, separate layer. It is not a model input and it is not
fitted to anything - nothing here has been validated against a single fungal
record. It answers one question: **has it rained more or less than usual here,
lately.**

What it writes:

* `rain21`, `rain7` - trailing rainfall totals, mm
* `tg7`             - trailing mean temperature, degrees C
* `daily_rain_mm`   - the kommune mean for each single day of the window,
  oldest first, so the interface can say "it rained 6 mm yesterday" instead of
  only "74 mm over three weeks". A forager asks about yesterday; a 21-day total
  cannot answer that, and the daily stack is already in memory here.
* `clim_mean`, `clim_sd`, `pct` - the same 21-day calendar window in each of
  1991-2020, so the current total can be read as a percentile against its own
  history rather than as a bare number of millimetres. 60 mm means different
  things on the coast and inland, and the percentile is what travels.

The grid is seNorge's own 1 km, not the model's 128 m, and it is left that way.
Resampling 1 km weather onto a 128 m grid would invent detail that is not in
the source; the interface samples it bilinearly for display and says what its
resolution is.

seNorge lags real time by a day or two. The lag is measured here from the
file's own time axis rather than assumed, and carried into the output so the
interface can say how old the number is instead of implying it is live.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import yaml

from src.geometry import Boundary
from src.ingest.senorge import BASE, CTX, UA, SourceError, daily, parse_dods, window_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "data" / "processed" / "conditions.json"
OUT_MD = ROOT / "data" / "processed" / "conditions.md"

WINDOW_DAYS = 21          # the trailing window that matters for fruiting
SHORT_DAYS = 7
CLIM_FROM, CLIM_TO = 1991, 2020   # the same baseline the model's feature uses
EPOCH = dt.datetime(1900, 1, 1)


def available_days(year: int) -> list[dt.date]:
    """Dates actually present in this year's file, read from its time axis.

    Asked rather than assumed. The archive is written daily and runs a day or
    two behind; hardcoding "today minus two" would silently produce a window
    shifted by a day whenever that changed.
    """
    url = f"{BASE.format(year=year)}.dods?time"
    payload = _raw(url)
    marker = payload.find(b"Data:\n")
    if marker < 0:
        raise SourceError("no data section when reading the time axis")
    body = payload[marker + 6:]
    n1, n2 = np.frombuffer(body[:8], dtype=">u4")
    if n1 != n2:
        raise SourceError("time axis element counts disagree")
    hours = np.frombuffer(body[8:8 + int(n1) * 8], dtype=">f8")
    return [(EPOCH + dt.timedelta(hours=float(h))).date() for h in hours]


def _raw(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=300, context=CTX) as r:
        return r.read()


def span_for(days: list[dt.date], end: dt.date, n: int) -> tuple[int, int]:
    """Half-open time-index range for the n days ending on `end`, inclusive."""
    start = end - dt.timedelta(days=n - 1)
    try:
        i0 = days.index(start)
        i1 = days.index(end) + 1
    except ValueError as exc:
        raise SourceError(
            f"the {n}-day window {start}..{end} is not wholly inside this "
            "year's file. Crossing the year boundary is not handled; run this "
            "again after the new year's file has a few weeks in it.") from exc
    return i0, i1


def main() -> int:
    # `--if-new` makes this safe to run on a timer. seNorge publishes once a
    # day with a lag of a day or two, so a scheduler that fires hourly would
    # otherwise refetch thirty years of climatology for a file that has not
    # moved. Checking the time axis is one small request.
    if "--if-new" in sys.argv and OUT_JSON.exists():
        try:
            have = json.loads(OUT_JSON.read_text(encoding="utf-8")).get("last_day")
            latest = available_days(dt.date.today().year)[-1].isoformat()
        except (OSError, ValueError, SourceError, IndexError) as exc:
            print(f"could not check for new data ({exc}); fetching anyway", flush=True)
        else:
            if have == latest:
                print(f"seNorge still ends {latest}; nothing to do", flush=True)
                return 0
            print(f"seNorge moved {have} -> {latest}", flush=True)

    area = yaml.safe_load((ROOT / "config" / "study_area.yaml").read_text(encoding="utf-8"))
    cfg = area["prediction_extent"]
    fc = json.loads((ROOT / cfg["boundary_file"]).read_text(encoding="utf-8"))
    bbox = Boundary(fc["features"][0]["geometry"], name=cfg["name"]).bbox
    w = window_for(bbox)
    print(f"seNorge window {w.width} x {w.height} cells over {cfg['name']}", flush=True)

    today = dt.date.today()
    days = available_days(today.year)
    last = days[-1]
    lag = (today - last).days
    print(f"latest day in the archive: {last} ({lag} day(s) behind {today})", flush=True)

    i0, i1 = span_for(days, last, WINDOW_DAYS)
    rr = daily(today.year, i0, i1, w, "rr")
    rain21 = np.nansum(rr, axis=0)
    rain21[np.isnan(rr).all(axis=0)] = np.nan
    rain7 = np.nansum(rr[-SHORT_DAYS:], axis=0)
    rain7[np.isnan(rr[-SHORT_DAYS:]).all(axis=0)] = np.nan

    j0, j1 = span_for(days, last, SHORT_DAYS)
    tg7 = np.nanmean(daily(today.year, j0, j1, w, "tg"), axis=0)

    # The same calendar window in each baseline year. `daily` is called with
    # indices computed from real dates, so a leap year shifts by one step and
    # the window still means the same dates.
    print(f"climatology: {WINDOW_DAYS}-day window ending {last:%d %b}, "
          f"{CLIM_FROM}-{CLIM_TO} ...", flush=True)
    stack = []
    for year in range(CLIM_FROM, CLIM_TO + 1):
        end = dt.date(year, last.month, last.day)
        start = end - dt.timedelta(days=WINDOW_DAYS - 1)
        jan1 = dt.date(year, 1, 1)
        t0, t1 = (start - jan1).days, (end - jan1).days + 1
        tot = np.nansum(daily(year, t0, t1, w, "rr"), axis=0)
        stack.append(tot)
        print(f"  {year}: mean {np.nanmean(tot):5.1f} mm", flush=True)
    clim = np.array(stack)

    clim_mean = np.nanmean(clim, axis=0)
    clim_sd = np.nanstd(clim, axis=0)
    # Empirical percentile against this cell's own 30 years, which needs no
    # distributional assumption and is what the interface actually shows.
    pct = (clim < rain21[None, :, :]).sum(axis=0) / clim.shape[0]
    pct = np.where(np.isnan(rain21), np.nan, pct)

    def flat(a, nd=1):
        return [None if not np.isfinite(v) else round(float(v), nd) for v in a.ravel()]

    # Mean over the kommune for each day of the window. `rr` is (day, y, x)
    # and already fetched, so this costs nothing beyond 21 floats in the file.
    per_day = [None if not np.isfinite(v) else round(float(v), 1)
               for v in np.nanmean(rr, axis=(1, 2))]

    valid = np.isfinite(rain21)
    summary = {
        "rain21_mean": round(float(np.nanmean(rain21)), 1),
        "rain7_mean": round(float(np.nanmean(rain7)), 1),
        "tg7_mean": round(float(np.nanmean(tg7)), 1),
        "clim_mean": round(float(np.nanmean(clim_mean)), 1),
        "ratio": round(float(np.nanmean(rain21) / max(np.nanmean(clim_mean), 0.1)), 2),
        "pct_mean": round(float(np.nanmean(pct)), 3),
    }
    out = {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "MET seNorge_2018 via THREDDS/OPeNDAP",
        "last_day": last.isoformat(),
        "first_day": (last - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        "lag_days": lag,
        "window_days": WINDOW_DAYS,
        "short_days": SHORT_DAYS,
        "baseline": [CLIM_FROM, CLIM_TO],
        "crs": "EPSG:25833",
        "grid": {"west": w.west, "north": w.north, "step": 1000.0,
                 "w": w.width, "h": w.height},
        "daily_rain_mm": per_day,
        "rain21": flat(rain21), "rain7": flat(rain7), "tg7": flat(tg7),
        "clim_mean": flat(clim_mean), "clim_sd": flat(clim_sd),
        "pct": flat(pct, 3),
        "summary": summary,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")

    a = []
    p = a.append
    p("# Recent rainfall conditions\n")
    p(f"Generated by `scripts/fetch_recent_weather.py` on "
      f"{dt.date.today().isoformat()}.\n")
    p("## What this is, and what it is not\n")
    p("**Not a model input.** The fitted model's rainfall feature is a "
      "1991-2020 climatological mean for the fruiting window, which describes "
      "what a place is usually like. This file describes what the last three "
      "weeks were like, which is a different question and is not fitted to "
      "anything. No fungal record was used to derive or validate it.\n")
    p("## Currency\n")
    p(f"| | |\n|---|---|")
    p(f"| latest day in the archive | **{last.isoformat()}** |")
    p(f"| lag behind today | {lag} day(s) |")
    p(f"| trailing window | {WINDOW_DAYS} days |")
    p(f"| baseline | {CLIM_FROM}-{CLIM_TO}, same calendar window |")
    p(f"| grid | seNorge 1 km, {w.width} x {w.height} cells |")
    p("")
    p("The lag is read from the file's own time axis on every run rather than "
      "assumed, so the window cannot silently slide if MET's publishing "
      "schedule changes.\n")
    p("## Conditions now\n")
    p(f"| | |\n|---|---|")
    p(f"| rain, last {WINDOW_DAYS} days | **{summary['rain21_mean']} mm** |")
    p(f"| normal for the date | {summary['clim_mean']} mm |")
    p(f"| ratio to normal | **{summary['ratio']:.2f}x** |")
    p(f"| percentile against {CLIM_FROM}-{CLIM_TO} | {summary['pct_mean']:.0%} |")
    p(f"| rain, last {SHORT_DAYS} days | {summary['rain7_mean']} mm |")
    p(f"| mean temp, last {SHORT_DAYS} days | {summary['tg7_mean']} C |")
    p("")
    p("Figures are means over the Kristiansand window. The interface uses the "
      "per-cell values, which vary across the kommune by more than the mean "
      "suggests - 45 km of coast-to-inland gradient is enough to matter.\n")
    p("## Why a percentile rather than millimetres\n")
    p("60 mm means different things in different places. A cell's total is "
      "compared against its own 30 years for the same calendar window, so the "
      "number that reaches the interface is 'wetter or drier than this place "
      "usually is by now', which is comparable across the map.\n")
    OUT_MD.write_text("\n".join(a), encoding="utf-8")

    print(f"\nrain {summary['rain21_mean']} mm vs normal "
          f"{summary['clim_mean']} mm = {summary['ratio']:.2f}x "
          f"({summary['pct_mean']:.0%} percentile)")
    print(f"written: {OUT_JSON.relative_to(ROOT)}")
    print(f"written: {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
