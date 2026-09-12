"""Minimal planar geometry on EPSG:25833 coordinates.

Deliberately stdlib-only. Everything here operates on metres in UTM33; nothing
reprojects. Reprojection happens once, on ingest, never mid-pipeline.
"""
from __future__ import annotations


def _rings(geojson_geom: dict) -> list[list[tuple[float, float]]]:
    """Flatten a (Multi)Polygon into a list of rings (outer and inner alike)."""
    t = geojson_geom["type"]
    coords = geojson_geom["coordinates"]
    if t == "Polygon":
        polys = [coords]
    elif t == "MultiPolygon":
        polys = coords
    else:
        raise ValueError(f"unsupported geometry type: {t}")
    out = []
    for poly in polys:
        for ring in poly:
            out.append([(float(x), float(y)) for x, y, *_ in ring])
    return out


class Boundary:
    """Point-in-polygon test with a bounding-box prefilter.

    Even-odd ray casting over every ring. Because holes are counted as rings,
    a point inside a hole flips an even number of times and correctly falls
    outside. Good enough for administrative boundaries; not a general geometry
    engine.
    """

    def __init__(self, geojson_geom: dict, name: str = ""):
        self.name = name
        self.rings = _rings(geojson_geom)
        xs = [p[0] for r in self.rings for p in r]
        ys = [p[1] for r in self.rings for p in r]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))

    def __contains__(self, pt: tuple[float, float]) -> bool:
        x, y = pt
        minx, miny, maxx, maxy = self.bbox
        if not (minx <= x <= maxx and miny <= y <= maxy):
            return False
        inside = False
        for ring in self.rings:
            n = len(ring)
            j = n - 1
            for i in range(n):
                xi, yi = ring[i]
                xj, yj = ring[j]
                if (yi > y) != (yj > y):
                    xint = (xj - xi) * (y - yi) / (yj - yi) + xi
                    if x < xint:
                        inside = not inside
                j = i
        return inside


def cells_inside(boundary: "Boundary", cell_m: float) -> list[tuple[float, float]]:
    """Centres of every cell_m square whose centre lies inside the boundary.

    Scanline fill rather than a point-in-polygon call per cell. Testing each
    centre individually costs O(vertices) per cell, which is minutes against a
    Kartverket boundary; computing each row's edge crossings once and filling
    the spans between them is O(vertices) per *row*.

    Even-odd, matching Boundary.__contains__, so holes stay outside.

    Used to draw uniform random points inside a region without rejection
    sampling: pick a cell, jitter within it. The region is therefore
    approximated at cell_m resolution, which only matters at the edge.
    """
    if cell_m <= 0:
        raise ValueError("cell_m must be positive")
    minx, miny, maxx, maxy = boundary.bbox
    edges = []
    for ring in boundary.rings:
        n = len(ring)
        for i in range(n):
            edges.append((ring[i - 1], ring[i]))

    out: list[tuple[float, float]] = []
    ny = int((maxy - miny) // cell_m) + 1
    nx0 = int(minx // cell_m)
    nx1 = int(maxx // cell_m)
    y0 = int(miny // cell_m)
    for row in range(ny + 1):
        y = (y0 + row) * cell_m + cell_m / 2.0
        if not (miny <= y <= maxy):
            continue
        xs = []
        for (xi, yi), (xj, yj) in edges:
            if (yi > y) != (yj > y):
                xs.append((xj - xi) * (y - yi) / (yj - yi) + xi)
        if len(xs) < 2:
            continue
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            a, b = xs[k], xs[k + 1]
            ca = max(nx0, int(a // cell_m))
            cb = min(nx1, int(b // cell_m) + 1)
            for col in range(ca, cb + 1):
                x = col * cell_m + cell_m / 2.0
                if a <= x <= b:
                    out.append((x, y))
    return out
