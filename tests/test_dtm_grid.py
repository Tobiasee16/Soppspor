"""Where the national DTM grid is allowed to sit.

Agder got away with the default. `snap_bbox` expands a bbox to whole multiples
of the resolution, so the Agder 32 m DTM starts at -21824, which is exactly
-682 * 32 - and SR16's Agder raster happens to start there too, so the terrain
grid and the forest grid are commensurate by luck rather than by design.

Nationally that luck runs out. SR16's landsdekkende mosaic starts at
x = -99513.02, which is not a multiple of 16, let alone 32 (see FINDINGS 17d).
Snapping Norway's bbox to 32 m would put the terrain grid at -99552 and leave
it about 7 m out of step with the forest data it is stacked against - a
sub-cell offset on every cell of the country, invisible and permanent.

So `download_dtm` takes an explicit origin. Given SR16's, every 32 m terrain
cell is exactly the 2x2 block of SR16 cells underneath it.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.ingest.dtm import grid_for

SR16_NATIONAL_ORIGIN = (-99513.02, 8000000.0)


class TestDefaultSnapping:
    def test_the_agder_case_is_unchanged(self):
        """Phase 0's DTM must keep landing on exactly the same grid."""
        (minx, miny, maxx, maxy), w, h = grid_for(
            (-21824.0, 6426048.0, 190432.0, 6640704.0), 32.0)
        assert minx == -21824.0 and maxy == 6640704.0
        assert (w, h) == (6633, 6708)

    def test_snapping_expands_outwards_to_the_resolution(self):
        (minx, miny, maxx, maxy), _, _ = grid_for((100.0, 100.0, 300.0, 300.0), 32.0)
        assert minx == 96.0 and miny == 96.0      # floor
        assert maxx == 320.0 and maxy == 320.0    # ceil


class TestExplicitOrigin:
    def test_the_grid_starts_exactly_on_the_given_origin(self):
        (minx, miny, maxx, maxy), w, h = grid_for(
            (-99551.0, 6426048.0, 1121941.0, 7962744.0), 32.0,
            origin=SR16_NATIONAL_ORIGIN)
        # Not -99552, which is what snapping to 32 m would have produced.
        assert minx != -99552.0
        # The first 32 m grid line at or below the requested edge, counted from
        # the origin: floor((-99551 + 99513.02) / 32) = -2, so -99513.02 - 64.
        assert minx == pytest.approx(-99577.02)
        assert minx <= -99551.0        # and it therefore still covers the bbox
        # maxy stops at the first aligned line ABOVE the requested edge rather
        # than running all the way up to the origin at y = 8,000,000. Otherwise
        # the grid would carry 37 km of empty sea north of Norway.
        assert maxy == pytest.approx(7962752.0)
        assert maxy >= 7962744.0
        assert w > 38000 and h > 47000

    def test_every_terrain_cell_is_a_whole_block_of_sr16_cells(self):
        """The point of the exercise: 32 m cells on the 16 m SR16 grid."""
        (minx, _, _, maxy), _, _ = grid_for(
            (-99551.0, 6426048.0, 1121941.0, 7962744.0), 32.0,
            origin=SR16_NATIONAL_ORIGIN)
        ox, oy = SR16_NATIONAL_ORIGIN
        assert (minx - ox) % 16.0 == pytest.approx(0.0)
        assert (oy - maxy) % 16.0 == pytest.approx(0.0)

    def test_the_grid_still_covers_the_whole_requested_bbox(self):
        bbox = (-99551.0, 6426048.0, 1121941.0, 7962744.0)
        (minx, miny, maxx, maxy), _, _ = grid_for(bbox, 32.0,
                                                  origin=SR16_NATIONAL_ORIGIN)
        assert minx <= bbox[0] and miny <= bbox[1]
        assert maxx >= bbox[2] and maxy >= bbox[3]

    def test_an_origin_finer_than_the_resolution_is_honoured_not_rounded(self):
        (minx, _, _, maxy), _, _ = grid_for((0.0, 0.0, 100.0, 100.0), 32.0,
                                            origin=(0.5, 200.5))
        assert (minx - 0.5) % 32.0 == pytest.approx(0.0)
        assert (200.5 - maxy) % 32.0 == pytest.approx(0.0)
