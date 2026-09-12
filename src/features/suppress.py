"""Suppression of output cells near sensitive habitat.

CLAUDE.md: "Build the capability to suppress or blur output cells near
sensitive habitat into the tile pipeline from the start, not as a later policy
layer. The habitats richest in interesting fungi (kalkskog, old-growth) are
disproportionately the ones holding threatened species."

This module is that capability. It sits between prediction and export, and the
export path calls it unconditionally - there is no code path that writes a
suitability raster without passing through here. That is the point of building
it early: a suppression step that can be skipped is a policy layer, and a
policy layer gets forgotten.

Two modes, because they fail differently:

* **mask** removes the cells outright. Honest and obvious, but a hole in a map
  is itself a signal - a sharp nodata disc says "something is here" as loudly
  as a high score would. Use where the location is already public.
* **blur** replaces the neighbourhood with a smoothed version of itself, so the
  fine structure that would let someone find a specific stand is gone while the
  map stays continuous and the suppression is not visible as a shape.

`blur` is the default. Neither mode is a security control: both assume the
sensitive locations themselves are not published alongside the output.

**The sensitive set is an input, not a default.** With an empty set both modes
are no-ops, and callers are expected to report that rather than let an
unsuppressed map look reviewed. `describe()` exists for exactly that.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SuppressionReport:
    """What suppression actually did, for the export to print.

    Returned rather than logged, because "we suppressed nothing because we had
    nothing to suppress with" and "we suppressed nothing because suppression
    is off" must never look the same in a report.
    """
    mode: str
    radius_m: float
    n_sites: int
    cells_affected: int
    cells_total: int

    @property
    def active(self) -> bool:
        return self.n_sites > 0 and self.mode != "off"

    @property
    def pct(self) -> float:
        return 100.0 * self.cells_affected / self.cells_total if self.cells_total else 0.0

    def describe(self) -> str:
        if self.mode == "off":
            return ("SUPPRESSION OFF - this raster has not been screened for "
                    "sensitive habitat and must not be published.")
        if self.n_sites == 0:
            return ("SUPPRESSION RAN WITH AN EMPTY SENSITIVE SET - the pipeline "
                    "is wired in and did nothing, because no sensitive sites "
                    "were supplied. This raster is NOT screened. Populate the "
                    "sensitive set (Naturbase NiN naturtyper, red-listed "
                    "occurrences) before publishing.")
        return (f"suppression `{self.mode}` applied around {self.n_sites} "
                f"sensitive site(s) at {self.radius_m:.0f} m: "
                f"{self.cells_affected:,} of {self.cells_total:,} cells "
                f"affected ({self.pct:.2f}%).")


def _distance_mask(shape: tuple[int, int], transform, sites: np.ndarray,
                   radius_m: float) -> np.ndarray:
    """Boolean mask of cells within `radius_m` of any site.

    Only the window around each site is touched, so cost scales with the number
    of sites and the radius rather than with the size of the raster. A county
    at 16 m is tens of millions of cells and a full distance transform over it
    would be wasteful when the sensitive set is a few hundred points.
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    if sites.size == 0 or radius_m <= 0:
        return mask

    cell_x = abs(transform.a)
    cell_y = abs(transform.e)
    inv = ~transform
    rx = int(np.ceil(radius_m / cell_x))
    ry = int(np.ceil(radius_m / cell_y))

    for east, north in sites:
        fc = inv.a * east + inv.b * north + inv.c
        fr = inv.d * east + inv.e * north + inv.f
        c0, r0 = int(np.floor(fc)), int(np.floor(fr))
        rlo, rhi = max(0, r0 - ry), min(h, r0 + ry + 1)
        clo, chi = max(0, c0 - rx), min(w, c0 + rx + 1)
        if rlo >= rhi or clo >= chi:
            continue
        rows = (np.arange(rlo, rhi)[:, None] + 0.5 - fr) * cell_y
        cols = (np.arange(clo, chi)[None, :] + 0.5 - fc) * cell_x
        mask[rlo:rhi, clo:chi] |= (rows ** 2 + cols ** 2) <= radius_m ** 2
    return mask


