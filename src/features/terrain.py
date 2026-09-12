"""Terrain derivatives: slope, aspect, curvature and the topographic wetness index.

CLAUDE.md names TWI as the derivative expected to matter most - *Craterellus
tubaeformis* is predicted to sit in damp hollows, and TWI is how a raster says
"damp hollow". It is also the only feature here that cannot be computed from
point lookups, because it depends on the whole upslope catchment.

Implemented in numpy rather than by adding richdem or whitebox. CLAUDE.md
deliberately leaves that choice open ("neither is needed yet"), and the pieces
are small enough to test directly against surfaces whose correct answer is
known analytically - a tilted plane has a slope we can write down, a cone has
an aspect that rotates, a single channel has a flow accumulation we can count.
That is a better guarantee than an untested dependency. If a future step needs
D-infinity routing or stream burning, swapping in a library is the right move.

TWI = ln(a / tan(beta)), where `a` is upslope contributing area per unit
contour width and `beta` is the local slope. High where a lot of ground drains
into a flat place.
"""
from __future__ import annotations

import numpy as np

# D8 neighbour offsets: (row, col) and the distance multiplier for diagonals.
_D8 = [(-1, -1), (-1, 0), (-1, 1),
       (0, -1),           (0, 1),
       (1, -1), (1, 0), (1, 1)]
_D8_DIST = [np.sqrt(2), 1.0, np.sqrt(2),
            1.0, 1.0,
            np.sqrt(2), 1.0, np.sqrt(2)]


def _neighbours(dem: np.ndarray):
    """The nine 3x3 window offsets, with missing neighbours filled from the centre.

    A NaN neighbour must not poison the cell being computed. Horn's kernel sums
    all eight neighbours, so a plain implementation gives every land cell that
    touches masked water a NaN slope - a one-cell halo around every coastline.
    Against Agder's coastline that silently deleted 28 of 904 thinned presence
    records, and it did so unevenly by species (1.2% of tubaeformis against 6.9%
    of muscaria), which would have quietly tilted the ordering test that the
    whole design rests on.

    Substituting the centre value for a missing neighbour treats the boundary as
    locally flat in that direction, which understates slope at the very edge but
    keeps the cell. That is the right trade: a slightly flat coastal cell is a
    small error, a dropped coastal record is a missing observation.
    """
    z = dem.astype("float64")
    padded = np.pad(z, 1, mode="edge")
    centre = z
    out = {}
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            nb = padded[1 + dr:1 + dr + z.shape[0], 1 + dc:1 + dc + z.shape[1]]
            out[(dr, dc)] = np.where(np.isnan(nb), centre, nb)
    return out


def slope_aspect(dem: np.ndarray, cell_m: float) -> tuple[np.ndarray, np.ndarray]:
    """Slope (radians) and aspect (radians clockwise from north) by Horn's method.

    Horn's 3x3 kernel rather than a simple difference: it is the standard used
    by GDAL and ArcGIS, so our numbers are comparable to anything else computed
    from the same DEM. Edges are handled by edge-replication, which makes the
    outermost ring flatter than truth.

    NaN neighbours are replaced by the centre value rather than propagated - see
    `_neighbours`. Cells that are themselves NaN stay NaN.
    """
    n = _neighbours(dem)
    dzdx = ((n[(-1, 1)] + 2 * n[(0, 1)] + n[(1, 1)])
            - (n[(-1, -1)] + 2 * n[(0, -1)] + n[(1, -1)])) / (8 * cell_m)
    dzdy = ((n[(1, -1)] + 2 * n[(1, 0)] + n[(1, 1)])
            - (n[(-1, -1)] + 2 * n[(-1, 0)] + n[(-1, 1)])) / (8 * cell_m)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    # Aspect is the DOWNSLOPE direction, clockwise from north, as a compass
    # reads and as GDAL reports it. `dzdy` here is the gradient along
    # increasing row, which points south, so the downslope vector is
    # (-dzdx east, +dzdy north) and the compass bearing is atan2(east, north).
    # Passing these in the other order gives an angle counter-clockwise from
    # east instead, which is wrong by a reflection and is not obvious in a map.
    aspect = np.arctan2(-dzdx, dzdy)
    aspect = np.where(aspect < 0, aspect + 2 * np.pi, aspect)
    missing = np.isnan(dem)
    slope[missing] = np.nan
    aspect[missing] = np.nan
    return slope, aspect


