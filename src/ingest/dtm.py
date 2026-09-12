"""Terrain model download from Kartverket's WCS, tiled and mosaicked.

Verified 2026-09-09:
    https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833
    coverage `nhm_dtm_topo_25833`, formats GeoTIFF / NetCDF / HDF / JPEG2000

This is the one layer that cannot be assembled from point queries. The
topographic wetness index needs flow accumulation, which needs a continuous
surface: an upslope catchment cannot be reconstructed from lookups at the
points we happen to care about.

VERIFIED BEHAVIOUR:

* The server resamples on request, so a 16 m grid is a `width`/`height` choice
  rather than something we downsample ourselves.
* 3000x3000 at 16 m returns in about 8 seconds. **4000x4000 returns HTTP 504.**
  Tiles are therefore capped well below that.
* `nodata` is not declared in the returned GeoTIFF and sea reads as values near
  zero rather than as a fill value. Sea is *not* distinguishable from low-lying
  land by elevation alone - mask with the boundary polygon, never by threshold.
* Tiles are snapped to a multiple of the resolution from the CRS origin, so
  repeated runs and adjacent extents share one grid.
"""
from __future__ import annotations

import io
import math
import pathlib
import ssl
import time
import urllib.parse
import urllib.request

import numpy as np
import rasterio
from rasterio.transform import from_origin

WCS = "https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833"
COVERAGE = "nhm_dtm_topo_25833"
CRS = "EPSG:25833"
TIMEOUT = 300
CTX = ssl.create_default_context()
UA = {"User-Agent": "soppkart/0.1 (research)"}

# 3000 works and 4000 times out; leave headroom for a loaded server.
MAX_TILE_PX = 2500


class DtmError(RuntimeError):
    """The DTM service did not answer as verified. Never filled with a guess."""


def snap_bbox(bbox: tuple[float, float, float, float], res_m: float) -> tuple[float, float, float, float]:
    """Expand a bbox outwards to whole multiples of the resolution."""
    minx, miny, maxx, maxy = bbox
    return (
        math.floor(minx / res_m) * res_m,
        math.floor(miny / res_m) * res_m,
        math.ceil(maxx / res_m) * res_m,
        math.ceil(maxy / res_m) * res_m,
    )


def _tile(minx: float, miny: float, maxx: float, maxy: float, w: int, h: int) -> np.ndarray:
    q = urllib.parse.urlencode({
        "service": "WCS", "request": "GetCoverage", "version": "1.0.0",
        "coverage": COVERAGE, "crs": CRS, "response_crs": CRS,
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "width": w, "height": h, "format": "GeoTIFF",
    })
    req = urllib.request.Request(f"{WCS}?{q}", headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read()
    except Exception as e:  # noqa: BLE001
        raise DtmError(f"WCS tile ({minx},{miny})-({maxx},{maxy}) failed: {e}") from e

    if body[:2] not in (b"II", b"MM"):
        raise DtmError(
            f"WCS returned {len(body)} bytes that are not a TIFF: "
            f"{body[:200]!r}"
        )
    with rasterio.open(io.BytesIO(body)) as ds:
        arr = ds.read(1)
    if arr.shape != (h, w):
        raise DtmError(f"WCS returned shape {arr.shape}, expected {(h, w)}")
    return arr


def download_dtm(
    bbox: tuple[float, float, float, float],
    dest: pathlib.Path,
    resolution_m: float = 16.0,
    tile_px: int = MAX_TILE_PX,
    pause_s: float = 0.5,
    progress=None,
) -> dict:
    """Fetch a DTM mosaic for a bbox and write it as a single GeoTIFF.

    Fails loudly on any bad tile and removes the partial file. A DTM with a
    silently empty tile would produce a plausible-looking wetness index over a
    hole in the terrain, which is exactly the kind of error that survives to a
    result.
    """
    if tile_px > MAX_TILE_PX:
        raise ValueError(f"tile_px {tile_px} risks HTTP 504; the measured ceiling is ~3000")

    minx, miny, maxx, maxy = snap_bbox(bbox, resolution_m)
    width = int(round((maxx - minx) / resolution_m))
    height = int(round((maxy - miny) / resolution_m))
    nx = math.ceil(width / tile_px)
    ny = math.ceil(height / tile_px)

    dest.parent.mkdir(parents=True, exist_ok=True)
    transform = from_origin(minx, maxy, resolution_m, resolution_m)
    profile = {
        "driver": "GTiff", "dtype": "float32", "count": 1,
        "width": width, "height": height, "crs": CRS, "transform": transform,
        "compress": "deflate", "predictor": 2, "tiled": True,
        "blockxsize": 256, "blockysize": 256, "nodata": np.nan,
    }

    done = 0
    try:
        with rasterio.open(dest, "w", **profile) as out:
            for ty in range(ny):
                for tx in range(nx):
                    col0 = tx * tile_px
                    row0 = ty * tile_px
                    w = min(tile_px, width - col0)
                    h = min(tile_px, height - row0)
                    tminx = minx + col0 * resolution_m
                    tmaxy = maxy - row0 * resolution_m
                    tmaxx = tminx + w * resolution_m
                    tminy = tmaxy - h * resolution_m

                    arr = _tile(tminx, tminy, tmaxx, tmaxy, w, h)
                    out.write(arr.astype("float32"), 1,
                              window=rasterio.windows.Window(col0, row0, w, h))
                    done += 1
                    if progress:
                        progress(done, nx * ny, (tminx, tminy, tmaxx, tmaxy))
                    time.sleep(pause_s)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    return {
        "path": str(dest),
        "bbox": [minx, miny, maxx, maxy],
        "resolution_m": resolution_m,
        "width": width, "height": height,
        "tiles": nx * ny,
        "crs": CRS,
        "source": WCS,
        "coverage": COVERAGE,
    }
