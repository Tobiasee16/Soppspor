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

**The byte is a rank, not a scaled score.** Scaling linearly between the
surface's own min and max - what the Kristiansand payload does - puts 62.8% of
*C. tubaeformis*'s land into bytes 1-3, because the national distribution has a
p98/p2 ratio of 814 (FINDINGS 23c). Two thirds of the country would be one flat
value. Storing a rank spends all 255 levels where they are needed.

**The rank is against the surrounding 45 km, not against the country.** This
was national at first, and that was wrong in a way no test caught, because
nationally the spread looked perfect: 21/24/24/18/12 across the five bands.
The problem only appears when you stand somewhere. Kristiansand is lowland,
coastal, spruce, mild and wet, so it sits in the national top decile almost
everywhere - 77% of the kommune came out in the top band, median 97.5th
percentile, the whole map one colour. The Kristiansand build it replaced had
ranked within Kristiansand and gave a full 20/25/25/20/10 spread, which is what
made it useful. A forager standing in a wood is asking "is this better than the
rest of around here", not "is this better than Finnmark".

A neighbourhood rank answers the first question while keeping what the national
rank was for: the value is precomputed per cell, so it does not move when the
user pans, and the same wood is the same colour at every zoom. Ranking against
whatever is on screen would have restored the contrast and lost that.

**Cells that grow nothing are not ranked at all.** Every built-up cell and every
lake carried an estimate, and above the national median at that: built-up came
out at the 68th percentile with 46% of it reading "Hoy" or better, freshwater at
the 62nd. The model discriminates correctly where it has habitat data - forest
78th, open ground 21st, glacier 1st - it was simply never told to stay off water
and asphalt. AR50 decides: forest, open ground and mire are scored, everything
else is left blank. Masking is not a substitute for the change above and does
not pretend to be; on its own it moved Kristiansand's top band from 76.9% to
79.4%, because habitat is 88% of the scored cells anyway.

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
from scipy.ndimage import uniform_filter

from src import species as species_cfg
from src.report_tables import held_out_counts, transfer_aucs

MAPS = ROOT / "data" / "processed" / "maps_norge"
OUT = ROOT / "data" / "processed" / "zone_tiles"
TRANSFER = ROOT / "data" / "processed" / "transferability_norge.md"

FACTOR = 4                 # 32 m source -> 128 m display grid
AR50_TIF = ROOT / "data" / "interim" / "ar50_norge_32m.tif"
# Skog, apen fastmark, myr. Not bebygd, jordbruk, ferskvann, hav, sno og isbre.
HABITAT_AR50 = (30, 50, 60)
NEIGHBOURHOOD_M = 25_000   # the ground a cell is ranked against; see
                           # FINDINGS for the sweep that chose it
COARSE_M = 8192            # the block the neighbourhood histogram is built on
TILE = 256                 # cells per tile side; 256 * 128 m = 32.768 km
MODEL = "maxent"

# Three resolutions, each a factor of four coarser than the last. The app holds
# a budget of cells rather than a budget of ground, so one level covers about
# 9,800 km2 on screen and then runs out; without a coarser one the zones simply
# stop, which is what a beginner hits the moment they zoom out to see where in
# their county is worth walking.
#
# Four-fold steps rather than two-fold because the cost is in the number of
# levels, not their coarseness: level 1 is a sixteenth of level 0's tiles and
# level 2 a sixteenth of that, so the pyramid adds under 7% to what is shipped.
# Three of them is enough to put the whole mainland on a phone screen.
#
# The switch happens where the finer level runs out of budget, which is also
# where its extra detail is below one screen pixel - so crossing a level should
# be close to invisible rather than a visible re-draw of every boundary.
LEVELS = [4, 16, 64]       # 32 m source -> 128 m, 512 m, 2048 m


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
    """Mean the raster down by `factor`, ignoring nodata. Returns (array, transform)."""
    sums, counts, transform = downsample_sums(path, factor)
    return means(sums, counts), transform


