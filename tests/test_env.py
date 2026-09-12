"""Tests for the environmental point-query client.

Offline: every network call is stubbed with a payload actually captured from
the live service on 2026-09-09. The traps these guard against are silent ones -
a wrong grid index or a dropped fill value returns a plausible number rather
than an error, and would reach a model as data.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest import env

E, N = 93674.87, 6466470.01

# Captured from wms.nibio.no/cgi-bin/ar5, trimmed.
AR5_GML = """<?xml version="1.0" encoding="UTF-8"?>
<msGMLOutput xmlns:gml="http://www.opengis.net/gml">
  <Arealtype_layer>
  <gml:name>Arealtype</gml:name>
    <Arealtype_feature>
      <gml:boundedBy>
        <gml:Box srsName="EPSG:25833">
          <gml:coordinates>93630.9,6466459.6 93682.8,6466516.9</gml:coordinates>
        </gml:Box>
      </gml:boundedBy>
      <artype>30</artype>
      <artype_beskrivelse>Skog</artype_beskrivelse>
      <artreslag>31</artreslag>
      <artreslag_beskrivelse>Barskog</artreslag_beskrivelse>
      <arskogbon_beskrivelse>Middels</arskogbon_beskrivelse>
    </Arealtype_feature>
  </Arealtype_layer>
</msGMLOutput>"""

EMPTY_GML = """<?xml version="1.0" encoding="UTF-8"?>
<msGMLOutput xmlns:gml="http://www.opengis.net/gml">
</msGMLOutput>"""

SENORGE_ASCII = """Dataset {
    Grid {
     ARRAY:
        Float32 rr[time = 3][Y = 1][X = 1];
    } rr;
} seNorge2018_2024.nc;
---------------------------------------------
rr.rr[3][1][1]
[0][0], 0.0
[1][0], 3.7055
[2][0], -999.99

rr.time[3]
1092966.0, 1092990.0, 1093014.0
"""


def _stub(monkeypatch, payload: str):
    monkeypatch.setattr(env, "_get", lambda url, retries=2: payload.encode("utf-8"))


# --- GML parsing --------------------------------------------------------------


def test_gml_attributes_are_extracted(monkeypatch):
    _stub(monkeypatch, AR5_GML)
    d = env.ar5(E, N)
    assert d["artype"] == "30"
    assert d["artype_beskrivelse"] == "Skog"
    assert d["artreslag_beskrivelse"] == "Barskog"


def test_gml_namespaced_elements_are_not_treated_as_fields(monkeypatch):
    """gml:name and gml:coordinates are structure, not data."""
    _stub(monkeypatch, AR5_GML)
    d = env.ar5(E, N)
    assert not any(k.startswith("gml") for k in d)
    assert "coordinates" not in d
    assert "name" not in d


def test_empty_gml_is_an_empty_result_not_a_failure(monkeypatch):
    """A location with no polygon is a real answer and must not raise."""
    _stub(monkeypatch, EMPTY_GML)
    assert env.losmasser(E, N) == {}


def test_service_exception_raises(monkeypatch):
    _stub(monkeypatch, "<ServiceExceptionReport><ServiceException code='InvalidFormat'>"
                       "bad</ServiceException></ServiceExceptionReport>")
    with pytest.raises(env.SourceError):
        env.sr16(E, N)


# --- seNorge grid indexing ----------------------------------------------------


def test_senorge_index_matches_the_verified_value():
    """Checked against the file's own coordinate arrays: X[168]=93500, Y[1533]=6466500."""
    assert env.senorge_index(E, N) == (168, 1533)


def test_senorge_y_axis_descends():
    """The trap: a more northerly point must give a SMALLER Y index."""
    _, iy_south = env.senorge_index(E, 6500000.0)
    _, iy_north = env.senorge_index(E, 6900000.0)
    assert iy_north < iy_south


def test_southern_agder_falls_outside_the_senorge_grid():
    """A real coverage limit, not a hypothetical.

    seNorge stops at Y = 6450500, while Agder reaches down to 6426048. Points
    south of that edge must raise rather than silently returning the nearest
    cell: 0 of 904 thinned presences are affected, but 5 of 3,882 background
    points are, and a clamped value there would be climate from somewhere else.
    """
    assert env.SENORGE_Y0 - (env.SENORGE_NY - 1) * env.SENORGE_STEP == 6450500.0
    with pytest.raises(env.SourceError, match="outside the seNorge grid"):
        env.senorge_index(E, 6440000.0)


