"""The zone tiles a browser will read as data, not as pictures.

Two things are worth real tests here.

The PNG encoder is hand-written, because Pillow is only present as a
matplotlib dependency and CLAUDE.md says to ask before adding one. Hand-written
means it has to be checked against a real decoder rather than against itself,
so every test below round-trips through Pillow - which is fine to rely on in a
test even though the shipped code must not.

The byte is a *national percentile* rather than a scaled score, which is a
deliberate departure from the Kristiansand payload. If that encoding is wrong
the map still renders, still looks plausible, and quietly misstates how good
a place is - so the rank properties are pinned explicitly.
"""
from __future__ import annotations

import io
import pathlib
import warnings
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_zone_tiles as ezt

PIL = pytest.importorskip("PIL.Image", reason="decoder for checking our encoder")


def decode(data: bytes) -> np.ndarray:
    return np.array(PIL.open(io.BytesIO(data)))


class TestPngEncoder:
    def test_round_trips_through_a_real_decoder(self):
        rng = np.random.default_rng(0)
        arr = rng.integers(0, 256, size=(37, 61), dtype="uint8")
        np.testing.assert_array_equal(decode(ezt.png_gray(arr)), arr)

    def test_all_zero_and_all_255(self):
        for value in (0, 255):
            arr = np.full((16, 16), value, dtype="uint8")
            np.testing.assert_array_equal(decode(ezt.png_gray(arr)), arr)

    def test_a_full_size_tile(self):
        rng = np.random.default_rng(1)
        arr = rng.integers(0, 256, size=(ezt.TILE, ezt.TILE), dtype="uint8")
        np.testing.assert_array_equal(decode(ezt.png_gray(arr)), arr)

    def test_it_is_greyscale_not_palette(self):
        # Colour type 0 in the IHDR. A palette PNG would decode to indices and
        # the values a browser reads back would be meaningless.
        # 8 signature + 4 length + 4 "IHDR" + 4 width + 4 height = 24, so bit
        # depth is byte 24 and colour type byte 25.
        data = ezt.png_gray(np.zeros((4, 4), dtype="uint8"))
        assert data[12:16] == b"IHDR"
        assert data[24] == 8 and data[25] == 0     # bit depth, colour type
        assert PIL.open(io.BytesIO(data)).mode == "L"

    def test_non_square_orientation_is_preserved(self):
        arr = np.arange(6, dtype="uint8").reshape(2, 3) * 40
        np.testing.assert_array_equal(decode(ezt.png_gray(arr)), arr)

    def test_a_wrong_dtype_is_refused(self):
        with pytest.raises(ValueError):
            ezt.png_gray(np.zeros((4, 4), dtype="float32"))


class TestPercentileEncoding:
    def test_nodata_is_zero_and_data_never_is(self):
        g = np.array([[np.nan, 1.0], [2.0, 3.0]], dtype="float32")
        out, n = ezt.to_percentile_bytes(g)
        assert out[0, 0] == 0 and n == 3
        assert (out[0, 1] > 0) and (out[1, 0] > 0) and (out[1, 1] > 0)

    def test_order_is_preserved(self):
        g = np.array([[0.001, 0.5], [0.02, 0.9]], dtype="float32")
        out, _ = ezt.to_percentile_bytes(g)
        flat = out.ravel()
        assert list(np.argsort(flat)) == list(np.argsort(g.ravel()))

    def test_it_spends_the_range_on_a_skewed_surface(self):
        """The reason this encoding exists at all.

        A national surface has a p98/p2 ratio in the hundreds. Linear scaling
        puts most of the country in the first few bytes; ranking does not.
        """
        rng = np.random.default_rng(2)
        g = rng.pareto(0.3, size=200_000).astype("float32")
        ranked, _ = ezt.to_percentile_bytes(g.reshape(400, 500))
        lo, hi = g.min(), g.max()
        linear = np.clip(np.round((g - lo) / (hi - lo) * 254) + 1, 1, 255)
        assert (ranked <= 3).mean() < 0.05, "ranking wasted the bottom of the range"
        assert (linear <= 3).mean() > 0.5, "the fixture is not skewed enough to matter"
        assert len(np.unique(ranked)) > 200

    def test_ties_take_the_lower_rank(self):
        g = np.array([[1.0, 1.0, 1.0, 9.0]], dtype="float32")
        out, _ = ezt.to_percentile_bytes(g)
        assert out[0, 0] == out[0, 1] == out[0, 2] == 1
        assert out[0, 3] == 255

    def test_an_empty_surface_gives_no_bytes(self):
        out, n = ezt.to_percentile_bytes(np.full((4, 4), np.nan, dtype="float32"))
        assert n == 0 and not out.any()