def _box_blur(arr: np.ndarray, radius_cells: int) -> np.ndarray:
    """Mean over a square window, ignoring NaN, via summed-area tables.

    NaN-aware because the suitability raster is NaN outside the boundary and on
    cells the feature stack could not answer for. A blur that let NaN bleed
    would eat a ring out of the map at every edge.
    """
    if radius_cells < 1:
        return arr.copy()
    valid = np.isfinite(arr)
    filled = np.where(valid, arr, 0.0)

    def windowed(a):
        pad = np.pad(a, radius_cells, mode="constant", constant_values=0.0)
        cum = pad.cumsum(axis=0).cumsum(axis=1)
        cum = np.pad(cum, ((1, 0), (1, 0)), mode="constant", constant_values=0.0)
        k = 2 * radius_cells + 1
        h, w = a.shape
        return (cum[k:k + h, k:k + w] - cum[0:h, k:k + w]
                - cum[k:k + h, 0:w] + cum[0:h, 0:w])

    total = windowed(filled)
    count = windowed(valid.astype("float64"))
    out = np.full(arr.shape, np.nan)
    np.divide(total, count, out=out, where=count > 0)
    return out


def _polygon_mask(shape: tuple[int, int], transform, polygons, radius_m: float
                  ) -> np.ndarray:
    """Boolean mask of cells inside any polygon, dilated by radius_m.

    The dilation is a real geometric buffer applied before rasterising, not a
    raster dilation afterwards. That keeps the suppressed region a true
    distance from the habitat boundary regardless of cell size, and it uses
    only shapely, which is already a declared dependency.
    """
    h, w = shape
    mask = np.zeros((h, w), dtype=bool)
    if not polygons:
        return mask

    from rasterio.features import rasterize
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    geoms = []
    for ring in polygons:
        if len(ring) < 3:
            continue
        try:
            poly = Polygon(ring)
            if not poly.is_valid:
                poly = poly.buffer(0)      # self-intersecting survey polygons
            if poly.is_empty:
                continue
            geoms.append(poly.buffer(radius_m) if radius_m > 0 else poly)
        except Exception:
            # One malformed survey polygon must not take the whole safety layer
            # down. It is dropped, and the caller's report counts what was used.
            continue
    if not geoms:
        return mask

    merged = unary_union(geoms)
    shapes = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
    burned = rasterize([(g, 1) for g in shapes], out_shape=(h, w),
                       transform=transform, fill=0, dtype="uint8")
    return burned.astype(bool)


def suppress(values: np.ndarray, transform, sites=(), polygons=None,
             radius_m: float = 500.0, mode: str = "blur",
             blur_radius_m: float | None = None
             ) -> tuple[np.ndarray, SuppressionReport]:
    """Blur or mask suitability cells near sensitive sites.

    `sites` is an iterable of (easting, northing) in the raster's CRS, and
    `polygons` an iterable of coordinate rings in the same CRS - typically the
    sensitive naturtype localities from `src/ingest/naturbase.py`. Both are
    treated the same way: everything within `radius_m` of either is suppressed.
    No reprojection happens here, per CLAUDE.md's reproject-on-ingest rule.

    Returns the treated array and a report. The array is a copy; the input is
    never modified in place, so a caller cannot accidentally publish the
    suppressed and unsuppressed versions of the same object.
    """
    if mode not in ("blur", "mask", "off"):
        raise ValueError(f"unknown suppression mode {mode!r}")

    arr = np.asarray(values, dtype="float64")
    sites_arr = np.asarray(list(sites), dtype="float64").reshape(-1, 2)

    n_sites = len(sites_arr) + len(polygons or [])
    if mode == "off":
        return arr.copy(), SuppressionReport("off", radius_m, n_sites, 0, arr.size)

    mask = _distance_mask(arr.shape, transform, sites_arr, radius_m)
    mask |= _polygon_mask(arr.shape, transform, polygons or [], radius_m)
    affected = int(mask.sum())
    out = arr.copy()

    if affected:
        if mode == "mask":
            out[mask] = np.nan
        else:
            cell = abs(transform.a)
            blur_m = blur_radius_m if blur_radius_m is not None else radius_m
            out_blurred = _box_blur(arr, max(1, int(round(blur_m / cell))))
            out[mask] = out_blurred[mask]

    return out, SuppressionReport(mode, radius_m, n_sites, affected, arr.size)
