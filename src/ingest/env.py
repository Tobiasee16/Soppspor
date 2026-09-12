"""Point queries against the Norwegian environmental data services.

Every endpoint here was verified live on 2026-09-09 at a real occurrence
record - a traktkantarell at (93674.87, 6466470.01) in Kristiansand, which the
elevation service confirms is forest at 38 m. Resolved URLs and access dates
are in data/SOURCES.md. Re-verify with `python scripts/verify_env_sources.py`
before trusting a run; these are other people's services and they change.

VERIFIED BEHAVIOUR - READ BEFORE CHANGING:

* **WMS axis order.** In WMS 1.3.0 with EPSG:25833 the BBOX is easting-first.
  Getting this backwards does not error - it queries a point in the sea off
  Sweden and returns "Search returned no results", which reads exactly like a
  location with no data. We use WMS 1.1.1 throughout, where the axis order is
  unambiguously x,y.

* **INFO_FORMAT must be `application/vnd.ogc.gml`.** `text/plain` is accepted
  and returns a feature ID with *no attributes at all* - it looks like a
  successful query that found an empty feature. Only GML carries the values.

* **SR16: query the vector layer `SRVVOLMB`, not the raster layers.** The
  raster layers (SRRTRESLAG, SRRBONITET, SRRHOYDEO ...) return no attributes
  through GetFeatureInfo. SRVVOLMB returns the entire SR16 stack in one
  request, including per-variable uncertainty bounds, which is what CLAUDE.md
  asks us to propagate rather than hide.

* **NGU: `Losmasse_flate` is queryable, `Losmasse_Norge` is a group layer and
  `Losmasseflate_detaljkart_full` returns nothing.**

* **seNorge is served over OPeNDAP, not NCSS** (the NetcdfSubset service
  returned HTTP 503). Square brackets in the constraint expression must be
  percent-encoded or the server returns HTTP 400.

* **The seNorge Y axis descends.** X runs -74500 upward, Y runs 7999500
  *downward*, both at 1000 m. Assuming an ascending Y silently reads a cell
  roughly 1500 km away, which returns the -999.99 fill value rather than an
  error.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

TIMEOUT = 90
PAUSE_S = 0.2
UA = {"User-Agent": "soppkart/0.1 (research; contact via repo)"}
CTX = ssl.create_default_context()

HOYDE_URL = "https://ws.geonorge.no/hoydedata/v1/punkt"
SR16_WMS = "https://wms.nibio.no/cgi-bin/sr16"
AR5_WMS = "https://wms.nibio.no/cgi-bin/ar5"
NGU_WMS = "https://geo.ngu.no/mapserver/LosmasserWMS2"
NATURBASE = ("https://kart.miljodirektoratet.no/arcgis/rest/services"
             "/naturtyper_nin/MapServer/0/query")
SENORGE = ("https://thredds.met.no/thredds/dodsC/senorge/seNorge_2018/Archive"
           "/seNorge2018_{year}.nc")
NVE_GTS = "https://gts.nve.no/api/GridTimeSeries"

# seNorge grid geometry, read from the file's own coordinate arrays rather than
# assumed. X ascends, Y DESCENDS.
SENORGE_X0 = -74500.0
SENORGE_Y0 = 7999500.0
SENORGE_STEP = 1000.0
SENORGE_NX = 1195
SENORGE_NY = 1550
SENORGE_FILL = -999.99


class SourceError(RuntimeError):
    """A source did not answer as verified. Never swallowed into a default."""


def _get(url: str, retries: int = 2) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - re-raised below with context
            last = e
            if attempt < retries:
                time.sleep(1.0 + attempt)
    raise SourceError(f"{url} failed after {retries + 1} attempts: {last}")


# --- elevation ----------------------------------------------------------------


def elevation(east: float, north: float) -> dict:
    """Terrain elevation from Kartverket's 1 m DTM, as a point lookup.

    Returns the reported source and terrain class alongside the height. The
    source matters: over water the service answers from `dybdekurver` (depth
    contours) and returns a negative height, which is a real answer to a
    different question and must not reach a model as an elevation.
    """
    q = urllib.parse.urlencode({"ost": east, "nord": north, "koordsys": 25833})
    d = json.loads(_get(f"{HOYDE_URL}?{q}"))
    pts = d.get("punkter") or []
    if not pts:
        raise SourceError(f"no elevation returned for ({east}, {north})")
    p = pts[0]
    return {
        "elevation_m": p.get("z"),
        "source": p.get("datakilde"),
        "terrain": p.get("terreng"),
    }


# --- WMS GetFeatureInfo -------------------------------------------------------

_FIELD = re.compile(r"<(?!/|gml:|\?)([A-Za-z_][\w.]*)>([^<>]{1,200})</\1>")


def _wms_gml(base: str, layer: str, east: float, north: float, half_m: float = 30.0) -> dict:
    """One GetFeatureInfo query, returned as a flat dict of attributes.

    WMS 1.1.1 and GML deliberately - see the module docstring. An empty dict
    means the query succeeded and the location genuinely has no feature, which
    is different from a failure and is returned as such.
    """
    q = urllib.parse.urlencode({
        "service": "WMS", "request": "GetFeatureInfo", "version": "1.1.1",
        "layers": layer, "query_layers": layer, "srs": "EPSG:25833",
        "bbox": f"{east - half_m},{north - half_m},{east + half_m},{north + half_m}",
        "width": 101, "height": 101, "x": 50, "y": 50,
        "info_format": "application/vnd.ogc.gml", "format": "image/png",
    })
    body = _get(f"{base}?{q}").decode("utf-8", "replace")
    if "ServiceException" in body:
        raise SourceError(f"{base} layer {layer}: {body[:300]}")
    return {k: v.strip() for k, v in _FIELD.findall(body) if v.strip()}


def sr16(east: float, north: float) -> dict:
    """NIBIO SR16 forest structure, whole stack, with its uncertainty bounds.

    Each variable `x` comes with `x_s`, `x_l` and `x_u`. CLAUDE.md records that
    SR16 volume error runs 30-60% of the mean and that tree species is right in
    about two thirds of pixels, so these bounds are carried through rather than
    dropped - a suitability model built on the point estimate alone would look
    more confident than the input supports.
    """
    return _wms_gml(SR16_WMS, "SRVVOLMB", east, north)


def ar5(east: float, north: float) -> dict:
    """NIBIO AR5 land cover: area type, tree species, site index, ground conditions."""
    return _wms_gml(AR5_WMS, "Arealtype", east, north)


def losmasser(east: float, north: float) -> dict:
    """NGU superficial deposits. Calcareous versus acidic substrate matters here."""
    return _wms_gml(NGU_WMS, "Losmasse_flate", east, north)


# --- Naturbase ----------------------------------------------------------------


def naturtype(east: float, north: float) -> dict:
    """Miljodirektoratet NiN habitat polygons. Context layer, often empty.

    Most of Agder is not mapped, so an empty result is the common case and is
    returned as an empty dict rather than treated as a failure.
    """
    geom = json.dumps({"x": east, "y": north, "spatialReference": {"wkid": 25833}})
    q = urllib.parse.urlencode({
        "geometry": geom, "geometryType": "esriGeometryPoint", "inSR": 25833,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "*",
        "returnGeometry": "false", "f": "json",
    })
    d = json.loads(_get(f"{NATURBASE}?{q}"))
    if "error" in d:
        raise SourceError(f"Naturbase: {d['error']}")
    feats = d.get("features") or []
    return dict(feats[0].get("attributes", {})) if feats else {}


# --- seNorge ------------------------------------------------------------------


def senorge_index(east: float, north: float) -> tuple[int, int]:
    """Grid indices for a UTM33 coordinate. Raises rather than clamping."""
    ix = int(round((east - SENORGE_X0) / SENORGE_STEP))
    iy = int(round((SENORGE_Y0 - north) / SENORGE_STEP))
    if not (0 <= ix < SENORGE_NX and 0 <= iy < SENORGE_NY):
        raise SourceError(
            f"({east}, {north}) falls outside the seNorge grid at index ({ix}, {iy})"
        )
    return ix, iy


def senorge_series(
    east: float,
    north: float,
    year: int,
    variable: str = "rr",
    day_from: int = 0,
    day_to: int | None = None,
) -> list[float]:
    """Daily seNorge values for one grid cell.

    `variable` is one of rr (precipitation, mm), tg (mean), tn (min), tx (max
    temperature, degrees C). Days are 0-based indices into the year, so
    day-of-year minus one; the file's time axis is hours since 1900-01-01 and
    this was checked against a known date rather than assumed.

    The -999.99 fill value is converted to None. It is not a small number to be
    averaged in - it means no data, and it appears over sea.
    """
    if variable not in ("rr", "tg", "tn", "tx"):
        raise ValueError(f"unknown seNorge variable {variable!r}")
    ix, iy = senorge_index(east, north)
    hi = day_to if day_to is not None else day_from
    con = f"{variable}%5B{day_from}:1:{hi}%5D%5B{iy}:1:{iy}%5D%5B{ix}:1:{ix}%5D"
    body = _get(f"{SENORGE.format(year=year)}.ascii?{con}").decode("utf-8", "replace")
    vals = [float(v) for v in re.findall(r"\[\d+\]\[0\], (-?\d+\.?\d*)", body)]
    if not vals:
        raise SourceError(f"seNorge returned no values for {variable} at ({east}, {north})")
    return [None if abs(v - SENORGE_FILL) < 0.01 else v for v in vals]


def day_index(d: date) -> int:
    """0-based index into a seNorge year file."""
    return d.timetuple().tm_yday - 1


# --- NVE ----------------------------------------------------------------------


def nve_series(
    east: float,
    north: float,
    start: str,
    end: str,
    theme: str = "gwb_sssrel",
) -> dict:
    """NVE gridded hydrology, 1 km. Default theme is relative soil saturation.

    Themes are listed at {NVE_GTS}/Themes/json. `gwb_sssrel` is soil saturation
    as a percentage; `gwb_gwt` is groundwater state. Dates are YYYY-MM-DD.
    """
    url = f"{NVE_GTS}/{int(east)}/{int(north)}/{start}/{end}/{theme}.json"
    d = json.loads(_get(url))
    if d.get("Data") is None:
        raise SourceError(f"NVE returned no data for {theme} at ({east}, {north})")
    return {
        "theme": theme,
        "unit": d.get("Unit"),
        "data": d.get("Data"),
        "no_data_value": d.get("NoDataValue"),
    }


def nve_themes() -> list[dict]:
    return json.loads(_get(f"{NVE_GTS}/Themes/json"))
