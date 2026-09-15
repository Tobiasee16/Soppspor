"""Tiles are cached as immutable, so their addresses must change when they do.

Two things promise a tile never changes: the `Cache-Control: immutable` header
the build writes for `/zones/*`, and `zoneFirst` in the service worker, which
is cache-first with no revalidation. Both rested on a claim in a comment - that
a rebuild writes a new tile rather than changing one in place - and the claim
was false. The exporter names a tile from its row and column, so a re-export
puts different bytes at exactly the same address.

That is not theoretical. When the ranking changed from a national percentile to
a rank within 25 km, every tile's contents changed and not one URL did. Anyone
who had opened the app before that could have gone on being shown the old
estimate, under a page whose own text described the new one, for up to a year.

The fix is a stamp in the query string, taken from the bytes. These tests are
about the two directions that matter: it must change when the tiles change, and
it must *not* change when they do not, or every page-only deploy would cost
every reader a fresh download of the country.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import build_hosted as bh  # noqa: E402


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A tiny stand-in for the two tile trees the stamp is taken over."""
    zones = tmp_path / "zones"
    ctx = tmp_path / "context"
    (zones / "kantarell" / "0").mkdir(parents=True)
    ctx.mkdir()
    (zones / "kantarell" / "0" / "1_2.png").write_bytes(b"\x89PNG-one")
    (zones / "kantarell" / "0" / "3_4.png").write_bytes(b"\x89PNG-two")
    (ctx / "1_2.png").write_bytes(b"\x89PNG-ctx")
    monkeypatch.setattr(bh, "NAT_TILES", zones)
    monkeypatch.setattr(bh, "NAT_CTX", ctx)
    return zones, ctx


class TestTheStampFollowsTheBytes:
    def test_it_is_stable_when_nothing_changes(self, fake):
        assert bh.tiles_stamp() == bh.tiles_stamp()

    def test_it_changes_when_a_tile_changes(self, fake):
        zones, _ = fake
        before = bh.tiles_stamp()
        (zones / "kantarell" / "0" / "1_2.png").write_bytes(b"\x89PNG-ONE")
        assert bh.tiles_stamp() != before, (
            "a tile changed under the same name and the address did not - which "
            "is exactly the case the immutable header gets wrong")

    def test_it_changes_when_a_context_tile_changes(self, fake):
        _, ctx = fake
        before = bh.tiles_stamp()
        (ctx / "1_2.png").write_bytes(b"\x89PNG-CTX")
        assert bh.tiles_stamp() != before

    def test_it_changes_when_a_tile_is_added_or_removed(self, fake):
        zones, _ = fake
        before = bh.tiles_stamp()
        (zones / "kantarell" / "0" / "5_6.png").write_bytes(b"\x89PNG-new")
        after = bh.tiles_stamp()
        assert after != before
        (zones / "kantarell" / "0" / "5_6.png").unlink()
        assert bh.tiles_stamp() == before

    def test_moving_a_tile_changes_it_even_with_the_same_bytes(self, fake):
        """The name is hashed too, or two tiles swapping places would pass."""
        zones, _ = fake
        before = bh.tiles_stamp()
        d = zones / "kantarell" / "0"
        a, b = (d / "1_2.png").read_bytes(), (d / "3_4.png").read_bytes()
        (d / "1_2.png").write_bytes(b)
        (d / "3_4.png").write_bytes(a)
        assert bh.tiles_stamp() != before

    def test_it_is_short_enough_to_read_in_a_url(self, fake):
        s = bh.tiles_stamp()
        assert re.fullmatch(r"[0-9a-f]{12}", s), s


@pytest.mark.skipif(not (ROOT / "dist_norge" / "index.html").exists(),
                    reason="no national build to inspect")
class TestTheShippedPayloadCarriesIt:
    @staticmethod
    def _payload() -> dict:
        s = (ROOT / "dist_norge" / "index.html").read_text(encoding="utf-8")
        i = s.index("SOPP")
        j = s.index("{", i)
        depth, k = 0, j
        while True:
            if s[k] == "{":
                depth += 1
            elif s[k] == "}":
                depth -= 1
            if depth == 0:
                break
            k += 1
        return json.loads(s[j:k + 1])

    def test_every_tile_url_is_stamped(self):
        p = self._payload()
        meta = p["meta"]
        stamp = meta.get("tiles_build")
        assert stamp, "the build did not record which tiles it shipped"
        urls = [meta["tiles"]["url"]]
        if meta.get("mask"):
            urls.append(meta["mask"]["url"])
        if p.get("ctx"):
            urls.append(p["ctx"]["url"])
        for u in urls:
            assert u.endswith("?v=" + stamp), (
                f"{u} is not stamped, so a browser that has it cached will "
                "keep it for a year whatever the tiles now say")
        assert len(urls) >= 2, "expected zone and mask urls at least"

    def test_the_stamp_matches_the_tiles_on_disk(self):
        """The stamp must describe what shipped, not a run from last week."""
        assert self._payload()["meta"]["tiles_build"] == bh.tiles_stamp()
