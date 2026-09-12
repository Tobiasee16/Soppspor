"""seNorge_2018 daily grids from MET, over OPeNDAP.

Endpoint verified 2026-09-09:
    https://thredds.met.no/thredds/dodsC/senorge/seNorge_2018/Archive/seNorge2018_{year}.nc

VERIFIED STRUCTURE - READ BEFORE CHANGING:

* `rr[time][Y][X]`, Float32, units kg/m^2 (= mm), `_FillValue -999.99`,
  long_name "daily total precipitation (06UTC-06UTC)".
* X ascends from -74500 to 1119500; **Y DESCENDS** from 7999500 to 6450500.
  Both are cell centres on a 1000 m grid, 1195 x 1550. Getting the Y direction
  wrong flips the country north-south and produces a plausible-looking raster.
* time is "hours since 1900-01-01", one step per day, 365 or 366 per file.
* The southern edge of the grid is north 6450000. That is 21 km north of the
  southernmost point of the Kristiansand boundary, which sounds alarming and
  is not: those extremes are outer skerries. Measured against the terrain
  raster, 2 km2 of Agder land and **zero** Kristiansand land fall outside
  the grid. Checked rather than assumed, because a climate feature with a hole
  in the prediction extent would have been a real problem.

WHY THE BINARY .dods FORMAT AND NOT .ascii

The point queries elsewhere in this package use `.ascii`, which is readable
and fine for a handful of values. A 30-year seasonal climatology over Agder is
about 92 million values; as ASCII that is over a gigabyte of text to move and
parse. `.dods` is XDR: a DDS header, the literal bytes `Data:\\n`, then for each
array two big-endian uint32 element counts followed by big-endian elements.
That is a dozen lines to parse and roughly thirty times smaller.
"""
from __future__ import annotations

import calendar
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from dataclasses import dataclass

import numpy as np

BASE = ("https://thredds.met.no/thredds/dodsC/senorge/seNorge_2018/Archive"
        "/seNorge2018_{year}.nc")

X0, Y0, STEP = -74500.0, 7999500.0, 1000.0     # cell centres; Y descends
NX, NY = 1195, 1550
FILL = -999.99

TIMEOUT = 300
UA = {"User-Agent": "soppkart/0.1 (research; contact via repo)"}
CTX = ssl.create_default_context()


class SourceError(RuntimeError):
    """The source did not answer as verified. Never swallowed into a default."""


@dataclass(frozen=True)
class Window:
    """A half-open index window into the seNorge grid, plus its geography."""
    ix0: int
    ix1: int
    iy0: int
    iy1: int

    @property
    def width(self) -> int:
        return self.ix1 - self.ix0

    @property
    def height(self) -> int:
        return self.iy1 - self.iy0

    @property
    def west(self) -> float:
        """Western edge of the leftmost cell, not its centre."""
        return X0 + self.ix0 * STEP - STEP / 2

    @property
    def north(self) -> float:
        """Northern edge of the top row. Y descends, so iy0 is the NORTH end."""
        return Y0 - self.iy0 * STEP + STEP / 2


def window_for(bbox: tuple[float, float, float, float], margin: int = 1) -> Window:
    """Grid window covering a bbox, clipped to the grid.

    A margin of one cell is kept by default so that bilinear resampling onto a
    finer grid has a neighbour to reach for at the edges instead of smearing
    the boundary cell outward.
    """
    left, bottom, right, top = bbox
    ix0 = int(np.floor((left - X0) / STEP)) - margin
    ix1 = int(np.ceil((right - X0) / STEP)) + 1 + margin
    iy0 = int(np.floor((Y0 - top) / STEP)) - margin
    iy1 = int(np.ceil((Y0 - bottom) / STEP)) + 1 + margin
    w = Window(max(0, ix0), min(NX, ix1), max(0, iy0), min(NY, iy1))
    if w.width <= 0 or w.height <= 0:
        raise SourceError(f"bbox {bbox} does not intersect the seNorge grid")
    return w


def time_index(doy: int, year: int) -> int:
    """File time index (0-based) for a day-of-year on the 365-day axis.

    Day-of-year is carried on a collapsed 365-day axis throughout this project
    so that a window means the same calendar dates in every year - see
    `src.ingest.artskart._doy`, which collapses record dates the same way. Here
    the collapse has to be undone, because the files themselves have 366 steps
    in a leap year.

    Both halves of that convention must agree. If they drift apart, the
    rainfall window silently slides by a day against the fruiting window it was
    derived from, in every fourth year.
    """
    doy_real = doy + (1 if calendar.isleap(year) and doy > 59 else 0)
    n = 366 if calendar.isleap(year) else 365
    if not 1 <= doy_real <= n:
        raise ValueError(f"day {doy} is outside year {year}")
    return doy_real - 1


