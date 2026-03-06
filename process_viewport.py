#!/usr/bin/env python3
"""
Single-script pipeline: download tiles + pyramids + vectors per year.

Replaces download_embeddings.py, create_rgb_embeddings.py, and extract_vectors.py.
Calls fetch_mosaic_for_region once per year, then produces all outputs in memory
with zero intermediate GeoTIFF files.

Usage:
    python process_viewport.py --years 2024,2025
    python process_viewport.py                    # all years 2017-2025
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
    import rasterio
    from rasterio.enums import Resampling
    from scipy.ndimage import zoom
    import geotessera as gt
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

try:
    from lib.viewport_utils import get_active_viewport
    from lib.progress_tracker import ProgressTracker
    from lib.config import DATA_DIR, EMBEDDINGS_DIR, PYRAMIDS_DIR, VECTORS_DIR
except ImportError as e:
    print(f"LIB IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger(__name__)

DEFAULT_YEARS = range(2017, 2026)
EMBEDDING_DIM = 128
NUM_ZOOM_LEVELS = 6
TARGET_BASE = 4408


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


def create_pyramid_level(input_file, output_file, scale_factor, target_width, target_height):
    """Create a single pyramid level: 2x downsample then upscale to target dims."""
    with rasterio.open(input_file) as src:
        intermediate_height = max(1, int(src.height / 2))
        intermediate_width = max(1, int(src.width / 2))

        downsampled = src.read(
            out_shape=(src.count, intermediate_height, intermediate_width),
            resampling=Resampling.nearest
        )

        scale_y = target_height / downsampled.shape[1]
        scale_x = target_width / downsampled.shape[2]
        final_data = zoom(downsampled, (1, scale_y, scale_x), order=0)

        transform = src.transform * src.transform.scale(
            (src.width / intermediate_width) * (intermediate_width / target_width),
            (src.height / intermediate_height) * (intermediate_height / target_height)
        )

        profile = src.profile.copy()
        profile.update({
            'height': target_height,
            'width': target_width,
            'transform': transform
        })

        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(final_data)

    spatial_scale = 10 * (2 ** scale_factor)
    size_kb = output_file.stat().st_size / 1024
    print(f"    Level {scale_factor}: {target_width}x{target_height} @ {spatial_scale}m/pixel ({size_kb:.1f} KB)")


def write_pyramid_levels(rgb_upscaled, up_transform, crs, output_dir):
    """Write level_0 from in-memory array, then create downsampled levels.

    Args:
        rgb_upscaled: (3, H, W) uint8 array (already 3x upscaled)
        up_transform: Affine transform for the upscaled image
        crs: CRS string
        output_dir: Path to year-specific pyramids directory
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Level 0: write full-resolution upscaled image
    level_0 = output_dir / "level_0.tif"
    _, source_height, source_width = rgb_upscaled.shape
    profile = {
        'driver': 'GTiff',
        'dtype': 'uint8',
        'count': 3,
        'height': source_height,
        'width': source_width,
        'crs': crs,
        'transform': up_transform,
        'compress': 'lzw',
    }
    with rasterio.open(level_0, 'w', **profile) as dst:
        dst.write(rgb_upscaled)

    size_kb = level_0.stat().st_size / 1024
    print(f"    Level 0: {source_width}x{source_height} @ 10m/pixel ({size_kb:.1f} KB)")

    # Calculate rectangular target dims
    if source_width >= source_height:
        target_width = TARGET_BASE
        target_height = int(TARGET_BASE * source_height / source_width)
    else:
        target_height = TARGET_BASE
        target_width = int(TARGET_BASE * source_width / source_height)

    # Downsample levels
    prev_level = level_0
    for level in range(1, NUM_ZOOM_LEVELS):
        level_file = output_dir / f"level_{level}.tif"
        create_pyramid_level(prev_level, level_file, level, target_width, target_height)
        prev_level = level_file

    print(f"  Created {NUM_ZOOM_LEVELS} pyramid levels in {output_dir}")


# ---------- Vector helpers (ported from extract_vectors.py) ----------

def save_vectors(quantized, coords, dim_min, dim_max, transform, height, width,
                 viewport_id, year, output_dir):
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
    metadata_file = output_dir / "metadata.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)


# ---------- Per-year processing ----------

