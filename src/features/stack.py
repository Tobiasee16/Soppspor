"""Assemble the model-ready feature matrix by sampling rasters at points.

Everything in this project is EPSG:25833 by the time it reaches here, so this
module does no reprojection at all - deliberately. CLAUDE.md says reproject on
ingest, never mid-pipeline, and a silent reprojection here would be the easiest
place in the codebase to introduce a shift nobody notices: the points would
still land on the raster and still return numbers.

Two sampling methods, chosen per layer rather than globally:

* **nearest** for the 32 m terrain bands and for anything categorical. A slope
  is already a local average over its cell; interpolating between cells invents
  a surface the DTM does not have. And interpolating a category code is
  meaningless - halfway between "bare rock" and "peat" is not a substrate.
* **bilinear** for the 1 km climate raster. Sampled nearest, a 1 km field turns
  into visible 1 km steps, and neighbouring presence and background points a
  few hundred metres apart get identical values for no physical reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import rasterio


@dataclass
class RasterLayer:
    """One raster and the bands to take from it.

    `missing_as` and `categories` exist for SR16, where absence is meaningful
    rather than accidental. SR16 maps productive forest; a cell with no value
    is water, farmland, bog, bare rock or ground above the forest line. That is
    a fact about the place, not a measurement failure, and it applies to 19% of
    the target-group pool.

    Dropping those rows would discard the whole non-forest end of the gradient
    - exactly the contrast that makes "is this forest at all" learnable - and
    would silently change what the AUC is measured against. So absence is
    encoded instead: forest quantities go to zero, which is physically true of
    a lake or a mountainside, and `missing_indicator` adds a column saying the
    zero came from absence rather than from measurement. Without that column
    the model could not tell an unforested cell from a measured stand of no
    volume, and the encoding really would be inventing data.
    """
    path: str
    bands: list[int] | None = None          # 1-based, as rasterio counts
    names: list[str] | None = None
    method: str = "nearest"
    prefix: str = ""
    missing_as: float | None = None
    missing_indicator: bool = False
    categories: list[int] | None = None     # expand to one-hot; None = numeric
    # Human-readable name per category, in the same order. Without this a
    # ten-class substrate layer reports as `losmasse_1` ... `losmasse_10`, and
    # the permutation-importance table - the main diagnostic in this project -
    # becomes a lookup exercise. Naming is not cosmetic when the output is
    # meant to be read by someone deciding whether to believe a model.
    category_labels: list[str] | None = None

    def __post_init__(self):
        if self.method not in ("nearest", "bilinear"):
            raise ValueError(f"unknown sampling method {self.method!r}")
        if self.categories is not None and self.method == "bilinear":
            # Halfway between spruce and pine is not a tree species.
            raise ValueError("a categorical layer cannot be interpolated")
        if self.missing_indicator and self.missing_as is None:
            raise ValueError("missing_indicator needs missing_as to be set")
        if self.category_labels is not None:
            if self.categories is None:
                raise ValueError("category_labels needs categories to be set")
            if len(self.category_labels) != len(self.categories):
                raise ValueError(
                    f"{len(self.category_labels)} labels for "
                    f"{len(self.categories)} categories in {self.path}")

    def suffixes(self) -> list[str]:
        """What each one-hot column is called after the band name."""
        if self.categories is None:
            return []
        if self.category_labels is not None:
            return list(self.category_labels)
        return [str(c) for c in self.categories]

    def resolve(self) -> tuple[list[int], list[str]]:
        with rasterio.open(self.path) as ds:
            bands = self.bands or list(range(1, ds.count + 1))
            if self.names:
                names = list(self.names)
            else:
                names = [ds.descriptions[b - 1] or f"band{b}" for b in bands]
        if len(names) != len(bands):
            raise ValueError(f"{len(names)} names for {len(bands)} bands in {self.path}")
        return bands, [f"{self.prefix}{n}" for n in names]


def _read_float(ds, band: int) -> np.ndarray:
    """A band as float64 with nodata as NaN.

    The cast to float has to happen BEFORE the fill. Filling first raises on
    an integer raster - NaN is not an int16 - and SR16 ships every forest
    variable as int16 with nodata -9999. Getting this the wrong way round
    fails loudly, which is the good case; the bad case would have been a
    silent -9999 travelling into the model as a very negative tree volume.
    """
    return ds.read(band, masked=True).astype("float64").filled(np.nan)


def _pixel_coords(ds, coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fractional (column, row) for each point.

    The inverse affine is applied directly rather than through `ds.index`,
    which is not vectorised in every rasterio version and would otherwise mean
    a Python-level loop over several million cells when the prediction map is
    built.
    """
    inv = ~ds.transform
    x, y = coords[:, 0], coords[:, 1]
    fc = inv.a * x + inv.b * y + inv.c
    fr = inv.d * x + inv.e * y + inv.f
    return fc, fr


def _nearest(ds, band: int, coords: np.ndarray) -> np.ndarray:
    fc, fr = _pixel_coords(ds, coords)
    cols = np.floor(fc).astype(int)
    rows = np.floor(fr).astype(int)
    arr = _read_float(ds, band)
    out = np.full(len(coords), np.nan)
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    out[ok] = arr[rows[ok], cols[ok]]
    return out


