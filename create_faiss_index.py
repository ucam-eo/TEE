#!/usr/bin/env python3
"""
Create FAISS index from embedding mosaics for fast similarity search.

Two-step approach:
1. Create IVF-PQ index from sampled embeddings (every 4×4 pixels)
2. Store ALL embeddings as numpy array for threshold-based filtering

Enables queries like: "Find all pixels similar to embedding X with similarity > threshold"
"""

import sys
import numpy as np
import rasterio
from rasterio import windows as rasterio_windows
from pathlib import Path
import json
import logging

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
from lib.config import DATA_DIR, MOSAICS_DIR, FAISS_DIR

# Configuration
FAISS_INDICES_DIR = FAISS_DIR
SAMPLING_FACTOR = 4  # Every 4×4 pixels (reduces 19M → 1.2M vectors)
EMBEDDING_DIM = 128
YEARS = range(2017, 2026)  # Support 2017-2025

def check_faiss_installed():
    """Check if FAISS is installed, provide helpful message if not."""
    try:
        import faiss
        return True
    except ImportError:
        logger.error("FAISS not installed. Install with: pip install faiss-cpu")
        return False


def normalize_embeddings(embeddings):
    """Legacy function - NOT USED. Embeddings are already float32 in native range [-13.64, 17.22]."""
    return embeddings.astype(np.float32) / 255.0


