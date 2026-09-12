"""Miljodirektoratet Naturbase NiN naturtyper - the red-list suppression source.

This layer is not a model feature and never becomes one. It exists so that
`src/features/suppress.py` has something to screen against, which CLAUDE.md
requires from the start rather than as a later policy layer:

    "The habitats richest in interesting fungi (kalkskog, old-growth) are
    disproportionately the ones holding threatened species."

That sentence is the whole argument. A habitat suitability map is *most*
accurate exactly where publishing it does most harm, because the terrain that
scores well for an interesting fungus is the terrain that shelters threatened
ones. So the better this model gets, the more this layer matters.

**Which polygons count as sensitive is a policy decision, not a technical one**,
so it is written down here in one place rather than spread through the export
code. See `SENSITIVE_RULE`.

Source: ArcGIS REST, `naturtyper_nin/MapServer/0`. Verified 2026-09-09 as a
point query (`src/ingest/env.py`); this module adds the polygon pull.
"""
from __future__ import annotations

import json
import pathlib
import urllib.parse
import urllib.request

SERVICE = ("https://kart.miljodirektoratet.no/arcgis/rest/services"
           "/naturtyper_nin/MapServer/0/query")

USER_AGENT = "soppkart/0.1 (habitat suitability research; contact via repo)"
PAGE = 500

# Attributes the suppression rule reads. Deliberately a short list: the service
# returns 40+ fields including free-text survey descriptions, and pulling those
# for thousands of polygons is rude to a public service and useless to us.
#
# Field names carrying Norwegian characters (Hovedokosystem is really
# "Hovedøkosystem") are NOT requested. ArcGIS returns an empty result rather
# than an error when an outField name does not match, which fails silently -
# a query that quietly returns nothing looks exactly like "no sensitive
# habitat here", which is the most dangerous possible failure for this layer.
FIELDS = ["OBJECTID", "NiNID", "Naturtype", "Naturtypekode", "UK_Truet",
          "Lokalitetskvalitet", "Naturmangfold", "Kommuner"]


class NaturbaseError(RuntimeError):
    pass


def _get(url: str, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    # The service declares UTF-8 and mostly means it, but survey text has been
    # seen to carry latin-1 bytes. Replace rather than raise: a mangled place
    # name must not stop a safety layer from loading.
    d = json.loads(raw.decode("utf-8", "replace"))
    if isinstance(d, dict) and "error" in d:
        raise NaturbaseError(f"Naturbase: {d['error']}")
    return d


def count(bbox: tuple[float, float, float, float]) -> int:
    """How many naturtype polygons intersect the bbox. Cheap, and a liveness check."""
    q = urllib.parse.urlencode({
        "geometry": ",".join(f"{v:.0f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope", "inSR": 25833,
        "spatialRel": "esriSpatialRelIntersects",
        "returnCountOnly": "true", "f": "json",
    })
    return int(_get(f"{SERVICE}?{q}").get("count", 0))


def fetch(bbox: tuple[float, float, float, float], page: int = PAGE) -> list[dict]:
    """Every naturtype polygon intersecting the bbox, with geometry, in EPSG:25833.

    Paged through `resultOffset`. The expected total is checked against
    `count()` afterwards by the caller - a short read here would silently
    shrink the sensitive set, and this layer failing quiet is the failure mode
    that matters.
    """
    out: list[dict] = []
    offset = 0
    while True:
        q = urllib.parse.urlencode({
            "geometry": ",".join(f"{v:.0f}" for v in bbox),
            "geometryType": "esriGeometryEnvelope", "inSR": 25833,
            "outSR": 25833, "spatialRel": "esriSpatialRelIntersects",
            "outFields": ",".join(FIELDS), "returnGeometry": "true",
            "geometryPrecision": 1,
            "resultOffset": offset, "resultRecordCount": page, "f": "json",
        })
        d = _get(f"{SERVICE}?{q}")
        feats = d.get("features") or []
        out.extend(feats)
        if len(feats) < page:
            break
        offset += len(feats)
        if offset > 200_000:
            raise NaturbaseError("paging did not terminate; refusing to loop further")
    return out


def is_sensitive(attrs: dict) -> tuple[bool, str]:
    """SENSITIVE_RULE - the policy, in one place.

    Returns (sensitive, reason). Deliberately over-inclusive: the cost of
    suppressing an ordinary patch of forest is that a map is slightly blurrier
    there, and the cost of failing to suppress a threatened one is that we
    helped someone find and trample it. Those are not symmetric, so the rule
    errs toward suppression.

    Three independent triggers, any one of which is enough:

    * `UK_Truet` - Miljodirektoratet's own flag that the locality represents a
      threatened habitat type. The single most direct signal available.
    * `Lokalitetskvalitet >= 3` - the top of a 0-4 locality-quality scale.
    * `Naturmangfold >= 2` - the top of a 0-2 biodiversity scale.

    Anything the survey did not score (`None`) is NOT treated as sensitive, and
    that is a real gap rather than a judgement: an unscored locality is one
    nobody assessed, not one assessed as ordinary. Most of Agder is not mapped
    at all, so the far larger gap is everything outside these polygons.
    """
    def num(key):
        v = attrs.get(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if num("UK_Truet") == 1:
        return True, "threatened habitat type (UK_Truet)"
    lk = num("Lokalitetskvalitet")
    if lk is not None and lk >= 3:
        return True, f"high locality quality ({lk:.0f}/4)"
    nm = num("Naturmangfold")
    if nm is not None and nm >= 2:
        return True, f"high biodiversity ({nm:.0f}/2)"
    return False, ""


def rings_to_polygons(feature: dict) -> list[list[tuple[float, float]]]:
    """ArcGIS `rings` to plain coordinate lists.

    Holes are not distinguished from outer rings. For suppression that is the
    safe direction: treating a hole as solid suppresses slightly more ground,
    never less.
    """
    geom = feature.get("geometry") or {}
    return [[(float(x), float(y)) for x, y in ring]
            for ring in geom.get("rings", []) if len(ring) >= 3]


def sensitive_polygons(features: list[dict]) -> tuple[list, dict]:
    """Split a feature list into sensitive polygon rings plus a summary."""
    polys, reasons, kept, skipped = [], {}, 0, 0
    for f in features:
        attrs = f.get("attributes", {})
        sensitive, reason = is_sensitive(attrs)
        if not sensitive:
            skipped += 1
            continue
        rings = rings_to_polygons(f)
        if not rings:
            continue
        kept += 1
        polys.extend(rings)
        reasons[reason] = reasons.get(reason, 0) + 1
    return polys, {"sensitive": kept, "not_sensitive": skipped,
                   "rings": len(polys), "reasons": reasons}


def save(features: list[dict], path: str | pathlib.Path) -> pathlib.Path:
    """Write the raw pull as GeoJSON so the export does not depend on the network."""
    fc = {"type": "FeatureCollection", "crs": {
        "type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25833"}},
        "features": []}
    for f in features:
        rings = rings_to_polygons(f)
        if not rings:
            continue
        fc["features"].append({
            "type": "Feature",
            "properties": dict(f.get("attributes", {})),
            "geometry": {"type": "Polygon", "coordinates": [
                [list(pt) for pt in ring] for ring in rings]},
        })
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fc), encoding="utf-8")
    return path


def load(path: str | pathlib.Path) -> list[dict]:
    """Read a saved pull back into the ArcGIS-shaped feature list."""
    fc = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return [{"attributes": f.get("properties", {}),
             "geometry": {"rings": [[tuple(pt) for pt in ring]
                                    for ring in f["geometry"]["coordinates"]]}}
            for f in fc.get("features", [])]
