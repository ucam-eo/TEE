#!/usr/bin/env python3
"""
Download Tessera embeddings for current viewport

Reads viewport bounds from active viewport configuration.
Uses cache checking to avoid re-downloading for previously-selected viewports.
Downloads multiple years in parallel for faster throughput.
"""

import sys
import json
import traceback
import threading
import time as _time
from pathlib import Path

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

# Import dependencies with error reporting
try:
    import gc
    import numpy as np
    import rasterio
    from rasterio.transform import Affine
    import geotessera as gt
    import math
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

try:
    from lib.viewport_utils import get_active_viewport
    from lib.progress_tracker import ProgressTracker
    from lib.config import DATA_DIR, EMBEDDINGS_DIR, MOSAICS_DIR
except ImportError as e:
    print(f"LIB IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

# Configuration
DEFAULT_YEARS = range(2017, 2026)  # Support 2017-2025 (Sentinel-2 availability)
MAX_CONCURRENT_DOWNLOADS = 3  # Limit parallel downloads to bound memory usage

# Parse command line arguments for year selection
import argparse
parser = argparse.ArgumentParser(description='Download Tessera embeddings')
parser.add_argument('--years', type=str, help='Comma-separated years to download (e.g., 2017,2018,2024)')
args = parser.parse_args()

if args.years:
    try:
        # Parse comma-separated years and convert to integers
        requested_years = sorted([int(y.strip()) for y in args.years.split(',') if y.strip()])
        if requested_years:
            YEARS = requested_years
        else:
            YEARS = DEFAULT_YEARS
    except (ValueError, IndexError):
        YEARS = DEFAULT_YEARS
else:
    YEARS = DEFAULT_YEARS

# Tessera embeddings parameters
EMBEDDING_BANDS = 128
BYTES_PER_BAND = 4  # float32
PIXEL_SIZE_METERS = 10
METERS_PER_DEGREE_LAT = 111320  # Constant
COMPRESSION_RATIO = 0.4  # LZW compression typically achieves ~40% of original size

def estimate_mosaic_dimensions(bbox):
    """Estimate mosaic dimensions from bounding box.

    Args:
        bbox: tuple of (lon_min, lat_min, lon_max, lat_max)

    Returns:
        tuple of (estimated_width, estimated_height, estimated_file_size_mb)
    """
    lon_min, lat_min, lon_max, lat_max = bbox

    # Calculate center latitude for longitude scaling
    center_lat = (lat_min + lat_max) / 2
    cos_lat = math.cos(math.radians(center_lat))

    # Meters per degree at this latitude
    meters_per_degree_lon = METERS_PER_DEGREE_LAT * cos_lat

    # Calculate dimensions in pixels
    height_pixels = int((lat_max - lat_min) * METERS_PER_DEGREE_LAT / PIXEL_SIZE_METERS)
    width_pixels = int((lon_max - lon_min) * meters_per_degree_lon / PIXEL_SIZE_METERS)

    # Calculate uncompressed file size (width × height × bands × bytes_per_band)
    uncompressed_bytes = width_pixels * height_pixels * EMBEDDING_BANDS * BYTES_PER_BAND

    # Estimate compressed size with LZW compression
    compressed_bytes = int(uncompressed_bytes * COMPRESSION_RATIO)
    compressed_mb = compressed_bytes / (1024 * 1024)

    return width_pixels, height_pixels, compressed_mb, compressed_bytes


def download_single_year(tessera, year, BBOX, viewport_id, output_file, est_bytes, est_mb,
                         progress, progress_lock, cumulative_bytes, total_estimated_bytes, total_years):
    """Download and save embeddings for a single year. Thread-safe.

    Returns:
        (year, success: bool, size_mb: float or 0)
    """
    year_label = f"{year}"

    # Skip if already exists
    if output_file.exists():
        actual_size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"   [{year}] ✓ Already exists ({actual_size_mb:.1f} MB)")
        with progress_lock:
            cumulative_bytes[0] += est_bytes
            progress.update("processing", f"Using existing {year}",
                           current_value=cumulative_bytes[0], total_value=total_estimated_bytes,
                           current_file=output_file.name)
        return (year, True, actual_size_mb)

    # Calculate download requirements
    try:
        tiles = list(tessera.registry.iter_tiles_in_region(BBOX, year))
        total_download_bytes, total_files, _ = tessera.registry.calculate_download_requirements(
            tiles, EMBEDDINGS_DIR, format_type='npy', check_existing=True
        )
        total_download_mb = total_download_bytes / (1024 * 1024)
        print(f"   [{year}] Download required: {total_files} files, {total_download_mb:.1f} MB")
    except Exception as e:
        print(f"   [{year}] ⚠️  Could not calculate download size: {e}")
        total_download_bytes = est_bytes
        total_download_mb = est_mb

    # Progress callback (throttled, thread-safe)
    _last_write = [0]

    def on_progress(current, total, status, _year=year, _total_mb=total_download_mb):
        now = _time.monotonic()
        if now - _last_write[0] < 0.5:
            return
        _last_write[0] = now
        if total > 0:
            year_bytes = int((current / total) * total_download_bytes)
            year_mb_done = year_bytes / (1024 * 1024)
            with progress_lock:
                overall_bytes = cumulative_bytes[0] + year_bytes
                progress.update("downloading",
                               f"{_year}: {status} ({year_mb_done:.1f} / {_total_mb:.1f} MB)",
                               current_value=overall_bytes,
                               total_value=total_estimated_bytes,
                               current_file=output_file.name)

    # Retry logic
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            print(f"   [{year}] Downloading (attempt {attempt}/{max_retries})...")

            mosaic_array, mosaic_transform, crs = tessera.fetch_mosaic_for_region(
                bbox=BBOX,
                year=year,
                target_crs='EPSG:4326',
                auto_download=True,
                progress_callback=on_progress
            )

            print(f"   [{year}] ✓ Downloaded. Shape: {mosaic_array.shape}")

            # Save to GeoTIFF
            height, width, bands = mosaic_array.shape
            with rasterio.open(
                output_file, 'w', driver='GTiff',
                height=height, width=width, count=bands,
                dtype=mosaic_array.dtype, crs=crs,
                transform=mosaic_transform, compress='lzw'
            ) as dst:
                dst.write(mosaic_array.transpose(2, 0, 1))

            # Validate
            with rasterio.open(output_file) as src:
                _ = src.read(1)

            actual_size_mb = output_file.stat().st_size / (1024 * 1024)
            print(f"   [{year}] ✓ Saved ({actual_size_mb:.1f} MB)")

            with progress_lock:
                cumulative_bytes[0] += est_bytes
                progress.update("processing", f"✓ {year} saved ({actual_size_mb:.1f} MB)",
                               current_value=cumulative_bytes[0], total_value=total_estimated_bytes,
                               current_file=output_file.name)

            del mosaic_array, mosaic_transform
            gc.collect()
            return (year, True, actual_size_mb)

        except Exception as e:
            if attempt < max_retries:
                print(f"   [{year}] ⚠️  Attempt {attempt} failed, retrying: {type(e).__name__}: {e}")
                _time.sleep(5)
                # Delete corrupted file if it exists
                if output_file.exists():
                    output_file.unlink()
                continue
            else:
                print(f"   [{year}] ⚠️  Not available: {type(e).__name__}: {e}")
                traceback.print_exc(file=sys.stderr)
                if output_file.exists():
                    output_file.unlink()
                with progress_lock:
                    cumulative_bytes[0] += est_bytes
                    progress.update("processing", f"Skipped {year} (not available)",
                                   current_value=cumulative_bytes[0], total_value=total_estimated_bytes,
                                   current_file=output_file.name)
                return (year, False, 0)

    return (year, False, 0)


