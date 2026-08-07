"""Tests for process_viewport.py's _assemble_indices_from_tiles: the tile-drop
fix (tessera-vq >=0.6.0's pulled-back last tile, not a dropped remainder strip).

See save_vectors_rvq's comment in process_viewport.py for the bug this
closes: a mosaic not an exact multiple of tile_size used to silently lose
its last row/col of real, finite-valued embedding data.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from process_viewport import _assemble_indices_from_tiles


def _stub_qs(positions, tile_size, indices):
    """Minimal stand-in for the fields _assemble_indices_from_tiles reads off
    a tessera_vq.client.QuantizedStructure -- avoids constructing a real one."""
    return SimpleNamespace(
        positions=np.asarray(positions, dtype=np.int32),
        tile_size=tile_size,
        indices1=indices,
    )


def test_assemble_indices_exact_multiple_matches_plain_stride() -> None:
    """No remainder: behaves exactly like the old r*t/c*t placement."""
    t = 16
    idx = np.stack(
        [np.full((t, t), v, dtype=np.uint8) for v in (1, 2, 3, 4)]
    )  # 4 tiles, distinct fill values
    qs = _stub_qs(positions=[(0, 0), (0, 1), (1, 0), (1, 1)], tile_size=t, indices=idx)
    full = _assemble_indices_from_tiles(qs, "indices1", out_h=32, out_w=32)
    assert full.shape == (32, 32)
    assert (full[0:16, 0:16] == 1).all()
    assert (full[0:16, 16:32] == 2).all()
    assert (full[16:32, 0:16] == 3).all()
    assert (full[16:32, 16:32] == 4).all()


def test_assemble_indices_covers_remainder_tile_not_dropped() -> None:
    """A genuine remainder (out_h not a multiple of t): the last row of tiles
    is included, pulled back to end exactly at out_h -- not silently dropped
    the way plain r*t placement (with an out-of-bounds guard) used to."""
    t = 16
    # 3 row-tiles (ceil(33/16)=3), 1 col-tile (32/16=2 exact -> no remainder there).
    idx = np.stack(
        [np.full((t, t), v, dtype=np.uint8) for v in (1, 2, 3, 10, 20, 30)]
    )  # (row, col) fill: (0,0)=1 (0,1)=2 (1,0)=3 (1,1)=10 (2,0)=20 (2,1)=30
    positions = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1)]
    qs = _stub_qs(positions=positions, tile_size=t, indices=idx)
    full = _assemble_indices_from_tiles(qs, "indices1", out_h=33, out_w=32)
    assert full.shape == (33, 32)
    # Regular row-tile 0 placed at plain r*t: [0, 16).
    assert (full[0:16, 0:16] == 1).all()
    # Regular row-tile 1 at [16, 32) -- but the pulled-back last tile ([17, 33),
    # written after it in position order) overwrites rows [17, 32) of the
    # overlap, so only row 16 still shows tile 1's value.
    assert (full[16:17, 0:16] == 3).all()
    # Last row-tile (index 2) pulled back to end exactly at out_h=33 -> [17, 33).
    assert (full[17:33, 0:16] == 20).all()
    assert (full[17:33, 16:32] == 30).all()
    # No row is left at the zero-fill default (nothing silently dropped).
    assert not (full == 0).any()


def test_assemble_indices_last_tile_overlap_resolves_to_last_position() -> None:
    """Where the pulled-back last tile overlaps the regular second-to-last
    tile, the later position in `positions` wins (later numpy assignment
    overwrites) -- matching the server's own per-pixel index assembly, so the
    two stay in agreement (see vq_reconstruct.js::tileIndexForPixel, which
    inverts this same "last position wins" rule client-side)."""
    t = 16
    idx = np.stack([np.full((t, t), 1, dtype=np.uint8), np.full((t, t), 2, dtype=np.uint8)])
    qs = _stub_qs(positions=[(0, 0), (1, 0)], tile_size=t, indices=idx)
    full = _assemble_indices_from_tiles(qs, "indices1", out_h=17, out_w=16)
    # n_tile_rows = ceil(17/16) = 2; last tile (idx 1) offset = 17-16 = 1.
    # Regular tile 0 at rows [0,16); last tile at rows [1,17) -- overlap [1,16).
    assert (full[0:1, :] == 1).all()  # only tile 0 covers row 0
    assert (full[1:17, :] == 2).all()  # overlap + true edge: tile 1 (written later) wins


@pytest.mark.parametrize("attr_name", ["indices1", "indices2"])
def test_assemble_indices_reads_requested_attr(attr_name: str) -> None:
    """Reads whichever attribute name is requested (indices1 vs indices2 for RVQ)."""
    t = 8
    idx = np.stack([np.full((t, t), 7, dtype=np.uint8)])
    qs = SimpleNamespace(
        positions=np.asarray([(0, 0)], dtype=np.int32),
        tile_size=t,
        **{attr_name: idx},
    )
    full = _assemble_indices_from_tiles(qs, attr_name, out_h=8, out_w=8)
    assert (full == 7).all()
