#!/usr/bin/env python3
"""
Extract embeddings from GeoTIFF mosaics for similarity search.

Reads embedding mosaics, clips to viewport bounds, and saves:
- all_embeddings.npy: all pixel embeddings as float32
- pixel_coords.npy: corresponding (x, y) pixel coordinates
- metadata.json: geotransform and dimension info

The client downloads these files and does brute-force search in JavaScript.
"""

import sys
import os
import numpy as np
import rasterio
from rasterio import windows as rasterio_windows
from pathlib import Path
import json
import logging
from concurrent.futures import ProcessPoolExecutor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.viewport_utils import get_active_viewport
from lib.progress_tracker import ProgressTracker
from lib.config import DATA_DIR, MOSAICS_DIR, VECTORS_DIR
EMBEDDING_DIM = 128
YEARS = range(2017, 2026)  # Support 2017-2025


def extract_vectors_for_year(viewport_id, bounds, year):
    """Extract and store all embeddings for a specific year.

    Reads the GeoTIFF mosaic, clips to viewport bounds, and saves:
    - all_embeddings.npy, pixel_coords.npy, metadata.json
    """

    # Initialize progress tracker - use script-specific progress file to avoid conflicts with pipeline orchestrator
    progress = ProgressTracker(f"{viewport_id}_vectors")
    progress.update("starting", f"Extracting embeddings for {viewport_id} ({year})...")

    # Find mosaic file (year-specific)
    mosaic_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
    if not mosaic_file.exists():
        logger.warning(f"Mosaic file not found: {mosaic_file}")
        return False

    logger.info("=" * 70)
    logger.info(f"Extracting Embeddings ({year})")
    logger.info("=" * 70)
    logger.info(f"Viewport: {viewport_id}")
    logger.info(f"Year: {year}")
    logger.info(f"Mosaic file: {mosaic_file.name}")

    # Create output directory (year-specific)
    output_dir = VECTORS_DIR / viewport_id / str(year)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with rasterio.open(mosaic_file) as src:
            height, width = src.height, src.width
            logger.info(f"Mosaic dimensions: {width}×{height}")
            logger.info(f"Total pixels: {width * height:,}")

            # Clip to viewport bounds
            transform = src.transform
            min_lon, min_lat, max_lon, max_lat = bounds  # bounds_tuple is (min_lon, min_lat, max_lon, max_lat)

            # Convert lat/lon bounds to pixel coordinates
            # x = (lon - transform.c) / transform.a
            # y = (lat - transform.f) / transform.e
            pixel_min_x = int((min_lon - transform.c) / transform.a)
            pixel_max_x = int((max_lon - transform.c) / transform.a)
            pixel_min_y = int((max_lat - transform.f) / transform.e)
            pixel_max_y = int((min_lat - transform.f) / transform.e)

            # Ensure within mosaic bounds
            pixel_min_x = max(0, min(pixel_min_x, width - 1))
            pixel_max_x = max(0, min(pixel_max_x, width - 1))
            pixel_min_y = max(0, min(pixel_min_y, height - 1))
            pixel_max_y = max(0, min(pixel_max_y, height - 1))

            # Ensure min < max
            if pixel_min_x > pixel_max_x:
                pixel_min_x, pixel_max_x = pixel_max_x, pixel_min_x
            if pixel_min_y > pixel_max_y:
                pixel_min_y, pixel_max_y = pixel_max_y, pixel_min_y

            clipped_width = pixel_max_x - pixel_min_x
            clipped_height = pixel_max_y - pixel_min_y

            logger.info(f"Viewport bounds: [{min_lat:.6f}, {min_lon:.6f}] to [{max_lat:.6f}, {max_lon:.6f}]")
            logger.info(f"Clipped to pixels: x=[{pixel_min_x}, {pixel_max_x}], y=[{pixel_min_y}, {pixel_max_y}]")
            logger.info(f"Clipped dimensions: {clipped_width}×{clipped_height}")
            logger.info(f"Clipped pixels: {clipped_width * clipped_height:,}")

            # Read ALL embeddings (clipped to viewport)
            logger.info(f"\n💾 Step 1: Storing all pixel embeddings (clipped)...")
            logger.info(f"   Reading {clipped_width * clipped_height:,} pixels in viewport...")

            all_embeddings = []
            pixel_coords = []

            # Read in chunks to manage memory
            chunk_size = 256
            for y_start in range(pixel_min_y, pixel_max_y, chunk_size):
                y_end = min(y_start + chunk_size, pixel_max_y)
                logger.info(f"   Processing rows {y_start}-{y_end}...")

                # Update progress
                percent = min(100, int((y_start - pixel_min_y) / clipped_height * 100))
                progress.update("processing", f"Loading embeddings ({percent}%)...",
                              current_value=y_start - pixel_min_y, total_value=clipped_height, current_file="all_embeddings")

                # Read all bands for this chunk (clipped to viewport width)
                window = rasterio_windows.Window(pixel_min_x, y_start, clipped_width, y_end - y_start)
                chunk_data = src.read(window=window)  # (128, chunk_height, clipped_width)

                # Reshape: (128, chunk_height, clipped_width) → (chunk_height*clipped_width, 128)
                chunk_height = chunk_data.shape[1]
                chunk_embeddings = chunk_data.transpose(1, 2, 0).reshape(-1, EMBEDDING_DIM)
                all_embeddings.append(chunk_embeddings)

                # Generate pixel coordinates vectorized with meshgrid
                ys = np.arange(y_start, y_end)
                xs = np.arange(pixel_min_x, pixel_max_x)
                yy, xx = np.meshgrid(ys, xs, indexing='ij')
                pixel_coords.append(np.column_stack([xx.ravel(), yy.ravel()]))

            # Keep as float32 (no conversion to uint8 - embeddings are already float32 in GeoTIFF)
            all_embeddings = np.vstack(all_embeddings).astype(np.float32)
            logger.info(f"   ✓ Loaded all embeddings (clipped): {all_embeddings.shape}")
            logger.info(f"     Embeddings: {all_embeddings.shape[0]:,} pixels × {all_embeddings.shape[1]} dims")

            # Validate embeddings are not all zeros (indicates corrupt/empty mosaic)
            if np.count_nonzero(all_embeddings) == 0:
                error_msg = f"All embeddings are zero for {year} — mosaic may be corrupt or empty"
                logger.error(f"   ✗ {error_msg}")
                progress.error(error_msg)
                return False

            # Save all embeddings
            embeddings_file = output_dir / "all_embeddings.npy"
            np.save(embeddings_file, all_embeddings)
            logger.info(f"   ✓ Saved all embeddings: {embeddings_file}")
            embeddings_size_mb = embeddings_file.stat().st_size / (1024 * 1024)
            logger.info(f"     Size: {embeddings_size_mb:.1f} MB")

            # Save pixel coordinates (x, y) as numpy array for quick lookup
            coords_array = np.vstack(pixel_coords).astype(np.int32) if pixel_coords else np.empty((0, 2), dtype=np.int32)
            coords_file = output_dir / "pixel_coords.npy"
            np.save(coords_file, coords_array)

            # Step 2: Create metadata JSON
            logger.info(f"\n📋 Step 2: Creating metadata...")

            metadata = {
                "viewport_id": viewport_id,
                "viewport_bounds": list(bounds),  # (min_lat, min_lon, max_lat, max_lon)
                "mosaic_file": str(mosaic_file),
                "mosaic_height": height,
                "mosaic_width": width,
                "clipped_height": clipped_height,
                "clipped_width": clipped_width,
                "clipped_pixel_bounds": {"min_x": pixel_min_x, "max_x": pixel_max_x, "min_y": pixel_min_y, "max_y": pixel_max_y},
                "num_total_pixels": clipped_width * clipped_height,  # Only pixels in viewport
                "embedding_dim": EMBEDDING_DIM,
                "pixel_size_meters": 10,
                "crs": "EPSG:4326",
                "geotransform": {
                    "a": src.transform.a,  # pixel width (degrees)
                    "b": src.transform.b,  # rotation
                    "c": src.transform.c,  # x offset (longitude)
                    "d": src.transform.d,  # rotation
                    "e": src.transform.e,  # pixel height (degrees, negative)
                    "f": src.transform.f   # y offset (latitude)
                }
            }

            metadata_file = output_dir / "metadata.json"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"   ✓ Saved metadata: {metadata_file}")

    except Exception as e:
        logger.error(f"Error extracting embeddings for {year}: {e}")
        import traceback
        traceback.print_exc()
        progress.error(f"Embedding extraction failed: {e}")
        return False

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ Embedding extraction complete for {year}!")
    logger.info(f"\nFiles created in {output_dir}/:")
    logger.info(f"  - all_embeddings.npy ({embeddings_size_mb:.1f} MB)")
    logger.info(f"  - pixel_coords.npy ({coords_file.stat().st_size / 1024:.1f} KB)")
    logger.info(f"  - metadata.json")
    total_size = (embeddings_size_mb +
                  coords_file.stat().st_size / (1024 * 1024) +
                  metadata_file.stat().st_size / (1024 * 1024))
    logger.info(f"\nTotal size: {total_size:.1f} MB")
    logger.info("=" * 70)

    # Update progress to complete
    progress.complete(f"Extracted embeddings for {year}: {total_size:.1f} MB total")
    return True


