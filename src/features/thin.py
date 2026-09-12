"""Spatial thinning of presence records.

Presence-only occurrence data clusters hard around access. Thinning to one
record per grid cell is the crudest defensible decorrelation step. It does not
replace target-group background sampling; it just stops a single well-visited
hillside from counting fifty times.

Deterministic by construction: the record retained in a cell is the one with
the lowest (precision, id), so reruns give identical sets.
"""
from __future__ import annotations


def thin(points: list[dict], cell_m: float) -> list[dict]:
    """One record per cell_m square cell on the EPSG:25833 grid.

    cell_m <= 0 returns the input unchanged (the no-thinning baseline).
    """
    if cell_m <= 0:
        return list(points)

    best: dict[tuple[int, int], dict] = {}
    for p in points:
        key = (int(p["east"] // cell_m), int(p["north"] // cell_m))
        incumbent = best.get(key)
        if incumbent is None or _rank(p) < _rank(incumbent):
            best[key] = p
    return list(best.values())


def _rank(p: dict) -> tuple[float, str]:
    prec = p.get("precision_m")
    return (float("inf") if prec is None else float(prec), str(p.get("id") or ""))
