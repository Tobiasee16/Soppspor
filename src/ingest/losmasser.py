"""NGU superficial deposits (losmassekart), grouped and burned onto a grid.

CLAUDE.md lists this layer because calcareous versus acidic substrate strongly
structures fungal communities. What NGU actually ships is a polygon coverage
with 30 distinct `jordart` codes present in Agder, and 30 one-hot columns
against 246 thinned presences would be mostly empty columns contributing noise.
So the codes are collapsed into ten groups by depositional origin, which is the
axis that carries the drainage and chemistry the ecology cares about.

The grouping is fixed here, before the layer is fitted, and is not to be
adjusted after seeing a result. Two of the groups exist specifically to be
watched rather than believed:

* **marine** (hav-, fjord- og strandavsetning) is the best agricultural land in
  Agder and therefore where the settlements are. A model gaining from it may be
  reading accessibility, not substrate. FINDINGS section 8 registered this as
  the thing to check if *A. muscaria* improves.
* **anthropogenic** (fyllmasse, menneskepavirket materiale) is 0.2% of the area
  and is by definition where people have built. It is kept as its own column
  rather than folded into "other" precisely so that effort bias landing on it
  is visible instead of hidden inside a residual class.

Area shares over Agder, from the 2024-06-21 extract: bare bedrock 44.0%,
moraine 35.1%, thin organic cover 11.8%, peat 3.3%, glaciofluvial 1.5%,
fluvial 1.3%, marine 1.0%, submerged/unspecified 0.8%, colluvial 0.7%,
anthropogenic 0.2%.
"""
from __future__ import annotations

import pathlib

import numpy as np

# Group code -> (short name, NGU jordart codes). Codes are the national
# standard from the Losmasser product specification, not local inventions.
GROUPS: dict[int, tuple[str, set[int]]] = {
    1: ("moraine", {10, 11, 12, 14, 15, 16}),          # till: the default forest substrate
    2: ("glaciofluvial", {20, 22, 30}),                # sorted sand/gravel, sharply drained
    3: ("marine", {41, 42, 43}),                       # WATCH: also where people live
    4: ("fluvial", {50, 57, 60}),                      # river, flood and eolian sediment
    5: ("colluvial", {70, 72, 80, 81, 82, 305, 307, 308}),   # weathered and slope material
    6: ("peat", {90}),                                 # bog: mycorrhizal fungi largely absent
    7: ("organic_thin", {100}),                        # thin humus directly over bedrock
    8: ("anthropogenic", {120, 122}),                  # WATCH: fill and made ground
    9: ("bedrock_bare", {130}),                        # exposed rock, no deposit
    10: ("water_unspec", {0, 1}),                      # submerged or not assigned
}

# Burned into the raster as "no polygon here". Zero rather than a sentinel
# because rasterio's mask turns nodata into NaN and the categorical one-hot in
# src/features/stack.py then emits an explicit `_missing` column.
NODATA = 0

CATEGORIES = sorted(GROUPS)
LABELS = {code: name for code, (name, _) in GROUPS.items()}

_LOOKUP: dict[int, int] = {
    jordart: group for group, (_, codes) in GROUPS.items() for jordart in codes
}


def group_of(jordart: int) -> int:
    """Group code for one NGU jordart code, or NODATA if unrecognised.

    Unrecognised returns NODATA rather than raising or silently landing in a
    residual class. A code NGU adds later must show up as absent - and so be
    countable in the coverage report - rather than being quietly absorbed into
    "other", where it would look like a mapped substrate that it is not.
    """
    return _LOOKUP.get(int(jordart), NODATA)


def group_codes(jordart_values) -> np.ndarray:
    """Vectorised `group_of` over an array of jordart codes."""
    arr = np.asarray(jordart_values)
    out = np.full(arr.shape, NODATA, dtype="uint8")
    for jordart, group in _LOOKUP.items():
        out[arr == jordart] = group
    return out


def unmapped_codes(jordart_values) -> dict[int, int]:
    """Jordart codes present in the data that the grouping does not cover.

    Returned rather than warned about, so the caller decides. Anything in here
    becomes NODATA in the raster, and a non-empty result means the grouping has
    fallen behind the source and should be revisited before the run is read.
    """
    arr = np.asarray(jordart_values)
    out: dict[int, int] = {}
    for value in np.unique(arr):
        if int(value) not in _LOOKUP:
            out[int(value)] = int((arr == value).sum())
    return out