@pytest.fixture
def surface(tmp_path):
    """A 32 m raster with a land patch, for the downsample and tiling path."""
    h, w = 64, 80
    a = np.full((h, w), np.nan, dtype="float32")
    a[8:56, 12:68] = np.linspace(0.0, 1.0, 48 * 56).reshape(48, 56)
    path = tmp_path / "s.tif"
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype="float32", crs="EPSG:25833", nodata=float("nan"),
                       transform=from_origin(100_000.0, 7_000_000.0, 32.0, 32.0)) as ds:
        ds.write(a, 1)
    return path


class TestDownsample:
    def test_cell_size_is_exactly_the_factor(self, surface):
        _, t = ezt.downsample(surface, factor=4)
        assert abs(t.a) == 128.0 and abs(t.e) == 128.0

    def test_the_origin_does_not_move(self, surface):
        with rasterio.open(surface) as src:
            before = (src.transform.c, src.transform.f)
        _, t = ezt.downsample(surface, factor=4)
        assert (t.c, t.f) == before

    def test_shape_is_the_whole_blocks_only(self, surface):
        g, _ = ezt.downsample(surface, factor=4)
        assert g.shape == (64 // 4, 80 // 4)

    def test_nodata_does_not_poison_a_partly_covered_cell(self, surface):
        g, _ = ezt.downsample(surface, factor=4)
        # The land patch's edge blocks are part nodata; they must still carry a
        # value, or every coastline would erode by one display cell.
        assert np.isfinite(g[2, 3])

    def test_values_stay_in_range(self, surface):
        g, _ = ezt.downsample(surface, factor=4)
        f = g[np.isfinite(g)]
        assert f.min() >= 0.0 and f.max() <= 1.0


class TestTiling:
    def test_empty_tiles_are_not_written(self, tmp_path):
        byts = np.zeros((ezt.TILE * 2, ezt.TILE * 2), dtype="uint8")
        byts[:10, :10] = 200                       # only tile 0_0 has anything
        ids = ezt.cut_tiles(byts, tmp_path / "sp")
        assert ids == ["0_0"]
        assert len(list((tmp_path / "sp").glob("*.png"))) == 1

    def test_edge_tiles_are_padded_not_stretched(self, tmp_path):
        byts = np.full((ezt.TILE + 5, ezt.TILE + 7), 9, dtype="uint8")
        ids = ezt.cut_tiles(byts, tmp_path / "sp")
        assert set(ids) == {"0_0", "0_1", "1_0", "1_1"}
        edge = decode((tmp_path / "sp" / "1_1.png").read_bytes())
        assert edge.shape == (ezt.TILE, ezt.TILE)
        assert (edge[:5, :7] == 9).all() and edge[5:, :].sum() == 0

    def test_a_tile_holds_the_right_cells(self, tmp_path):
        rng = np.random.default_rng(3)
        byts = rng.integers(1, 256, size=(ezt.TILE * 2, ezt.TILE), dtype="uint8")
        ezt.cut_tiles(byts, tmp_path / "sp")
        got = decode((tmp_path / "sp" / "1_0.png").read_bytes())
        np.testing.assert_array_equal(got, byts[ezt.TILE:, :])


class TestThePyramid:
    """Coarser levels exist so that zooming out shows something rather than a
    line of text. They are built from sums and counts rather than from means,
    and that is the part worth testing: a mean of means is not the mean.
    """

    def test_a_level_is_the_mean_of_the_source_cells_it_covers(self):
        """The coast is where this goes wrong. A 512 m cell built from sixteen
        128 m cells is only right if the sixteen are weighted by how many 32 m
        cells each actually had - and at the coast they had very different
        numbers."""
        rng = np.random.default_rng(5)
        src = rng.random((32, 32)).astype("float32") * 100
        # A coastline: the left half of the top rows is sea.
        src[:16, :20] = np.nan
        sums = np.where(np.isfinite(src), src, 0.0).reshape(8, 4, 8, 4).sum(axis=(1, 3))
        counts = np.isfinite(src).reshape(8, 4, 8, 4).sum(axis=(1, 3)).astype("float32")

        s2, c2 = ezt.coarsen(sums, counts, 4)
        got = ezt.means(s2, c2)
        with warnings.catch_warnings():
            # The all-sea block, in the reference rather than in our code.
            warnings.simplefilter("ignore", RuntimeWarning)
            want = np.nanmean(src.reshape(2, 16, 2, 16), axis=(1, 3))
        np.testing.assert_allclose(got, want, rtol=1e-5)

    def test_a_mean_of_means_would_have_been_different(self):
        """Otherwise the test above passes against either implementation."""
        src = np.full((8, 8), np.nan, dtype="float32")
        src[0, 0] = 1.0                       # one cell in the first block
        src[4:, 4:] = 9.0                     # sixteen in the last
        sums = np.where(np.isfinite(src), src, 0.0).reshape(4, 2, 4, 2).sum(axis=(1, 3))
        counts = np.isfinite(src).reshape(4, 2, 4, 2).sum(axis=(1, 3)).astype("float32")
        exact = ezt.means(*ezt.coarsen(sums, counts, 4))[0, 0]
        naive = np.nanmean(ezt.means(sums, counts))
        assert abs(exact - np.nanmean(src)) < 1e-5
        assert abs(exact - naive) > 0.5, "the fixture cannot tell them apart"

    def test_an_all_sea_block_has_no_value(self):
        sums = np.zeros((4, 4), dtype="float32")
        counts = np.zeros((4, 4), dtype="float32")
        counts[0, 0] = 3.0
        sums[0, 0] = 6.0
        out = ezt.means(*ezt.coarsen(sums, counts, 2))
        assert out[0, 0] == pytest.approx(2.0)
        assert np.isnan(out[0, 1]) and np.isnan(out[1, 1])

    def test_trailing_partial_blocks_are_dropped_not_padded(self):
        """Padding would invent ground; the origin must not move either."""
        sums = np.ones((9, 11), dtype="float32")
        counts = np.ones((9, 11), dtype="float32")
        s2, c2 = ezt.coarsen(sums, counts, 4)
        assert s2.shape == (2, 2) and c2.shape == (2, 2)

    def test_the_levels_get_coarser_by_the_factor(self, surface):
        sums, counts, transform = ezt.downsample_sums(surface, factor=2)
        got = [ezt.level_meta(sums, counts, transform, ezt.LEVELS[0])["cell_m"]]
        for z in range(1, len(ezt.LEVELS)):
            step = ezt.LEVELS[z] // ezt.LEVELS[z - 1]
            sums, counts = ezt.coarsen(sums, counts, step)
            got.append(ezt.level_meta(sums, counts, transform, ezt.LEVELS[z])["cell_m"])
        assert got == [got[0] * ezt.LEVELS[z] / ezt.LEVELS[0]
                       for z in range(len(ezt.LEVELS))]

    def test_downsample_still_agrees_with_the_sums_it_now_uses(self, surface):
        """`downsample` is the old entry point and several callers still use it."""
        a, ta = ezt.downsample(surface, factor=4)
        sums, counts, tb = ezt.downsample_sums(surface, factor=4)
        np.testing.assert_allclose(a, ezt.means(sums, counts), equal_nan=True)
        assert ta == tb
