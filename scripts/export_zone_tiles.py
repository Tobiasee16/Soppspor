"""Cut the national suitability rasters into zone tiles the app can fetch.

    python scripts/export_zone_tiles.py
    python scripts/export_zone_tiles.py --only craterellus_tubaeformis

Writes `data/processed/zone_tiles/<species>/<row>_<col>.png` plus one
`meta.json`. Each tile is a 256 x 256 eight-bit greyscale PNG on a 128 m grid,
so one tile covers 32.768 km square.

Why tiles at all: the Kristiansand payload inlines the whole suitability
surface as one base64 blob, which is 3.78 MB for a 45 x 64 km window. Norway is
111 times that area and the same approach would be several hundred megabytes in
a single download. EXPANSION.md section 5 calls the delivery format the hard
stop, and this is the replacement it names - zone data fetched per region, the
way the basemap already works.

**The byte is a national percentile, not a scaled score.** This is the one real
departure from the Kristiansand payload and it is forced by the data. That
payload scales linearly between the surface's own min and max; run that over
Norway and 62.8% of *C. tubaeformis*'s land falls into bytes 1-3, because the
national distribution has a p98/p2 ratio of 814 (FINDINGS 23c). Two thirds of
the country would be a single flat value and no zone boundary below the 63rd
percentile could be drawn at all. Storing the rank instead spends all 255
levels where they are actually needed.

Nothing is lost by it. The interface never reads a raw suitability value - it
sorts the bytes and works in percentiles throughout - so the rank *is* what it
wanted, computed once over the whole country instead of over whatever happens
to be on screen. That last part matters: percentiles computed per view would
change as the user pans, and "top 5% of habitat" would silently mean "top 5% of
what you are looking at".

The GeoTIFFs keep the real values and remain the artefact.
"""
from __future__ import annotations

import json
import pathlib
import struct
import sys
import time
import warnings
import zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import numpy as np
import rasterio
import yaml

from src import species as species_cfg
from src.report_tables import held_out_counts, transfer_aucs

MAPS = ROOT / "data" / "processed" / "maps_norge"
OUT = ROOT / "data" / "processed" / "zone_tiles"
TRANSFER = ROOT / "data" / "processed" / "transferability_norge.md"

FACTOR = 4                 # 32 m source -> 128 m display grid
TILE = 256                 # cells per tile side; 256 * 128 m = 32.768 km
MODEL = "maxent"


# --- a minimal greyscale PNG encoder ------------------------------------
# Stdlib rather than Pillow. Pillow is present in this environment only as a
# matplotlib dependency, and CLAUDE.md says to ask before adding one; an
# eight-bit greyscale PNG is twenty lines of zlib and struct, and
# tests/test_zone_tiles.py checks every one of them round-trips through
# Pillow's decoder. Writing it by hand also keeps the tile format explicit,
# which matters because a browser will be reading these as data rather than as
# pictures.

def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_gray(arr: np.ndarray) -> bytes:
    """An 8-bit greyscale PNG. `arr` must be uint8 and 2-D."""
    if arr.dtype != np.uint8 or arr.ndim != 2:
        raise ValueError(f"expected a 2-D uint8 array, got {arr.dtype} {arr.shape}")
    h, w = arr.shape
    # Filter type 0 (None) on every scanline. Filtering would compress a little
    # better and would make the bytes depend on their neighbours, which is the
    # wrong trade for data being read back as values.
    raw = b"".join(b"\x00" + arr[r].tobytes() for r in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


# --- the grid -----------------------------------------------------------

def downsample(path: pathlib.Path, factor: int = FACTOR):
    """Mean the raster down by `factor`, ignoring nodata. Returns (array, transform).

    Explicit block means rather than `read(out_shape=...)`. GDAL maps the full
    extent onto whatever shape it is given, so an out_shape that does not
    divide the raster exactly yields cells of 128.003 m pretending to be 128 -
    a georeferencing error that nothing would ever report. Here the trailing
    rows and columns that do not fill a whole block are dropped instead, which
    costs 32 m at the east edge and 64 m at the south, both of them sea.
    """
    with rasterio.open(path) as src:
        h = src.height // factor
        w = src.width // factor
        out = np.empty((h, w), dtype="float32")
        for r0 in range(0, h, 256):
            r1 = min(r0 + 256, h)
            win = rasterio.windows.Window(0, r0 * factor, w * factor,
                                          (r1 - r0) * factor)
            block = src.read(1, window=win).astype("float32")
            block = block.reshape(r1 - r0, factor, w, factor)
            # A block that is entirely nodata is the normal case out at sea,
            # and nanmean warns about it. The NaN it returns is exactly what is
            # wanted, so the warning is noise rather than a signal.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", "Mean of empty slice",
                                        RuntimeWarning)
                out[r0:r1] = np.nanmean(block, axis=(1, 3))
        t = src.transform
        transform = rasterio.Affine(t.a * factor, t.b, t.c,
                                    t.d, t.e * factor, t.f)
    return out, transform


