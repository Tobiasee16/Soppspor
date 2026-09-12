"""Tests for the Geonorge Nedlasting API client.

The failure this file mostly exists to prevent is a truncated multi-gigabyte
archive sitting on disk under its real name. Nothing downstream would notice:
a partial zip opens, lists some members, and yields a raster covering part of
the county. CLAUDE.md forbids exactly that - a failed download must stop and
report, never leave something that could quietly reach a model.

The rest guards against ordering the wrong thing: a projection or an area the
host does not actually publish comes back as an empty or wrong order rather
than an error, so those checks live client-side.
"""
import io
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest import geonorge_download as gd
from src.ingest.geonorge_download import DownloadError, download, find_area, order

BASE = "https://example.invalid"
UUID = "0000-uuid"

AGDER = {
    "code": "42", "type": "fylke", "name": "Agder",
    "projections": [{"code": "25833", "name": "EUREF89 UTM sone 33, 2d"},
                    {"code": "25832", "name": "EUREF89 UTM sone 32, 2d"}],
    "formats": [{"name": "RASTER"}, {"name": "SHAPE"}],
}
OSLO = {"code": "03", "type": "fylke", "name": "Oslo",
        "projections": [{"code": "25833", "name": "x"}], "formats": [{"name": "SHAPE"}]}


@pytest.fixture
def catalogue(monkeypatch):
    """Stub the HTTP layer with a two-area host that records orders placed."""
    placed = []

    def fake(url, body=None, retries=2):
        if "/codelists/area/" in url:
            return [AGDER, OSLO]
        if url.endswith("/order"):
            placed.append(body)
            return {"referenceNumber": "ref-1",
                    "files": [{"name": "42_25833.zip", "status": "ReadyForDownload",
                               "downloadUrl": "https://example.invalid/f.zip"}]}
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr(gd, "_json", fake)
    return placed


# --- ordering the right thing -------------------------------------------------


def test_an_area_is_found_by_code_and_kind(catalogue):
    assert find_area(BASE, UUID, "42", "fylke")["name"] == "Agder"


def test_an_unlisted_area_is_an_error_that_says_what_is_listed(catalogue):
    with pytest.raises(DownloadError, match="not orderable"):
        find_area(BASE, UUID, "99", "fylke")


def test_a_kommune_code_is_not_matched_against_a_fylke(catalogue):
    """Codes are only unique within a kind; 42 as a kommune is not Agder."""
    with pytest.raises(DownloadError):
        find_area(BASE, UUID, "42", "kommune")


def test_the_order_carries_the_requested_projection(catalogue):
    order(BASE, UUID, AGDER, projection="25833", fmt="RASTER")
    line = catalogue[0]["orderLines"][0]
    assert line["projections"][0]["code"] == "25833"
    assert line["areas"][0]["code"] == "42"
    assert line["formats"] == [{"name": "RASTER"}]


def test_an_unavailable_projection_is_refused_rather_than_substituted(catalogue):
    """Silently taking whatever projection is offered would break the CRS rule."""
    with pytest.raises(DownloadError, match="not published in EPSG:25835"):
        order(BASE, UUID, AGDER, projection="25835")


def test_an_unoffered_format_is_refused(catalogue):
    with pytest.raises(DownloadError, match="not offered"):
        order(BASE, UUID, AGDER, fmt="GEOPACKAGE")


def test_no_email_is_ever_included_in_an_order(catalogue):
    """A person's address is not ours to hand to a third party for a download."""
    order(BASE, UUID, AGDER, fmt="RASTER")
    assert "email" not in json.dumps(catalogue[0]).lower()


def test_an_order_with_no_files_is_an_error(monkeypatch):
    monkeypatch.setattr(gd, "_json", lambda url, body=None, retries=2:
                        [AGDER] if "area" in url else {"files": []})
    with pytest.raises(DownloadError, match="returned no files"):
        order(BASE, UUID, AGDER, fmt="RASTER")


# --- never leaving a truncated file -------------------------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, payload, declared):
        super().__init__(payload)
        self.headers = {"Content-Length": str(declared)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def test_a_complete_download_lands_at_the_destination(monkeypatch, tmp_path):
    body = b"x" * 4096
    monkeypatch.setattr(gd.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(body, len(body)))
    dest = tmp_path / "ok.zip"
    meta = download("https://example.invalid/f.zip", dest)
    assert dest.read_bytes() == body
    assert meta["bytes"] == len(body)
    assert not list(tmp_path.glob("*.partial"))


def test_a_short_read_raises_and_leaves_nothing_behind(monkeypatch, tmp_path):
    """The whole point: a half-downloaded archive must not exist afterwards."""
    monkeypatch.setattr(gd.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(b"x" * 100, 999_999))
    dest = tmp_path / "truncated.zip"
    with pytest.raises(DownloadError, match="short read"):
        download("https://example.invalid/f.zip", dest)
    assert not dest.exists(), "a truncated file must not be left under the real name"
    assert not list(tmp_path.glob("*.partial")), "nor under a partial name"


def test_a_connection_failure_mid_stream_leaves_nothing_behind(monkeypatch, tmp_path):
    class Exploding(FakeResponse):
        def read(self, n=-1):
            raise OSError("connection reset")

    monkeypatch.setattr(gd.urllib.request, "urlopen",
                        lambda *a, **k: Exploding(b"", 999))
    dest = tmp_path / "boom.zip"
    with pytest.raises(OSError):
        download("https://example.invalid/f.zip", dest)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_a_server_that_declares_no_length_is_accepted(monkeypatch, tmp_path):
    """Without Content-Length there is nothing to verify against; do not invent one."""
    body = b"y" * 32
    monkeypatch.setattr(gd.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse(body, 0))
    dest = tmp_path / "nolen.zip"
    assert download("https://example.invalid/f.zip", dest)["bytes"] == 32
    assert dest.exists()
