"""Test tile disk caching in tee-compute server."""

import tempfile
from pathlib import Path

import numpy as np
import pytest


def test_tile_cache_roundtrip():
    """Save a tile to disk cache and load it back."""
    from tessera_eval.server import _save_tile_to_cache, _load_cached_tile, _tile_disk_cache_dir
    import tessera_eval.server as srv

    # Use a temp directory
    old_dir = srv._tile_disk_cache_dir
    try:
        srv._tile_disk_cache_dir = Path(tempfile.mkdtemp()) / "tiles"

        # Create fake tile data
        emb = np.random.randn(100, 100, 128).astype(np.float32)
        crs = "EPSG:32630"
        transform = [10.0, 0.0, 500000.0, 0.0, -10.0, 6000000.0]

        # Save
        _save_tile_to_cache(2024, -2.85, 54.45, emb, crs, transform)

        # Verify file exists
        cache_path = srv._tile_disk_cache_dir / "2024_-2.85_54.45.npz"
        assert cache_path.exists(), "Cache file not created"

        # Load
        result = _load_cached_tile(2024, -2.85, 54.45)
        assert result is not None, "Cache miss on saved tile"

        loaded_emb, loaded_crs, loaded_transform = result
        assert loaded_emb.shape == emb.shape
        np.testing.assert_allclose(loaded_emb, emb, atol=1e-6)
        assert str(loaded_crs) == crs
    finally:
        srv._tile_disk_cache_dir = old_dir


def test_tile_cache_miss():
    """Loading a non-existent tile returns None."""
    from tessera_eval.server import _load_cached_tile, _tile_disk_cache_dir
    import tessera_eval.server as srv

    old_dir = srv._tile_disk_cache_dir
    try:
        srv._tile_disk_cache_dir = Path(tempfile.mkdtemp()) / "tiles"
        result = _load_cached_tile(2099, 0.0, 0.0)
        assert result is None
    finally:
        srv._tile_disk_cache_dir = old_dir