def means(sums: np.ndarray, counts: np.ndarray) -> np.ndarray:
    """Sums and counts to a mean, with no count meaning no value."""
    out = np.full(sums.shape, np.nan, dtype="float32")
    ok = counts > 0
    out[ok] = sums[ok] / counts[ok]
    return out


def coarsen(sums: np.ndarray, counts: np.ndarray, factor: int):
    """One more level of the pyramid, from the level above it.

    Sums and counts rather than means, which is the whole reason this is not a
    mean of means. A 512 m cell drawn from sixteen 128 m cells is only the mean
    of the 32 m cells underneath it if each of those sixteen carried the same
    number of valid cells - and at the coast they do not. Carrying the counts
    makes every level exactly the mean of the source cells it covers, instead
    of a weighting that quietly favours whichever coarse cells happened to be
    half sea.

    Trailing rows and columns that do not fill a whole block are dropped, the
    same convention `downsample_sums` uses, so the origin never moves.
    """
    h, w = sums.shape[0] // factor, sums.shape[1] // factor
    cut = (slice(0, h * factor), slice(0, w * factor))
    shape = (h, factor, w, factor)
    return (sums[cut].reshape(shape).sum(axis=(1, 3)),
            counts[cut].reshape(shape).sum(axis=(1, 3)))


