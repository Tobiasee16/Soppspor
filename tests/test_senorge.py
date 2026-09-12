"""Tests for the seNorge OPeNDAP reader.

Three things here can go wrong silently, and each produces a raster that looks
entirely reasonable:

* the Y axis descends, so treating it as ascending flips Norway north-south;
* leap years shift the time index by one after 29 February, which would slide
  the rainfall window a day away from the fruiting window it was derived from;
* a truncated or misaligned binary read reshapes into plausible garbage.

None of these raise. All of them are checked here.
"""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest import senorge
from src.ingest.senorge import (
    FILL,
    NX,
    NY,
    STEP,
    SourceError,
    Window,
    parse_dods,
    seasonal_total,
    time_index,
    window_for,
)


def dods_payload(values: np.ndarray) -> bytes:
    """A minimal well-formed DODS response carrying one Float32 array."""
    n = np.uint32(values.size)
    return (b"Dataset {\n} test;\n\nData:\n"
            + np.array([n, n], dtype=">u4").tobytes()
            + values.astype(">f4").tobytes())


# --- the grid window ----------------------------------------------------------


def test_window_covers_the_requested_bbox():
    w = window_for((0.0, 6_500_000.0, 50_000.0, 6_550_000.0), margin=0)
    assert w.west <= 0.0
    assert w.north >= 6_550_000.0
    assert w.west + w.width * STEP >= 50_000.0


def test_the_north_edge_comes_from_the_low_row_index():
    """Y descends. If this inverts, the whole country flips."""
    north = window_for((0.0, 6_500_000.0, 10_000.0, 6_550_000.0)).north
    south = window_for((0.0, 6_900_000.0, 10_000.0, 6_950_000.0)).north
    assert south > north, "a bbox further north must have a larger north edge"


def test_window_is_clipped_to_the_grid():
    w = window_for((-5_000_000.0, 6_000_000.0, 5_000_000.0, 9_000_000.0))
    assert w.ix0 == 0 and w.ix1 == NX
    assert w.iy0 == 0 and w.iy1 == NY


def test_a_bbox_off_the_grid_is_an_error_not_an_empty_array():
    with pytest.raises(SourceError):
        window_for((5_000_000.0, 6_000_000.0, 6_000_000.0, 6_100_000.0))


def test_the_margin_widens_the_window():
    bbox = (0.0, 6_500_000.0, 50_000.0, 6_550_000.0)
    assert window_for(bbox, margin=2).width > window_for(bbox, margin=0).width


# --- the leap year convention -------------------------------------------------


def test_day_one_is_index_zero_in_every_year():
    assert time_index(1, 2019) == 0
    assert time_index(1, 2020) == 0


def test_the_window_lands_on_the_same_calendar_date_across_leap_years():
    """Day 221 must be 9 August in both, which is index 221 in a leap year."""
    assert time_index(221, 2019) == 220
    assert time_index(221, 2020) == 221


def test_days_before_the_leap_day_are_not_shifted():
    assert time_index(31, 2019) == time_index(31, 2020)


def test_the_last_day_of_the_year_is_in_range():
    assert time_index(365, 2019) == 364
    assert time_index(365, 2020) == 365


def test_a_day_outside_the_year_is_rejected():
    with pytest.raises(ValueError):
        time_index(400, 2019)


def test_the_convention_matches_the_record_parser():
    """Both halves of the 365-day collapse must agree, or the window slides.

    `artskart._doy` collapses record dates onto 365 days; `time_index` undoes
    that collapse against the files. A round trip has to land on the same
    calendar date it started from.
    """
    from datetime import date, timedelta

    from src.ingest.artskart import _doy

    for year in (2019, 2020):
        for doy in (1, 59, 60, 221, 293, 365):
            idx = time_index(doy, year)
            actual = date(year, 1, 1) + timedelta(days=idx)
            assert _doy(f"{actual.day:02d}.{actual.month:02d}.{year}") == doy


# --- the binary parser --------------------------------------------------------


def test_a_well_formed_payload_round_trips():
    vals = np.arange(12, dtype="float32")
    got = parse_dods(dods_payload(vals), (2, 2, 3))
    assert got.shape == (2, 2, 3)
    assert np.allclose(got.ravel(), vals)


def test_row_major_order_is_preserved():
    """Shape (t, y, x): the fastest-varying axis must be x."""
    got = parse_dods(dods_payload(np.arange(6, dtype="float32")), (1, 2, 3))
    assert got[0, 0, 1] == 1.0, "second element must be the next column, not the next row"
    assert got[0, 1, 0] == 3.0


def test_a_length_mismatch_is_an_error_not_a_reshape():
    with pytest.raises(SourceError, match="does not match"):
        parse_dods(dods_payload(np.arange(12, dtype="float32")), (2, 2, 4))


def test_a_truncated_body_is_an_error_not_a_padded_array():
    full = dods_payload(np.arange(12, dtype="float32"))
    with pytest.raises(SourceError, match="short DODS read"):
        parse_dods(full[:-8], (2, 2, 3))


def test_an_html_error_page_is_reported_with_what_the_server_said():
    with pytest.raises(SourceError, match="no DODS data section"):
        parse_dods(b"<html><body>404 Not Found</body></html>", (1,))


# --- fill handling ------------------------------------------------------------


def test_fill_becomes_nan_and_never_sums_to_zero(monkeypatch):
    """A cell outside Norway must stay missing. Zero rainfall is a false claim."""
    w = Window(0, 2, 0, 1)
    stack = np.array([[[FILL, 3.0]], [[FILL, 4.0]]], dtype="float32")
    monkeypatch.setattr(senorge, "daily", lambda *a, **k: np.where(
        np.abs(stack.astype("float64") - FILL) < 0.01, np.nan, stack.astype("float64")))
    total = seasonal_total(2019, 221, 222, w)
    assert np.isnan(total[0, 0]), "always-missing must stay missing, not become 0.0"
    assert total[0, 1] == pytest.approx(7.0)


def test_a_cell_missing_on_only_some_days_still_totals(monkeypatch):
    """A partial gap is a data gap, not an absent cell; nan_days reports it."""
    w = Window(0, 1, 0, 1)
    stack = np.array([[[np.nan]], [[4.0]]], dtype="float64")
    monkeypatch.setattr(senorge, "daily", lambda *a, **k: stack)
    assert seasonal_total(2019, 221, 222, w)[0, 0] == pytest.approx(4.0)
