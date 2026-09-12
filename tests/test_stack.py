"""Tests for feature-stack assembly.

The sampler is where a silent half-cell shift would live. Points would still
land on the raster and still return plausible numbers, and nothing downstream
would complain - the model would simply be reading its features from slightly
the wrong place. So the tests use rasters whose correct answer at a given
coordinate can be written down by hand.
"""
import pathlib
import sys

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.features.stack import Design, RasterLayer, build, sample_layers

CELL = 100.0
WEST, NORTH = 1000.0, 5000.0


def write_raster(path, arrays, names=None, cell=CELL, west=WEST, north=NORTH):
    arrays = np.asarray(arrays, dtype="float32")
    if arrays.ndim == 2:
        arrays = arrays[None]
    profile = {
        "driver": "GTiff", "height": arrays.shape[1], "width": arrays.shape[2],
        "count": arrays.shape[0], "dtype": "float32", "crs": "EPSG:25833",
        "transform": from_origin(west, north, cell, cell), "nodata": np.nan,
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(arrays)
        for i, n in enumerate(names or [], start=1):
            ds.set_band_description(i, n)
    return str(path)


def centre(col, row, cell=CELL, west=WEST, north=NORTH):
    """The coordinate of a cell centre, so expectations can be exact."""
    return (west + (col + 0.5) * cell, north - (row + 0.5) * cell)


# --- nearest sampling ---------------------------------------------------------


def test_a_point_at_a_cell_centre_returns_that_cell(tmp_path):
    grid = np.arange(9, dtype="float32").reshape(3, 3)
    path = write_raster(tmp_path / "a.tif", grid, ["v"])
    coords = [centre(c, r) for r in range(3) for c in range(3)]
    X, names = sample_layers([RasterLayer(path)], coords)
    assert names == ["v"]
    assert np.allclose(X[:, 0], grid.ravel())


def test_the_sampler_is_not_off_by_a_cell(tmp_path):
    """One hot cell. Only the point inside it may come back hot."""
    grid = np.zeros((5, 5), dtype="float32")
    grid[2, 3] = 99.0
    path = write_raster(tmp_path / "hot.tif", grid)
    X, _ = sample_layers([RasterLayer(path)],
                         [centre(3, 2), centre(2, 2), centre(3, 1), centre(4, 2)])
    assert X[0, 0] == 99.0
    assert (X[1:, 0] == 0.0).all(), "a neighbour must not pick up the hot cell"


def test_a_point_outside_the_raster_is_nan_not_an_edge_value(tmp_path):
    path = write_raster(tmp_path / "a.tif", np.ones((3, 3), dtype="float32"))
    X, _ = sample_layers([RasterLayer(path)], [(-99999.0, 5000.0), (99999.0, 0.0)])
    assert np.isnan(X).all()


def test_bands_and_names_come_through_in_order(tmp_path):
    arrays = np.stack([np.full((2, 2), 1.0), np.full((2, 2), 2.0), np.full((2, 2), 3.0)])
    path = write_raster(tmp_path / "m.tif", arrays, ["a", "b", "c"])
    X, names = sample_layers([RasterLayer(path)], [centre(0, 0)])
    assert names == ["a", "b", "c"]
    assert list(X[0]) == [1.0, 2.0, 3.0]


def test_a_subset_of_bands_can_be_taken(tmp_path):
    arrays = np.stack([np.full((2, 2), 1.0), np.full((2, 2), 2.0), np.full((2, 2), 3.0)])
    path = write_raster(tmp_path / "m.tif", arrays, ["a", "b", "c"])
    X, names = sample_layers([RasterLayer(path, bands=[1, 3])], [centre(0, 0)])
    assert names == ["a", "c"]
    assert list(X[0]) == [1.0, 3.0]


def test_a_prefix_disambiguates_layers_sharing_a_band_name(tmp_path):
    p1 = write_raster(tmp_path / "1.tif", np.ones((2, 2), dtype="float32"), ["mean"])
    p2 = write_raster(tmp_path / "2.tif", np.ones((2, 2), dtype="float32"), ["mean"])
    _, names = sample_layers(
        [RasterLayer(p1, prefix="rain_"), RasterLayer(p2, prefix="temp_")],
        [centre(0, 0)])
    assert names == ["rain_mean", "temp_mean"]


# --- bilinear sampling --------------------------------------------------------


def test_bilinear_at_a_cell_centre_equals_that_cell(tmp_path):
    grid = np.arange(16, dtype="float32").reshape(4, 4)
    path = write_raster(tmp_path / "b.tif", grid)
    X, _ = sample_layers([RasterLayer(path, method="bilinear")], [centre(2, 1)])
    assert X[0, 0] == pytest.approx(grid[1, 2])


def test_bilinear_halfway_between_two_cells_is_their_mean(tmp_path):
    grid = np.array([[0.0, 10.0], [0.0, 10.0]], dtype="float32")
    path = write_raster(tmp_path / "b.tif", grid)
    x0, y0 = centre(0, 0)
    x1, _ = centre(1, 0)
    X, _ = sample_layers([RasterLayer(path, method="bilinear")], [((x0 + x1) / 2, y0)])
    assert X[0, 0] == pytest.approx(5.0)


def test_bilinear_reproduces_a_linear_ramp_exactly(tmp_path):
    """A plane is what bilinear interpolation is exact on; anything else is a bug."""
    cols = np.arange(6, dtype="float32")
    grid = np.tile(cols, (6, 1)) * 3.0
    path = write_raster(tmp_path / "ramp.tif", grid)
    x0, y0 = centre(1, 3)
    x1, _ = centre(2, 3)
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        X, _ = sample_layers([RasterLayer(path, method="bilinear")],
                             [(x0 + frac * (x1 - x0), y0)])
        assert X[0, 0] == pytest.approx(3.0 * (1 + frac), abs=1e-6)


def test_bilinear_ignores_a_nan_neighbour_instead_of_returning_nan(tmp_path):
    """At the edge of coverage, use the data that exists rather than giving up."""
    grid = np.array([[np.nan, 10.0], [np.nan, 10.0]], dtype="float32")
    path = write_raster(tmp_path / "edge.tif", grid)
    X, _ = sample_layers([RasterLayer(path, method="bilinear")], [centre(1, 0)])
    assert X[0, 0] == pytest.approx(10.0)


def test_bilinear_with_no_finite_neighbour_is_nan(tmp_path):
    grid = np.full((3, 3), np.nan, dtype="float32")
    path = write_raster(tmp_path / "empty.tif", grid)
    X, _ = sample_layers([RasterLayer(path, method="bilinear")], [centre(1, 1)])
    assert np.isnan(X[0, 0])


def test_an_unknown_method_is_rejected_at_construction():
    with pytest.raises(ValueError, match="unknown sampling method"):
        RasterLayer("x.tif", method="cubic")


# --- assembling the design matrix ---------------------------------------------


def test_presences_are_labelled_one_and_background_zero(tmp_path):
    path = write_raster(tmp_path / "a.tif", np.ones((4, 4), dtype="float32"), ["v"])
    d = build([centre(0, 0), centre(1, 1)], [centre(2, 2)], [RasterLayer(path)])
    assert d.n_presence == 2 and d.n_background == 1
    assert list(d.y) == [1, 1, 0]


def test_rows_with_a_missing_feature_are_dropped_not_imputed(tmp_path):
    """A mean-filled row looks exactly like a real average site to the model."""
    grid = np.ones((3, 3), dtype="float32")
    grid[0, 0] = np.nan
    path = write_raster(tmp_path / "gap.tif", grid, ["v"])
    d = build([centre(0, 0), centre(1, 1)], [centre(2, 2)], [RasterLayer(path)])
    assert d.n_presence == 1, "the point over the gap must be gone"
    assert np.isfinite(d.X).all()


def test_what_was_dropped_is_reported_per_class(tmp_path):
    """Uneven dropping changes what the AUC is measured against, invisibly."""
    grid = np.ones((3, 3), dtype="float32")
    grid[0, 0] = np.nan
    path = write_raster(tmp_path / "gap.tif", grid, ["v"])
    d = build([centre(0, 0)], [centre(1, 1), centre(2, 2)], [RasterLayer(path)])
    assert d.dropped["presence"] == 1
    assert d.dropped["background"] == 0
    assert d.dropped["presence_pct"] == pytest.approx(100.0)
    assert d.dropped["by_feature"]["v"] == 1


def test_coordinates_survive_alongside_the_dropped_rows(tmp_path):
    """Blocked CV needs the coordinates of exactly the rows that remain."""
    grid = np.ones((3, 3), dtype="float32")
    grid[0, 0] = np.nan
    path = write_raster(tmp_path / "gap.tif", grid, ["v"])
    d = build([centre(0, 0), centre(1, 0)], [centre(2, 2)], [RasterLayer(path)])
    assert len(d.coords) == len(d.y) == len(d.X)
    assert d.coords[0] == pytest.approx(np.array(centre(1, 0)))


def test_several_layers_concatenate_in_order(tmp_path):
    p1 = write_raster(tmp_path / "1.tif", np.full((3, 3), 7.0, dtype="float32"), ["a"])
    p2 = write_raster(tmp_path / "2.tif", np.full((3, 3), 9.0, dtype="float32"), ["b"])
    d = build([centre(1, 1)], [], [RasterLayer(p1), RasterLayer(p2)])
    assert d.names == ["a", "b"]
    assert list(d.X[0]) == [7.0, 9.0]


def test_an_empty_background_set_is_not_a_crash(tmp_path):
    path = write_raster(tmp_path / "a.tif", np.ones((3, 3), dtype="float32"))
    d = build([centre(0, 0)], [], [RasterLayer(path)])
    assert d.n_presence == 1 and d.n_background == 0


# --- integer rasters ----------------------------------------------------------


def write_int_raster(path, grid, nodata=-9999):
    grid = np.asarray(grid, dtype="int16")
    profile = {
        "driver": "GTiff", "height": grid.shape[0], "width": grid.shape[1],
        "count": 1, "dtype": "int16", "crs": "EPSG:25833",
        "transform": from_origin(WEST, NORTH, CELL, CELL), "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(grid[None])
    return str(path)


def test_an_integer_raster_with_nodata_is_sampled_as_nan(tmp_path):
    """SR16 ships int16 with nodata -9999.

    Filling before casting raises, which is survivable. The failure that is
    not survivable is a -9999 arriving in the model as a very negative tree
    volume, so this pins the behaviour at NaN.
    """
    grid = np.array([[10, -9999], [30, 40]])
    path = write_int_raster(tmp_path / "i.tif", grid)
    X, _ = sample_layers([RasterLayer(path, names=["v"])],
                         [centre(0, 0), centre(1, 0), centre(0, 1)])
    assert X[0, 0] == 10.0
    assert np.isnan(X[1, 0]), "nodata must be NaN, never its sentinel value"
    assert X[2, 0] == 30.0


def test_bilinear_on_an_integer_raster_ignores_the_sentinel(tmp_path):
    grid = np.array([[-9999, 100], [-9999, 100]])
    path = write_int_raster(tmp_path / "i2.tif", grid)
    X, _ = sample_layers([RasterLayer(path, method="bilinear")], [centre(1, 0)])
    assert X[0, 0] == pytest.approx(100.0), "the sentinel must not drag the mean down"


# --- meaningful absence (SR16) ------------------------------------------------


def test_missing_can_be_encoded_as_a_value_with_an_indicator(tmp_path):
    """SR16 absence means 'not productive forest', not 'measurement failed'.

    Zero volume is physically true of a lake or a mountainside. The indicator
    is what stops that being a lie: without it the model cannot distinguish an
    unforested cell from a measured stand carrying no volume.
    """
    grid = np.array([[5.0, np.nan], [0.0, 7.0]], dtype="float32")
    path = write_raster(tmp_path / "sr.tif", grid, ["vol"])
    layer = RasterLayer(path, missing_as=0.0, missing_indicator=True)
    X, names = sample_layers([layer], [centre(0, 0), centre(1, 0), centre(0, 1)])
    assert names == ["vol_missing", "vol"]
    assert list(X[:, 1]) == [5.0, 0.0, 0.0], "absence becomes zero"
    assert list(X[:, 0]) == [0.0, 1.0, 0.0], "and only true absence is flagged"


def test_a_measured_zero_is_not_flagged_as_missing(tmp_path):
    grid = np.array([[0.0, np.nan]], dtype="float32")
    path = write_raster(tmp_path / "z.tif", grid, ["v"])
    X, names = sample_layers([RasterLayer(path, missing_as=0.0, missing_indicator=True)],
                            [centre(0, 0), centre(1, 0)])
    assert list(X[:, 1]) == [0.0, 0.0], "both read zero"
    assert list(X[:, 0]) == [0.0, 1.0], "but only one of them is absent"


def test_rows_with_encoded_absence_are_not_dropped(tmp_path):
    """The whole point: 19% of the pool is non-forest and must survive."""
    grid = np.array([[np.nan, np.nan], [1.0, 2.0]], dtype="float32")
    path = write_raster(tmp_path / "sr.tif", grid, ["vol"])
    layer = RasterLayer(path, missing_as=0.0, missing_indicator=True)
    d = build([centre(0, 0), centre(1, 0)], [centre(0, 1)], [layer])
    assert d.n_presence == 2, "non-forest presences must be kept, not dropped"
    assert d.dropped["presence"] == 0


def test_missing_without_an_encoding_still_drops(tmp_path):
    """The default stays strict; encoding absence has to be asked for."""
    grid = np.array([[np.nan, 1.0]], dtype="float32")
    path = write_raster(tmp_path / "s.tif", grid, ["v"])
    d = build([centre(0, 0), centre(1, 0)], [], [RasterLayer(path)])
    assert d.n_presence == 1


def test_an_indicator_without_a_fill_value_is_rejected():
    with pytest.raises(ValueError, match="needs missing_as"):
        RasterLayer("x.tif", missing_indicator=True)


# --- categorical layers -------------------------------------------------------


def test_a_categorical_layer_becomes_one_hot_columns(tmp_path):
    grid = np.array([[1.0, 2.0], [3.0, np.nan]], dtype="float32")
    path = write_raster(tmp_path / "t.tif", grid, ["treslag"])
    layer = RasterLayer(path, categories=[1, 2, 3], missing_as=0.0,
                        missing_indicator=True)
    X, names = sample_layers([layer], [centre(0, 0), centre(1, 0),
                                       centre(0, 1), centre(1, 1)])
    assert names == ["treslag_1", "treslag_2", "treslag_3", "treslag_missing"]
    assert list(X[0]) == [1.0, 0.0, 0.0, 0.0]
    assert list(X[1]) == [0.0, 1.0, 0.0, 0.0]
    assert list(X[2]) == [0.0, 0.0, 1.0, 0.0]
    assert list(X[3]) == [0.0, 0.0, 0.0, 1.0], "absent is its own category"


def test_one_hot_rows_are_never_dropped_for_being_absent(tmp_path):
    grid = np.array([[np.nan, 1.0]], dtype="float32")
    path = write_raster(tmp_path / "t.tif", grid, ["treslag"])
    layer = RasterLayer(path, categories=[1, 2, 3], missing_as=0.0,
                        missing_indicator=True)
    d = build([centre(0, 0), centre(1, 0)], [], [layer])
    assert d.n_presence == 2
    assert np.isfinite(d.X).all()


def test_a_categorical_layer_refuses_interpolation():
    """Halfway between spruce and pine is not a tree species."""
    with pytest.raises(ValueError, match="cannot be interpolated"):
        RasterLayer("x.tif", categories=[1, 2], method="bilinear")


def test_category_labels_name_the_one_hot_columns(tmp_path):
    """A ten-class substrate layer must not report as losmasse_1 ... losmasse_10.

    The permutation-importance table is the main diagnostic in this project.
    Column names that require a lookup make it harder to notice the thing it
    exists to expose - a model leaning on a substrate class that is really a
    proxy for where people live.
    """
    grid = np.array([[1.0, 2.0], [3.0, np.nan]], dtype="float32")
    path = write_raster(tmp_path / "t.tif", grid, ["losmasse"])
    layer = RasterLayer(path, categories=[1, 2, 3], missing_as=0.0,
                        missing_indicator=True,
                        category_labels=["moraine", "peat", "bedrock_bare"])
    X, names = sample_layers([layer], [centre(0, 0), centre(1, 0),
                                       centre(0, 1), centre(1, 1)])
    assert names == ["losmasse_moraine", "losmasse_peat",
                     "losmasse_bedrock_bare", "losmasse_missing"]
    assert list(X[0]) == [1.0, 0.0, 0.0, 0.0]
    assert list(X[3]) == [0.0, 0.0, 0.0, 1.0]


def test_labels_map_to_categories_positionally_not_by_value(tmp_path):
    """Category codes need not be contiguous or sorted; labels follow position."""
    grid = np.array([[5.0, 9.0]], dtype="float32")
    path = write_raster(tmp_path / "t.tif", grid, ["k"])
    layer = RasterLayer(path, categories=[9, 5], category_labels=["nine", "five"])
    X, names = sample_layers([layer], [centre(0, 0), centre(1, 0)])
    assert names == ["k_nine", "k_five"]
    assert list(X[0]) == [0.0, 1.0], "the cell holding 5 is 'five'"
    assert list(X[1]) == [1.0, 0.0]


def test_unlabelled_categories_still_fall_back_to_the_code(tmp_path):
    grid = np.array([[1.0, 2.0]], dtype="float32")
    path = write_raster(tmp_path / "t.tif", grid, ["treslag"])
    layer = RasterLayer(path, categories=[1, 2])
    _, names = sample_layers([layer], [centre(0, 0)])
    assert names == ["treslag_1", "treslag_2"]


def test_wrong_number_of_labels_is_rejected_at_construction():
    with pytest.raises(ValueError, match="2 labels for 3 categories"):
        RasterLayer("x.tif", categories=[1, 2, 3], category_labels=["a", "b"])


def test_labels_without_categories_are_rejected():
    with pytest.raises(ValueError, match="needs categories"):
        RasterLayer("x.tif", category_labels=["a"])