def download_embeddings():
    """Download Tessera embeddings for current viewport."""

    # Read active viewport
    try:
        viewport = get_active_viewport()
        BBOX = viewport['bounds_tuple']
        viewport_id = viewport['viewport_id']
    except Exception as e:
        print(f"ERROR: Failed to read viewport: {e}", file=sys.stderr)
        sys.exit(1)

    # Initialize progress tracker - use script-specific progress file to avoid conflicts with pipeline orchestrator
    progress = ProgressTracker(f"{viewport_id}_download")
    progress.update("starting", f"Initializing download for {viewport_id}...")

    # Create output directories
    EMBEDDINGS_DIR.mkdir(exist_ok=True)
    MOSAICS_DIR.mkdir(exist_ok=True)

    print(f"Downloading Tessera embeddings")
    print(f"Viewport: {viewport_id}")
    print(f"Bounding box: {BBOX}")
    print(f"Years: {min(YEARS)} to {max(YEARS)}")

    # Estimate file size and dimensions
    est_width, est_height, est_mb, est_bytes = estimate_mosaic_dimensions(BBOX)
    print(f"\nEstimated dimensions: {est_width} × {est_height} pixels")
    print(f"Estimated file size (compressed): {est_mb:.1f} MB")

    print(f"\nEmbeddings will be downloaded to: {EMBEDDINGS_DIR.absolute()}")
    print(f"Mosaics will be saved to: {MOSAICS_DIR.absolute()}")
    print("=" * 60)

    # Initialize GeoTessera with embeddings directory
    print(f"\nConnecting to GeoTessera registry...")
    print(f"   geotessera version: {gt.__version__ if hasattr(gt, '__version__') else 'unknown'}")
    print(f"   embeddings_dir: {EMBEDDINGS_DIR.absolute()}")
    progress.update("initializing", "Connecting to GeoTessera registry...")
    try:
        tessera = gt.GeoTessera(embeddings_dir=str(EMBEDDINGS_DIR))
        print(f"✓ Connected to registry")
    except Exception as e:
        print(f"✗ Failed to connect to GeoTessera: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        progress.error(f"GeoTessera connection failed: {e}")
        sys.exit(1)

    # Track progress across all years
    total_years = len(list(YEARS))
    total_estimated_bytes = est_bytes * total_years
    cumulative_bytes = [0]
    progress_lock = threading.Lock()  # kept for download_single_year signature

    successful_years = []

    # Download years sequentially (GeoTessera client is not thread-safe)
    print(f"\nDownloading {total_years} year(s)...")

    for year in YEARS:
        output_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
        try:
            yr, success, size_mb = download_single_year(
                tessera, year, BBOX, viewport_id, output_file, est_bytes, est_mb,
                progress, progress_lock, cumulative_bytes, total_estimated_bytes, total_years
            )
            if success:
                successful_years.append(yr)
        except Exception as e:
            print(f"   [{year}] ⚠️  Unexpected error: {type(e).__name__}: {e}")
            traceback.print_exc(file=sys.stderr)

    print("\n" + "=" * 60)
    print("Download complete!")
    print(f"\nTiles cached in: {EMBEDDINGS_DIR.absolute()}")
    print(f"Mosaics saved in: {MOSAICS_DIR.absolute()}")

    # Save metadata about successful downloads
    metadata_file = MOSAICS_DIR / f"{viewport_id}_years.json"
    metadata = {'available_years': sorted(successful_years)}
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f)
    print(f"✓ Saved metadata: {metadata_file}")
    print(f"Successfully downloaded years: {sorted(successful_years)}")

    # Check if any mosaics were successfully created
    if successful_years:
        print(f"\n✓ Created mosaics for {viewport_id}:")
        total_size_mb = 0
        for year in successful_years:
            mosaic_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
            if mosaic_file.exists():
                size_mb = mosaic_file.stat().st_size / (1024*1024)
                total_size_mb += size_mb
                compression_ratio = (size_mb / (est_mb / COMPRESSION_RATIO)) * 100 if est_mb > 0 else 0
                print(f"  - {mosaic_file.name} ({size_mb:.1f} MB, {compression_ratio:.1f}% compression)")
        print(f"\nTotal downloaded: {total_size_mb:.1f} MB for {len(successful_years)} years")
        progress.complete(f"Downloaded {total_size_mb:.1f} MB of embeddings ({len(successful_years)} years)")
    else:
        print(f"\n⚠️  No mosaics for {viewport_id} were created (no data available for years: {list(YEARS)})")
        print(f"   This is normal — not all regions have data for every year.", file=sys.stderr)
        progress.complete(f"No data available for requested years: {list(YEARS)}")

if __name__ == "__main__":
    import traceback
    try:
        download_embeddings()
    except SystemExit:
        raise  # Let sys.exit() propagate normally
    except Exception as e:
        print(f"\nFATAL ERROR in download_embeddings: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