def northness(aspect: np.ndarray, slope: np.ndarray) -> np.ndarray:
    """cos(aspect) weighted by slope. Circular aspect is useless to a model raw.

    Aspect is an angle: 359 degrees and 1 degree are neighbours, but a model
    handed those numbers sees opposite extremes. Decomposing into northness and
    eastness removes the wrap. Weighting by slope matters too - aspect on flat
    ground is arbitrary and should not vote.
    """
    return np.cos(aspect) * np.sin(slope)


def eastness(aspect: np.ndarray, slope: np.ndarray) -> np.ndarray:
    return np.sin(aspect) * np.sin(slope)


def curvature(dem: np.ndarray, cell_m: float) -> np.ndarray:
    """Laplacian curvature. Negative in hollows, positive on ridges.

    NaN neighbours are filled from the centre, as in `slope_aspect`, so the
    coastal ring survives instead of becoming a halo of missing data.
    """
    n = _neighbours(dem)
    centre = dem.astype("float64")
    d2x = (n[(0, 1)] - 2 * centre + n[(0, -1)]) / (cell_m ** 2)
    d2y = (n[(1, 0)] - 2 * centre + n[(-1, 0)]) / (cell_m ** 2)
    out = d2x + d2y
    out[np.isnan(dem)] = np.nan
    return out


