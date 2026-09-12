"""Turn a fitted model into a suitability raster over a grid.

Phase 0 artefact 3. Two things here are not incidental:

**The grid is sampled through exactly the same `sample_layers` the training
points went through.** A prediction path that read the rasters its own way
would be free to disagree with training about half-cell offsets, nodata, the
one-hot column order, or the encoding of SR16 absence, and the resulting map
would look plausible while being wrong. The feature *names* are checked against
the ones the model was fitted on, and a mismatch raises.

**Terrain is resampled from the 32 m stack rather than recomputed at 16 m.**
The map is 16 m because SR16 and the substrate layer carry real detail at that
scale. Terrain does not: TWI at 16 m is a different variable from TWI at 32 m -
different flow accumulation, different contributing areas - and feeding a model
a feature computed at a resolution it was never fitted on is not prediction, it
is extrapolation with the same column name. So the 16 m grid samples the 32 m
terrain nearest-neighbour, which reproduces the training distribution exactly.
"""
from __future__ import annotations

import numpy as np

from src.features.stack import sample_layers


def grid_coords(transform, height: int, width: int, row_slice: slice
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cell-centre eastings and northings for a block of rows."""
    rows = np.arange(row_slice.start, row_slice.stop)
    cols = np.arange(width)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    fr = rr + 0.5
    fc = cc + 0.5
    east = transform.c + transform.a * fc + transform.b * fr
    north = transform.f + transform.d * fc + transform.e * fr
    return east, north, rr


def predict_grid(model_predict, layers, feature_names, transform, height, width,
                 inside=None, inside_mask=None, rows_per_block: int = 256,
                 progress=None) -> np.ndarray:
    """Score every cell of a grid, in row blocks.

    `model_predict` takes a feature matrix and returns scores - the callable
    `Model.fit()` hands back, so the model is fitted once by the caller rather
    than refitted per block.

    Cells outside the study area are left NaN and never scored - Kristiansand
    at 16 m is 11.4M cells and most of its bounding box is sea or another
    kommune, so skipping them is most of the saving. Give either `inside_mask`,
    a boolean array the shape of the grid, or `inside`, a callable taking
    (east, north) arrays. Prefer the mask: a boundary with a real coastline has
    thousands of vertices and a per-cell callable dominates the run.
    """
    if inside_mask is not None and inside is not None:
        raise ValueError("give inside_mask or inside, not both")
    out = np.full((height, width), np.nan, dtype="float32")
    for start in range(0, height, rows_per_block):
        stop = min(start + rows_per_block, height)
        east, north, _ = grid_coords(transform, height, width, slice(start, stop))
        flat_e, flat_n = east.ravel(), north.ravel()

        if inside_mask is not None:
            keep = np.asarray(inside_mask[start:stop, :], dtype=bool).ravel()
        elif inside is not None:
            keep = np.asarray(inside(flat_e, flat_n), dtype=bool)
        else:
            keep = np.ones(flat_e.size, dtype=bool)
        if not keep.any():
            if progress:
                progress(stop, height)
            continue

        coords = list(zip(flat_e[keep], flat_n[keep]))
        X, names = sample_layers(layers, coords)
        if list(names) != list(feature_names):
            raise ValueError(
                "the prediction stack does not match the fitted feature set.\n"
                f"  fitted:    {list(feature_names)}\n"
                f"  predicting:{list(names)}"
            )

        # Rows the feature stack could not answer for stay NaN rather than
        # being imputed - the same rule `build` applies to training points.
        good = np.isfinite(X).all(axis=1)
        scores = np.full(len(coords), np.nan)
        if good.any():
            scores[good] = np.asarray(model_predict(X[good].tolist()), dtype="float64")

        block = np.full(flat_e.size, np.nan)
        block[keep] = scores
        out[start:stop, :] = block.reshape(stop - start, width).astype("float32")
        if progress:
            progress(stop, height)
    return out


def snap_grid(bbox, cell_m: float, origin: tuple[float, float]):
    """A grid covering `bbox`, aligned to `origin` at `cell_m` spacing.

    Aligning to the SR16 origin rather than to the bounding box means the 16 m
    output cells coincide exactly with SR16 cells instead of straddling them,
    so no forest value is smeared across two output cells by a half-cell
    offset.
    """
    from rasterio.transform import from_origin

    minx, miny, maxx, maxy = bbox
    ox, oy = origin
    left = ox + np.floor((minx - ox) / cell_m) * cell_m
    top = oy - np.floor((oy - maxy) / cell_m) * cell_m
    width = int(np.ceil((maxx - left) / cell_m))
    height = int(np.ceil((top - miny) / cell_m))
    return from_origin(left, top, cell_m, cell_m), height, width