def rasterise_windowed(sources, reference: str | pathlib.Path,
                       out_path: str | pathlib.Path, block: int = 4096,
                       progress=None) -> dict:
    """Burn grouped deposit codes from one or more sources, window by window.

    Two things force this, both found on the national delivery.

    **Size.** The national LosmasseFlate shapefile is 728 MB of geometry and the
    target grid is 1.83 billion cells; neither end fits comfortably in 15.7 GB.

    **Count.** NGU ships Agder as one polygon layer and Norway as TWO -
    `LosmasseFlate_20240621` and `_20240622` - and they are spatially disjoint:
    0.0% of sampled points from the second fall inside any polygon of the first.
    It is 11,482 km2 of extra ground, 14.4% of it in Nordland, the held-out
    region. Reading only the first would leave the transfer test scoring
    `losmasse_missing` over ground that is in fact mapped, and the weaker result
    would look like poor transferability rather than like missing data. Nothing
    in the download names the second file as a supplement; the extraction date
    in the filename is the only signal. See FINDINGS section 20.

    So `sources` is a list, and every one of them is burned. A missing source is
    an error rather than a silent skip, because a silently skipped source is
    exactly the failure this function exists to prevent.

    Burn order is largest-first WITHIN each window, across all sources together,
    which is what the whole-array path does globally. The window reads every
    polygon overlapping it, not only those centred in it, so the overlap
    resolves the same at a window edge as in the middle.
    """
    import geopandas as gpd
    import pandas as pd
    import rasterio
    from rasterio.features import rasterize

    paths = [pathlib.Path(p) for p in sources]
    for p in paths:
        if not p.exists():
            raise FileNotFoundError(f"losmasser source missing: {p}")

    with rasterio.open(reference) as ref:
        profile = ref.profile.copy()
        transform, H, W = ref.transform, ref.height, ref.width
        ref_epsg = ref.crs.to_epsg()

    # CRS is checked up front, per source, before any burning happens. A
    # mismatch discovered halfway through would leave a half-written raster.
    from pyogrio import read_info
    n_features = 0
    for p in paths:
        info = read_info(str(p))
        # The true polygon count comes from the file header. Summing what the
        # windows read would count every polygon crossing a window boundary
        # more than once, which is a made-up number that looks like a real one.
        n_features += int(info.get("features") or 0)
        crs = info.get("crs")
        epsg = None
        if crs:
            try:
                from pyproj import CRS as _CRS
                epsg = _CRS.from_user_input(crs).to_epsg()
            except Exception:
                epsg = None
        if epsg is not None and ref_epsg is not None and epsg != ref_epsg:
            raise ValueError(
                f"{p.name} is EPSG:{epsg}, reference grid is EPSG:{ref_epsg}. "
                f"Reproject on download, not here.")

    profile.update(count=1, dtype="uint8", nodata=NODATA, compress="deflate",
                   tiled=True, blockxsize=256, blockysize=256,
                   # Classic TIFF caps at 4 GB. This layer is 1.83 GB at
                   # 32 m, under the limit, but 7.3 GB at 16 m - and the
                   # national terrain build already proved that the GDAL
                   # estimate-based default guesses wrong once
                   # compression is in play.
                   BIGTIFF="IF_SAFER")
    profile.pop("photometric", None)

    cell = abs(transform.a)
    nblocks = ((H + block - 1) // block) * ((W + block - 1) // block)
    done = 0
    covered = 0
    nreads = 0
    by_group = {c: 0 for c in CATEGORIES}
    unmapped: dict[int, int] = {}

    # A partial raster opens cleanly and reads as genuine "unmapped"
    # ground over whatever it never reached - the national terrain build
    # left 3.1 GB of valid-looking GeoTIFF behind when it hit the 4 GB
    # TIFF limit. Same rule as everywhere else: on any failure, remove it.
    try:
        with rasterio.open(out_path, "w", **profile) as out:
            for row0 in range(0, H, block):
                for col0 in range(0, W, block):
                    h = min(block, H - row0)
                    w = min(block, W - col0)
                    win = rasterio.windows.Window(col0, row0, w, h)
                    wt = rasterio.windows.transform(win, transform)
                    bbox = (wt.c - cell, wt.f - h * cell - cell,
                            wt.c + w * cell + cell, wt.f + cell)

                    frames = []
                    for p in paths:
                        g = gpd.read_file(p, engine="pyogrio", columns=["jordart"],
                                          bbox=bbox)
                        if len(g):
                            frames.append(g)
                    tile = np.full((h, w), NODATA, dtype="uint8")
                    if frames:
                        gdf = (frames[0] if len(frames) == 1
                               else pd.concat(frames, ignore_index=True))
                        nreads += len(gdf)
                        vals = gdf["jordart"].to_numpy()
                        for code, n in unmapped_codes(vals).items():
                            unmapped[code] = unmapped.get(code, 0) + n
                        groups = group_codes(vals)
                        order = np.argsort(-gdf.geometry.area.to_numpy())
                        shapes = [(gdf.geometry.iloc[i], int(groups[i]))
                                  for i in order if groups[i] != NODATA]
                        if shapes:
                            tile = rasterize(shapes, out_shape=(h, w), transform=wt,
                                             fill=NODATA, dtype="uint8",
                                             all_touched=False)
                    covered += int((tile != NODATA).sum())
                    for c in CATEGORIES:
                        by_group[c] += int((tile == c).sum())
                    out.write(tile, 1, window=win)
                    done += 1
                    if progress:
                        progress(done, nblocks)
            out.set_band_description(1, "losmasse_group")

    except BaseException:
        pathlib.Path(out_path).unlink(missing_ok=True)
        raise
    total = H * W
    return {
        "sources": [str(p) for p in paths],
        "polygons": n_features,
        # Reads, not polygons: a polygon crossing a window boundary is read by
        # each window it touches. Kept because a large ratio to `polygons`
        # means the block size is small relative to the geometry.
        "polygon_reads": nreads,
        "blocks": nblocks,
        "cells": total,
        "cells_covered": covered,
        "coverage_pct": 100.0 * covered / total if total else 0.0,
        "unmapped_jordart": unmapped,
        "cells_by_group": {LABELS[c]: by_group[c] for c in CATEGORIES},
        "path": str(out_path),
    }


def rasterise(shapefile: str | pathlib.Path, reference: str | pathlib.Path,
              out_path: str | pathlib.Path) -> dict:
    """Burn grouped deposit codes onto the reference raster's exact grid.

    The grid is taken from `reference` rather than computed, so the result is
    cell-for-cell aligned with the terrain stack by construction. Computing an
    equivalent grid independently would put alignment at the mercy of two
    floating-point transforms agreeing, which is the kind of half-cell shift
    that produces plausible numbers and no error.

    No reprojection happens here. NGU ships Agder in EPSG:25833 already, and
    CLAUDE.md says reproject on ingest, never mid-pipeline; a mismatch is
    therefore a fault to report, not something to paper over.
    """
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize

    shapefile = pathlib.Path(shapefile)
    # latin1: the NGU dbf is not UTF-8, and the Norwegian names come back
    # mojibaked otherwise. Only the labels are affected, never the codes, but a
    # report that prints "l?smassene" is a report nobody trusts.
    gdf = gpd.read_file(shapefile, encoding="latin1")

    with rasterio.open(reference) as ref:
        profile = ref.profile.copy()
        transform, height, width = ref.transform, ref.height, ref.width
        ref_crs = ref.crs

    if gdf.crs is None or gdf.crs.to_epsg() != ref_crs.to_epsg():
        raise ValueError(
            f"{shapefile.name} is {gdf.crs}, reference grid is {ref_crs}. "
            "Reproject on download, not here."
        )

    unmapped = unmapped_codes(gdf["jordart"].to_numpy())
    groups = group_codes(gdf["jordart"].to_numpy())

    # Sorted by area, largest first, so that where polygons overlap the smaller
    # and more specific one is burned last and wins. rasterize takes the last
    # value written for a cell.
    order = np.argsort(-gdf.geometry.area.to_numpy())
    shapes = [(gdf.geometry.iloc[i], int(groups[i]))
              for i in order if groups[i] != NODATA]

    burned = rasterize(shapes, out_shape=(height, width), transform=transform,
                       fill=NODATA, dtype="uint8", all_touched=False)

    profile.update(count=1, dtype="uint8", nodata=NODATA, compress="deflate")
    profile.pop("photometric", None)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(burned, 1)
        dst.set_band_description(1, "losmasse_group")

    total = burned.size
    covered = int((burned != NODATA).sum())
    return {
        "polygons": len(gdf),
        "polygons_burned": len(shapes),
        "cells": total,
        "cells_covered": covered,
        "coverage_pct": 100.0 * covered / total if total else 0.0,
        "unmapped_jordart": unmapped,
        "cells_by_group": {LABELS[c]: int((burned == c).sum()) for c in CATEGORIES},
        "path": str(out_path),
    }