def _fetch(url: str, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise SourceError(f"seNorge did not answer after {retries + 1} tries: {url}\n{last}")


def parse_dods(payload: bytes, shape: tuple[int, ...]) -> np.ndarray:
    """The first Float32 array out of a DODS response, reshaped.

    DODS lays out an array as two big-endian uint32 element counts followed by
    the elements. The two counts are identical for a simple array; both are
    read and checked, because a mismatch means the layout is not what this
    parser assumes and silently reading past it would produce a raster of
    plausible-looking garbage.
    """
    marker = payload.find(b"Data:\n")
    if marker < 0:
        head = payload[:400].decode("utf-8", "replace")
        raise SourceError(f"no DODS data section in the response. Server said:\n{head}")
    body = payload[marker + 6:]
    if len(body) < 8:
        raise SourceError("DODS data section is empty")
    n1, n2 = np.frombuffer(body[:8], dtype=">u4")
    expect = int(np.prod(shape))
    if n1 != n2 or int(n1) != expect:
        raise SourceError(
            f"DODS array length {n1}/{n2} does not match the requested "
            f"{shape} = {expect}. The constraint and the parser disagree.")
    need = expect * 4
    if len(body) < 8 + need:
        raise SourceError(
            f"short DODS read: {len(body) - 8} bytes for {need} expected. "
            f"Truncated downloads must not be padded into a raster.")
    arr = np.frombuffer(body[8:8 + need], dtype=">f4").astype("float32")
    return arr.reshape(shape)


def daily(year: int, t0: int, t1: int, w: Window, variable: str = "rr") -> np.ndarray:
    """Daily fields for time indices [t0, t1), as (time, y, x) with NaN fill."""
    con = (f"{variable}[{t0}:1:{t1 - 1}]"
           f"[{w.iy0}:1:{w.iy1 - 1}][{w.ix0}:1:{w.ix1 - 1}]")
    url = f"{BASE.format(year=year)}.dods?{urllib.parse.quote(con, safe='')}"
    arr = parse_dods(_fetch(url), (t1 - t0, w.height, w.width))
    out = arr.astype("float64")
    out[np.abs(out - FILL) < 0.01] = np.nan
    return out


def seasonal_total(year: int, doy_from: int, doy_to: int, w: Window,
                   variable: str = "rr") -> np.ndarray:
    """Total of `variable` across the window for one year."""
    t0 = time_index(doy_from, year)
    t1 = time_index(doy_to, year) + 1
    stack = daily(year, t0, t1, w, variable)
    # A cell outside Norway is fill on every day of the year, and must stay
    # missing rather than summing to zero - zero rainfall is a claim, and a
    # false one. A cell missing on only some days is a different matter and
    # would be a data gap worth knowing about; `nan_days` reports it.
    all_missing = np.isnan(stack).all(axis=0)
    total = np.nansum(stack, axis=0)
    total[all_missing] = np.nan
    return total


def nan_days(year: int, doy_from: int, doy_to: int, w: Window,
             variable: str = "rr") -> np.ndarray:
    t0 = time_index(doy_from, year)
    t1 = time_index(doy_to, year) + 1
    return np.isnan(daily(year, t0, t1, w, variable)).sum(axis=0)


def climatology(years, doy_from: int, doy_to: int, w: Window,
                variable: str = "rr", progress=None) -> tuple[np.ndarray, np.ndarray]:
    """Mean and standard deviation of the seasonal total across years.

    The standard deviation is returned alongside the mean and not discarded:
    CLAUDE.md asks for uncertainty to be propagated rather than hidden, and a
    30 year mean with a large spread behind it is a weaker claim about a place
    than the same mean with a small one.
    """
    years = list(years)
    totals = []
    for i, y in enumerate(years, start=1):
        totals.append(seasonal_total(y, doy_from, doy_to, w, variable))
        if progress:
            progress(i, len(years), y)
    stack = np.stack(totals)
    with np.errstate(invalid="ignore"):
        return np.nanmean(stack, axis=0), np.nanstd(stack, axis=0)


def climatology_variants(years, variants: dict[str, tuple[int, int]], w: Window,
                         variable: str = "rr", progress=None) -> dict[str, dict]:
    """Climatologies for several day windows, from one pass over the data.

    The registered window is one choice among neighbouring ones, and FINDINGS
    commits to reporting how much that choice matters rather than assuming it
    does not. Downloading each variant separately would multiply a 30-year
    pull by the number of variants; instead the union span is fetched once per
    year and every variant is accumulated from it. The cost is the difference
    between the union span and the widest variant, which is nothing.
    """
    years = list(years)
    lo = min(v[0] for v in variants.values())
    hi = max(v[1] for v in variants.values())
    acc = {k: [] for k in variants}

    for i, year in enumerate(years, start=1):
        t0 = time_index(lo, year)
        stack = daily(year, t0, time_index(hi, year) + 1, w, variable)
        for key, (a, b) in variants.items():
            sl = stack[time_index(a, year) - t0:time_index(b, year) - t0 + 1]
            total = np.nansum(sl, axis=0)
            total[np.isnan(sl).all(axis=0)] = np.nan
            acc[key].append(total)
        if progress:
            progress(i, len(years), year)

    out = {}
    for key, totals in acc.items():
        st = np.stack(totals)
        # Cells outside Norway are NaN in every year, so nanmean and nanstd
        # warn about empty slices for each of them. That is expected and would
        # otherwise bury a warning worth reading under thousands that are not.
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Mean of empty slice")
            warnings.filterwarnings("ignore", message="Degrees of freedom <= 0")
            out[key] = {"mean": np.nanmean(st, axis=0), "sd": np.nanstd(st, axis=0)}
    return out
