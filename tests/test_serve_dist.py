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
import re
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


class TestThePrivacyPolicyIsItsOwnPage:
    """It is a second real page behind a single-page fallback, which is exactly
    the shape that goes wrong quietly: a rewrite that swallows it returns the
    app with a 200, so a reader who asked for the policy gets the map and no
    error, and nobody finds out."""

    def test_it_is_served_as_itself(self, dist):
        (dist / "personvern.html").write_text(
            "<!doctype html>personvern", encoding="utf-8")
        handled, h = get(dist, "/personvern.html")
        assert handled and h.sent == [("ok", 200)]
        assert b"personvern" in h.body
        assert b"page" not in h.body, "the fallback served the app instead"

    def test_a_build_that_lost_it_404s_rather_than_showing_the_map(self, dist):
        handled, h = get(dist, "/personvern.html")
        assert handled
        assert h.sent == [("error", 404)], h.sent
        assert b"page" not in h.body


class TestTheDeployedRuleMatches:
    """The host has to behave the way the local server does, or the build that
    was tested is not the build that is live."""

    def test_the_build_writes_exactly_one_rule(self):
        """Cloudflare Pages will not give us a 404 for missing tiles *and* a
        single-page fallback, and three deploys went into establishing that.

        A missing tile ideally 404s, because a rewrite that returns the whole
        app with a 200 makes a failed upload indistinguishable from a working
        site. What was tried, and what each attempt actually did on the live
        site:

          * `/zones/*  /zones/:splat  200` - a rewrite of a path to itself -
            does not stop `/*` claiming the path when no file matches. The
            missing tile came back as 283 kB of app.
          * `/zones/*  /some.html  404` is ignored outright. What appeared to
            work was Cloudflare serving a root 404.html of its own accord.
          * and that automatic 404.html overrides `/*  /index.html  200` for
            every unmatched path, so shipping one broke deep links: /steder
            came back 404 instead of opening the app.

        The fallback wins, because a reader who opens a shared link has to land
        on the map. The failure the 404 was guarding against is checked
        directly instead, by scripts/check_deployed.py sampling real tiles.

        This test exists to stop the first two being re-added by someone who
        reasons about it the way I did rather than measuring it.
        """
        src = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        # Quoted rule literals only. The explanation above them names the rules
        # that were tried and rejected, and a substring search cannot tell a
        # rule that is written from one that is merely described.
        rules = set(re.findall(r'"(/\S*\s{2}\S+\s{2}\d{3})"', src))
        assert rules == {"/*  /index.html  200"}, rules

    def test_no_root_not_found_page_is_shipped(self):
        """A root 404.html takes over from the single-page fallback."""
        src = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        assert not (ROOT / "web" / "404.html").exists()
        assert 'out / "404.html"' not in src

    def test_stale_pages_are_cleared_from_the_build(self):
        """A renamed page that is no longer written does not go away on its own.

        Renaming the not-found page left the old one in the output directory,
        where it was uploaded and went on being treated as the site's automatic
        404 - so the rename appeared to change nothing, and cost a deploy to
        work out. Only the top-level pages: clearing the tile directories would
        mean re-copying 154 MB every build.
        """
        src = (ROOT / "scripts" / "build_hosted.py").read_text(encoding="utf-8")
        assert 'out.glob("*.html")' in src
        assert "stale" in src

    def test_the_tiles_are_checked_on_the_deployed_site_instead(self):
        """Since the routing cannot prove an upload landed, something must."""
        py = (ROOT / "scripts" / "check_deployed.py").read_text(encoding="utf-8")
        assert "every sampled zone tile comes back as a PNG" in py
        assert "the build holds every tile its payload names" in py
