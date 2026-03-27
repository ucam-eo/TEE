"""Test rasterize_shapefile with a pre-fitted LabelEncoder (Fix 1)."""

import numpy as np
import pytest
from affine import Affine
from sklearn.preprocessing import LabelEncoder
import geopandas as gpd
from shapely.geometry import box

from tessera_eval.rasterize import rasterize_shapefile


@pytest.fixture
def sample_gdf():
    """GDF with three habitat types covering a small grid."""
    return gpd.GeoDataFrame({
        "geometry": [
            box(0, 0, 5, 5),
            box(5, 0, 10, 5),
        ],
        "habitat": ["grassland", "woodland"],
    }, crs="EPSG:4326")


def test_prefitted_encoder_matches_ordering(sample_gdf):
    """A pre-fitted encoder whose classes are a superset still produces
    correct 1-based IDs that match the encoder's ordering."""
    le = LabelEncoder()
    # Superset: includes 'wetland' which is NOT in the GDF
    le.fit(["grassland", "wetland", "woodland"])

    transform = Affine(1, 0, 0, 0, -1, 10)  # 1 px = 1 unit

    raster = rasterize_shapefile(
        sample_gdf, "habitat", transform, width=10, height=10, label_encoder=le,
    )

    # Encoder ordering: grassland=0, wetland=1, woodland=2
    # 1-based: grassland=1, wetland=2, woodland=3
    grassland_id = le.transform(["grassland"])[0] + 1  # 1
    woodland_id = le.transform(["woodland"])[0] + 1  # 3

    # Left half (x 0-5) should be grassland
    assert raster[5, 2] == grassland_id
    # Right half (x 5-10) should be woodland
    assert raster[5, 7] == woodland_id
    # No wetland pixels should exist
    wetland_id = le.transform(["wetland"])[0] + 1  # 2
    assert wetland_id not in raster


def test_without_encoder_fits_locally(sample_gdf):
    """Without a pre-fitted encoder the function fits its own (backward-compat)."""
    transform = Affine(1, 0, 0, 0, -1, 10)
    raster = rasterize_shapefile(sample_gdf, "habitat", transform, width=10, height=10)
    # Should have exactly two class IDs plus 0 (nodata at edges if any)
    unique = set(np.unique(raster)) - {0}
    assert unique == {1, 2}
