"""Order and download the bulk environmental rasters via the Geonorge API.

Two datasets, both open, both ordered for Agder in EPSG:25833:

* **SR16 skogressurskart**, 16 m, from NIBIO. Tree species, volume, basal
  area, Lorey's height, site index, stand age. CLAUDE.md expects this and TWI
  to be what carries *Craterellus tubaeformis*, if anything does.
* **Losmasser**, superficial deposits, from NGU. Calcareous against acidic
  substrate structures fungal communities strongly.

FKB-AR5 is deliberately absent, and the reason is a licence rather than a
missing technical route - see data/SOURCES.md. Geonorge's own catalogue reports
it as `AccessIsRestricted: true`, `DataAccess: "Norge digitalt begrenset"`,
Geovekst terms. AR50 is ordered instead: open data under NLOD, same NIBIO
download API, 1:50,000 instead of 1:5,000.

The historical note below is kept because it is still true of AR5. It needs
a Geonorge login with the `nd.filnedlasting.fkb` role, which is a person's
credential and not something to work around. The verified per-point WMS route
in `src/ingest/env.py` covers the presence and background sets meanwhile; only
the wall-to-wall prediction map needs the raster.

    python scripts/download_rasters.py             # skips what is already there
    python scripts/download_rasters.py --only sr16
    python scripts/download_rasters.py --refresh
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

from src.ingest import geonorge_download as gd

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"

DATASETS = {
    "sr16": {
        "base": "https://kart8.nibio.no",
        "uuid": "5de45872-f534-4e97-840e-3cfd8db04398",
        "title": "SR16 - Skogressurskart 16x16 meter - Raster",
        "format": "RASTER",
        "dest_dir": RAW / "sr16",
    },
    "ar50": {
        "base": "https://kart8.nibio.no",
        "uuid": "4bc2d1e0-f693-4bf2-820d-c11830d849a3",
        "title": "Arealressurskart - AR50",
        # GML, not gdb or sosi: SOSI is a Norwegian national format nothing in
        # this stack reads, and a file geodatabase needs a driver we have not
        # asked to add. GDAL reads GML natively.
        "format": "gml",
        # AR50 does NOT offer EPSG:25833 for Agder - only 4258 and 25832,
        # because Agder genuinely falls in UTM zone 32. This is the exact CRS
        # trap CLAUDE.md warns about. It is reprojected to 25833 on ingest by
        # src/ingest/ar50.py, never mid-pipeline.
        "projection": "25832",
        "dest_dir": RAW / "ar50",
    },
    "losmasser": {
        "base": "https://nedlasting.ngu.no",
        "uuid": "3de4ddf6-d6b8-4398-8222-f5c47791a757",
        "title": "Losmasser (NGU)",
        # SHAPE rather than SOSI or a file geodatabase: SOSI is a Norwegian
        # national format nothing in this stack reads, and a file geodatabase
        # needs a driver we have not asked to add. NGU offers all three.
        "format": "SHAPE",
        "dest_dir": RAW / "losmasser",
    },
}

AREA_CODE, AREA_KIND, PROJECTION = "42", "fylke", "25833"


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} GB"


def fetch(key: str, spec: dict, refresh: bool) -> dict:
    print(f"\n=== {key}: {spec['title']} ===", flush=True)
    area = gd.find_area(spec["base"], spec["uuid"], AREA_CODE, AREA_KIND)
    fmts = ", ".join(str(f.get("name")) for f in area.get("formats", []))
    print(f"area {area['name']} ({area['type']} {area['code']}), formats: {fmts}",
          flush=True)

    files = gd.order(spec["base"], spec["uuid"], area,
                     projection=spec.get("projection", PROJECTION),
                     fmt=spec["format"])
    print(f"ordered {len(files)} file(s)", flush=True)

    out = []
    for f in files:
        dest = spec["dest_dir"] / f["name"]
        meta_path = dest.with_suffix(dest.suffix + ".meta.json")
        if dest.exists() and not refresh:
            print(f"  {f['name']}: already have {human(dest.stat().st_size)}, skipping",
                  flush=True)
            out.append({"name": f["name"], "path": str(dest), "skipped": True})
            continue
        if f.get("status") != "ReadyForDownload":
            raise gd.DownloadError(
                f"{f['name']} came back as {f.get('status')!r}, not ReadyForDownload")

        t0 = time.time()
        last = [0.0]

        def prog(got, total, name=f["name"]):
            now = time.time()
            if now - last[0] < 10:
                return
            last[0] = now
            pct = f"{100 * got / total:.0f}%" if total else "?"
            rate = got / max(now - t0, 1e-9)
            print(f"  {name}: {human(got)} of {human(total)} ({pct}), "
                  f"{human(rate)}/s", flush=True)

        print(f"  {f['name']}: downloading from {f['downloadUrl']}", flush=True)
        meta = gd.download(f["downloadUrl"], dest, progress=prog)
        meta.update({
            "dataset": spec["title"], "metadataUuid": spec["uuid"],
            "area": f.get("areaName"), "projection": f.get("projection"),
            "format": f.get("format"), "host": spec["base"],
        })
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"  {f['name']}: {human(meta['bytes'])} in {time.time() - t0:.0f}s",
              flush=True)
        out.append(meta)
    return {"key": key, "files": out}


def main() -> int:
    refresh = "--refresh" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
        if only not in DATASETS:
            raise SystemExit(f"unknown dataset {only!r}; have {', '.join(DATASETS)}")

    failures = []
    for key, spec in DATASETS.items():
        if only and key != only:
            continue
        try:
            fetch(key, spec, refresh)
        except gd.DownloadError as exc:
            # One dataset failing must not silently take the other with it, and
            # must not be papered over: CLAUDE.md says stop and report.
            print(f"\nFAILED {key}: {exc}", flush=True)
            failures.append(key)

    if failures:
        print(f"\n{len(failures)} dataset(s) failed: {', '.join(failures)}")
        return 1
    print("\nall requested datasets downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
