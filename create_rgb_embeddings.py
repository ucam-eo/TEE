#!/usr/bin/env python3
"""
Create RGB visualization from Tessera embeddings.
Uses the first 3 bands directly (no PCA).
"""

import sys
import numpy as np
import rasterio
from pathlib import Path
# from tqdm import tqdm  # Disabled to reduce output

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.viewport_utils import get_active_viewport
from lib.config import DATA_DIR, MOSAICS_DIR

# Configuration
OUTPUT_DIR = MOSAICS_DIR / "rgb"
YEARS = range(2017, 2026)  # Support 2017-2025
N_COMPONENTS = 3  # RGB
CHUNK_SIZE = 1000  # Process in chunks to save memory

def create_rgb_from_embeddings(year, viewport_id=None, bounds=None):
    """Create RGB visualization from first 3 embedding bands (clipped to viewport)."""

    # Use viewport-specific filename
    if viewport_id:
        input_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
        output_file = OUTPUT_DIR / f"{viewport_id}_{year}_rgb.tif"
    else:
        print(f"⚠️  Skipping RGB {year}: No viewport specified")
        return False

    if output_file.exists():
        print(f"✓ Skipping {year}: RGB file already exists")
        return True

    if not input_file.exists():
        print(f"⚠️  Skipping {year}: File not found")
        return False

    print(f"\n📊 Processing {year} embeddings...")

    with rasterio.open(input_file) as src:
        # Get dimensions
        n_bands = src.count
        height = src.height
        width = src.width

        print(f"  Input: {width}×{height} with {n_bands} bands")

        # Clip to viewport bounds if provided
        if bounds:
            transform = src.transform
            min_lon, min_lat, max_lon, max_lat = bounds  # bounds_tuple is (min_lon, min_lat, max_lon, max_lat)

            # Convert lat/lon bounds to pixel coordinates
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

            print(f"  Clipping to viewport: x=[{pixel_min_x}, {pixel_max_x}], y=[{pixel_min_y}, {pixel_max_y}]")
            print(f"  Clipped dimensions: {clipped_width}×{clipped_height}")
        else:
            pixel_min_x = 0
            pixel_min_y = 0
            clipped_width = width
            clipped_height = height

        # Read first 3 bands directly (no PCA)
        print(f"  Reading first {N_COMPONENTS} bands as RGB...")
        if n_bands < N_COMPONENTS:
            print(f"  ⚠️  WARNING: Only {n_bands} bands available, need {N_COMPONENTS}")
            return False

        # Read first 3 bands (clipped to viewport)
        from rasterio import windows as rasterio_windows
        if bounds:
            window = rasterio_windows.Window(pixel_min_x, pixel_min_y, clipped_width, clipped_height)
        else:
            window = None

        pca_image = src.read([1, 2, 3], window=window).astype(np.float32)

        print(f"  Using first {N_COMPONENTS} bands directly as RGB")

        # Normalize to 0-255 for RGB visualization
        print(f"  Normalizing to RGB (0-255)...")
        rgb_image = np.zeros_like(pca_image, dtype=np.uint8)

        for i in range(N_COMPONENTS):
            band = pca_image[i]
            valid_band = band[~np.isnan(band)]

            if len(valid_band) > 0:
                # Use percentile normalization (2nd to 98th percentile)
                p2, p98 = np.percentile(valid_band, [2, 98])

                # Clip and scale to 0-255
                band_clipped = np.clip(band, p2, p98)
                band_normalized = ((band_clipped - p2) / (p98 - p2) * 255).astype(np.uint8)
                rgb_image[i] = band_normalized

                print(f"    Band {i+1}: range [{p2:.2f}, {p98:.2f}] → [0, 255]")

        # Save RGB result
        print(f"  Saving to {output_file}...")
        OUTPUT_DIR.mkdir(exist_ok=True)

        profile = src.profile.copy()
        profile.update({
            'count': N_COMPONENTS,
            'dtype': 'uint8',
            'width': clipped_width,
            'height': clipped_height
        })

        # Update geotransform to start at the clipped region
        if bounds:
            # Shift geotransform to start at clipped region
            new_transform = rasterio.transform.Affine(
                transform.a, transform.b, transform.c + pixel_min_x * transform.a,
                transform.d, transform.e, transform.f + pixel_min_y * transform.e
            )
            profile['transform'] = new_transform

        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(rgb_image)

        # Print info
        print(f"\n  ✓ RGB visualization complete!")
        print(f"    Using first 3 embedding dimensions as RGB")

        size_mb = output_file.stat().st_size / (1024 * 1024)
        print(f"    Output: {output_file} ({size_mb:.1f} MB)")

        return True

def main():
    """Process all years."""
    print("=" * 70)
    print("Creating RGB visualizations from embedding first 3 bands")
    print("=" * 70)

    # Try to get active viewport, but continue if not available for backwards compatibility
    viewport_id = None
    bounds = None
    try:
        viewport = get_active_viewport()
        viewport_id = viewport['viewport_id']
        bounds = viewport['bounds_tuple']
        print(f"Viewport: {viewport_id}")
        print(f"Bounds: {bounds}")
    except Exception as e:
        print(f"Warning: Could not read active viewport: {e}")
        print("Processing any available mosaic files (no clipping)...")

    success_count = 0

    for year in YEARS:
        # Skip years without downloaded embeddings
        input_file = MOSAICS_DIR / f"{viewport_id}_embeddings_{year}.tif"
        if not input_file.exists():
            print(f"⚠️  Skipping {year}: Embeddings not found")
            continue

        if create_rgb_from_embeddings(year, viewport_id, bounds):
            success_count += 1

    print("\n" + "=" * 70)
    print(f"✅ Complete! Processed {success_count} years")
    print(f"\nRGB embeddings saved in: {OUTPUT_DIR.absolute()}")
    print("\nNext steps:")
    print("  1. Create pyramids: run create_pyramids.py")

if __name__ == "__main__":
    main()
