"""Artskart (Artsdatabanken) occurrence download.

Endpoint verified 2026-09-09:
    https://artskart.artsdatabanken.no/publicapi/api/observations/list

VERIFIED BEHAVIOUR - READ BEFORE CHANGING:

* The working path is /observations/list. Plain /observations returns HTTP 400.
  A request with no filter at all returns HTTP 500; something must be filtered on.
* The API SILENTLY IGNORES unknown query parameters. A wrong parameter name
  does not error - it returns an unfiltered count that looks like a result.
  Never add a filter without checking that TotalCount actually changes.
* CORRECTION (2026-09-09, same day): an earlier note here claimed there was no
  server-side spatial filter. That was wrong. Filter parameters are namespaced
  under `filter.` and the bare names tried first (Counties, County, Fylker,
  Municipalities, BBox ...) were silently ignored for that reason. The real
  parameter list is published at
      https://artskart.artsdatabanken.no/publicapi/swagger/docs/v1
  and includes filter.countys, filter.areas, filter.wktPolygon,
  filter.bounds.leftBottom/rightTop, filter.taxons and filter.taxongroups.
  Read the swagger document before guessing a parameter name again.
* Server-side filters verified to change TotalCount, and cross-checked against
  our own client-side clip of the national pull - the two agree exactly:
      filter.taxons=39039                    national   4277
      filter.taxons=39039&filter.countys=42       380  (clip also gives 380)
      filter.taxons=39039&filter.areas=4204        85  (clip also gives  85)
  That agreement validates the boundary geometry and the point-in-polygon code
  as much as it validates the filter.
* filter.taxongroups=9 selects Sopper (fungi): 1,325,029 records nationally,
  82,129 in Agder, 11,668 in Kristiansand. This is the target-group background.
* The `crs` query parameter does NOT work and must not be used. crs=25833
  returns East/North as null; crs=EPSG:25833 returns integer-rounded values
  while still reporting Projection 32633. Coordinates are taken as delivered.
* Counts drift day to day - this is a live database. tubaeformis read 4276 in
  the morning and 4277 in the afternoon of 2026-09-09. Record the access date
  with any count, and do not treat a small change as corruption.
* Paging is PageIndex (0-based) plus PageSize. PageIndex is honoured; PageSize
  is a request, not a promise, and TotalCount does not agree with what paging
  returns. Measured 2026-09-12 on Boletus edulis (taxon 96845), identically on
  three consecutive attempts:
      TotalCount   4565
      page sizes   1000, 1005, 1000, 994, 563  -> 4562 rows
      unique Ids   4557
  Pages come back both over and under PageSize, the paged rows total three
  fewer than TotalCount, and five of those rows are administrative fan-out
  (same Id, same coordinate, different Municipality - see `usable_points`).
  Cantharellus cibarius shows the same shape in the other direction: 6800
  reported, 6801 rows, 6795 unique. So TotalCount cannot be used as a
  completeness assertion, and a fixed page size cannot be used to detect a
  dropped page. What IS reliable is that a page before the last is never
  empty, which is what the download checks.
* Coordinates arrive as East/North with Projection=32633 (WGS84 UTM33N), not
  25833 (ETRS89 UTM33N). The two differ by roughly half a metre in Norway and
  the offset grows with plate motion. Irrelevant at our thinning distances,
  material if anyone ever asserts sub-metre accuracy. Recorded, not silently
  swallowed. See data/SOURCES.md.
* Latitude/Longitude are strings with a COMMA decimal separator. East/North are
  proper floats. Use East/North.
"""
from __future__ import annotations

import calendar
import json
import pathlib
from datetime import date
import time
import urllib.parse
import urllib.request

BASE = "https://artskart.artsdatabanken.no/publicapi/api/observations/list"
PAGE_SIZE = 1000
TIMEOUT = 180
SOURCE_PROJECTION = 32633


