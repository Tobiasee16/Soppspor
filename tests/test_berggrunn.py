"""The bedrock lime-content comparison, and the confound it exists to remove.

FINDINGS 30c records a false finding that survived long enough to be written
down: comparing a species' mean KA against the national background said
*Craterellus cornucopioides* avoids lime-rich ground, by the largest margin of
any species and in the exact opposite direction to its ecology. The species is
southern, southern Norway is acid basement, and the comparison was measuring
latitude.

Nothing in that failure raises. Both numbers are arithmetically correct and the
sign is stable across resamples, so a bootstrap confirms it rather than catching
it. The only thing that catches it is asking the question with the geography
held fixed.

So the central test here plants a geographic confound in data containing **no
substrate preference whatsoever** and asserts that the naive comparison reports
one and the block-matched comparison does not. If `block_matched_shift` ever
stops holding geography fixed, this fails.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest.berggrunn import (DEFAULT_BLOCK_M, KA_LABELS, KA_MAX, KA_MIN,
                                  block_id, block_matched_shift, naive_shift)


class TestScale:
    def test_the_ka_scale_is_ngus_five_steps_and_not_relabelled(self):
        assert sorted(KA_LABELS) == [1, 2, 3, 4, 5]
        assert (KA_MIN, KA_MAX) == (1, 5)
        # The labels are NGU's own wording. Translating or shortening them here
        # would quietly detach this module from the NiN standard it claims.
        assert KA_LABELS[1].startswith("Sv")
        assert "kalkfattig" in KA_LABELS[1]
        assert "kalkrik" in KA_LABELS[5]


class TestBlockId:
    def test_points_in_the_same_block_share_an_id(self):
        assert block_id(0, 0) == block_id(DEFAULT_BLOCK_M - 1, DEFAULT_BLOCK_M - 1)

    def test_neighbouring_blocks_differ(self):
        assert block_id(0, 0) != block_id(DEFAULT_BLOCK_M, 0)
        assert block_id(0, 0) != block_id(0, DEFAULT_BLOCK_M)

    def test_easting_and_northing_cannot_collide(self):
        """The packing multiplier must exceed how many blocks Norway is wide."""
        # Norway spans about 1,220 km east-west and 1,540 km north-south, which
        # is roughly 50 x 62 blocks at 25 km. A multiplier of 100,000 leaves
        # three orders of magnitude of headroom; a tuple-free key is only safe
        # while that holds.
        B = DEFAULT_BLOCK_M
        assert block_id(B, 0) != block_id(0, B)
        # One block east must never land on any block reachable by going north,
        # which is what fails if the multiplier is ever lowered to fit the data.
        northward = {block_id(0, k * B) for k in range(200)}
        assert block_id(B, 0) not in northward

    def test_negative_eastings_do_not_fold_onto_positive_ones(self):
        """Norway's national grid runs to about -99,551 m east. Floor division
        keeps that separate; truncation would merge it with the first block."""
        assert block_id(-1, 0) != block_id(1, 0)


def _flat_world(n_per_block, blocks, ka_by_block):
    """Points with KA decided purely by where they are, never by what they are."""
    out = []
    for bx, by in blocks:
        for i in range(n_per_block):
            east = bx * DEFAULT_BLOCK_M + 10 + i
            north = by * DEFAULT_BLOCK_M + 10
            out.append((east, north, ka_by_block[(bx, by)]))
    return out


class TestTheConfound:
    """The heart of it. No species in these fixtures prefers anything."""

    # A south that is lime-poor and a north that is lime-rich, which is roughly
    # Norway. Every point in a block carries that block's KA, so there is no
    # within-block substrate signal for anything to find.
    SOUTH = [(0, 0), (1, 0), (2, 0)]
    NORTH = [(0, 40), (1, 40), (2, 40)]
    KA = {b: 1 for b in SOUTH} | {b: 5 for b in NORTH}

    def _background(self):
        # Fungi in general are recorded across the whole country.
        return _flat_world(20, self.SOUTH + self.NORTH, self.KA)

    def _southern_species(self):
        # A southern species. It has no preference: it takes whatever rock is
        # under the south, because that is the only place it grows.
        return _flat_world(10, self.SOUTH, self.KA)

    def test_the_naive_comparison_invents_an_effect(self):
        shift = naive_shift([ka for _, _, ka in self._southern_species()],
                            [ka for _, _, ka in self._background()])
        # Species mean 1.0, background mean 3.0. The naive number is enormous
        # and entirely geographic - this is the -0.248 of FINDINGS 30c, with
        # the confound turned up so the sign is unmistakable.
        assert shift == pytest.approx(-2.0)

    def test_the_block_matched_comparison_does_not(self):
        shift, n, blocks = block_matched_shift(self._southern_species(),
                                               self._background())
        assert blocks == len(self.SOUTH)
        assert n == 30
        assert shift == pytest.approx(0.0, abs=1e-12)

    def test_a_real_preference_still_comes_through(self):
        """Matching must remove the geography without removing the signal."""
        # Same southern species, but now sitting on rock one step richer than
        # the background does within each of its own blocks.
        bg = self._background()
        # Give each southern block a mix, so there is something to prefer.
        bg = [(e, n, ka if i % 2 else ka + 1) for i, (e, n, ka) in enumerate(bg)]
        sp = [(e, n, ka + 1) for e, n, ka in self._southern_species()]
        shift, _, _ = block_matched_shift(sp, bg)
        assert shift > 0.4, shift


class TestThinBackground:
    def test_blocks_the_background_cannot_speak_for_are_dropped(self):
        bg = _flat_world(2, [(0, 0)], {(0, 0): 1})          # only 2 points
        sp = _flat_world(5, [(0, 0)], {(0, 0): 5})
        shift, n, blocks = block_matched_shift(sp, bg, min_background=5)
        assert blocks == 0 and n == 0
        assert math.isnan(shift), "a dropped block must not read as zero shift"

    def test_a_species_with_no_usable_block_returns_nan_not_zero(self):
        """NaN and 0.0 mean opposite things: 'could not ask' versus 'asked, no
        difference'. Returning 0.0 here would report a null result for a
        question that was never put."""
        bg = _flat_world(20, [(0, 0)], {(0, 0): 1})
        sp = _flat_world(5, [(9, 9)], {(9, 9): 5})          # nowhere near it
        shift, _, _ = block_matched_shift(sp, bg)
        assert math.isnan(shift)


class TestWeighting:
    def test_blocks_weigh_by_the_species_not_the_background(self):
        """A block where fungi in general are heavily recorded must not drown
        out a block where this species actually is. Weighting by background
        would turn the comparison back into a map of recording effort."""
        bg = (_flat_world(100, [(0, 0)], {(0, 0): 1})       # heavily recorded
              + _flat_world(10, [(5, 0)], {(5, 0): 1}))     # barely recorded
        # The species is in the quiet block only, and is one step richer there.
        sp = [(5 * DEFAULT_BLOCK_M + 10, 10, 2)] * 8
        shift, n, blocks = block_matched_shift(sp, bg)
        assert blocks == 1 and n == 8
        assert shift == pytest.approx(1.0)
