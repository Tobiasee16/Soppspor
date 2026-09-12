"""NIBIO AR50 land cover - the open stand-in for FKB-AR5.

CLAUDE.md's data table asks for AR5 at 1:5,000. We cannot have it. Geonorge's
own catalogue reports FKB-AR5 as `AccessIsRestricted: true`,
`DataAccess: "Norge digitalt begrenset"`, under Geovekst terms, and that is a
licensing restriction rather than a missing technical route - there is nothing
to engineer around, and working around it would mean using data we are not
licensed for. AR50 is the same agency's generalised product at 1:50,000,
published as open data under NLOD.

**What that costs.** A factor of ten in scale. AR50's minimum mapping unit is
far larger than AR5's, so small clearings, narrow bogs and field edges vanish.
Against a 32 m model grid it is a coarse layer, and it should be read as
"roughly what kind of ground is this" rather than as a boundary.

**What it buys.** The one thing nothing else in the stack supplies. SR16 maps
productive forest only, so every non-forest cell currently reaches the model as
a single undifferentiated `treslag_missing` flag - which carries a real
permutation importance of +0.034 and could mean bog, farmland, bare rock,
water or a housing estate. AR50 splits that bucket. Bog in particular matters:
*Craterellus tubaeformis* is a moist spruce-forest species, not a mire species,
and the model has so far had no way to tell those apart.

**Two classes to watch rather than trust.** `built` and `agriculture` are
literally where people are, and recording effort follows people. A species that
gains from either is a species whose gain needs checking against the null
before it is believed.

CRS: AR50 is offered for Agder only in EPSG:4258 and **25832** - Agder really
does fall in UTM zone 32 - so this module reprojects to 25833 on ingest, which
is where CLAUDE.md says reprojection belongs. It is the same zone-32 trap the
project's CRS note warns about, arriving from a different direction.
"""
from __future__ import annotations

import pathlib

import numpy as np

# AR50 `arealtype`, verified against the delivered data on 2026-09-10 rather
# than taken from the product spec. The check that mattered: AR50 codes
# agriculture as 20, where AR5 uses 21/22/23 and puts transport at 12. Every
# polygon with arealtype 20 in the Agder delivery carries a `jordbruk` code
# (24 fulldyrka, 25 innmarksbeite) and no other arealtype does, which settles
# it. Reading 20 as "transport" - the AR5 meaning - would have mislabelled
# 5,363 agricultural polygons as roads.
GROUPS: dict[int, tuple[str, str]] = {
    10: ("built", "Bebygd"),
    20: ("agriculture", "Jordbruksareal"),
    30: ("forest", "Skog"),
    50: ("open_ground", "Åpen fastmark"),
    60: ("mire", "Myr"),
    70: ("snow_ice", "Snø og isbre"),
    81: ("freshwater", "Ferskvann"),
    82: ("sea", "Hav"),
}

# Not a class. 99 is "ikke kartlagt" - nobody mapped it - which is an absence
# of information, not a kind of ground, and it is left as nodata so the shared
# missingness indicator picks it up instead of it posing as a substrate.
NOT_MAPPED = 99
NODATA = 0

CATEGORIES = sorted(GROUPS)
LABELS = {code: name for code, (name, _) in GROUPS.items()}
NORWEGIAN = {code: no for code, (_, no) in GROUPS.items()}

SOURCE_CRS = "EPSG:25832"
TARGET_CRS = "EPSG:25833"


class AR50Error(RuntimeError):
    pass


def group_codes(arealtype_values) -> np.ndarray:
    """Map raw `arealtype` to the burn value, with unknown and 99 as nodata."""
    arr = np.asarray(arealtype_values)
    out = np.full(arr.shape, NODATA, dtype="uint8")
    for code in GROUPS:
        out[arr == code] = code
    return out


def unmapped_codes(arealtype_values) -> dict[int, int]:
    """Codes present in the delivery that the grouping does not name.

    `NOT_MAPPED` is excluded because leaving it out is a decision, not an
    oversight. Anything else appearing here means AR50 has changed and the
    grouping has fallen behind it.
    """
    arr = np.asarray(arealtype_values)
    out: dict[int, int] = {}
    for v in np.unique(arr):
        try:
            code = int(v)
        except (TypeError, ValueError):
            continue
        if code not in GROUPS and code != NOT_MAPPED:
            out[code] = int((arr == v).sum())
    return out


def rasterise(gml_path: str | pathlib.Path, reference: str | pathlib.Path,
              out_path: str | pathlib.Path) -> dict:
    """Reproject AR50 to 25833 and burn it onto the reference raster's grid.

    The grid comes from `reference` rather than being computed, so the result
    is cell-for-cell aligned with the terrain stack by construction - the same
    guarantee `src/ingest/losmasser.py` relies on, and for the same reason.
    """
    import geopandas as gpd
    import rasterio
    from rasterio.features import rasterize

    gml_path = pathlib.Path(gml_path)
    gdf = gpd.read_file(gml_path, columns=["arealtype"], engine="pyogrio")

    if gdf.crs is None:
        raise AR50Error(f"{gml_path.name} carries no CRS; refusing to guess one")
    src_epsg = gdf.crs.to_epsg()

    with rasterio.open(reference) as ref:
        profile = ref.profile.copy()
        transform, height, width = ref.transform, ref.height, ref.width
        ref_epsg = ref.crs.to_epsg()

    # Reprojection happens HERE, on ingest, and nowhere else. See CLAUDE.md.
    reprojected = False
    if src_epsg != ref_epsg:
        gdf = gdf.to_crs(epsg=ref_epsg)
        reprojected = True

    values = gdf["arealtype"].to_numpy()
    unmapped = unmapped_codes(values)
    burn = group_codes(values)
    not_mapped = int((values == NOT_MAPPED).sum())

    # Largest first, so a smaller and more specific polygon is burned last and
    # wins where two overlap - rasterize keeps the last value written.
    order = np.argsort(-gdf.geometry.area.to_numpy())
    shapes = [(gdf.geometry.iloc[i], int(burn[i]))
              for i in order if burn[i] != NODATA]

    burned = rasterize(shapes, out_shape=(height, width), transform=transform,
                       fill=NODATA, dtype="uint8", all_touched=False)

    profile.update(count=1, dtype="uint8", nodata=NODATA, compress="deflate")
    profile.pop("photometric", None)
    with rasterio.open(out_path, "w", **profile) as dst:
        dst.write(burned, 1)
        dst.set_band_description(1, "ar50_arealtype")

    covered = int((burned != NODATA).sum())
    return {
        "polygons": len(gdf),
        "polygons_burned": len(shapes),
        "not_mapped_polygons": not_mapped,
        "source_crs": f"EPSG:{src_epsg}",
        "target_crs": f"EPSG:{ref_epsg}",
        "reprojected": reprojected,
        "cells": int(burned.size),
        "cells_covered": covered,
        "coverage_pct": 100.0 * covered / burned.size if burned.size else 0.0,
        "unmapped_arealtype": unmapped,
        "cells_by_group": {LABELS[c]: int((burned == c).sum()) for c in CATEGORIES},
        "path": str(out_path),
    }