def _get(params: dict) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        if r.status != 200:
            raise RuntimeError(f"HTTP {r.status} from {url}")
        return json.loads(r.read().decode("utf-8"))


def total_count(taxon_id: int) -> int:
    return _get({"Taxons": taxon_id, "PageSize": 1})["TotalCount"]


# How far below TotalCount the unique-record count may fall before the download
# is treated as truncated rather than as the server's own inconsistency. The
# measured discrepancy is 0.18% (8 of 4565) on the worst taxon seen; a dropped
# page at PAGE_SIZE=1000 would be 20% of a 5000-record taxon and far more of a
# smaller one. There is no realistic value that confuses the two.
COUNT_TOLERANCE = 0.02


def download_taxon(taxon_id: int, dest: pathlib.Path, pause_s: float = 0.3) -> dict:
    """Download every national record for one taxon. Fails loudly, never partially.

    A short read is an error, not something to paper over: CLAUDE.md forbids
    fabricating or quietly degrading data. The difficulty is that this endpoint
    disagrees with itself - see the note on paging at the top of this module -
    so `len(obs) == TotalCount` is not available as the completeness test, and
    asserting it would simply make every download fail.

    Two checks replace it. A page before the last coming back empty means a page
    was genuinely lost, which is the failure that matters and which no amount of
    server-side inconsistency produces. And the unique-record count must land
    within COUNT_TOLERANCE of TotalCount, which catches a wholesale short read
    without tripping on the handful of records the server miscounts.

    All three figures are written into the file rather than reconciled, so a
    reader can see the discrepancy instead of inheriting a number that looks
    exact and is not.
    """
    first = _get({"Taxons": taxon_id, "PageSize": PAGE_SIZE, "PageIndex": 0})
    expected = first["TotalCount"]
    obs = list(first["Observations"])
    pages = first["TotalPages"]

    for page in range(1, pages):
        time.sleep(pause_s)
        chunk = _get({"Taxons": taxon_id, "PageSize": PAGE_SIZE, "PageIndex": page})
        got = chunk["Observations"]
        if not got:
            raise RuntimeError(
                f"taxon {taxon_id}: page {page} of {pages} came back empty. "
                f"That is a dropped page, not the endpoint's usual miscount. "
                f"Refusing to write a truncated file."
            )
        obs.extend(got)

    unique = len({o.get("Id") for o in obs if o.get("Id") is not None})
    if expected and unique < expected * (1 - COUNT_TOLERANCE):
        raise RuntimeError(
            f"taxon {taxon_id}: TotalCount says {expected} but paging yielded "
            f"only {unique} unique records ({len(obs)} rows), a shortfall of "
            f"{100 * (1 - unique / expected):.1f}%. That is past the "
            f"{COUNT_TOLERANCE:.0%} the endpoint's own inconsistency accounts "
            f"for. Refusing to write a truncated file."
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "taxon_id": taxon_id,
        "source": BASE,
        "source_projection": SOURCE_PROJECTION,
        "total_count_reported": expected,
        "records_downloaded": len(obs),
        "unique_ids": unique,
        "observations": obs,
    }
    dest.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def total_count_filtered(filters: dict) -> int:
    """TotalCount for an arbitrary filter. Use to prove a filter actually bites."""
    return _get({**filters, "pageSize": 1})["TotalCount"]


