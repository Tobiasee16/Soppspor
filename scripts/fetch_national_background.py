"""The national target-group background: every fungal record in Norway.

    python scripts/fetch_national_background.py

The Agder pool is 82,129 records and 147 MB. Nationally it is about 1.33
million and roughly 2.4 GB, streamed to JSON Lines one record per line so a
truncated file is obvious on inspection rather than being a silently short
JSON array.

This is the one download the national expansion cannot avoid. The occurrence
records for the eight species are already on disk - they were always national
pulls, clipped to Agder at model time - but the *background* was fetched with a
county filter, and a national model needs a national comparison set.

Why it matters more than its size suggests: the background is the entire
effort-bias correction. Fungal records cluster along roads, trails and near
towns, so a model given uniformly random comparison points learns
"reachable", not "suitable". Drawing the background from the same recording
effort as the presences is what forces it to find something else. Get this
wrong at national scale and every national number is meaningless, which is
why the null control runs alongside every fit.

Resumable: if the file already exists and its sidecar says it is complete,
this does nothing.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ingest import artskart

DEST = ROOT / "data" / "raw" / "artskart" / "target_group_sopper_norge.jsonl"
META = DEST.with_suffix(".meta.json")
# Sopper. Verified to actually change TotalCount - a wrong parameter name is
# silently ignored by this API and returns an unfiltered count that looks fine.
FILTERS = {"filter.taxongroups": 9}


def main() -> int:
    if META.exists():
        m = json.loads(META.read_text(encoding="utf-8"))
        if m.get("complete"):
            print(f"already have {m['records']:,} records in {DEST.name}")
            return 0

    expected = artskart.total_count_filtered(FILTERS)
    print(f"national fungal records: {expected:,}", flush=True)
    print(f"streaming to {DEST.relative_to(ROOT)} ...", flush=True)

    t0 = time.time()
    last = [0.0]

    def progress(page: int, pages: int, written: int) -> None:
        now = time.time()
        if now - last[0] < 20 and page < pages:
            return
        last[0] = now
        el = now - t0
        rate = written / el if el else 0
        eta = (expected - written) / rate if rate else 0
        print(f"  page {page:>4}/{pages}  {written:>9,} records  "
              f"{100*page/pages:5.1f}%  {rate:,.0f} rec/s  "
              f"eta {eta/60:.0f} min", flush=True)

    got = artskart.download_filtered(FILTERS, DEST, progress=progress)
    META.write_text(json.dumps({
        "source": artskart.BASE,
        "filters": FILTERS,
        "total_count_reported": got.get("total_count_reported", expected),
        "records": got.get("records_downloaded"),
        "network_retries": got.get("network_retries", 0),
        "complete": True,
        "bytes": DEST.stat().st_size,
        "seconds": round(time.time() - t0),
    }, indent=1), encoding="utf-8")
    print(f"\ndone: {DEST.stat().st_size/1e9:.2f} GB in "
          f"{(time.time()-t0)/60:.0f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
