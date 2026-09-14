"""What the local server does with a path that is not there.

`serve_interface.py --dist` exists so the thing that gets tested is the thing
that gets deployed, which makes its fallback rule load-bearing rather than a
convenience. The rule has to cut two ways at once:

* a path a person could arrive at - a bookmark, a shared link, the hash from a
  magic link - has to reach the app, because the app creates no deep paths
  itself and the host would otherwise show its own 404 page;
* a **missing asset** has to 404. Served the page instead, a zone tile that
  failed to upload comes back as 229 kB of HTML with a 200, the decoder rejects
  it, and the map shows no zones anywhere with nothing reporting a problem.

The second is the one worth a test. It is the failure mode of a bad deploy, and
it is completely silent.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import serve_interface as si


class Fake(si.Handler):
    """The handler with the socket taken out, so a path can be asked about."""

    def __init__(self, dist: pathlib.Path):
        self.dist = dist
        self.sent: list = []
        self.headers_out: dict = {}
        self.body = b""

    def send_error(self, code, *a, **k):
        self.sent.append(("error", code))

    def send_response(self, code, *a, **k):
        self.sent.append(("ok", code))

    def send_header(self, k, v):
        self.headers_out[k] = v

    def end_headers(self):
        pass

    @property
    def wfile(self):
        outer = self

        class W:
            def write(self, b):
                outer.body += b
        return W()


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>page", encoding="utf-8")
    (tmp_path / "sw.js").write_text("// worker", encoding="utf-8")
    tiles = tmp_path / "zones" / "craterellus_tubaeformis"
    tiles.mkdir(parents=True)
    (tiles / "1_2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    return tmp_path


def get(dist, path):
    h = Fake(dist)
    handled = h._serve_from_dist(path)
    return handled, h


class TestAMissingAssetIs404:
    @pytest.mark.parametrize("path", [
        "/zones/craterellus_tubaeformis/0_0.png",     # a tile never written
        "/zones/cantharellus_cibarius/1_2.png",       # a species not uploaded
        "/icons/icon-512.png",                        # any asset at all
        "/conditions.json",
    ])
    def test_it_does_not_come_back_as_the_page(self, dist, path):
        handled, h = get(dist, path)
        assert handled
        assert h.sent == [("error", 404)], h.sent
        assert b"page" not in h.body

    def test_a_tile_that_exists_is_served_as_a_png(self, dist):
        handled, h = get(dist, "/zones/craterellus_tubaeformis/1_2.png")
        assert handled and h.sent == [("ok", 200)]
        assert h.headers_out["Content-Type"] == "image/png"
        assert h.body.startswith(b"\x89PNG")


class TestTheAppStillRoams:
    @pytest.mark.parametrize("path", ["/", "/steder", "/om/kilder", "/index.html"])
    def test_a_path_a_person_could_arrive_at_reaches_the_app(self, dist, path):
        handled, h = get(dist, path)
        assert handled and h.sent == [("ok", 200)]
        assert b"page" in h.body

    def test_the_worker_is_served_with_its_own_scope(self, dist):
        _, h = get(dist, "/sw.js")
        assert h.headers_out["Service-Worker-Allowed"] == "/"
        assert h.headers_out["Cache-Control"] == "no-cache"


class TestTheDeployedRuleMatches:
    """The host has to behave the way the local server does, or the build that
    was tested is not the build that is live."""

    def test_the_build_writes_a_zone_passthrough_before_the_fallback(self):
        import build_hosted as bh
        src = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        assert "/zones/*  /zones/:splat  200" in src
        i = src.index("/zones/*  /zones/:splat  200")
        j = src.index("/*  /index.html  200")
        assert i < j, "the catch-all would swallow the zone rule"
        assert bh is not None