def _bilinear(ds, band: int, coords: np.ndarray) -> np.ndarray:
    """Bilinear interpolation between cell centres.

    Missing neighbours are not treated as zero and do not poison the result:
    the weights of whichever neighbours exist are renormalised, so a point at
    the edge of coverage still gets a value from the data that is there. A
    point with no finite neighbour at all stays NaN, which is the honest
    answer.
    """
    arr = _read_float(ds, band)
    # Cell centres sit at (col + 0.5, row + 0.5) in pixel space, so shifting by
    # half a cell puts the interpolation lattice on the centres where the
    # values actually live. Omitting this shift is the classic half-cell error:
    # every sampled value comes back subtly wrong and nothing looks broken.
    fc, fr = _pixel_coords(ds, coords)
    fc = fc - 0.5
    fr = fr - 0.5
    c0 = np.floor(fc).astype(int)
    r0 = np.floor(fr).astype(int)
    dc = fc - c0
    dr = fr - r0

    total = np.zeros(len(coords))
    weight = np.zeros(len(coords))
    for drow, wr in ((0, 1 - dr), (1, dr)):
        for dcol, wc in ((0, 1 - dc), (1, dc)):
            rr, cc = r0 + drow, c0 + dcol
            inside = (rr >= 0) & (rr < arr.shape[0]) & (cc >= 0) & (cc < arr.shape[1])
            vals = np.full(len(coords), np.nan)
            vals[inside] = arr[rr[inside], cc[inside]]
            w = wr * wc
            good = inside & np.isfinite(vals)
            total[good] += vals[good] * w[good]
            weight[good] += w[good]
    out = np.full(len(coords), np.nan)
    live = weight > 1e-12
    out[live] = total[live] / weight[live]
    return out


def sample_layers(layers: list[RasterLayer],
                  coords: list[tuple[float, float]]) -> tuple[np.ndarray, list[str]]:
    """Sample every band of every layer at every point. Shape (n_points, n_features)."""
    pts = np.asarray(coords, dtype="float64").reshape(-1, 2)
    cols, names = [], []
    for layer in layers:
        bands, band_names = layer.resolve()
        with rasterio.open(layer.path) as ds:
            take = _bilinear if layer.method == "bilinear" else _nearest
            raw = [take(ds, band, pts) for band in bands]

        if layer.categories is not None:
            # One-hot, with an explicit column for absence when asked for.
            # A category code fed to a linear model as a number would assert
            # that pine sits midway between spruce and birch, which is not a
            # statement anyone intends to make.
            suffixes = layer.suffixes()
            for value, name in zip(raw, band_names):
                for cat, suffix in zip(layer.categories, suffixes):
                    cols.append((value == cat).astype("float64"))
                    names.append(f"{name}_{suffix}")
                if layer.missing_indicator:
                    cols.append(np.isnan(value).astype("float64"))
                    names.append(f"{name}_missing")
            continue

        for value, name in zip(raw, band_names):
            if layer.missing_as is not None:
                if layer.missing_indicator:
                    cols.append(np.isnan(value).astype("float64"))
                    names.append(f"{name}_missing")
                value = np.where(np.isnan(value), layer.missing_as, value)
            cols.append(value)
            names.append(name)

    if not cols:
        return np.empty((len(pts), 0)), []
    return np.column_stack(cols), names


@dataclass
class Design:
    """A design matrix and everything needed to interpret it honestly."""
    X: np.ndarray
    y: np.ndarray
    coords: np.ndarray
    names: list[str]
    dropped: dict = field(default_factory=dict)

    @property
    def n_presence(self) -> int:
        return int((self.y == 1).sum())

    @property
    def n_background(self) -> int:
        return int((self.y == 0).sum())


def build(presence_coords, background_coords, layers: list[RasterLayer]) -> Design:
    """Sample both point sets and drop rows that any layer could not answer for.

    Rows with a missing feature are dropped rather than imputed. Imputation
    here would be inventing environmental data at a place we could not measure,
    which CLAUDE.md forbids outright; a mean-filled row also looks exactly like
    a real average site to the model.

    What is dropped is counted per class and returned, never just discarded.
    Uneven dropping between presences and background changes what the AUC is
    measured against, and it does so invisibly.
    """
    pres = np.asarray(presence_coords, dtype="float64").reshape(-1, 2)
    back = np.asarray(background_coords, dtype="float64").reshape(-1, 2)
    coords = np.vstack([pres, back]) if len(pres) or len(back) else np.empty((0, 2))
    y = np.concatenate([np.ones(len(pres), dtype=int), np.zeros(len(back), dtype=int)])

    X, names = sample_layers(layers, [tuple(c) for c in coords])
    good = np.isfinite(X).all(axis=1) if X.size else np.ones(len(coords), dtype=bool)

    dropped = {
        "presence": int((~good & (y == 1)).sum()),
        "background": int((~good & (y == 0)).sum()),
        "presence_pct": 100.0 * float((~good & (y == 1)).sum()) / max(len(pres), 1),
        "background_pct": 100.0 * float((~good & (y == 0)).sum()) / max(len(back), 1),
        "by_feature": {
            name: int((~np.isfinite(X[:, i])).sum()) for i, name in enumerate(names)
        } if X.size else {},
    }
    return Design(X=X[good], y=y[good], coords=coords[good], names=names, dropped=dropped)
