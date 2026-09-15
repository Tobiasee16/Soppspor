"""Export the habitat mask as tiles, four times finer than the zone tiles.

Why this exists. A zone tile holds 128 m cells, and a cell that is mostly lake
or mostly street carries no score. The contour tracer therefore has to close a
ring around every such cell, and a ring around a single cell can only be a
diamond: four edge midpoints joined up. Nationally there are 63,011 blank
patches exactly one cell across, so the map was stippled with little rotated
squares that matched nothing on the ground. Reported from the phone as looking
"half done", and it did.

The fix is to stop asking a 128 m grid to draw the edge of a 100 m tarn. The
contour is allowed to run through small blanks, and the true shape is cut out
afterwards with this mask, which is AR50 at its own 32 m resolution.

Geometry. One mask tile per zone tile position, covering exactly the same
ground at four times the resolution: 1024 px against the zone tile's 256. So
level 0 is the 32 m source itself, level 1 is 128 m and level 2 is 512 m, and
a mask tile can be drawn with the zone tile's own bounding box without any
separate index.

Format. A one-bit palette PNG whose transparency is the payload: habitat is
transparent, everything else opaque. That is what `destination-out` wants, so
the interface erases with the file as it arrives rather than decoding it into
a mask of its own. Hand-rolled for the same reason as png_gray in
export_zone_tiles.py - Pillow is in this environment as a matplotlib
dependency, not as a declared one.

    python scripts/export_mask_tiles.py
"""
from __future__ import annotations

import json
import pathlib
import struct
import sys
import time
import zlib

import numpy as np
import rasterio

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import export_zone_tiles as ez  # noqa: E402

OUT = ez.OUT / "mask"
MASK_PX = 1024                  # 4x the zone tile's 256, over the same ground
FINER = MASK_PX // ez.TILE      # 4


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def png_mask(habitat: np.ndarray) -> bytes:
    """One-bit PNG: opaque where `habitat` is False, transparent where True.

    Palette index 0 is the opaque colour and index 1 the transparent one, so
    tRNS is two bytes. The colour itself never shows - only its alpha is ever
    used - but it is black rather than undefined so that anything rendering the
    file as a picture gets something sensible.
    """
    if habitat.dtype != np.bool_ or habitat.ndim != 2:
        raise ValueError(
            f"expected a 2-D bool array, got {habitat.dtype} {habitat.shape}")
    h, w = habitat.shape
    packed = np.packbits(habitat, axis=1, bitorder="big")
    raw = b"".join(b"\x00" + packed[r].tobytes() for r in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 3, 0, 0, 0))
            + _chunk(b"PLTE", b"\x00\x00\x00\x00\x00\x00")
            + _chunk(b"tRNS", bytes([255, 0]))
            + _chunk(b"IDAT", zlib.compress(raw, 9))
            + _chunk(b"IEND", b""))


def wanted(meta: dict) -> list[set]:
    """Tile positions to write, per level: every zone tile and its neighbours.

    The neighbours matter because the interface lets a contour run a couple of
    cells past the ground it was traced from, and a couple of cells from a tile
    edge is the next tile along. A rim of zone left un-erased there would be
    exactly the artefact this file exists to remove.
    """
    out = []
    for z in range(len(meta["levels"])):
        seen = set()
        for sp in meta["species"]:
            for tid in sp["tiles"][z]:
                r, c = (int(v) for v in tid.split("_"))
                seen.add((r, c))
        cols = meta["levels"][z]["tiles"]["cols"]
        rows = meta["levels"][z]["tiles"]["rows"]
        grown = set()
        for r, c in seen:
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols:
                        grown.add((rr, cc))
        out.append(grown)
    return out


def level_masks():
    """The habitat mask at the resolution levels 1 and 2 need.

    Level 1 uses the same rule the zone grid itself uses - a 128 m cell counts
    as habitat when at least half of it is - so the overlay and the scoring
    agree about which cells exist. Level 2 takes any habitat in the block,
    because that is how coarsening keeps a cell scored: a 2048 m zone cell
    survives if any of the 128 m cells under it did.
    """
    t0 = time.time()
    keep128 = ez.habitat_fraction(ez.AR50_TIF) >= 0.5
    print(f"128 m mask: {keep128.sum():,} of {keep128.size:,} cells "
          f"({keep128.sum() / keep128.size * 100:.1f}%)  "
          f"({time.time() - t0:.0f}s)", flush=True)
    h, w = keep128.shape
    f = ez.LEVELS[2] // ez.LEVELS[1]        # 512 m blocks of 128 m cells
    h2, w2 = h // f, w // f
    keep512 = keep128[:h2 * f, :w2 * f].reshape(h2, f, w2, f).any(axis=(1, 3))
    return keep128, keep512


def main() -> int:
    meta_path = ez.OUT / "meta.json"
    if not meta_path.exists():
        raise SystemExit(
            f"missing {meta_path} - run scripts/export_zone_tiles.py first")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if not ez.AR50_TIF.exists():
        raise SystemExit(f"missing {ez.AR50_TIF}")

    todo = wanted(meta)
    keep128, keep512 = level_masks()
    ids = []
    total = 0

    hab32 = np.zeros(256, dtype=bool)
    for code in ez.HABITAT_AR50:
        hab32[code] = True

    with rasterio.open(ez.AR50_TIF) as src:
        for z in range(len(meta["levels"])):
            t0 = time.time()
            dest = OUT / str(z)
            dest.mkdir(parents=True, exist_ok=True)
            here, skipped, bytes_out = [], 0, 0
            for (r, c) in sorted(todo[z]):
                if z == 0:
                    win = rasterio.windows.Window(c * MASK_PX, r * MASK_PX,
                                                  MASK_PX, MASK_PX)
                    block = hab32[src.read(1, window=win, boundless=True,
                                           fill_value=0)]
                else:
                    grid = keep128 if z == 1 else keep512
                    y0, x0 = r * MASK_PX, c * MASK_PX
                    block = np.zeros((MASK_PX, MASK_PX), dtype=bool)
                    sub = grid[y0:y0 + MASK_PX, x0:x0 + MASK_PX]
                    block[:sub.shape[0], :sub.shape[1]] = sub
                if block.all():
                    skipped += 1        # nothing to erase here
                    continue
                data = png_mask(block)
                (dest / f"{r}_{c}.png").write_bytes(data)
                bytes_out += len(data)
                here.append(f"{r}_{c}")
            ids.append(here)
            total += bytes_out
            print(f"  level {z}: {len(here):5d} tiles  {bytes_out / 1024:9.0f} kB"
                  f"   ({skipped} all-habitat tiles skipped)  "
                  f"({time.time() - t0:.0f}s)", flush=True)

    meta["mask"] = {"url": "mask/{z}/{row}_{col}.png", "px": MASK_PX,
                    "finer": FINER, "tiles": ids,
                    "note": ("habitat is transparent, everything else opaque; "
                             "one tile per zone tile position, four times finer")}
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    print(f"\n{sum(len(i) for i in ids):,} mask tiles, {total / 1e6:.1f} MB")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
