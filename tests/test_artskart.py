"""Tests for Artskart record parsing.

Only the date handling is covered here, and deliberately so: the day-of-year
is what the fruiting season - and therefore the climate feature - is derived
from. A silent off-by-one or a malformed date parsed as January would move the
rainfall accumulation window, and nothing downstream would flag it.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from src.ingest import artskart
from src.ingest.artskart import _doy, _year, usable_points


def test_year_reads_the_last_component():
    assert _year("11.09.1985") == 1985


def test_doy_of_new_year_and_new_years_eve():
    assert _doy("01.01.2020") == 1
    assert _doy("31.12.2021") == 365


def test_doy_is_stable_across_leap_years():
    """Day 250 must be the same calendar date in a leap year as in a common one.

    Without the leap collapse, a window defined on day numbers slides by a day
    in every fourth year of a 30-year climatology.
    """
    assert _doy("07.09.2019") == _doy("07.09.2020")
    assert _doy("15.10.2019") == _doy("15.10.2020")


def test_the_leap_day_itself_does_not_create_a_366th_slot():
    assert _doy("29.02.2020") == _doy("28.02.2020")


def test_dates_before_the_leap_day_are_untouched():
    assert _doy("01.02.2020") == _doy("01.02.2021")


def test_missing_and_malformed_dates_return_none_rather_than_guessing():
    for bad in (None, "", "1985", "09.1985", "not a date", "31.02.2020"):
        assert _doy(bad) is None, bad


def test_usable_points_carries_the_day_of_year():
    rec = {
        "Id": "urn:catalog:test:1",
        "East": 63848.74,
        "North": 6603496.02,
        "Precision": "707",
        "CollectedDate": "11.09.1985",
    }
    (pt,) = usable_points([rec])
    assert pt["year"] == 1985
    assert pt["doy"] == _doy("11.09.1985")


def test_a_record_with_no_date_still_yields_a_point():
    """A dateless record is still a valid presence for a static habitat model."""
    rec = {"Id": "x", "East": 1.0, "North": 2.0, "Precision": None, "CollectedDate": None}
    (pt,) = usable_points([rec])
    assert pt["doy"] is None and pt["year"] is None


# --- the completeness check, after the 2026-09-12 finding --------------------
#
# The endpoint disagrees with itself: TotalCount, the number of rows paging
# returns, and the number of unique Ids among them are three different numbers.
# The check has to tolerate that and still catch a genuinely truncated read,
# and those two requirements pull against each other, so both directions are
# pinned here.

def _pages(page_rows, total):
    """A fake _get that serves fixed pages, like the real endpoint does."""
    def fake(params):
        i = int(params.get("PageIndex", 0))
        return {"TotalCount": total, "TotalPages": len(page_rows),
                "Observations": page_rows[i] if i < len(page_rows) else []}
    return fake


def _rows(n, start=0):
    return [{"Id": f"urn:uuid:{i:08d}", "East": 60000.0 + i, "North": 6470000.0,
             "Precision": 10} for i in range(start, start + n)]


def test_download_accepts_the_endpoints_own_miscount(tmp_path, monkeypatch):
    """The real Boletus edulis shape: pages over and under PageSize, three
    fewer rows than TotalCount, and fan-out duplicates among them."""
    pages = [_rows(1000, 0), _rows(1005, 1000), _rows(1000, 2005),
             _rows(994, 3005), _rows(563, 3999)]
    pages[1] = pages[1] + [dict(pages[1][0], Municipality="Melhus")]  # fan-out
    monkeypatch.setattr(artskart, "_get", _pages(pages, 4565))
    out = artskart.download_taxon(96845, tmp_path / "b.json", pause_s=0)
    assert out["total_count_reported"] == 4565
    assert out["records_downloaded"] == sum(len(p) for p in pages)
    assert out["unique_ids"] < out["records_downloaded"]   # the fan-out is visible
    assert (tmp_path / "b.json").exists()


def test_download_refuses_a_dropped_page(tmp_path, monkeypatch):
    pages = [_rows(1000, 0), [], _rows(1000, 2000)]
    monkeypatch.setattr(artskart, "_get", _pages(pages, 3000))
    with pytest.raises(RuntimeError, match="dropped page"):
        artskart.download_taxon(1, tmp_path / "x.json", pause_s=0)
    assert not (tmp_path / "x.json").exists()


def test_download_refuses_a_wholesale_short_read(tmp_path, monkeypatch):
    """Half the records missing is not a miscount, whatever the server says."""
    pages = [_rows(1000, 0), _rows(500, 1000)]
    monkeypatch.setattr(artskart, "_get", _pages(pages, 3000))
    with pytest.raises(RuntimeError, match="shortfall"):
        artskart.download_taxon(1, tmp_path / "x.json", pause_s=0)
    assert not (tmp_path / "x.json").exists()


# --- transient network failure ---------------------------------------------
#
# A national background pull is 1,326 requests over about 25 minutes. The first
# attempt died at page 102 with an IncompleteRead - the server closed the
# connection with 1.49 MB of a 1.69 MB response still outstanding. Nothing was
# wrong with the request; it would have succeeded on a second try.
#
# That is the failure mode these cover. At this many requests a transient error
# is not an exceptional event to abort on, it is an expected one to absorb - but
# only for errors that a retry can actually fix. A wrong filter name returns 400
# and will return 400 forever, so retrying it just delays the report by half a
# minute and buries the cause.

class _FakeResponse:
    def __init__(self, body):
        self._body = body
        self.status = 200

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen_raising(failures, body=b'{"TotalCount": 1, "Observations": []}'):
    """A urlopen that fails `failures` times, then succeeds. Counts its calls."""
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return _FakeResponse(body)

    fake.calls = calls
    return fake


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(artskart.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(artskart, "RETRIES", 0, raising=False)
    return slept


def test_get_retries_a_dropped_connection(monkeypatch, no_sleep):
    """The exact failure that killed the national download."""
    import http.client
    fake = _urlopen_raising([
        http.client.IncompleteRead(b"partial", 1493426),
        http.client.IncompleteRead(b"partial", 900000),
    ])
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    out = artskart._get({"Taxons": 1})
    assert out["TotalCount"] == 1
    assert len(fake.calls) == 3          # two failures, then the real answer
    assert len(no_sleep) == 2            # and it waited between them
    assert no_sleep[1] > no_sleep[0]     # backing off, not hammering


def test_get_retries_a_reset_and_a_timeout(monkeypatch, no_sleep):
    fake = _urlopen_raising([ConnectionResetError("reset"), TimeoutError("slow")])
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    assert artskart._get({"Taxons": 1})["TotalCount"] == 1
    assert len(fake.calls) == 3


def test_get_retries_a_truncated_body_that_is_not_valid_json(monkeypatch, no_sleep):
    """A short read does not always raise - sometimes it just cuts the JSON."""
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        body = b'{"TotalCount": 1, "Obser' if len(calls) == 1 else \
               b'{"TotalCount": 1, "Observations": []}'
        return _FakeResponse(body)

    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    assert artskart._get({"Taxons": 1})["TotalCount"] == 1
    assert len(calls) == 2


def test_get_retries_a_rate_limit_and_a_server_error(monkeypatch, no_sleep):
    import urllib.error
    fake = _urlopen_raising([
        urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None),
        urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None),
    ])
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    assert artskart._get({"Taxons": 1})["TotalCount"] == 1
    assert len(fake.calls) == 3


def test_get_does_not_retry_a_bad_request(monkeypatch, no_sleep):
    """A wrong filter name returns 400 and will return 400 forever.

    Retrying it wastes half a minute and buries the cause under four identical
    failures. This endpoint silently ignores unknown parameters, so a 400 is a
    real signal and deserves to surface immediately.
    """
    import urllib.error
    fake = _urlopen_raising([urllib.error.HTTPError("u", 400, "Bad Request", {}, None)])
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        artskart._get({"Taxons": 1})
    assert len(fake.calls) == 1
    assert no_sleep == []


def test_get_gives_up_rather_than_retrying_forever(monkeypatch, no_sleep):
    """A genuinely dead endpoint must stop the pipeline, not stall it."""
    import http.client
    fails = [http.client.IncompleteRead(b"", 1)] * 50
    fake = _urlopen_raising(fails)
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    with pytest.raises(http.client.IncompleteRead):
        artskart._get({"Taxons": 1})
    assert len(fake.calls) == artskart.RETRY_ATTEMPTS


def test_retries_are_counted_rather_than_hidden(monkeypatch, no_sleep):
    """A download that needed 200 retries is still suspect even if it finished.

    The count goes into the sidecar so that is visible afterwards instead of
    being absorbed into a clean-looking file.
    """
    import http.client
    fake = _urlopen_raising([http.client.IncompleteRead(b"", 1)] * 2)
    monkeypatch.setattr(artskart.urllib.request, "urlopen", fake)
    artskart._get({"Taxons": 1})
    assert artskart.RETRIES == 2


# --- download_filtered's failure paths --------------------------------------
#
# These exist because download_taxon's equivalents did NOT cover them. It
# accumulates into a list and holds no file open, so its dropped-page test
# could never catch what broke here: download_filtered calls dest.unlink()
# from inside `with dest.open(...)`, which on Windows raises PermissionError
# and replaces the informative error with an opaque one.
#
# The other lesson is in the docstring this module used to carry. It claimed "a
# page before the last is never empty, which is what the download checks."
# Measured 2026-09-13 on the national pull: page 1072 came back empty mid-run
# and returned 1000 rows when asked again minutes later, and pages 1070-1326
# all serve data. An empty page is transient flakiness, not proof of a dropped
# page, and treating it as proof destroyed a download at 81%.

def _filtered_pages(page_rows, total, fail_empty_once=()):
    """A fake _get for download_filtered, which pages in lowercase."""
    seen = {}

    def fake(params):
        i = int(params.get("pageIndex", 0))
        seen[i] = seen.get(i, 0) + 1
        rows = page_rows[i] if i < len(page_rows) else []
        # Pages named in fail_empty_once come back empty on their FIRST ask
        # only - exactly what the live endpoint did.
        if i in fail_empty_once and seen[i] == 1:
            rows = []
        return {"TotalCount": total, "TotalPages": len(page_rows),
                "Observations": rows}

    fake.seen = seen
    return fake


def test_filtered_download_retries_a_transient_empty_page(tmp_path, monkeypatch):
    """The failure that killed the national pull at 1.07 of 1.33 million."""
    pages = [_rows(1000, 0), _rows(1000, 1000), _rows(1000, 2000), _rows(500, 3000)]
    fake = _filtered_pages(pages, 3500, fail_empty_once={2})
    monkeypatch.setattr(artskart, "_get", fake)
    monkeypatch.setattr(artskart.time, "sleep", lambda s: None)

    dest = tmp_path / "bg.jsonl"
    meta = artskart.download_filtered({"f": 1}, dest, pause_s=0)

    assert meta["records_downloaded"] == 3500
    assert dest.exists()
    assert len(dest.read_text(encoding="utf-8").strip().splitlines()) == 3500
    assert fake.seen[2] == 2          # asked twice, kept the second answer
    assert meta["empty_page_retries"] == 1


def test_filtered_download_skips_a_permanently_empty_page(tmp_path, monkeypatch):
    """A page that is empty every time is the endpoint, not a dropped page.

    Measured 2026-09-13: pageIndex 1075 of the national fungal query returns
    zero rows deterministically, while 1074 returns 260 and 1076 returns 724,
    equally deterministically. Sixty random pages sampled across the whole
    range contained no other empty one, and their mean size of 995 extrapolates
    to 99.5% of TotalCount.

    So an empty page costs about a thousand records out of 1.33 million and is
    worth skipping. Aborting on it is what killed two consecutive runs at 81%.
    """
    # 60 pages, one of them empty - the real ratio is 1 page in 1,326, and the
    # skipped records have to stay inside COUNT_TOLERANCE or the aggregate gate
    # correctly refuses the file.
    pages = [_rows(100, i * 100) for i in range(60)]
    pages[37] = []
    fake = _filtered_pages(pages, 6000)
    monkeypatch.setattr(artskart, "_get", fake)
    monkeypatch.setattr(artskart.time, "sleep", lambda s: None)

    dest = tmp_path / "bg.jsonl"
    meta = artskart.download_filtered({"f": 1}, dest, pause_s=0)

    assert meta["records_downloaded"] == 5900      # 1.67% short, inside the 2%
    assert meta["empty_pages_skipped"] == [37]
    assert dest.exists()
    # It tried before giving up on that page, rather than skipping on sight.
    assert fake.seen[37] == artskart.EMPTY_PAGE_ATTEMPTS


def test_filtered_download_fails_when_the_skipping_adds_up(tmp_path, monkeypatch):
    """Skipping one page is a rounding error; skipping half of them is a hole.

    With no per-page signal that distinguishes the two, the aggregate shortfall
    against TotalCount is the only gate left, so it has to actually bite.
    """
    pages = [_rows(1000, 0), [], [], [], _rows(1000, 4000)]
    monkeypatch.setattr(artskart, "_get", _filtered_pages(pages, 5000))
    monkeypatch.setattr(artskart.time, "sleep", lambda s: None)

    dest = tmp_path / "bg.jsonl"
    with pytest.raises(RuntimeError, match="shortfall"):
        artskart.download_filtered({"f": 1}, dest, pause_s=0)
    assert not dest.exists()


def test_filtered_download_removes_the_file_on_a_short_read(tmp_path, monkeypatch):
    pages = [_rows(1000, 0), _rows(500, 1000)]
    monkeypatch.setattr(artskart, "_get", _filtered_pages(pages, 3000))
    monkeypatch.setattr(artskart.time, "sleep", lambda s: None)

    dest = tmp_path / "bg.jsonl"
    with pytest.raises(RuntimeError, match="shortfall"):
        artskart.download_filtered({"f": 1}, dest, pause_s=0)
    assert not dest.exists()
