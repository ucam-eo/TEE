"""Tests for save_vectors_rvq's viewport-bounds crop (crop_window).

A 5km viewport that straddles a ~0.1deg embedding tile boundary pulls a 2x2
tile block from the provider -- several times the pixels the ROI needs.
process_year() clips the reconstructed mosaic to the viewport bounds;
save_vectors_rvq takes the same window and slices its assembled per-pixel
index grid to match, while keeping the tile grid + codebooks at the full
mosaic shape. crop_offset in vq_metadata.json lets the browser map a
cropped-local pixel back to its global position for the tile lookup.
"""

from __future__ import annotations

import gzip
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from affine import Affine

from process_viewport import _assemble_indices_from_tiles, save_vectors_rvq


def _load_npy_gz(path):
    with gzip.open(path, "rb") as f:
        return np.load(io.BytesIO(f.read()))


def _make_qs(*, full_h, full_w, t, k1, k2, dim, seed=0):
    """Minimal QuantizedStructure stand-in with real arrays."""
    from tessera_vq.client import n_tiles_along

    rows, cols = n_tiles_along(full_h, t), n_tiles_along(full_w, t)
    n_tiles = rows * cols
    rng = np.random.default_rng(seed)
    positions = np.array([(r, c) for r in range(rows) for c in range(cols)], dtype=np.int32)
    is_rvq = k2 is not None
    return SimpleNamespace(
        tile_size=t,
        mosaic_shape=(full_h, full_w),
        positions=positions,
        k1=k1,
        k2=k2 if is_rvq else None,
        metric="euclidean",
        codebooks1=rng.standard_normal((n_tiles, k1, dim)).astype(np.float32),
        codebooks2=(rng.standard_normal((n_tiles, k2, dim)).astype(np.float32) if is_rvq else None),
        indices1=rng.integers(0, k1, size=(n_tiles, t, t), dtype=np.uint8),
        indices2=(rng.integers(0, k2, size=(n_tiles, t, t), dtype=np.uint8) if is_rvq else None),
    )


IDENTITY = Affine(1e-4, 0.0, 100.0, 0.0, -1e-4, 10.0)


@pytest.mark.parametrize("window", [
    (0, 0, 40, 44),      # whole mosaic -> no-op crop
    (7, 11, 33, 39),     # interior, tile-unaligned both axes
    (0, 0, 20, 22),      # top-left corner
    (25, 30, 40, 44),    # bottom-right, includes the pulled-back last tile
])
def test_indices_sliced_to_window_and_metadata_matches(tmp_path, window):
    full_h, full_w, t, k1, k2, dim = 40, 44, 16, 20, 64, 8
    qs = _make_qs(full_h=full_h, full_w=full_w, t=t, k1=k1, k2=k2, dim=dim, seed=3)
    r0, c0, r1, c1 = window

    save_vectors_rvq(qs, IDENTITY, "vp", 2024, tmp_path, crop_window=window)

    meta = json.loads((tmp_path / "vq_metadata.json").read_text())
    assert meta["mosaic_shape"] == [full_h, full_w], "tile grid reference stays full"
    assert meta["output_shape"] == [r1 - r0, c1 - c0], "shipped grid is the crop window"
    assert meta["crop_offset"] == [r0, c0]
    assert meta["num_total_pixels"] == (r1 - r0) * (c1 - c0)
    # n_tile_rows/cols are for the FULL grid (codebooks keep every tile)
    from tessera_vq.client import n_tiles_along
    assert meta["n_tile_rows"] == n_tiles_along(full_h, t)
    assert meta["n_tile_cols"] == n_tiles_along(full_w, t)

    full_idx1 = _assemble_indices_from_tiles(qs, "indices1", full_h, full_w)
    got_idx1 = _load_npy_gz(tmp_path / "indices1.npy.gz")
    assert got_idx1.shape == (r1 - r0, c1 - c0)
    np.testing.assert_array_equal(got_idx1, full_idx1[r0:r1, c0:c1])

    full_idx2 = _assemble_indices_from_tiles(qs, "indices2", full_h, full_w)
    got_idx2 = _load_npy_gz(tmp_path / "indices2.npy.gz")
    np.testing.assert_array_equal(got_idx2, full_idx2[r0:r1, c0:c1])

    # Codebooks are untouched by the crop -- every tile still shipped.
    cb1 = _load_npy_gz(tmp_path / "codebooks1_uint8.npy.gz")
    assert cb1.shape[0] == qs.codebooks1.shape[0]


def test_no_crop_window_is_identical_to_full_output(tmp_path):
    """Omitting crop_window == passing the whole-mosaic window: format unchanged
    for callers (and existing viewports) that don't clip."""
    full_h, full_w, t, k1, k2, dim = 30, 30, 16, 20, 64, 8
    qs = _make_qs(full_h=full_h, full_w=full_w, t=t, k1=k1, k2=k2, dim=dim, seed=9)

    a = tmp_path / "a"
    b = tmp_path / "b"
    save_vectors_rvq(qs, IDENTITY, "vp", 2024, a)
    save_vectors_rvq(qs, IDENTITY, "vp", 2024, b, crop_window=(0, 0, full_h, full_w))

    ma = json.loads((a / "vq_metadata.json").read_text())
    mb = json.loads((b / "vq_metadata.json").read_text())
    assert ma["output_shape"] == mb["output_shape"] == [full_h, full_w]
    assert ma["output_shape"] == ma["mosaic_shape"]
    assert ma["crop_offset"] == [0, 0]
    np.testing.assert_array_equal(
        _load_npy_gz(a / "indices1.npy.gz"), _load_npy_gz(b / "indices1.npy.gz")
    )


def test_vq_single_stage_crop(tmp_path):
    """k2=None (plain VQ): only indices1, no crash, window applied."""
    full_h, full_w, t, k1, dim = 24, 28, 8, 16, 8
    qs = _make_qs(full_h=full_h, full_w=full_w, t=t, k1=k1, k2=None, dim=dim, seed=1)
    save_vectors_rvq(qs, IDENTITY, "vp", 2024, tmp_path, crop_window=(2, 3, 20, 25))

    meta = json.loads((tmp_path / "vq_metadata.json").read_text())
    assert meta["kind"] == "vq"
    assert meta["output_shape"] == [18, 22]
    assert meta["crop_offset"] == [2, 3]
    assert not (tmp_path / "indices2.npy.gz").exists()
    got = _load_npy_gz(tmp_path / "indices1.npy.gz")
    full_idx1 = _assemble_indices_from_tiles(qs, "indices1", full_h, full_w)
    np.testing.assert_array_equal(got, full_idx1[2:20, 3:25])
