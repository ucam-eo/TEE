"""Test result disk caching in tee-compute server."""

import tempfile
from pathlib import Path

import numpy as np
import pytest


def test_result_cache_roundtrip():
    """Save evaluation results to disk cache and load them back."""
    import tessera_eval.server as srv
    import geopandas as gpd
    from shapely.geometry import box

    old_dir = srv._tile_disk_cache_dir
    try:
        srv._tile_disk_cache_dir = Path(tempfile.mkdtemp())

        # Create a simple GDF for hashing
        gdf = gpd.GeoDataFrame({
            "geometry": [box(0, 0, 1, 1)] * 5,
            "habitat": ["woodland"] * 5,
        }, crs="EPSG:4326")

        vectors = np.random.randn(1000, 128).astype(np.float32)
        labels = np.random.randint(0, 5, size=1000).astype(np.int32)
        class_names = ["a", "b", "c", "d", "e"]
        stats = {"tile_count": 4, "tiles_with_data": 3, "total_pixels": 1000, "n_classes": 5}

        # Save
        srv._save_cached_result("habitat", 2024, gdf, vectors, labels, class_names, stats)

        # Load
        result = srv._load_cached_result("habitat", 2024, gdf)
        assert result is not None, "Cache miss on saved result"

        loaded_vectors, loaded_labels, loaded_names, loaded_stats = result
        np.testing.assert_array_equal(loaded_vectors, vectors)
        np.testing.assert_array_equal(loaded_labels, labels)
        assert loaded_names == class_names
        assert loaded_stats["tile_count"] == 4

    finally:
        srv._tile_disk_cache_dir = old_dir


def test_result_cache_miss():
    """Loading a non-existent result returns None."""
    import tessera_eval.server as srv
    import geopandas as gpd
    from shapely.geometry import box

    old_dir = srv._tile_disk_cache_dir
    try:
        srv._tile_disk_cache_dir = Path(tempfile.mkdtemp())

        gdf = gpd.GeoDataFrame({
            "geometry": [box(0, 0, 1, 1)],
            "habitat": ["x"],
        }, crs="EPSG:4326")

        result = srv._load_cached_result("nonexistent", 2099, gdf)
        assert result is None

    finally:
        srv._tile_disk_cache_dir = old_dir
