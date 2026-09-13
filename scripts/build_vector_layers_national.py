"""Burn AR50 and losmasser onto the national terrain grid.

    python scripts/build_vector_layers_national.py
    python scripts/build_vector_layers_national.py --only ar50
    python scripts/build_vector_layers_national.py --refresh

Both layers take their grid from `terrain_norge_32m.tif` rather than computing
an equivalent one, so they are cell-for-cell aligned with terrain by
construction. Computing a matching grid independently would put alignment at
the mercy of two floating-point transforms agreeing, which is the kind of
half-cell shift that produces plausible numbers and no error at all.

Both run windowed. AR50 is 1,948,279 MultiPolygons and losmasser 660,923, the
target is 1.83 billion cells, and the machine has 15.7 GB.

**Losmasser reads two files, not one.** NGU's national delivery contains
`LosmasseFlate_20240621` and `LosmasseFlate_20240622`, and they are spatially
disjoint - the second is 11,482 km2 of ground the first does not cover, 14.4%
of it in Nordland, which is the held-out region. Nothing in the download names
it as a supplement. See FINDINGS section 20.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

INTERIM = ROOT / "data" / "interim"
REFERENCE = INTERIM / "terrain_norge_32m.tif"

AR50_GDB = INTERIM / "ar50_norge" / "0000_25833_ar50_gdb.gdb"
AR50_OUT = INTERIM / "ar50_norge_32m.tif"

LOSM_DIR = INTERIM / "losmasser_norge" / "Losmasse"
LOSM_OUT = INTERIM / "losmasser_norge_32m.tif"

BLOCK = 4096


def _progress(label: str, t0: float):
    last = [0.0]

    def prog(done: int, total: int) -> None:
        now = time.time()
        if now - last[0] < 20 and done < total:
            return
        last[0] = now
        el = now - t0
        print(f"  {label} block {done}/{total}  {100*done/total:5.1f}%  "
              f"~{el/done*(total-done)/60:.0f} min left", flush=True)

    return prog


def do_ar50(refresh: bool) -> dict | None:
    from src.ingest.ar50 import rasterise_windowed
    if AR50_OUT.exists() and not refresh:
        print(f"ar50: using cached {AR50_OUT.name}")
        return None
    if not AR50_GDB.exists():
        raise SystemExit(f"missing {AR50_GDB} - unzip data/raw/ar50/0000_25833_ar50_gdb.zip")
    t0 = time.time()
    print(f"ar50: burning {AR50_GDB.name} onto the terrain grid ...", flush=True)
    out = rasterise_windowed(AR50_GDB, REFERENCE, AR50_OUT, layer="ar50",
                             block=BLOCK, progress=_progress("ar50", t0))
    out["seconds"] = round(time.time() - t0)
    AR50_OUT.with_suffix(".meta.json").write_text(json.dumps(out, indent=1),
                                                  encoding="utf-8")
    print(f"ar50: {AR50_OUT.stat().st_size/1e9:.2f} GB, "
          f"{out['coverage_pct']:.1f}% of cells covered, "
          f"in {out['seconds']/60:.0f} min")
    return out


def do_losmasser(refresh: bool) -> dict | None:
    from src.ingest.losmasser import rasterise_windowed
    if LOSM_OUT.exists() and not refresh:
        print(f"losmasser: using cached {LOSM_OUT.name}")
        return None
    sources = sorted(LOSM_DIR.glob("LosmasseFlate_*.shp"))
    if not sources:
        raise SystemExit(f"no LosmasseFlate_*.shp under {LOSM_DIR}")
    # Loud, because finding only one when the national delivery has two is the
    # failure this whole layer was nearly built with.
    print(f"losmasser: {len(sources)} source file(s): "
          f"{', '.join(p.name for p in sources)}", flush=True)
    if len(sources) < 2:
        print("  WARNING: the national NGU delivery contains TWO LosmasseFlate "
              "layers.\n  Only one was found. See FINDINGS section 20 before "
              "trusting this layer.", flush=True)
    t0 = time.time()
    out = rasterise_windowed(sources, REFERENCE, LOSM_OUT, block=BLOCK,
                             progress=_progress("losmasser", t0))
    out["seconds"] = round(time.time() - t0)
    LOSM_OUT.with_suffix(".meta.json").write_text(json.dumps(out, indent=1),
                                                  encoding="utf-8")
    print(f"losmasser: {LOSM_OUT.stat().st_size/1e9:.2f} GB, "
          f"{out['coverage_pct']:.1f}% of cells covered, "
          f"in {out['seconds']/60:.0f} min")
    return out


def main() -> int:
    refresh = "--refresh" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    if not REFERENCE.exists():
        raise SystemExit(f"missing {REFERENCE} - run "
                         f"scripts/build_terrain_national.py first")
    import rasterio
    with rasterio.open(REFERENCE) as ref:
        print(f"grid: {ref.width:,} x {ref.height:,} at {abs(ref.transform.a):.0f} m, "
              f"{ref.crs}\n")

    if only in (None, "ar50"):
        do_ar50(refresh)
    if only in (None, "losmasser"):
        do_losmasser(refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