def fill_depressions(dem: np.ndarray, epsilon: float = 1e-3) -> np.ndarray:
    """Depression filling by priority-flood (Barnes, Lehman & Mulla 2014).

    Flow routing needs every cell to have a downhill neighbour. Real DEMs are
    full of pits - some genuine, most interpolation artefacts - and a pit
    swallows all the flow reaching it, leaving a dry channel downstream and one
    spuriously wet cell at the bottom.

    The algorithm floods inward from the edges, always expanding from the
    lowest cell seen so far, so each cell is finalised exactly once at the
    lowest elevation water could reach it from outside. `epsilon` adds a slight
    tilt across filled flats so D8 has a direction to choose in them; without
    it flats route arbitrarily and stripe the accumulation.

    Chosen after measuring two alternatives on real terrain. A pure-numpy
    relaxation propagated one cell per pass in Python and did not finish a
    500x500 tile in ten minutes. The same relaxation with a C-speed
    neighbourhood filter took 309 seconds on 2000x2000, because the pass count
    still scales with the width of the widest flat. Priority-flood is a single
    pass and does not care how wide the flats are.

    NaN cells are outside the surface: water leaves there and does not return.
    """
    import heapq

    z = dem.astype("float64")
    h, w = z.shape
    nan = np.isnan(z)
    if nan.all():
        return z.copy()

    filled = np.where(nan, np.nan, z)
    closed = nan.copy()

    zf = z.ravel()
    closed_f = closed.ravel()
    filled_f = filled.ravel()

    heap = []
    # Seeds: the raster border, and every real cell adjacent to a NaN. Both are
    # places water can leave the surface.
    seeds = np.zeros((h, w), dtype=bool)
    seeds[0, :] = seeds[-1, :] = True
    seeds[:, 0] = seeds[:, -1] = True
    if nan.any():
        padded = np.pad(nan, 1, constant_values=False)
        touch = np.zeros((h, w), dtype=bool)
        for dr, dc in _D8:
            touch |= padded[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
        seeds |= touch
    seeds &= ~nan

    for idx in np.flatnonzero(seeds.ravel()):
        heapq.heappush(heap, (float(zf[idx]), int(idx)))
        closed_f[idx] = True

    offsets = [dr * w + dc for dr, dc in _D8]
    push = heapq.heappush
    pop = heapq.heappop

    while heap:
        zc, idx = pop(heap)
        r, c = divmod(idx, w)
        for (dr, dc), off in zip(_D8, offsets):
            rr = r + dr
            cc = c + dc
            if rr < 0 or rr >= h or cc < 0 or cc >= w:
                continue
            j = idx + off
            if closed_f[j]:
                continue
            closed_f[j] = True
            zj = zf[j]
            nz = zj if zj > zc + epsilon else zc + epsilon
            filled_f[j] = nz
            push(heap, (nz, int(j)))

    filled[nan] = np.nan
    return filled


def flow_accumulation(filled: np.ndarray, cell_m: float) -> np.ndarray:
    """D8 upslope cell count, including the cell itself.

    Cells are processed from high to low, so every cell's own contribution has
    already arrived before it passes water on. That ordering is what makes a
    single pass correct; it is the standard trick and it removes any need to
    iterate to convergence.
    """
    h, w = filled.shape
    nan = np.isnan(filled)
    acc = np.ones((h, w), dtype="float64")
    acc[nan] = np.nan

    z = np.where(nan, -np.inf, filled)
    # Steepest-descent neighbour for every cell, computed vectorised.
    best_drop = np.zeros((h, w), dtype="float64")
    best_dir = np.full((h, w), -1, dtype="int8")
    for k, ((dr, dc), dist) in enumerate(zip(_D8, _D8_DIST)):
        nb = np.full((h, w), -np.inf, dtype="float64")
        r0, r1 = max(0, -dr), h - max(0, dr)
        c0, c1 = max(0, -dc), w - max(0, dc)
        nb[r0:r1, c0:c1] = z[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        with np.errstate(invalid="ignore"):
            drop = (z - nb) / (dist * cell_m)
        better = (drop > best_drop) & np.isfinite(nb)
        best_drop = np.where(better, drop, best_drop)
        best_dir = np.where(better, k, best_dir)

    order = np.argsort(z, axis=None)[::-1]           # high to low
    rows, cols = np.unravel_index(order, (h, w))
    bd = best_dir.ravel()
    accf = acc.ravel()
    zf = z.ravel()

    for idx, r, c in zip(order, rows, cols):
        if zf[idx] == -np.inf:
            continue
        k = bd[idx]
        if k < 0:
            continue                                  # no downhill neighbour: an outlet
        dr, dc = _D8[k]
        rr, cc = r + dr, c + dc
        if 0 <= rr < h and 0 <= cc < w:
            j = rr * w + cc
            if zf[j] != -np.inf:
                accf[j] += accf[idx]
    return acc


def twi(
    dem: np.ndarray,
    cell_m: float,
    min_slope_rad: float = 0.001,
    fill: bool = True,
) -> np.ndarray:
    """Topographic wetness index, ln(a / tan(beta)).

    `min_slope_rad` floors the slope so perfectly flat cells do not divide by
    zero and return infinity. 0.001 rad is about 0.06 degrees - below anything
    a 16 m DEM can resolve, so it changes only cells that were already
    unresolvable.
    """
    filled = fill_depressions(dem) if fill else dem.astype("float64")
    acc = flow_accumulation(filled, cell_m)
    slope, _ = slope_aspect(filled, cell_m)
    tan_beta = np.tan(np.maximum(slope, min_slope_rad))
    specific_area = acc * cell_m          # A/L with A = acc * cell^2, L = cell
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.log(specific_area / tan_beta)
    out[np.isnan(dem)] = np.nan
    return out


def sea_mask(dem: np.ndarray, max_elev_m: float = 0.5) -> np.ndarray:
    """Cells that are sea: near zero elevation AND connected to the map edge.

    Necessary because the Kartverket administrative polygon for a fylke extends
    to the territorial baseline, not the coastline. Clipping Agder's DTM to that
    polygon leaves 6,576 km2 of sea inside the mask - measured, not estimated:
    the remainder after removing it is 16,481 km2 against a true land area of
    16,434 km2.

    Left in, the sea becomes one enormous flat basin. Every cell in it has a
    huge upslope area and no slope, so TWI reads as maximal: median 11.07 there
    against 6.19 on real terrain, with 31.8% of cells above TWI 15 against 1.6%
    on land. A suitability map built on that would show the sea as the wettest
    habitat in the county.

    Elevation alone is not enough to identify it - a coastal meadow is also
    near zero - so connectivity to the raster edge is required as well. A lake
    at sea level in the interior is therefore kept, correctly, as land.
    """
    from collections import deque

    z = dem.astype("float64")
    h, w = z.shape
    candidate = np.isfinite(z) & (z < max_elev_m)
    sea = np.zeros((h, w), dtype=bool)
    if not candidate.any():
        return sea

    q = deque()
    cand = candidate
    for c in range(w):
        for r in (0, h - 1):
            if cand[r, c] and not sea[r, c]:
                sea[r, c] = True
                q.append((r, c))
    for r in range(h):
        for c in (0, w - 1):
            if cand[r, c] and not sea[r, c]:
                sea[r, c] = True
                q.append((r, c))

    while q:
        r, c = q.popleft()
        for dr, dc in _D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and cand[rr, cc] and not sea[rr, cc]:
                sea[rr, cc] = True
                q.append((rr, cc))
    return sea
