#!/usr/bin/env python3
"""
Single-script pipeline: download tiles + pyramids + vectors per year.

Replaces download_embeddings.py, create_rgb_embeddings.py, and extract_vectors.py.
Calls fetch_mosaic_for_region once per year, then produces all outputs in memory
with zero intermediate GeoTIFF files.

Usage:
    python process_viewport.py --years 2024,2025
    python process_viewport.py                    # all years 2018-2025
"""

import sys
import os
import gc
import gzip
import json
import time as _time
import traceback
import argparse
import logging
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

# Force unbuffered stdout so pipeline can stream lines in real-time
sys.stdout.reconfigure(line_buffering=True)

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    import numpy as np
    from affine import Affine
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

try:
    from lib.viewport_utils import get_active_viewport, get_viewport_vq_config
    from lib.progress_tracker import ProgressTracker
    from lib.config import EMBEDDINGS_DIR, PYRAMIDS_DIR, VECTORS_DIR, pyramid_exists
except ImportError as e:
    print(f"LIB IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger(__name__)

DEFAULT_YEARS = range(2018, 2026)
EMBEDDING_DIM = 128
NUM_ZOOM_LEVELS = 6

# ---------- Module-level caches ----------

_provider_instance = None  # cached VQTessera client for this process
_provider_kind = None      # always 'vqtessera' now; kept for the log lines below


def _get_provider(viewport_name):
    """Return a cached embeddings client for this process.

    VQTessera (the tessera-vq bolt-on) is now the standard embeddings client
    for every viewport -- it has replaced GeoTessera as the origin fetch path,
    not just an opt-in accelerator. This is what lets save_vectors_rvq's dual
    write (``if qs is not None``) produce the compact browser bundle for every
    viewport unconditionally, instead of only ones that opted into the fast
    path. A viewport's stored ``_config.json`` still supplies custom t/k/k2/m
    if tuned at creation time; otherwise the study-recommended RVQ defaults
    apply (t=512, k1=20, k2=256, L2).

    process_viewport.py handles exactly one viewport per invocation, so a
    cached singleton avoids re-constructing the client per year.
    """
    global _provider_instance, _provider_kind
    if _provider_instance is not None:
        return _provider_instance

    t0 = _time.monotonic()
    from tessera_vq.client import VQTessera
    vq = get_viewport_vq_config(viewport_name) if viewport_name else None
    if vq is None:
        vq = {
            't':  int(os.environ.get("TESSERA_VQ_DEFAULT_T", "512")),
            'k':  int(os.environ.get("TESSERA_VQ_DEFAULT_K", "20")),
            'k2': int(os.environ.get("TESSERA_VQ_DEFAULT_K2", "256")),
            'm':  os.environ.get("TESSERA_VQ_DEFAULT_M", "euclidean"),
        }
    # Public, rate-limited gateway by default -- correct for local dev and any
    # deployment not co-located with the bolt-on. The co-located production
    # container overrides this to the direct backdoor (see deploy/README.md).
    url = os.environ.get("TESSERA_VQ_URL", "http://tee.cl.cam.ac.uk:8000")
    timeout = float(os.environ.get("TESSERA_VQ_TIMEOUT_SECONDS", "300"))
    _provider_instance = VQTessera(
        server_url=url, t=vq['t'], k=vq['k'], m=vq['m'],
        timeout=timeout, k2=vq['k2'],
    )
    _provider_kind = 'vqtessera'
    logger.info(
        "VQTessera initialized in %.1fs (t=%d k=%d k2=%s m=%s url=%s)",
        _time.monotonic() - t0, vq['t'], vq['k'], vq['k2'], vq['m'], url,
    )
    return _provider_instance


# ---------- NPY mosaic fetching ----------


def _fetch_mosaic_npy(tessera, bounds, year, progress_fn=None):
    """Fetch mosaic via the NPY path (GeoTessera.fetch_mosaic_for_region).

    Wraps the existing threaded-download logic with download-progress reporting.
    Returns (mosaic, transform, crs).
    """
    import threading as _threading

    t0 = _time.monotonic()

    # Estimate download size
    expected_mb = 0
    try:
        tiles_needed = tessera.registry.load_blocks_for_region(bounds, year)
        reg = tessera.registry._registry_gdf
        for _, tile_lon, tile_lat in tiles_needed:
            match = reg[(reg['lon'] == tile_lon) & (reg['lat'] == tile_lat) & (reg['year'] == year)]
            if len(match) > 0:
                row = match.iloc[0]
                expected_mb += (row.get('file_size', 0) + row.get('scales_size', 0)) / (1024 * 1024)
        if expected_mb > 0:
            print(f"    Expected download: {expected_mb:.1f} MB ({len(tiles_needed)} tiles)")
    except Exception:
        pass

    if progress_fn:
        progress_fn(1, f"Fetching {expected_mb:.0f} MB..." if expected_mb > 0 else "Fetching mosaic...")

    _fetch_result = [None, None, None, None]  # mosaic, transform, crs, error
    _fetch_status = [None]

    def _do_fetch():
        try:
            def _gt_progress(current, total, status):
                _fetch_status[0] = f"{status} ({current}/{total})"
            # progress_callback is GeoTessera-only; VQTessera's
            # fetch_mosaic_for_region doesn't accept it. Feature-detect so the
            # same call path serves both clients.
            kwargs = dict(bbox=bounds, year=year,
                          target_crs='EPSG:4326', auto_download=True)
            import inspect as _inspect
            try:
                params = _inspect.signature(tessera.fetch_mosaic_for_region).parameters
                if 'progress_callback' in params:
                    kwargs['progress_callback'] = _gt_progress
            except (TypeError, ValueError):
                pass
            m, t, c = tessera.fetch_mosaic_for_region(**kwargs)
            _fetch_result[:3] = [m, t, c]
        except Exception as ex:
            _fetch_result[3] = ex

    def _dir_size(path):
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat().st_size
                elif entry.is_dir(follow_symlinks=False):
                    total += _dir_size(entry.path)
        except OSError:
            pass
        return total

    size_before = _dir_size(str(EMBEDDINGS_DIR))
    ft = _threading.Thread(target=_do_fetch, daemon=True)
    ft.start()
    data_started = False

    while ft.is_alive():
        ft.join(timeout=2)
        if ft.is_alive():
            downloaded_mb = (_dir_size(str(EMBEDDINGS_DIR)) - size_before) / (1024 * 1024)
            elapsed = _time.monotonic() - t0
            if downloaded_mb > 0.1:
                if not data_started:
                    data_started = True
                    print(f"    Download started after {elapsed:.0f}s")
                speed_mbs = downloaded_mb / elapsed if elapsed > 0 else 0
                if progress_fn:
                    if expected_mb > 0:
                        dl_pct = min(55, int(55 * downloaded_mb / expected_mb))
                        progress_fn(max(1, dl_pct),
                                    f"Downloading: {downloaded_mb:.1f}/{expected_mb:.0f} MB "
                                    f"({int(100*downloaded_mb/expected_mb)}%, {speed_mbs:.1f} MB/s)")
                    else:
                        progress_fn(1, f"Downloading: {downloaded_mb:.1f} MB ({speed_mbs:.1f} MB/s)")
            else:
                gt_status = _fetch_status[0]
                if progress_fn:
                    if gt_status:
                        progress_fn(1, gt_status)
                    else:
                        progress_fn(1, f"Waiting for tiles ({elapsed:.0f}s)")

    if _fetch_result[3] is not None:
        raise _fetch_result[3]

    return _fetch_result[0], _fetch_result[1], _fetch_result[2]


# ---------- Pyramid helpers (ported from create_pyramids.py) ----------

def percentile_normalize(band_data):
    """Normalize float32 band to uint8 using 2nd-98th percentile.

    Args:
        band_data: (H, W) float32 array

    Returns:
        (H, W) uint8 array
    """
    valid = band_data[~np.isnan(band_data)]
    if len(valid) == 0:
        return np.zeros_like(band_data, dtype=np.uint8)
    p2, p98 = np.percentile(valid, [2, 98])
    clipped = np.clip(band_data, p2, p98)
    if p98 - p2 == 0:
        return np.zeros_like(band_data, dtype=np.uint8)
    return ((clipped - p2) / (p98 - p2) * 255).astype(np.uint8)


def write_pyramid_levels(rgb, transform, crs, output_dir):
    """Write PNG pyramid levels + pyramid_meta.json.

    Args:
        rgb: (3, H, W) uint8 array (native resolution, no upscale)
        transform: Affine transform for the image
        crs: CRS string
        output_dir: Path to year-specific pyramids directory
    """
    from PIL import Image as PILImage

    output_dir.mkdir(parents=True, exist_ok=True)

    _, source_height, source_width = rgb.shape

    # Level 0: write full-resolution PNG
    level_0_path = output_dir / "level_0.png"
    img_0 = PILImage.fromarray(np.transpose(rgb, (1, 2, 0)), mode='RGB')
    img_0.save(level_0_path, format='PNG')

    size_kb = level_0_path.stat().st_size / 1024
    print(f"    Level 0: {source_width}x{source_height} @ 10m/pixel ({size_kb:.1f} KB)")

    def _transform_dict(t):
        return {"a": t.a, "b": t.b, "c": t.c, "d": t.d, "e": t.e, "f": t.f}

    meta = {
        "crs": str(crs),
        "levels": [{
            "file": "level_0.png",
            "width": source_width,
            "height": source_height,
            "transform": _transform_dict(transform),
        }],
    }

    # Create downsampled levels 1-5 (halve dimensions each level)
    for level in range(1, NUM_ZOOM_LEVELS):
        lw = max(1, source_width >> level)
        lh = max(1, source_height >> level)

        level_img = img_0.resize((lw, lh), PILImage.NEAREST)
        level_path = output_dir / f"level_{level}.png"
        level_img.save(level_path, format='PNG')

        # Transform: scale pixel size to match reduced resolution
        level_transform = transform * Affine.scale(
            source_width / lw, source_height / lh
        )

        meta["levels"].append({
            "file": f"level_{level}.png",
            "width": lw,
            "height": lh,
            "transform": _transform_dict(level_transform),
        })

        spatial_scale = 10 * (2 ** level)
        size_kb = level_path.stat().st_size / 1024
        print(f"    Level {level}: {lw}x{lh} @ {spatial_scale}m/pixel ({size_kb:.1f} KB)")

    # Write metadata
    with open(output_dir / "pyramid_meta.json", 'w') as f:
        json.dump(meta, f, indent=2)

    print(f"  Created {NUM_ZOOM_LEVELS} pyramid levels in {output_dir}")


# ---------- Vector helpers (ported from extract_vectors.py) ----------

def save_vectors(quantized, coords, dim_min, dim_max, transform, height, width,
                 viewport_id, year, output_dir, dataset_version=None):
    """Save quantized vectors, coordinates, and metadata.

    Args:
        quantized: (N, 128) uint8 array
        coords: (N, 2) int32 array of (x, y) pixel coordinates
        dim_min: (128,) float64 per-dimension min
        dim_max: (128,) float64 per-dimension max
        transform: rasterio Affine transform
        height: mosaic height in pixels
        width: mosaic width in pixels
        viewport_id: viewport name string
        year: year int
        output_dir: Path to year-specific vectors directory
        dataset_version: Tessera dataset release (e.g. "1.0", "1.1") these
            embeddings came from, per tessera_vq.client.VQTessera.fetch_dataset_version.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save quantized uint8 embeddings (gzipped)
    quantized_gz = output_dir / "all_embeddings_uint8.npy.gz"
    import io
    buf = io.BytesIO()
    np.save(buf, quantized)
    buf.seek(0)
    with gzip.open(quantized_gz, 'wb', compresslevel=6) as f_out:
        f_out.write(buf.read())
    q_gz_mb = quantized_gz.stat().st_size / (1024 * 1024)
    print(f"  Quantized uint8+gz: {q_gz_mb:.1f} MB")

    # Save quantization parameters
    quant_file = output_dir / "quantization.json"
    with open(quant_file, 'w') as f:
        json.dump({'dim_min': dim_min.tolist(), 'dim_max': dim_max.tolist()}, f)

    # Save pixel coordinates (gzipped)
    coords_gz = output_dir / "pixel_coords.npy.gz"
    buf = io.BytesIO()
    np.save(buf, coords)
    buf.seek(0)
    with gzip.open(coords_gz, 'wb', compresslevel=6) as f_out:
        f_out.write(buf.read())
    coords_kb = coords_gz.stat().st_size / 1024
    print(f"  Compressed pixel_coords: {coords_kb:.1f} KB")

    # Save metadata
    metadata = {
        "viewport_id": viewport_id,
        "mosaic_height": height,
        "mosaic_width": width,
        "clipped_height": height,
        "clipped_width": width,
        "num_total_pixels": height * width,
        "embedding_dim": EMBEDDING_DIM,
        "pixel_size_meters": 10,
        "crs": "EPSG:4326",
        "geotransform": {
            "a": transform.a,
            "b": transform.b,
            "c": transform.c,
            "d": transform.d,
            "e": transform.e,
            "f": transform.f
        }
    }
    # Tag the legacy format explicitly so the browser can branch on metadata.kind
    # without having to detect it from file presence.
    metadata["kind"] = "uint8"
    metadata["dataset_version"] = dataset_version
    metadata_file = output_dir / "metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)


# ---------- VQ-structure helpers (Path A: codebooks + indices on the wire) ----

def _save_npy_gz(path, arr, compresslevel=6):
    """Write a numpy array to a gzipped .npy file (compatible with the existing
    pixel_coords / embeddings format the frontend already knows how to decode)."""
    import io
    buf = io.BytesIO()
    np.save(buf, arr)
    buf.seek(0)
    with gzip.open(path, 'wb', compresslevel=compresslevel) as f_out:
        f_out.write(buf.read())


def _quantise_codebook(cb):
    """Uint8-quantise a per-tile codebook with per-tile per-dim min/max scales.

    Args:
        cb: (n_tiles, k, 128) float32.
    Returns:
        cb_uint8: (n_tiles, k, 128) uint8.
        scales: (n_tiles, 128, 2) float32 — per-tile per-dim ``(min, max)``.
        Dims with zero range get max := min + 1 so the uint8 decoder produces
        the original min value (correct reconstruction of constant dimensions).
    """
    n_tiles, k, dim = cb.shape
    cb_min = cb.min(axis=1)                       # (n_tiles, 128)
    cb_max = cb.max(axis=1)                       # (n_tiles, 128)
    span = cb_max - cb_min
    # Avoid div-by-zero on constant dims; the recovered value (min + 0/(1)*1) = min.
    safe_span = np.where(span == 0, 1.0, span).astype(np.float32)
    cb_uint8 = (
        (cb - cb_min[:, None, :]) / safe_span[:, None, :] * 255.0
    ).clip(0, 255).astype(np.uint8)
    scales = np.stack([cb_min.astype(np.float32),
                       cb_max.astype(np.float32)], axis=-1)   # (n_tiles, 128, 2)
    return cb_uint8, scales


def _assemble_indices_from_tiles(qs, attr_name, out_h, out_w):
    """Stitch per-tile (n_tiles, t, t) indices into a flat (out_h, out_w) array.

    ``out_h``/``out_w`` must be the *exact* full mosaic dims (``qs.mosaic_shape``),
    not floor-truncated to a tile_size multiple. Tiles are placed via
    tessera_vq.client's n_tiles_along/tile_pixel_offset (tessera-vq >=0.6.0):
    fixed t-stride, except the last row/col of tiles is pulled back to end
    exactly at out_h/out_w instead of a remainder strip going uncovered --
    mirrors tessera_vq.client.reconstruct_from_structure.

    Callers that persist the result (save_vectors_rvq) only affect *newly
    created* viewports this way -- already-written files on disk keep whatever
    out_h/out_w/n_tile_rows/n_tile_cols they were built with (always an exact
    t multiple, from the pre-0.6.0 floor-based scheme this replaces), and the
    browser's reconstructFloatMosaic (public/js/vq_reconstruct.js) reads both
    correctly: the pulled-back-last-tile rule provably reduces to plain
    ``idx * t`` whenever out_h/out_w is already a t multiple, so old viewports
    aren't affected by this change at all.
    """
    from tessera_vq.client import n_tiles_along, tile_pixel_offset

    per_tile = getattr(qs, attr_name)              # (n_tiles, t, t) uint8/16
    t = qs.tile_size
    full = np.zeros((out_h, out_w), dtype=per_tile.dtype)
    rows, cols = n_tiles_along(out_h, t), n_tiles_along(out_w, t)
    for i in range(per_tile.shape[0]):
        r, c = int(qs.positions[i, 0]), int(qs.positions[i, 1])
        row_off = tile_pixel_offset(r, rows, out_h, t)
        col_off = tile_pixel_offset(c, cols, out_w, t)
        full[row_off:row_off + t, col_off:col_off + t] = per_tile[i]
    return full


def save_vectors_rvq(qs, transform, viewport_id, year, output_dir, dataset_version=None):
    """Save VQ structure (codebooks + indices + metadata) for the browser.

    Replaces the ~28 MB uint8 mosaic + pixel_coords with a ~5 MB codebook-and-
    indices bundle the browser can decode tile-by-tile. ``qs`` is the
    ``QuantizedStructure`` returned by ``tessera_vq.client.fetch_quantized_structure``;
    ``transform`` is the affine returned by ``reconstruct_from_structure`` so
    we never recompute it ourselves and never drift from upstream.
    ``dataset_version`` is the Tessera dataset release (e.g. "1.0", "1.1")
    these embeddings came from, per ``VQTessera.fetch_dataset_version``.
    """
    from tessera_vq.client import n_tiles_along

    output_dir.mkdir(parents=True, exist_ok=True)

    t = qs.tile_size
    full_h, full_w = int(qs.mosaic_shape[0]), int(qs.mosaic_shape[1])
    # Exact full mosaic, not floor-truncated to a tile_size multiple -- see
    # _assemble_indices_from_tiles's docstring. Existing on-disk viewports are
    # unaffected (this only runs when creating a new one); this was
    # deliberately left as the pre-0.6.0 truncated behaviour for a while
    # after tessera-vq's tiling fix landed, since viewports here are 5km
    # square and the truncation loss (well under one tile) wasn't worth
    # touching without checking -- now fixed for newly-created viewports too.
    out_h, out_w = full_h, full_w
    is_rvq = qs.codebooks2 is not None

    # Per-pixel indices at the full mosaic shape; tile_id derived in the
    # browser (public/js/vq_reconstruct.js::tileIndexForPixel) from
    # (n_tile_rows/cols, output_shape, tile_size) below.
    idx1 = _assemble_indices_from_tiles(qs, 'indices1', out_h, out_w)
    _save_npy_gz(output_dir / 'indices1.npy.gz', idx1)
    if is_rvq:
        idx2 = _assemble_indices_from_tiles(qs, 'indices2', out_h, out_w)
        _save_npy_gz(output_dir / 'indices2.npy.gz', idx2)

    # uint8-quantised codebooks + per-tile per-dim scales. Per-tile scales add
    # ~64 KB and keep reconstruction quality close to the bolt-on's k-means.
    cb1_u8, cb1_scales = _quantise_codebook(qs.codebooks1)
    _save_npy_gz(output_dir / 'codebooks1_uint8.npy.gz', cb1_u8)
    _save_npy_gz(output_dir / 'codebooks1_scales.npy.gz', cb1_scales)
    if is_rvq:
        cb2_u8, cb2_scales = _quantise_codebook(qs.codebooks2)
        _save_npy_gz(output_dir / 'codebooks2_uint8.npy.gz', cb2_u8)
        _save_npy_gz(output_dir / 'codebooks2_scales.npy.gz', cb2_scales)

    n_tile_rows = n_tiles_along(full_h, t)
    n_tile_cols = n_tiles_along(full_w, t)
    tile_index = {
        'tile_size': t,
        'n_tiles': int(qs.codebooks1.shape[0]),
        'n_tile_rows': n_tile_rows,
        'n_tile_cols': n_tile_cols,
        'tiles': [
            {'id': i, 'row': int(qs.positions[i, 0]), 'col': int(qs.positions[i, 1])}
            for i in range(qs.codebooks1.shape[0])
        ],
    }
    with open(output_dir / 'tile_index.json', 'w') as f:
        json.dump(tile_index, f)

    # VQ-aware metadata (separate from the legacy metadata.json which is also
    # written; the browser branches on metadata.kind to pick the read path).
    vq_meta = {
        'viewport_id': viewport_id,
        'kind': 'rvq' if is_rvq else 'vq',
        'mosaic_shape': [full_h, full_w],         # full reprojected shape
        'output_shape': [out_h, out_w],           # == mosaic_shape now (no truncation); where indices live
        'num_total_pixels': out_h * out_w,
        'embedding_dim': qs.codebooks1.shape[-1],
        'pixel_size_meters': 10,
        'crs': 'EPSG:4326',
        'geotransform': {
            'a': transform.a, 'b': transform.b, 'c': transform.c,
            'd': transform.d, 'e': transform.e, 'f': transform.f,
        },
        'tile_size': t,
        'k1': int(qs.k1),
        'k2': int(qs.k2) if is_rvq else None,
        'n_tile_rows': n_tile_rows,
        'n_tile_cols': n_tile_cols,
        'metric': qs.metric,
        'dataset_version': dataset_version,
    }
    with open(output_dir / 'vq_metadata.json', 'w') as f:
        json.dump(vq_meta, f, indent=2)

    files = ['indices1.npy.gz', 'codebooks1_uint8.npy.gz', 'codebooks1_scales.npy.gz']
    if is_rvq:
        files += ['indices2.npy.gz', 'codebooks2_uint8.npy.gz', 'codebooks2_scales.npy.gz']
    total_kb = sum((output_dir / fn).stat().st_size for fn in files) / 1024
    print(f"  VQ structure: {total_kb:.1f} KB "
          f"({'RVQ' if is_rvq else 'VQ'}, n_tiles={tile_index['n_tiles']}, "
          f"t={t}, k1={qs.k1}{', k2='+str(qs.k2) if is_rvq else ''})")


# ---------- Per-year processing ----------

def process_year(tessera, viewport_id, bounds, year, pyramids_dir, vectors_dir,
                 progress=None, year_idx=0, num_years=1):
    """Process a single year: fetch mosaic -> pyramids -> vectors.

    Tries the VQ quantized-structure fast path first, falls back to a plain
    NPY fetch via fetch_mosaic_for_region().

    All data stays in memory; no intermediate GeoTIFF files are created.

    Returns:
        (year, success: bool, message: str)
    """
    def _progress(pct, msg):
        """Report progress scaled to this year's slice of the overall 5-95% range."""
        if not progress:
            return
        per_year = 90 / num_years
        overall = 5 + year_idx * per_year + pct / 100 * per_year
        progress.update("processing", msg, percent=int(overall))

    year_pyramids_dir = pyramids_dir / str(year)
    year_vectors_dir = vectors_dir / str(year)

    # Skip check: pyramids AND vectors must both exist
    pyramids_ok = pyramid_exists(year_pyramids_dir)
    vectors_ok = (year_vectors_dir / 'all_embeddings_uint8.npy.gz').exists()
    if pyramids_ok and vectors_ok:
        print(f"  [{year}] Already processed (pyramids + vectors exist), skipping")
        return (year, True, "already exists")

    # --- FETCH MOSAIC ---
    # Two paths, tried in order:
    #   1. QS fast path: VQTessera + the upstream's public
    #      fetch_quantized_structure → reconstruct_from_structure. Gives us
    #      the per-tile structure for the new browser format AND the full
    #      (real full_h x full_w, not tile-multiple-truncated -- see
    #      tessera-vq >=0.6.0) reconstructed mosaic in one round-trip;
    #      transform math comes from upstream so it can't drift.
    #   2. NPY path: tessera.fetch_mosaic_for_region. Used when the QS fetch
    #      fails for a transient reason (VQTessera is the only client now,
    #      so this is always a VQTessera NPY fetch, not a GeoTessera one).
    _progress(1, f"[{year}] Fetching mosaic...")
    print(f"  [{year}] Fetching mosaic...")
    t0 = _time.monotonic()

    # Cached on the VQTessera client instance, so this is one extra HTTP GET
    # per process (not per year) -- best-effort provenance, never blocks the fetch.
    dataset_version = None
    if hasattr(tessera, 'fetch_dataset_version'):
        try:
            dataset_version = tessera.fetch_dataset_version()
        except Exception as e:
            print(f"  [{year}] Could not fetch dataset version: {e}")

    qs = None
    if _provider_kind == 'vqtessera' and hasattr(tessera, 'fetch_quantized_structure'):
        try:
            from tessera_vq.client import (
                NoCoverageError, reconstruct_from_structure,
            )
            print(f"  [{year}] Fetching quantized structure (VQ fast path)...")
            qs = tessera.fetch_quantized_structure(bbox=bounds, year=year)
            mosaic, transform, crs = reconstruct_from_structure(qs)
            elapsed = _time.monotonic() - t0
            n_tiles = int(qs.codebooks1.shape[0])
            k2_str = f", k2={qs.k2}" if qs.codebooks2 is not None else ""
            print(f"  [{year}] Fetched VQ structure: n_tiles={n_tiles} "
                  f"output_shape={mosaic.shape[:2]} t={qs.tile_size} "
                  f"k1={qs.k1}{k2_str} ({elapsed:.1f}s)")
        except NoCoverageError as e:
            # Explicit "no coverage" from upstream — fail clean with the same
            # message wording the post-fetch NaN heuristic would have produced.
            msg = (f"No embeddings available for this region in {year} "
                   f"(upstream vqtessera: {e})")
            print(f"  [{year}] {msg}")
            return (year, False, msg)
        except Exception as e:
            # Any other error: drop QS and fall through to the legacy mosaic
            # path so a transient bolt-on issue doesn't break processing.
            print(f"  [{year}] QS fetch failed ({type(e).__name__}: {e}); "
                  f"falling back to mosaic path")
            qs = None

    if qs is None:
        # QS fetch failed for a transient reason (see except Exception above) --
        # fall back to a plain NPY fetch via the same VQTessera client.
        print(f"  [{year}] Using NPY path")

        max_retries = 3
        mosaic = None
        transform = None
        crs = None

        for attempt in range(1, max_retries + 1):
            try:
                def _npy_progress(pct, msg):
                    _progress(pct, f"[{year}] {msg}")
                mosaic, transform, crs = _fetch_mosaic_npy(
                    tessera, bounds, year, progress_fn=_npy_progress)
                break
            except Exception as e:
                err_str = str(e)
                if 'No embedding tiles found' in err_str:
                    short_msg = f"No embeddings available for {year} at this location"
                else:
                    short_msg = f"{type(e).__name__}: {err_str}"

                if attempt < max_retries:
                    print(f"  [{year}] Attempt {attempt}/{max_retries} failed, retrying in 5s: {short_msg}")
                    _time.sleep(5)
                else:
                    print(f"  [{year}] Failed after {max_retries} attempts: {short_msg}")
                    return (year, False, short_msg)

        if mosaic is None:
            return (year, False, "fetch failed")

        elapsed = _time.monotonic() - t0
        height, width = mosaic.shape[:2]
        print(f"  [{year}] Fetched {width}x{height} mosaic via NPY ({elapsed:.1f}s)")
    else:
        height, width = mosaic.shape[:2]

    # The QS path already raised NoCoverageError for empty/all-NaN responses,
    # and its mosaic's dimensions are exactly what save_vectors_rvq's own
    # tile-index assembly will use (see _assemble_indices_from_tiles -- it no
    # longer needs an exact tile_size multiple, tessera-vq >=0.6.0 covers any
    # full_h/full_w correctly). Both the legacy NaN heuristic and the bbox
    # crop below would desync the mosaic from that tile grid, so skip them
    # when we came via QS.
    if qs is None:
        # No-coverage detection: a fully-NaN mosaic means the upstream provider
        # (typically the tessera-vq bolt-on) has no data for this bbox/year.
        # Detect *before* the vector path's nan_to_num, which would otherwise
        # convert all-NaN -> all-zero and surface as the misleading "all-zero
        # embeddings" error in the UI.
        if np.all(np.isnan(mosaic)):
            msg = (f"No embeddings available for this region in {year} "
                   f"(upstream vqtessera returned no coverage for bbox {bounds})")
            print(f"  [{year}] {msg}")
            del mosaic
            gc.collect()
            return (year, False, msg)

        # Crop mosaic to exact viewport bounds (grid tiles may extend beyond ROI)
        col_start = max(0, int(np.floor((bounds[0] - transform.c) / transform.a)))
        col_end = min(width, int(np.ceil((bounds[2] - transform.c) / transform.a)))
        row_start = max(0, int(np.floor((bounds[3] - transform.f) / transform.e)))
        row_end = min(height, int(np.ceil((bounds[1] - transform.f) / transform.e)))
        if col_start > 0 or row_start > 0 or col_end < width or row_end < height:
            mosaic = mosaic[row_start:row_end, col_start:col_end, :]
            transform = transform * Affine.translation(col_start, row_start)
            height, width = mosaic.shape[:2]
            print(f"  [{year}] Cropped to viewport: {width}x{height}")

    # --- PYRAMIDS (bands 0-2 -> RGB) ---
    if not pyramids_ok:
        _progress(60, f"[{year}] Creating pyramids...")
        print(f"  [{year}] Creating pyramids...")
        rgb = np.stack([
            percentile_normalize(mosaic[:, :, 0]),
            percentile_normalize(mosaic[:, :, 1]),
            percentile_normalize(mosaic[:, :, 2]),
        ], axis=0)  # (3, H, W) uint8

        write_pyramid_levels(rgb, transform, crs, year_pyramids_dir)
        del rgb
    else:
        print(f"  [{year}] Pyramids already exist, skipping")

    # --- VECTORS (all 128 bands) ---
    if not vectors_ok:
        _progress(70, f"[{year}] Extracting vectors...")
        print(f"  [{year}] Creating vectors...")
        all_embeddings = mosaic.reshape(-1, EMBEDDING_DIM)

        # Zero-fill NaN nodata before extraction. Reprojection and edge tiles
        # leave NaN at the mosaic corners; NaN can't be quantised
        # (it propagates through min/max -> every value casts to 0) and can't be
        # stored in JSON the browser will accept (JS JSON.parse rejects literal
        # NaN). Matches the NPY path's nodata handling. In-place is safe — the
        # pyramids are already written and `mosaic` is freed below.
        np.nan_to_num(all_embeddings, copy=False, nan=0.0)

        # Validate non-zero
        if not np.any(all_embeddings):
            print(f"  [{year}] All embeddings are zero - mosaic may be corrupt")
            del mosaic, all_embeddings
            gc.collect()
            return (year, False, "all-zero embeddings")

        # Pixel coordinates (regular grid)
        yy, xx = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')
        coords = np.column_stack([xx.ravel(), yy.ravel()]).astype(np.int32)

        # Quantize to uint8
        dim_min = all_embeddings.min(axis=0).astype(np.float64)
        dim_max = all_embeddings.max(axis=0).astype(np.float64)
        dim_scale = dim_max - dim_min
        dim_scale[dim_scale == 0] = 1
        quantized = ((all_embeddings - dim_min) / dim_scale * 255).astype(np.uint8)

        _progress(80, f"[{year}] Saving vectors...")
        save_vectors(quantized, coords, dim_min, dim_max, transform,
                     height, width, viewport_id, year, year_vectors_dir,
                     dataset_version=dataset_version)
        del quantized, coords, dim_min, dim_max

        # Path A dual-write: when we came via the VQ fast path, also persist
        # the codebooks + indices so the browser can download a ~5 MB bundle
        # instead of the ~28 MB uint8 mosaic. Phase 2 (browser reader) detects
        # kind='vq'|'rvq' in vq_metadata.json and switches over; Phase 3 will
        # then drop the legacy uint8 emission above.
        if qs is not None:
            save_vectors_rvq(qs, transform, viewport_id, year, year_vectors_dir,
                              dataset_version=dataset_version)
    else:
        print(f"  [{year}] Vectors already exist, skipping")

    del mosaic
    gc.collect()

    _progress(100, f"[{year}] Done")
    print(f"  [{year}] Done")
    return (year, True, "processed")


def _process_year_worker(args):
    """Worker function for ProcessPoolExecutor. Uses the cached provider."""
    viewport_id, bounds, year, pyramids_dir, vectors_dir = args
    tessera = _get_provider(viewport_id)
    return process_year(tessera, viewport_id, bounds, year, pyramids_dir, vectors_dir)


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description='Process viewport: download + pyramids + vectors')
    parser.add_argument('--viewport', type=str,
                        help='Viewport name to process (required for concurrent safety)')
    parser.add_argument('--years', type=str,
                        help='Comma-separated years (e.g., 2024,2025)')
    args = parser.parse_args()

    if args.years:
        try:
            years = sorted([int(y.strip()) for y in args.years.split(',') if y.strip()])
        except ValueError:
            years = list(DEFAULT_YEARS)
    else:
        years = list(DEFAULT_YEARS)

    # Read viewport — prefer explicit --viewport arg for concurrent safety
    try:
        if args.viewport:
            from lib.viewport_utils import read_viewport_file
            viewport = read_viewport_file(args.viewport)
            viewport_id = args.viewport
            bounds = viewport['bounds_tuple']
        else:
            # Fallback to active viewport (legacy, not concurrent-safe)
            viewport = get_active_viewport()
            viewport_id = viewport['viewport_id']
            bounds = viewport['bounds_tuple']
    except Exception as e:
        print(f"ERROR: Failed to read viewport: {e}", file=sys.stderr)
        sys.exit(1)

    # Initialize progress tracker
    progress = ProgressTracker(f"{viewport_id}_pipeline")
    progress.update("starting", f"Initializing processing for {viewport_id}...")

    # Directories
    EMBEDDINGS_DIR.mkdir(exist_ok=True)
    pyramids_dir = PYRAMIDS_DIR / viewport_id
    pyramids_dir.mkdir(parents=True, exist_ok=True)
    vectors_dir = VECTORS_DIR / viewport_id
    vectors_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing viewport: {viewport_id}")
    print(f"Bounds: {bounds}")
    print(f"Years: {years}")
    print("=" * 60)

    # Filter to years that need processing
    years_to_process = []
    for year in years:
        pyramids_ok = pyramid_exists(pyramids_dir / str(year))
        vectors_ok = (vectors_dir / str(year) / 'all_embeddings_uint8.npy.gz').exists()
        if pyramids_ok and vectors_ok:
            print(f"  [{year}] Already complete, skipping")
        else:
            years_to_process.append(year)

    if not years_to_process:
        print("\nAll years already processed!")
        progress.update("processing", f"All years already processed for {viewport_id}", percent=95)
        return

    print(f"\nProcessing {len(years_to_process)} year(s): {years_to_process}")

    progress.update("processing", "Connecting to embeddings provider...", percent=1)
    print("  Initializing embeddings provider (cached)...")
    t_init = _time.monotonic()
    tessera = _get_provider(viewport_id)
    init_secs = _time.monotonic() - t_init
    label = "VQTessera" if _provider_kind == 'vqtessera' else "GeoTessera"
    print(f"  {label} ready ({init_secs:.1f}s)")

    progress.update("processing", f"Processing {len(years_to_process)} year(s)...", percent=3)

    # Process years sequentially so progress is reported for each year.
    # (Parallel workers can't share the ProgressTracker, and each spawns
    # a separate GeoTessera instance with its own 28s registry download.)
    n = len(years_to_process)
    results = []
    for i, year in enumerate(years_to_process):
        results.append(
            process_year(tessera, viewport_id, bounds, year,
                         pyramids_dir, vectors_dir, progress=progress,
                         year_idx=i, num_years=n)
        )

    # Summary
    succeeded = [year for year, ok, _ in results if ok]
    failed = [(year, msg) for year, ok, msg in results if not ok]

    print("\n" + "=" * 60)
    if succeeded:
        print(f"Processed: {succeeded}")
    if failed:
        for year, msg in failed:
            print(f"  [{year}] FAILED: {msg}")

    if failed and not succeeded:
        summary = '; '.join(f"{y}: {m}" for y, m in failed)
        progress.update("processing", f"All years failed — {summary}", percent=95)
        sys.exit(1)
    else:
        progress.update("processing", f"Processed {len(succeeded)} year(s)", percent=95)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\nFATAL ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