def download_filtered(
    filters: dict,
    dest: pathlib.Path,
    pause_s: float = 0.3,
    progress=None,
) -> dict:
    """Stream a filtered query to JSON Lines, one observation per line.

    Used for the target-group background: all fungal records in Agder is 82k
    records and roughly 129 MB, which is worth streaming rather than assembling
    in memory. One record per line also means a truncated file is obvious on
    inspection instead of being a silently short JSON array.

    Metadata goes to a sidecar rather than a wrapper object, so the data file
    stays append-only. Fails loudly on a short read, like download_taxon: a
    partial background set would quietly bias every model that used it.
    """
    first = _get({**filters, "pageSize": PAGE_SIZE, "pageIndex": 0})
    expected = first["TotalCount"]
    pages = first["TotalPages"]

    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with dest.open("w", encoding="utf-8") as fh:
        for o in first["Observations"]:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
            written += 1
        for page in range(1, pages):
            time.sleep(pause_s)
            chunk = _get({**filters, "pageSize": PAGE_SIZE, "pageIndex": page})
            for o in chunk["Observations"]:
                fh.write(json.dumps(o, ensure_ascii=False) + "\n")
                written += 1
            if progress:
                progress(page + 1, pages, written)

    if written < expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"expected {expected} records for {filters}, got {written}. "
            f"Removed the partial file rather than leave a truncated background set."
        )

    meta = {
        "source": BASE,
        "filters": {k: str(v) for k, v in filters.items()},
        "source_projection": SOURCE_PROJECTION,
        "total_count_reported": expected,
        "records_downloaded": written,
    }
    dest.with_suffix(".meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def iter_jsonl(path: pathlib.Path):
    """Yield observations from a JSON Lines download without loading it all."""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load(path: pathlib.Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["observations"]


def usable_points(observations: list[dict], dedupe: bool = True) -> list[dict]:
    """Records carrying a usable planar coordinate.

    Drops records with no East/North. Keeps Precision so downstream code can
    filter on coordinate uncertainty explicitly rather than by accident.

    Deduplicates on Id by default. Artskart emits one row per administrative
    unit a record intersects, so an observation whose uncertainty circle
    straddles a municipality or county line comes back two to four times with
    identical coordinates and differing Municipality/County. Verified 2026-09-09
    on urn:uuid:9ca0c111-2ad5-4de1-a611-bb7660ac3c12 (precision 1364 m, listed
    under both Nordre Follo and Oslo). Left undeduplicated, coarse records would
    silently carry several times the weight of precise ones - exactly backwards.
    """
    seen: set[str] = set()
    out = []
    for o in observations:
        if dedupe:
            oid = o.get("Id")
            if oid is not None:
                if oid in seen:
                    continue
                seen.add(oid)
        e, n = o.get("East"), o.get("North")
        if e in (None, 0) or n in (None, 0):
            continue
        try:
            prec = float(o["Precision"]) if o.get("Precision") not in (None, "") else None
        except (TypeError, ValueError):
            prec = None
        out.append(
            {
                "id": o.get("Id"),
                "east": float(e),
                "north": float(n),
                "precision_m": prec,
                "year": _year(o.get("CollectedDate")),
                "doy": _doy(o.get("CollectedDate")),
                "municipality": o.get("Municipality"),
                "county": o.get("County"),
                "dataset": o.get("DatasetName"),
                "basis": o.get("BasisOfRecord"),
            }
        )
    return out


def _year(datestr):
    # Artskart CollectedDate is dd.mm.yyyy, sometimes blank or partial.
    if not datestr:
        return None
    parts = str(datestr).split(".")
    if parts and parts[-1].isdigit() and len(parts[-1]) == 4:
        return int(parts[-1])
    return None


def _doy(datestr):
    """Day of year, 1-365, from Artskart's dd.mm.yyyy.

    Needed to derive the fruiting season from the records themselves rather
    than asserting it from field-guide months. Leap years are collapsed onto a
    365-day axis (29 February shares 28 February's index) so that day-of-year
    means the same calendar position in every year - otherwise a window defined
    on day numbers drifts by a day across a 30-year normal period.

    Returns None for blank, partial or malformed dates rather than guessing;
    a record with no date must not be silently placed in the season.
    """
    if not datestr:
        return None
    parts = str(datestr).split(".")
    if len(parts) != 3:
        return None
    try:
        day, month, year = (int(p) for p in parts)
        d = date(year, month, day)
    except (TypeError, ValueError):
        return None
    doy = d.timetuple().tm_yday
    if calendar.isleap(year) and doy > 59:   # after 29 February
        doy -= 1
    return doy