def process_year(tessera, viewport_id, bounds, year, pyramids_dir, vectors_dir,
                 progress=None, year_idx=0, num_years=1):
    """Process a single year: fetch mosaic -> pyramids -> vectors.

    All data stays in memory; no intermediate GeoTIFF files are created.

    Returns:
        (year, success: bool, message: str)
    """
    def _progress(pct, msg):
        """Report progress scaled to this year's slice of the overall 5-95% range."""
        if not progress:
            return
        # Each year gets an equal slice of 5-95%
        per_year = 90 / num_years  # e.g. 90% for 1 year, 45% for 2
        overall = 5 + year_idx * per_year + pct / 100 * per_year
        progress.update("processing", msg, percent=int(overall))
    year_pyramids_dir = pyramids_dir / str(year)
    year_vectors_dir = vectors_dir / str(year)

    # Skip check: pyramids AND vectors must both exist
    pyramids_ok = (year_pyramids_dir / 'level_0.tif').exists()
    vectors_ok = (year_vectors_dir / 'all_embeddings_uint8.npy.gz').exists()
    if pyramids_ok and vectors_ok:
        print(f"  [{year}] Already processed (pyramids + vectors exist), skipping")
        return (year, True, "already exists")

    # --- FETCH MOSAIC ---
    _progress(0, f"[{year}] Fetching mosaic...")
    print(f"  [{year}] Fetching mosaic...")
    t0 = _time.monotonic()

    max_retries = 3
    mosaic = None
    for attempt in range(1, max_retries + 1):
        try:
            mosaic, transform, crs = tessera.fetch_mosaic_for_region(
                bbox=bounds,
                year=year,
                target_crs='EPSG:4326',
                auto_download=True,
            )
            break
        except Exception as e:
            if attempt < max_retries:
                print(f"  [{year}] Attempt {attempt} failed, retrying: {type(e).__name__}: {e}")
                _time.sleep(5)
            else:
                print(f"  [{year}] Not available: {type(e).__name__}: {e}")
                return (year, False, str(e))

    if mosaic is None:
        return (year, False, "fetch failed")

    height, width = mosaic.shape[:2]
    elapsed = _time.monotonic() - t0
    print(f"  [{year}] Fetched {width}x{height} mosaic ({elapsed:.1f}s)")

    # --- PYRAMIDS (bands 0-2 -> RGB) ---
    if not pyramids_ok:
        _progress(60, f"[{year}] Creating pyramids...")
        print(f"  [{year}] Creating pyramids...")
        rgb = np.stack([
            percentile_normalize(mosaic[:, :, 0]),
            percentile_normalize(mosaic[:, :, 1]),
            percentile_normalize(mosaic[:, :, 2]),
        ], axis=0)  # (3, H, W) uint8

        # Upscale 3x for crisp pixel boundaries
        rgb_upscaled = np.repeat(np.repeat(rgb, 3, axis=1), 3, axis=2)
        up_transform = transform * transform.scale(1/3, 1/3)

        write_pyramid_levels(rgb_upscaled, up_transform, crs, year_pyramids_dir)
        del rgb, rgb_upscaled
    else:
        print(f"  [{year}] Pyramids already exist, skipping")

    # --- VECTORS (all 128 bands) ---
    if not vectors_ok:
        _progress(70, f"[{year}] Extracting vectors...")
        print(f"  [{year}] Creating vectors...")
        all_embeddings = mosaic.reshape(-1, EMBEDDING_DIM)

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
                     height, width, viewport_id, year, year_vectors_dir)
        del quantized, coords, dim_min, dim_max
    else:
        print(f"  [{year}] Vectors already exist, skipping")

    del mosaic
    gc.collect()

    _progress(100, f"[{year}] Done")
    print(f"  [{year}] Done")
    return (year, True, "processed")


def _process_year_worker(args):
    """Worker function for ProcessPoolExecutor. Creates its own GeoTessera instance."""
    viewport_id, bounds, year, pyramids_dir, vectors_dir = args
    tessera = gt.GeoTessera(embeddings_dir=str(EMBEDDINGS_DIR))
    return process_year(tessera, viewport_id, bounds, year, pyramids_dir, vectors_dir)


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description='Process viewport: download + pyramids + vectors')
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

    # Read active viewport
    try:
        viewport = get_active_viewport()
        viewport_id = viewport['viewport_id']
        bounds = viewport['bounds_tuple']
    except Exception as e:
        print(f"ERROR: Failed to read viewport: {e}", file=sys.stderr)
        sys.exit(1)

    # Initialize progress tracker
    progress = ProgressTracker(f"{viewport_id}_process")
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
        pyramids_ok = (pyramids_dir / str(year) / 'level_0.tif').exists()
        vectors_ok = (vectors_dir / str(year) / 'all_embeddings_uint8.npy.gz').exists()
        if pyramids_ok and vectors_ok:
            print(f"  [{year}] Already complete, skipping")
        else:
            years_to_process.append(year)

    if not years_to_process:
        print("\nAll years already processed!")
        progress.complete(f"All years already processed for {viewport_id}")
        return

    print(f"\nProcessing {len(years_to_process)} year(s): {years_to_process}")

    # Process years in parallel
    max_workers = min(len(years_to_process), os.cpu_count() or 1)
    args_list = [
        (viewport_id, bounds, year, pyramids_dir, vectors_dir)
        for year in years_to_process
    ]

    progress.update("processing", f"Processing {len(years_to_process)} year(s)...", percent=5)

    n = len(years_to_process)
    if max_workers == 1:
        # Single year: run in main process to avoid subprocess overhead
        tessera = gt.GeoTessera(embeddings_dir=str(EMBEDDINGS_DIR))
        results = [
            process_year(tessera, viewport_id, bounds, years_to_process[0],
                         pyramids_dir, vectors_dir, progress=progress)
        ]
    else:
        print(f"Using {max_workers} parallel workers")
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(_process_year_worker, args_list))

    # Summary
    succeeded = [year for year, ok, _ in results if ok]
    failed = [(year, msg) for year, ok, msg in results if not ok]

    print("\n" + "=" * 60)
    if succeeded:
        print(f"Processed: {succeeded}")
    if failed:
        print(f"Failed: {[(y, m) for y, m in failed]}")

    if failed and not succeeded:
        progress.error(f"All years failed for {viewport_id}")
        sys.exit(1)
    else:
        progress.complete(f"Processed {len(succeeded)} year(s) for {viewport_id}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"\nFATAL ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