def downsample_sums(path: pathlib.Path, factor: int = FACTOR):
    """Block sums and valid-cell counts. Returns (sums, counts, transform).

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
        sums = np.empty((h, w), dtype="float32")
        counts = np.empty((h, w), dtype="float32")
        for r0 in range(0, h, 256):
            r1 = min(r0 + 256, h)
            win = rasterio.windows.Window(0, r0 * factor, w * factor,
                                          (r1 - r0) * factor)
            block = src.read(1, window=win).astype("float32")
            block = block.reshape(r1 - r0, factor, w, factor)
            ok = np.isfinite(block)
            sums[r0:r1] = np.where(ok, block, 0.0).sum(axis=(1, 3))
            counts[r0:r1] = ok.sum(axis=(1, 3))
        t = src.transform
        transform = rasterio.Affine(t.a * factor, t.b, t.c,
                                    t.d, t.e * factor, t.f)
    return sums, counts, transform


def habitat_fraction(path: pathlib.Path, factor: int = FACTOR) -> np.ndarray:
    """Per display cell, the share of its 32 m sub-cells that could grow anything.

    Read in the same blocks as `downsample_sums`, from a raster on exactly the
    same grid as the predictions, so the mask lines up by construction rather
    than by a reprojection nobody would check.
    """
    keep = np.zeros(256, dtype=bool)
    for code in HABITAT_AR50:
        keep[code] = True
    with rasterio.open(path) as src:
        h, w = src.height // factor, src.width // factor
        out = np.empty((h, w), dtype="float32")
        for r0 in range(0, h, 256):
            r1 = min(r0 + 256, h)
            win = rasterio.windows.Window(0, r0 * factor, w * factor,
                                          (r1 - r0) * factor)
            ok = keep[src.read(1, window=win)]
            out[r0:r1] = ok.reshape(r1 - r0, factor, w, factor).mean(axis=(1, 3))
    return out


def local_percentile(grid: np.ndarray, cell_m: float,
                     radius_m: float = NEIGHBOURHOOD_M,
                     bins: int = 128) -> np.ndarray:
    """Rank each cell against the habitat within `radius_m` of it.

    Done exactly, this is 114 million cells each ranked against roughly 400,000
    neighbours, which is not a computation anybody runs. Done on a coarse grid
    of histograms it is seconds: bin every score once using national quantiles
    as edges, sum the histograms over a moving window of ~8 km blocks, and read
    each cell's percentile off the window's CDF. The bin edges are national
    quantiles rather than equal widths so the resolution sits where the data
    is - 128 bins, each holding 0.8% of the country.

    The four surrounding block CDFs are interpolated between, or the map would
    carry visible 8 km seams where one block's distribution gave way to the
    next.
    """
    finite = np.isfinite(grid)
    out = np.full(grid.shape, np.nan, dtype="float32")
    vals = grid[finite]
    if vals.size == 0:
        return out

    cuts = np.quantile(vals, np.linspace(0.0, 1.0, bins + 1))
    edges = cuts[1:-1].astype("float32")          # interior, for searchsorted
    lo_e = cuts[:-1].astype("float32")            # per-bin bounds, for the
    hi_e = cuts[1:].astype("float32")             # interpolation below
    span = np.maximum(hi_e - lo_e, 1e-12)
    block = max(1, int(round(COARSE_M / cell_m)))
    h, w = grid.shape
    cy, cx = -(-h // block), -(-w // block)

    hist = np.zeros((cy, cx, bins), dtype="float32")
    for by in range(cy):
        strip = grid[by * block:min((by + 1) * block, h)]
        f = np.isfinite(strip)
        if not f.any():
            continue
        b = np.searchsorted(edges, strip[f], side="right")
        col = np.nonzero(f)[1] // block
        hist[by] = np.bincount(col * bins + b,
                               minlength=cx * bins).reshape(cx, bins)

    # Sum over the neighbourhood. Zero padding at the edges is right: a cell on
    # the coast is ranked against the land near it and nothing else.
    r = max(1, int(round(radius_m / (block * cell_m))))
    win = uniform_filter(hist, size=(2 * r + 1, 2 * r + 1, 1),
                         mode="constant", cval=0.0)
    cdf = np.cumsum(win, axis=2)
    tot = cdf[:, :, -1:].copy()
    np.divide(cdf, np.maximum(tot, 1e-9), out=cdf)
    cdf[np.broadcast_to(tot <= 0, cdf.shape)] = 0.0
    # A leading zero, so cdf[..., b] is the share *below* bin b and
    # cdf[..., b + 1] the share up to and including it. Reading only the upper
    # figure gave every cell in a bin the same answer, and in a neighbourhood
    # whose habitat sits inside a handful of national bins that is a plateau:
    # 13.5% of Kristiansand came out at exactly 1.0, so the top zone covered
    # 13.5% of the kommune rather than the 5% it claims to.
    cdf = np.concatenate([np.zeros(cdf.shape[:2] + (1,), dtype=cdf.dtype), cdf],
                         axis=2)

    for y0 in range(0, h, 512):
        y1 = min(y0 + 512, h)
        sub_g = grid[y0:y1]
        f = np.isfinite(sub_g)
        if not f.any():
            continue
        ys, xs = np.nonzero(f)
        v = sub_g[f]
        b = np.clip(np.searchsorted(edges, v, side="right"), 0, bins - 1)
        # Where the value sits inside its own bin, so the answer moves
        # continuously across the bin instead of stepping at its edge.
        frac = np.clip((v - lo_e[b]) / span[b], 0.0, 1.0).astype("float32")
        fy = (ys + y0 + 0.5) / block - 0.5
        fx = (xs + 0.5) / block - 0.5
        i0 = np.clip(np.floor(fy), 0, cy - 1).astype(np.int32)
        j0 = np.clip(np.floor(fx), 0, cx - 1).astype(np.int32)
        i1 = np.minimum(i0 + 1, cy - 1)
        j1 = np.minimum(j0 + 1, cx - 1)
        wy = np.clip(fy - i0, 0.0, 1.0).astype("float32")
        wx = np.clip(fx - j0, 0.0, 1.0).astype("float32")
        def at(i, j):
            base = cdf[i, j, b]
            return base + frac * (cdf[i, j, b + 1] - base)

        out[y0:y1][f] = ((1 - wy) * (1 - wx) * at(i0, j0)
                         + (1 - wy) * wx * at(i0, j1)
                         + wy * (1 - wx) * at(i1, j0)
                         + wy * wx * at(i1, j1))
    return out


def to_percentile_bytes(pct: np.ndarray) -> tuple[np.ndarray, int]:
    """A 0..1 rank to bytes 1..255. 0 = no data.

    Ties take the lower rank, matching the `lowerBound` the interface already
    uses to turn a byte into a percentile.
    """
    finite = np.isfinite(pct)
    out = np.zeros(pct.shape, dtype="uint8")
    n = int(finite.sum())
    if n:
        out[finite] = np.clip(np.round(pct[finite] * 254.0) + 1,
                              1, 255).astype("uint8")
    return out, n


def _blur_pass(P: np.ndarray, V: np.ndarray, axis: int):
    """One validity-weighted 3-tap box pass, matching `smoothPct` in shell.html.

    Weighted by validity rather than plain, because only a fifth of the grid
    holds a value and the rest is sea: a plain blur averages every coastal cell
    against a crowd of zeros and drags the whole field down. The interface
    found this the hard way - the 95th-percentile zone disappeared completely
    and the 85th covered 0.1% of the grid.

    Edges count only the neighbours that exist, exactly as the JS does, so the
    two implementations agree cell for cell.
    """
    def shift(a, k):
        out = np.zeros_like(a)
        if k > 0:
            sl_dst = (slice(k, None), slice(None)) if axis == 0 else (slice(None), slice(k, None))
            sl_src = (slice(None, -k), slice(None)) if axis == 0 else (slice(None), slice(None, -k))
        else:
            sl_dst = (slice(None, k), slice(None)) if axis == 0 else (slice(None), slice(None, k))
            sl_src = (slice(-k, None), slice(None)) if axis == 0 else (slice(None), slice(-k, None))
        out[sl_dst] = a[sl_src]
        return out

    PV = P * V
    num = PV + shift(PV, 1) + shift(PV, -1)
    den = V + shift(V, 1) + shift(V, -1)
    ones = np.ones_like(V)
    n = ones + shift(ones, 1) + shift(ones, -1)
    newP = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0).astype("float32")
    newV = (den / n).astype("float32")
    return newP, newV


def smoothed_rank_lut(pct: np.ndarray, buckets: int = 256) -> list[float]:
    """What the interface's post-smoothing re-rank would give, precomputed.

    `habitatField` in shell.html smooths the percentile surface and then ranks
    it *again*, because smoothing pulls the extremes in hard - 5% of cells sit
    above the 95th percentile and after the blur none of them do, so the top of
    the ramp stops existing. Re-ranking restores it.

    That second rank still has to be precomputed, and for the same reason the
    first one is: done over the visible window, "the top 5%" silently becomes
    "the top 5% of what is on screen" and the zones move when the user pans.
    What is ranked here is the neighbourhood percentile surface, not the raw
    scores, so the answer the app reads back is already local.
    """
    finite = np.isfinite(pct)
    if not finite.any():
        return [0.0] * buckets
    P = np.where(finite, pct, 0.0).astype("float32")
    V = finite.astype("float32")
    for _ in range(2):
        P, V = _blur_pass(P, V, axis=1)
        P, V = _blur_pass(P, V, axis=0)
    P[~finite] = 0.0                      # never scored stays under every zone

    smooth = np.sort(P[finite])
    edges = np.arange(buckets, dtype="float64") / (buckets - 1)
    return (np.searchsorted(smooth, edges, side="left")
            / smooth.size).round(6).tolist()


def level_meta(sums, counts, transform, factor):
    """What one level of the pyramid contributes to meta.json."""
    return {"cell_m": abs(transform.a) * factor / LEVELS[0],
            "grid": {"w": int(sums.shape[1]), "h": int(sums.shape[0])},
            "tiles": {"cols": (sums.shape[1] + TILE - 1) // TILE,
                      "rows": (sums.shape[0] + TILE - 1) // TILE}}


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

    # One raster for every species, so read it once. Majority rather than any:
    # a 128 m cell that is mostly lake is a lake.
    if not AR50_TIF.exists():
        raise SystemExit(f"missing {AR50_TIF} - the habitat mask needs AR50")
    t_mask = time.time()
    keep = habitat_fraction(AR50_TIF) >= 0.5
    print(f"habitat mask: {keep.sum():,} of {keep.size:,} cells kept "
          f"({keep.sum() / keep.size * 100:.1f}%)  ({time.time() - t_mask:.0f}s)",
          flush=True)

    meta = {"crs": "EPSG:25833", "cell_m": None, "tile_px": TILE,
            "encoding": (f"rank within {NEIGHBOURHOOD_M // 1000} km of habitat, "
                         "1..255; 0 means no value or not habitat"),
            "neighbourhood_m": NEIGHBOURHOOD_M,
            "habitat_ar50": list(HABITAT_AR50),
            # One entry per pyramid level, coarsest last. The app picks the
            # finest whose window fits its cell budget.
            "levels": None,
            # An explicit flag rather than leaving the interface to parse the
            # sentence above. The Kristiansand payload carries linearly scaled
            # bytes and must keep sorting them; these are already ranks.
            "percentile_bytes": True,
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
        # Read the source once. Every coarser level is built from the sums and
        # counts of the level above it, so the 7.3 GB GeoTIFF is walked once
        # per species rather than once per species per level.
        sums, counts, transform = downsample_sums(tif)
        # Blank the ground that grows nothing before anything is ranked, so it
        # neither gets a colour nor takes up room in the distribution its
        # neighbours are measured against.
        sums[~keep] = 0.0
        counts[~keep] = 0.0
        levels, tiles, luts, n_finite = [], [], [], None
        for z, factor in enumerate(LEVELS):
            if z:
                sums, counts = coarsen(sums, counts, factor // LEVELS[z - 1])
            grid = means(sums, counts)
            cell_m = abs(transform.a) * factor / LEVELS[0]
            pct = local_percentile(grid, cell_m)
            byts, n = to_percentile_bytes(pct)
            if z == 0:
                n_finite = n
            levels.append(level_meta(sums, counts, transform, factor))
            luts.append(smoothed_rank_lut(pct))
            tiles.append(cut_tiles(byts, OUT / key / str(z)))
            del grid, pct

        if meta["cell_m"] is None:
            meta.update(cell_m=abs(transform.a),
                        origin_x=transform.c, origin_y=transform.f,
                        grid=levels[0]["grid"], tiles=levels[0]["tiles"],
                        levels=levels)
        ids = tiles[0]
        sci = sp.get("scientific_name", sp.get("label", key))
        kb = sum(p.stat().st_size for p in (OUT / key).rglob("*.png")) / 1024
        meta["species"].append({
            "key": key, "scientific": sci,
            "no": sp.get("vernacular_no", ""), "en": sp.get("vernacular_en", ""),
            "is_null": key == spp["null_control"]["key"],
            "transfer_auc": aucs.get(sci, {}).get("auc"),
            "transfer_ci": aucs.get(sci, {}).get("ci"),
            "held_out_n": held.get(sci) or held.get(sp.get("label", "")),
            "scored_cells": n_finite,
            # Per level, coarsest last: which tiles exist, and a 256-entry CDF
            # of that level's nationally smoothed percentile surface. The app
            # smooths its own window and looks the result up, so the zone
            # thresholds mean the same thing wherever the user is - and each
            # level needs its own, because the same blur over a coarser grid
            # covers different ground and would otherwise be read against a
            # distribution that was never computed from it.
            "tiles": tiles, "smoothed_rank_lut": luts,
        })
        print(f"{key:30s}{sum(len(t) for t in tiles):5d} tiles "
              f"({'+'.join(str(len(t)) for t in tiles)}){kb/1024:8.1f} MB  "
              f"{n_finite:,} cells  ({time.time()-t0:.0f}s)", flush=True)

    (OUT / "meta.json").write_text(json.dumps(meta, separators=(",", ":")),
                                   encoding="utf-8")
    total = sum(p.stat().st_size for p in OUT.rglob("*.png"))
    print(f"\n{len(list(OUT.rglob('*.png'))):,} tiles, {total/1e6:.1f} MB total")
    for z, lv in enumerate(meta["levels"]):
        print(f"  level {z}: {lv['grid']['w']:,} x {lv['grid']['h']:,} at "
              f"{lv['cell_m']:.0f} m, {lv['tiles']['cols']} x "
              f"{lv['tiles']['rows']} tiles")
    print(f"-> {(OUT / 'meta.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
