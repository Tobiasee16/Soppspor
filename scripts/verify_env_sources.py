"""Verify every environmental endpoint against a known location.

CLAUDE.md: "Endpoints change. Verify each before hardcoding." This is that
check, made re-runnable so a silent change is caught rather than discovered as
a strange model result months later.

The test location is a real *Craterellus tubaeformis* record in Kristiansand.
That matters: several of these services answer a query outside their coverage
with something that looks like an answer rather than an error. The elevation
service returns a negative height from depth contours over water; the WMS
services return "no results" when the axis order is wrong. Checking against a
point where we know a fungus grew means a wrong answer is visible.

Each check asserts on the *content*, not just on HTTP 200.

    python scripts/verify_env_sources.py

Exits non-zero if any source fails, so it can gate a pipeline run.
"""
from __future__ import annotations

import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from src.ingest import env

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "env_source_verification.md"

# A real traktkantarell record: forest, 38 m, inland Kristiansand.
E, N = 93674.87, 6466470.01


def check(name, fn, expect):
    """Run one probe. `expect` returns (ok, evidence) from the payload."""
    try:
        got = fn()
        ok, evidence = expect(got)
        return {"name": name, "ok": ok, "evidence": evidence}
    except Exception as e:  # noqa: BLE001 - reported, never swallowed
        return {"name": name, "ok": False,
                "evidence": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=2)}


def main():
    checks = []

    checks.append(check(
        "Kartverket hoydedata (elevation point)",
        lambda: env.elevation(E, N),
        lambda d: (
            d["elevation_m"] is not None and d["elevation_m"] > 0 and d["source"] == "dtm1",
            f"{d['elevation_m']} m, source={d['source']}, terrain={d['terrain']}",
        ),
    ))

    checks.append(check(
        "NIBIO SR16 (forest structure + uncertainty)",
        lambda: env.sr16(E, N),
        lambda d: (
            bool(d) and "srvolmb" in d and "srvolmb_l" in d and "srtreslag" in d,
            (f"volume {d.get('srvolmb')} m3/ha "
             f"[{d.get('srvolmb_l')}-{d.get('srvolmb_u')}], "
             f"species={d.get('srtreslag')}, age={d.get('srtrealder')}, "
             f"LAI={d.get('srlai')}, {len(d)} fields"),
        ),
    ))

    checks.append(check(
        "NIBIO AR5 (land cover)",
        lambda: env.ar5(E, N),
        lambda d: (
            bool(d) and "artype" in d,
            (f"artype={d.get('artype')} ({d.get('artype_beskrivelse')}), "
             f"treslag={d.get('artreslag_beskrivelse')}, "
             f"bonitet={d.get('arskogbon_beskrivelse')}, "
             f"grunnforhold={d.get('argrunnf_beskrivelse')}"),
        ),
    ))

    checks.append(check(
        "NGU losmasser (superficial deposits)",
        lambda: env.losmasser(E, N),
        lambda d: (
            bool(d) and "losmassetype" in d,
            f"{d.get('losmassetype')} = {d.get('losmassetype_tekst')}",
        ),
    ))

    checks.append(check(
        "Miljodirektoratet Naturbase (NiN)",
        lambda: env.naturtype(E, N),
        # An empty result is correct here: most of Agder is unmapped. The check
        # is that the service answers in the expected shape, not that this
        # particular point is inside a mapped polygon.
        lambda d: (isinstance(d, dict),
                   f"{len(d)} attributes" + (f", naturtype={d.get('Naturtype')}" if d else
                                             " (no mapped polygon here, which is normal)")),
    ))

    checks.append(check(
        "MET seNorge 2018 (precipitation, OPeNDAP)",
        lambda: env.senorge_series(E, N, 2024, "rr", 250, 254),
        lambda v: (
            len(v) == 5 and all(x is not None for x in v),
            f"rr days 251-255 of 2024: {v} mm",
        ),
    ))

    checks.append(check(
        "MET seNorge 2018 (mean temperature)",
        lambda: env.senorge_series(E, N, 2024, "tg", 250, 254),
        lambda v: (
            len(v) == 5 and all(x is not None for x in v),
            f"tg days 251-255 of 2024: {v} degC",
        ),
    ))

    checks.append(check(
        "seNorge grid indexing (Y descends)",
        lambda: env.senorge_index(E, N),
        lambda t: (
            t == (168, 1533),
            f"index {t}; cell centre should be (93500, 6466500), "
            f"within {env.SENORGE_STEP:.0f} m of the query point",
        ),
    ))

    checks.append(check(
        "NVE gridded hydrology (soil saturation)",
        lambda: env.nve_series(E, N, "2024-09-01", "2024-09-05"),
        lambda d: (
            d["data"] and len(d["data"]) == 5,
            f"gwb_sssrel {d['data']} {d['unit']}",
        ),
    ))

    n_ok = sum(1 for c in checks if c["ok"])
    lines = []
    w = lines.append
    w("# Environmental source verification")
    w("")
    w("Generated by `scripts/verify_env_sources.py`. Re-run before trusting a")
    w("pipeline run - these are other people's services and they change.")
    w("")
    w(f"Probe location: a real *Craterellus tubaeformis* record at ({E}, {N}) ")
    w("EPSG:25833, inland Kristiansand. Checked against known values rather than")
    w("HTTP status, because several of these services answer an out-of-coverage")
    w("query with something that looks like data.")
    w("")
    w(f"**{n_ok} of {len(checks)} sources verified.**")
    w("")
    w("| Source | Status | Evidence |")
    w("|---|---|---|")
    for c in checks:
        w(f"| {c['name']} | {'OK' if c['ok'] else '**FAIL**'} | {c['evidence']} |")
    w("")
    w("## Traps confirmed the hard way")
    w("")
    w("- **WMS axis order.** EPSG:25833 in WMS 1.3.0 is easting-first. Reversed,")
    w("  the query lands in the sea and returns \"Search returned no results\" -")
    w("  indistinguishable from a location with no data. We use WMS 1.1.1.")
    w("- **INFO_FORMAT.** `text/plain` returns a feature ID and *no attributes*.")
    w("  Only `application/vnd.ogc.gml` carries values.")
    w("- **SR16.** The raster layers return nothing through GetFeatureInfo. The")
    w("  vector layer `SRVVOLMB` returns the whole stack including uncertainty.")
    w("- **seNorge.** NetcdfSubset returns HTTP 503; OPeNDAP works. The Y axis")
    w("  descends, and assuming otherwise reads a cell ~1500 km away that")
    w("  returns the fill value rather than an error.")
    w("- **Elevation over water** is answered from depth contours as a negative")
    w("  height. It is a real answer to a different question.")
    w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    for c in checks:
        if not c["ok"] and "trace" in c:
            print(f"\n--- {c['name']} ---\n{c['trace']}")
    print(f"written: {OUT.relative_to(ROOT)}")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
