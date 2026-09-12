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