def to_percentile_bytes(grid: np.ndarray) -> tuple[np.ndarray, int]:
    """Rank every finite cell against the whole country, as 1..255. 0 = no data.

    Ties take the lower rank, matching the `lowerBound` the interface already
    uses to turn a byte into a percentile.
    """
    finite = np.isfinite(grid)
    vals = grid[finite]
    out = np.zeros(grid.shape, dtype="uint8")
    if vals.size == 0:
        return out, 0
    ordered = np.sort(vals)
    pct = np.searchsorted(ordered, vals, side="left") / max(vals.size - 1, 1)
    out[finite] = np.clip(np.round(pct * 254.0) + 1, 1, 255).astype("uint8")
    return out, int(vals.size)


def cut_tiles(byts: np.ndarray, dest: pathlib.Path) -> list[str]:
    """Write every non-empty tile. Returns the ids that exist, as 'row_col'."""
    dest.mkdir(parents=True, exist_ok=True)
    h, w = byts.shape
    ids = []
    for row in range((h + TILE - 1) // TILE):
        for col in range((w + TILE - 1) // TILE):
            block = byts[row * TILE:(row + 1) * TILE, col * TILE:(col + 1) * TILE]
            if not block.any():
                continue                      # all nodata: the app assumes empty
            if block.shape != (TILE, TILE):   # pad the edges, never stretch
                padded = np.zeros((TILE, TILE), dtype="uint8")
                padded[:block.shape[0], :block.shape[1]] = block
                block = padded
            (dest / f"{row}_{col}.png").write_bytes(png_gray(block))
            ids.append(f"{row}_{col}")
    return ids


def main() -> int:
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    spp = yaml.safe_load((ROOT / "config" / "species.yaml").read_text(encoding="utf-8"))

    entries = [(sp["key"], sp) for sp in species_cfg.modelled(spp)]
    entries.append((spp["null_control"]["key"], spp["null_control"]))

    aucs, null_auc = transfer_aucs(TRANSFER)
    held = held_out_counts(TRANSFER)

    meta = {"crs": "EPSG:25833", "cell_m": None, "tile_px": TILE,
            "encoding": "national percentile, 1..255; 0 means no value",
            "source": f"data/processed/maps_norge (32 m, {MODEL})",
            "null_transfer_auc": null_auc, "species": []}
    OUT.mkdir(parents=True, exist_ok=True)

    for key, sp in entries:
        if only and key != only:
            continue
        tif = MAPS / f"suitability_{key}_{MODEL}_32m.tif"
        if not tif.exists():
            raise SystemExit(f"missing {tif} - run scripts/build_maps_national.py")
        t0 = time.time()
        grid, transform = downsample(tif)
        byts, n_finite = to_percentile_bytes(grid)
        ids = cut_tiles(byts, OUT / key)

        if meta["cell_m"] is None:
            meta.update(cell_m=abs(transform.a),
                        origin_x=transform.c, origin_y=transform.f,
                        grid={"w": int(grid.shape[1]), "h": int(grid.shape[0])},
                        tiles={"cols": (grid.shape[1] + TILE - 1) // TILE,
                               "rows": (grid.shape[0] + TILE - 1) // TILE})
        sci = sp.get("scientific_name", sp.get("label", key))
        kb = sum((OUT / key / f"{i}.png").stat().st_size for i in ids) / 1024
        meta["species"].append({
            "key": key, "scientific": sci,
            "no": sp.get("vernacular_no", ""), "en": sp.get("vernacular_en", ""),
            "is_null": key == spp["null_control"]["key"],
            "transfer_auc": aucs.get(sci, {}).get("auc"),
            "transfer_ci": aucs.get(sci, {}).get("ci"),
            "held_out_n": held.get(sci) or held.get(sp.get("label", "")),
            "scored_cells": n_finite, "tiles": ids,
        })
        print(f"{key:30s}{len(ids):5d} tiles{kb/1024:8.1f} MB  "
              f"{n_finite:,} cells  ({time.time()-t0:.0f}s)", flush=True)

    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")),
                                   encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.rglob("*.png"))
    print(f"\n{len(list(OUT.rglob('*.png'))):,} tiles, {total/1e6:.1f} MB total")
    print(f"grid {meta['grid']['w']:,} x {meta['grid']['h']:,} at "
          f"{meta['cell_m']:.0f} m, {meta['tiles']['cols']} x "
          f"{meta['tiles']['rows']} tiles")
    print(f"-> {(OUT / 'meta.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
