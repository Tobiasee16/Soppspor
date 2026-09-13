"""Pull the SR16 layers the model actually uses out of the download.

    python scripts/extract_sr16.py                 # Agder
    python scripts/extract_sr16.py --national

SR16 ships **23 raster layers** and this project fits on **eight** of them.
That is a detail worth acting on rather than noting: nationally the full set is
34.8 GB and the eight are 11.8 GB, so extracting everything would cost 23 GB of
disk to hold nineteen layers nothing reads.

The eight are the ones CLAUDE.md names - tree species, volume, basal area,
Lorey's height, LAI, site index, stand age - plus crown cover. The other
fifteen are mostly variants of the same quantity at different diameter
thresholds (TREANTALL_GE8, _GE10, _GE16) or bounds layers, and adding a
near-duplicate of a column already in the stack would split its permutation
importance rather than add information.

Two things differ between the regional and national downloads and both are
handled here rather than at the call site:

* **Naming.** The fylke order names members `sr16_42_SRRLAI.tif`; the
  landsdekkende order names them `SRRLAI.tif`.
* **Size.** The national zip is 34.8 GB and is STORED, not deflated - the
  GeoTIFFs are already internally compressed, so the zip adds nothing and
  extraction is a byte copy rather than a decompression.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time
import zipfile

NL_OUT = chr(10)

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

RAW = ROOT / "data" / "raw" / "sr16"

# Separate directories rather than one, even though the filenames differ.
# The regional members carry `sr16_42_` and say which extent they are; the
# national ones are bare `SRRLAI.tif` and say nothing at all. Phase 0 s
# published numbers come off the regional set, so the two must never be
# confusable by a glob or by a person reading the directory.
INTERIM = {False: ROOT / "data" / "interim" / "sr16",
           True: ROOT / "data" / "interim" / "sr16_norge"}

# Eight of twenty-three. Keep this in step with SR16_NUMERIC in run_models.py
# plus TRESLAG, which is categorical and carries the shared "SR16 absent"
# indicator for the whole block.
WANTED = ("TRESLAG", "VOLMB", "GRFLATE", "MHOYDE", "LAI",
          "TREALDER", "KRONEDEK", "BONITET")

ZIPS = {
    False: RAW / "42_25833_sr16_RASTER.zip",
    True: RAW / "0000_25833_sr16_RASTER.zip",
}


def wanted_members(names: list[str], wanted=WANTED) -> dict[str, str]:
    """Map each wanted SR16 code to the archive member that holds it.

    Matched on the `SRR<code>.tif` suffix rather than on the whole filename, so
    the regional `sr16_42_SRRLAI.tif` and the national `SRRLAI.tif` both
    resolve without the caller knowing which download it has.

    Exact, not prefix: `SRRTREANTALL.tif` must not satisfy a request for
    `TREANTALL_GE8`, and more to the point `SRRDIAMMIDDEL.tif` must not be
    picked up by a request for `DIAMMIDDEL_GE8` or the other way round.
    """
    out: dict[str, str] = {}
    for code in wanted:
        tail = f"SRR{code}.tif".lower()
        hits = [n for n in names if n.lower().endswith(tail)
                and n.lower().rsplit("/", 1)[-1].startswith(("srr", "sr16"))]
        if not hits:
            raise SystemExit(
                f"SR16 archive has no member for {code}. Members: "
                f"{', '.join(sorted(names)[:25])}")
        if len(hits) > 1:
            raise SystemExit(f"{code} matched {len(hits)} members: {hits}")
        out[code] = hits[0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--national", action="store_true")
    ap.add_argument("--force", action="store_true", help="re-extract existing files")
    args = ap.parse_args()

    src = ZIPS[args.national]
    interim = INTERIM[args.national]
    if not src.exists():
        raise SystemExit(f"missing {src} - run scripts/download_rasters.py"
                         + (" --national" if args.national else ""))
    interim.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(src) as z:
        names = [i.filename for i in z.infolist()]
        picked = wanted_members(names)
        total = sum(z.getinfo(m).file_size for m in picked.values())
        print(f"{src.name}: {len(names)} layers, taking {len(picked)} "
              f"({total/1e9:.2f} GB of "
              f"{sum(i.file_size for i in z.infolist())/1e9:.2f} GB)\n")

        done = 0
        for code, member in picked.items():
            dest = interim / pathlib.PurePosixPath(member).name
            size = z.getinfo(member).file_size
            if dest.exists() and dest.stat().st_size == size and not args.force:
                print(f"  {code:12s} have {dest.name} ({size/1e9:.2f} GB)")
                done += size
                continue
            t0 = time.time()
            with z.open(member) as fh, dest.open("wb") as out:
                while chunk := fh.read(1 << 22):
                    out.write(chunk)
            done += size
            el = time.time() - t0
            print(f"  {code:12s} {dest.name}  {size/1e9:.2f} GB in {el:.0f}s "
                  f"({size/1e6/max(el,1e-9):.0f} MB/s)  [{100*done/total:.0f}%]",
                  flush=True)

    print(NL_OUT + f"-> {interim}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