def create_faiss_index_for_year(viewport_id, bounds, year):
    """Create FAISS index and store all embeddings for a specific year."""

    # Check FAISS availability
    if not check_faiss_installed():
        return False

    import faiss

    # Initialize progress tracker - use script-specific progress file to avoid conflicts with pipeline orchestrator
    progress = ProgressTracker(f"{viewport_id}_faiss")
    progress.update("starting", f"Creating FAISS index for {viewport_id} ({year})...")

    # Find mosaic file (year-specific)
    mosaic_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
    if not mosaic_file.exists():
        logger.warning(f"Mosaic file not found: {mosaic_file}")
        return False

    logger.info("=" * 70)
    logger.info(f"Creating FAISS Index for Embeddings ({year})")
    logger.info("=" * 70)
    logger.info(f"Viewport: {viewport_id}")
    logger.info(f"Year: {year}")
    logger.info(f"Mosaic file: {mosaic_file.name}")
    logger.info(f"Sampling factor: {SAMPLING_FACTOR}×{SAMPLING_FACTOR}")

    # Create output directory (year-specific)
    output_dir = FAISS_INDICES_DIR / viewport_id / str(year)
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

            # Step 1: Read sampled embeddings for FAISS index
            logger.info(f"\n📊 Step 1: Creating IVF-PQ index from sampled pixels...")
            logger.info(f"   Reading every {SAMPLING_FACTOR}×{SAMPLING_FACTOR} pixel...")

            sampled_embeddings = []
            sampled_coords = []

            # Read full viewport in chunks, then subsample in-memory (much faster than per-pixel reads)
            sample_chunk_size = 256
            for y_start in range(pixel_min_y, pixel_max_y, sample_chunk_size):
                y_end = min(y_start + sample_chunk_size, pixel_max_y)
                window = rasterio_windows.Window(pixel_min_x, y_start, clipped_width, y_end - y_start)
                chunk_data = src.read(window=window)  # (128, chunk_h, clipped_w)
                # Sample every SAMPLING_FACTOR pixel within this chunk
                y_offset = y_start - pixel_min_y
                y_sample_start = (-y_offset) % SAMPLING_FACTOR  # align to global grid
                x_sample_start = 0
                sampled = chunk_data[:, y_sample_start::SAMPLING_FACTOR, x_sample_start::SAMPLING_FACTOR]
                if sampled.size > 0:
                    sampled_embeddings.append(sampled.reshape(EMBEDDING_DIM, -1).T)
                    ys = np.arange(y_start + y_sample_start, y_end, SAMPLING_FACTOR)
                    xs = np.arange(pixel_min_x, pixel_max_x, SAMPLING_FACTOR)
                    yy, xx = np.meshgrid(ys, xs, indexing='ij')
                    sampled_coords.append(np.column_stack([xx.ravel(), yy.ravel()])[:sampled.shape[2] * sampled.shape[1]])

            # Keep as float32 (no conversion to uint8 - embeddings are already float32 in GeoTIFF)
            sampled_embeddings = np.vstack(sampled_embeddings).astype(np.float32) if sampled_embeddings else np.empty((0, EMBEDDING_DIM), dtype=np.float32)
            sampled_coords = np.vstack(sampled_coords) if sampled_coords else np.empty((0, 2), dtype=np.int32)
            logger.info(f"   ✓ Sampled {len(sampled_embeddings):,} pixels")
            progress.update("processing", f"Sampled {len(sampled_embeddings):,} pixels", current_file="embeddings_sampled")

            # Use float32 embeddings directly (no normalization needed - keep native range)
            sampled_embeddings_f32 = sampled_embeddings

            # Create IVF-PQ index
            logger.info(f"   Creating IVF-PQ index...")
            progress.update("processing", "Creating IVF-PQ index...", current_file="embeddings_index")
            # IVF: 1024 cells, PQ: 64 subquantizers (128/2 = 64)
            nlist = min(1024, max(100, len(sampled_embeddings) // 100))
            quantizer = faiss.IndexFlatL2(EMBEDDING_DIM)
            index = faiss.IndexIVFPQ(quantizer, EMBEDDING_DIM, nlist, 64, 8)
            index.train(sampled_embeddings_f32)
            index.add(sampled_embeddings_f32)

            # Save FAISS index
            index_file = output_dir / "embeddings.index"
            faiss.write_index(index, str(index_file))
            logger.info(f"   ✓ Saved FAISS index: {index_file}")
            index_size_mb = index_file.stat().st_size / (1024 * 1024)
            logger.info(f"     Index size: {index_size_mb:.1f} MB")
            progress.update("processing", f"Created index ({index_size_mb:.1f} MB)", current_file="embeddings_index")

            # Step 2: Read ALL embeddings for threshold-based search (clipped to viewport)
            logger.info(f"\n💾 Step 2: Storing all pixel embeddings (clipped)...")
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

            # Step 3: Create metadata JSON
            logger.info(f"\n📋 Step 3: Creating metadata...")

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
                "num_sampled_pixels": len(sampled_embeddings),
                "sampling_factor": SAMPLING_FACTOR,
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
                },
                "faiss_index_type": f"IVF{nlist},PQ64"
            }

            metadata_file = output_dir / "metadata.json"
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"   ✓ Saved metadata: {metadata_file}")

    except Exception as e:
        logger.error(f"Error creating FAISS index for {year}: {e}")
        import traceback
        traceback.print_exc()
        progress.error(f"FAISS creation failed: {e}")
        return False

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"✅ FAISS index creation complete for {year}!")
    logger.info(f"\nFiles created in {output_dir}/:")
    logger.info(f"  - embeddings.index ({index_size_mb:.1f} MB)")
    logger.info(f"  - all_embeddings.npy ({embeddings_size_mb:.1f} MB)")
    logger.info(f"  - pixel_coords.npy ({coords_file.stat().st_size / 1024:.1f} KB)")
    logger.info(f"  - metadata.json")
    total_size = (index_size_mb + embeddings_size_mb +
                  coords_file.stat().st_size / (1024 * 1024) +
                  metadata_file.stat().st_size / (1024 * 1024))
    logger.info(f"\nTotal size: {total_size:.1f} MB")
    logger.info("=" * 70)

    # Update progress to complete
    progress.complete(f"Created FAISS index for {year}: {total_size:.1f} MB total")
    return True


def create_faiss_index():
    """Create FAISS indices for all available years."""
    # Check FAISS availability
    if not check_faiss_installed():
        sys.exit(1)

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

    # Create FAISS index for each available year
    for year in available_years:
        logger.info(f"\n📊 Creating FAISS index for year {year}...")
        success = create_faiss_index_for_year(viewport_id, bounds, year)
        if not success:
            logger.warning(f"Failed to create FAISS index for {year}")


if __name__ == "__main__":
    create_faiss_index()
