"""The habitat mask that cuts the zones, and whether it lands where it claims.

The whole point of this layer is that a lake keeps its own shape instead of the
128 m diamond the zone grid would draw around it. That only works if a mask tile
covers exactly the ground its zone tile covers. A half-cell of drift would be
invisible on a phone and would erase the wrong side of every shoreline in
Norway, so the alignment is checked against the real exported tiles rather than
argued for in a comment.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_mask_tiles as em  # noqa: E402
import export_zone_tiles as ez  # noqa: E402

TILES = ez.OUT
MASK = TILES / "mask"
META = TILES / "meta.json"


def decode_alpha(data: bytes) -> np.ndarray:
    """The alpha channel, through a decoder we did not write."""
    from PIL import Image
    return np.array(Image.open(io.BytesIO(data)).convert("RGBA"))[:, :, 3]


class TestTheEncoder:
    def test_habitat_is_transparent_and_the_rest_is_opaque(self):
        a = np.zeros((8, 16), dtype=bool)
        a[2:5, 3:9] = True
        alpha = decode_alpha(em.png_mask(a))
        assert ((alpha == 0) == a).all(), "habitat must be the transparent half"
        assert set(np.unique(alpha)) <= {0, 255}, "no partial alpha in the file"

    def test_it_refuses_anything_but_a_2d_bool_array(self):
        with pytest.raises(ValueError):
            em.png_mask(np.zeros((4, 4), dtype=np.uint8))
        with pytest.raises(ValueError):
            em.png_mask(np.zeros((4, 4, 2), dtype=bool))

    def test_a_uniform_tile_is_small(self):
        """Most of the country is one thing or the other, and pays almost nothing."""
        assert len(em.png_mask(np.zeros((1024, 1024), dtype=bool))) < 1024


class TestTheGeometry:
    def test_a_mask_tile_covers_exactly_one_zone_tile(self):
        """Same ground, four times the pixels, so no separate index is needed."""
        assert em.MASK_PX == ez.TILE * em.FINER
        assert em.FINER == 4

    def test_the_finest_level_is_the_source_resolution(self):
        """Level 0 is AR50 itself: 128 m zone cells over 32 m mask pixels."""
        assert ez.LEVELS[0] == em.FINER, (
            "level 0 downsamples AR50 by this much, and the mask undoes exactly "
            "that - if they diverge the mask is no longer the source raster")


class TestWhichTilesAreWritten:
    def test_the_neighbours_of_a_zone_tile_are_included(self):
        """drawField grows a contour two cells past its data, which at a tile
        edge is the next tile along. Without the halo that rim is never erased."""
        meta = {"levels": [{"tiles": {"cols": 10, "rows": 10}}],
                "species": [{"tiles": [["5_5"]]}]}
        got = em.wanted(meta)[0]
        assert (5, 5) in got
        for d in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1), (-1, -1)):
            assert (5 + d[0], 5 + d[1]) in got, f"missing neighbour {d}"
        assert len(got) == 9

    def test_it_does_not_wander_off_the_grid(self):
        meta = {"levels": [{"tiles": {"cols": 3, "rows": 3}}],
                "species": [{"tiles": [["0_0"]]}]}
        got = em.wanted(meta)[0]
        assert got == {(0, 0), (0, 1), (1, 0), (1, 1)}


needs_export = pytest.mark.skipif(
    not META.exists() or not MASK.is_dir(),
    reason="no exported tiles; run scripts/export_zone_tiles.py then export_mask_tiles.py")


@needs_export
class TestTheExportedTiles:
    @staticmethod
    def _meta():
        return json.loads(META.read_text(encoding="utf-8"))

    def test_the_meta_lists_what_is_on_disk(self):
        m = self._meta()
        assert "mask" in m, "the exporter did not record the mask in meta.json"
        for z, ids in enumerate(m["mask"]["tiles"]):
            for tid in ids:
                assert (MASK / str(z) / f"{tid}.png").exists(), \
                    f"meta names mask/{z}/{tid}.png and it is not there"

    def test_every_zone_tile_has_a_mask_tile_over_it(self):
        """A zone tile with no mask is a tile where nothing gets erased."""
        m = self._meta()
        for z, ids in enumerate(m["mask"]["tiles"]):
            have = set(ids)
            for sp in m["species"]:
                missing = [t for t in sp["tiles"][z] if t not in have]
                assert not missing, (
                    f"level {z}: {len(missing)} zone tiles with no mask, "
                    f"first {missing[:3]}")

    def test_the_mask_lines_up_with_the_zone_tile_under_it(self):
        """Decode both and check they agree cell for cell.

        A level 0 mask tile is 1024 px of 32 m; the zone tile beneath it is
        256 cells of 128 m. Blocking the mask down by four and applying the
        exporter's own majority rule must reproduce which cells carry a score -
        that is the same test the exporter's habitat_fraction performs, run
        against the bytes that actually shipped.
        """
        from PIL import Image
        m = self._meta()
        key = m["species"][0]["key"]
        checked = 0
        for tid in m["mask"]["tiles"][0]:
            zone = TILES / key / "0" / f"{tid}.png"
            if not zone.exists():
                continue
            alpha = decode_alpha((MASK / "0" / f"{tid}.png").read_bytes())
            habitat = alpha == 0
            f = em.FINER
            frac = habitat.reshape(ez.TILE, f, ez.TILE, f).mean(axis=(1, 3))
            scored = np.array(Image.open(zone).convert("L")) > 0
            # A scored cell must be habitat by the exporter's rule. The reverse
            # does not hold: a habitat cell the model never predicted for, or
            # one trimmed at the country's edge, is habitat with no score.
            bad = scored & (frac < 0.5)
            assert not bad.any(), (
                f"{tid}: {int(bad.sum())} scored cells the mask calls "
                f"non-habitat - the two layers are not on the same grid")
            checked += 1
            if checked >= 12:
                break
        assert checked >= 1, "no overlapping zone and mask tile was found to compare"