def _process_year(args):
    """Worker function for parallel year processing."""
    viewport_id, bounds, year = args
    logger.info(f"\n📊 Extracting embeddings for year {year}...")
    success = extract_vectors_for_year(viewport_id, bounds, year)
    if not success:
        logger.warning(f"Failed to extract embeddings for {year}")
    return year, success


def extract_vectors():
    """Extract embeddings for all available years (in parallel)."""

    # Read active viewport
    try:
        viewport = get_active_viewport()
        viewport_id = viewport['viewport_id']
        bounds = viewport['bounds_tuple']
    except Exception as e:
        logger.error(f"Failed to read viewport: {e}")
        sys.exit(1)

    # Find available years (those with downloaded embeddings)
    available_years = []
    for year in YEARS:
        mosaic_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
        if mosaic_file.exists():
            available_years.append(year)

    if not available_years:
        logger.warning(f"No embeddings found for {viewport_id} — no data available for requested years")
        return

    logger.info(f"Found embeddings for years: {available_years}")

    # Process years in parallel
    max_workers = min(len(available_years), os.cpu_count() or 1)
    logger.info(f"Processing {len(available_years)} years with {max_workers} workers...")

    args_list = [(viewport_id, bounds, year) for year in available_years]

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_process_year, args_list))

    succeeded = [year for year, ok in results if ok]
    failed = [year for year, ok in results if not ok]
    if succeeded:
        logger.info(f"✅ Successfully processed years: {succeeded}")
    if failed:
        logger.warning(f"⚠️  Failed years: {failed}")


if __name__ == "__main__":
    extract_vectors()