def test_senorge_index_is_within_half_a_cell_of_the_query():
    ix, iy = env.senorge_index(E, N)
    cx = env.SENORGE_X0 + ix * env.SENORGE_STEP
    cy = env.SENORGE_Y0 - iy * env.SENORGE_STEP
    assert abs(cx - E) <= env.SENORGE_STEP / 2
    assert abs(cy - N) <= env.SENORGE_STEP / 2


def test_point_outside_the_grid_raises_rather_than_clamping():
    with pytest.raises(env.SourceError, match="outside the seNorge grid"):
        env.senorge_index(-5_000_000.0, 6466470.0)
    with pytest.raises(env.SourceError, match="outside the seNorge grid"):
        env.senorge_index(E, 100_000.0)


# --- seNorge values -----------------------------------------------------------


def test_senorge_series_parses_values(monkeypatch):
    _stub(monkeypatch, SENORGE_ASCII)
    v = env.senorge_series(E, N, 2024, "rr", 250, 252)
    assert v[:2] == [0.0, 3.7055]


def test_senorge_fill_value_becomes_none_not_a_number(monkeypatch):
    """-999.99 means no data. Averaged in as a number it would be catastrophic."""
    _stub(monkeypatch, SENORGE_ASCII)
    v = env.senorge_series(E, N, 2024, "rr", 250, 252)
    assert v[2] is None


def test_senorge_rejects_an_unknown_variable():
    with pytest.raises(ValueError, match="unknown seNorge variable"):
        env.senorge_series(E, N, 2024, "humidity")


def test_senorge_empty_response_raises(monkeypatch):
    _stub(monkeypatch, "Dataset { } x;\n-----\n")
    with pytest.raises(env.SourceError, match="no values"):
        env.senorge_series(E, N, 2024, "rr", 0, 0)


def test_day_index_is_zero_based():
    import datetime
    assert env.day_index(datetime.date(2024, 1, 1)) == 0
    assert env.day_index(datetime.date(2024, 9, 7)) == 250


# --- elevation ----------------------------------------------------------------


def test_elevation_returns_source_and_terrain(monkeypatch):
    _stub(monkeypatch, '{"koordsys":25833,"punkter":[{"datakilde":"dtm1",'
                       '"terreng":"Skog","x":93674.87,"y":6466470.01,"z":38.05}]}')
    d = env.elevation(E, N)
    assert d["elevation_m"] == 38.05
    assert d["source"] == "dtm1"
    assert d["terrain"] == "Skog"


def test_elevation_over_water_is_reported_not_hidden(monkeypatch):
    """The service answers from depth contours over sea. Callers must be able
    to see that, because -15 m is a real answer to a different question."""
    _stub(monkeypatch, '{"koordsys":25833,"punkter":[{"datakilde":"dybdekurver",'
                       '"terreng":"Havflate","x":1,"y":2,"z":-15.1}]}')
    d = env.elevation(1, 2)
    assert d["source"] == "dybdekurver"
    assert d["elevation_m"] < 0


def test_elevation_with_no_points_raises(monkeypatch):
    _stub(monkeypatch, '{"koordsys":25833,"punkter":[]}')
    with pytest.raises(env.SourceError, match="no elevation"):
        env.elevation(E, N)


# --- NVE ----------------------------------------------------------------------


def test_nve_series_returns_unit_and_data(monkeypatch):
    _stub(monkeypatch, '{"Theme":"gwb_sssrel","Unit":"%","NoDataValue":-999,'
                       '"Data":[45,44,44,48,48]}')
    d = env.nve_series(E, N, "2024-09-01", "2024-09-05")
    assert d["data"] == [45, 44, 44, 48, 48]
    assert d["unit"] == "%"


def test_nve_missing_data_raises(monkeypatch):
    _stub(monkeypatch, '{"Theme":"gwb_sssrel","Unit":"%","Data":null}')
    with pytest.raises(env.SourceError, match="no data"):
        env.nve_series(E, N, "2024-09-01", "2024-09-05")
