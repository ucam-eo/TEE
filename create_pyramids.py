#!/usr/bin/env python3
"""
Create image pyramids for satellite RGB imagery.

Tessera embedding pyramids are now created by process_viewport.py.
This script only handles satellite RGB pyramid creation.

Output structure:
pyramids/
  └── <viewport>/
      └── satellite/
          ├── level_0.tif  (full resolution)
          ├── level_1.tif  (1/2 resolution)
          └── ...
"""

import sys
import numpy as np
import rasterio
from rasterio.enums import Resampling
from pathlib import Path
from scipy.ndimage import zoom

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))
from lib.progress_tracker import ProgressTracker
from lib.config import MOSAICS_DIR, PYRAMIDS_DIR

# Configuration
PYRAMIDS_BASE_DIR = PYRAMIDS_DIR
NUM_ZOOM_LEVELS = 6


def create_pyramid_level(input_file, output_file, scale_factor, target_width, target_height, use_nearest=True):
    """Create pyramid level - high-resolution RECTANGULAR output, with 2x2 averaging between levels.

    Maintains aspect ratio by using rectangular target dimensions instead of square.
    This preserves crisp 10m resolution boundaries without distortion.
    Uses nearest-neighbor resampling throughout for speed and crisp boundaries.

    Args:
        input_file: Path to the input GeoTIFF (previous pyramid level)
        output_file: Path to write the output GeoTIFF
        scale_factor: The pyramid level number (1, 2, 3, etc.)
        target_width: Target output width (maintains high resolution)
        target_height: Target output height (maintains aspect ratio)
        use_nearest: Resampling mode (default True = nearest-neighbor).
    """
    resampling_method = Resampling.nearest if use_nearest else Resampling.lanczos

    with rasterio.open(input_file) as src:
        original_height = src.height
        original_width = src.width

        # Calculate intermediate downsampled dimensions
        intermediate_height = max(1, int(original_height / 2))
        intermediate_width = max(1, int(original_width / 2))

        # Step 1: Downsample by 2x using nearest-neighbor
        downsampled_data = src.read(
            out_shape=(src.count, intermediate_height, intermediate_width),
            resampling=resampling_method
        )

        # Step 2: Upsample back to target size using scipy.ndimage.zoom (all bands at once)
        scale_y = target_height / downsampled_data.shape[1]
        scale_x = target_width / downsampled_data.shape[2]
        final_data = zoom(downsampled_data, (1, scale_y, scale_x), order=0)

        # Update transform to reflect the effective resolution change
        transform = src.transform * src.transform.scale(
            (src.width / intermediate_width) * (intermediate_width / target_width),
            (src.height / intermediate_height) * (intermediate_height / target_height)
        )

        # Update profile
        profile = src.profile.copy()
        profile.update({
            'height': target_height,
            'width': target_width,
            'transform': transform
        })

        # Write image
        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(final_data)

    size_kb = output_file.stat().st_size / 1024
    spatial_scale = 10 * (2 ** scale_factor)  # 20m, 40m, 80m, etc.
    print(f"    Level {scale_factor}: {target_width}x{target_height} @ {spatial_scale}m/pixel [nearest] ({size_kb:.1f} KB)")


def upscale_image(source_file, output_file, upscale_factor=3):
    """Upscale an RGB image with nearest-neighbor for crisp pixel boundaries."""
    print(f"  Upscaling {source_file.name} by {upscale_factor}x with nearest-neighbor...")

    with rasterio.open(source_file) as src:
        data = src.read()

        new_height = src.height * upscale_factor
        new_width = src.width * upscale_factor

        # Vectorized nearest-neighbor upscaling (pixel repetition)
        upscaled_data = np.repeat(np.repeat(data, upscale_factor, axis=1), upscale_factor, axis=2)

        # Update transform
        transform = src.transform * src.transform.scale(
            1.0 / upscale_factor,
            1.0 / upscale_factor
        )

        # Update profile
        profile = src.profile.copy()
        profile.update({
            'height': new_height,
            'width': new_width,
            'transform': transform
        })

        with rasterio.open(output_file, 'w', **profile) as dst:
            dst.write(upscaled_data)

    print(f"  Upscaled to {new_width}x{new_height}")
    return output_file


def create_pyramids_for_image(source_file, output_dir, name, upscale_factor=1):
    """Create all pyramid levels for a single image - high-resolution RECTANGULAR output."""
    print(f"\nCreating pyramids for {name}...")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Level 0: Native resolution (copy source file)
    level_0 = output_dir / "level_0.tif"

    with rasterio.open(source_file) as src:
        profile = src.profile.copy()
        data = src.read()
        source_width = src.width
        source_height = src.height
        with rasterio.open(level_0, 'w', **profile) as dst:
            dst.write(data)

    size_kb = level_0.stat().st_size / 1024
    print(f"    Level 0: {source_width}x{source_height} @ 10m/pixel ({size_kb:.1f} KB)")

    # Calculate rectangular target dimensions based on source aspect ratio
    TARGET_BASE = 4408
    if source_width >= source_height:
        target_width = TARGET_BASE
        target_height = int(TARGET_BASE * source_height / source_width)
    else:
        target_height = TARGET_BASE
        target_width = int(TARGET_BASE * source_width / source_height)

    # Create downsampled levels
    prev_level_file = level_0
    for level in range(1, NUM_ZOOM_LEVELS):
        level_file = output_dir / f"level_{level}.tif"
        create_pyramid_level(prev_level_file, level_file, level, target_width, target_height, use_nearest=True)
        prev_level_file = level_file

    print(f"  Created {NUM_ZOOM_LEVELS} zoom levels in {output_dir}")


def main():
    """Create satellite pyramids only."""
    try:
        from lib.viewport_utils import get_active_viewport
        viewport = get_active_viewport()
        viewport_id = viewport['viewport_id']
    except Exception as e:
        print(f"Warning: Could not read active viewport: {e}")
        viewport_id = None

    progress = ProgressTracker(f"{viewport_id}_pyramids" if viewport_id else "pyramids")
    progress.update("starting", "Initializing satellite pyramid creation...")

    print("=" * 70)
    print("Creating Satellite RGB Pyramids")
    print("=" * 70)
    if viewport_id:
        print(f"Viewport: {viewport_id}")

    PYRAMIDS_BASE_DIR.mkdir(exist_ok=True)

    # Process satellite RGB (upscale 3x to match Tessera resolution for consistency)
    if viewport_id:
        satellite_file = MOSAICS_DIR / f"{viewport_id}_satellite_rgb.tif"
    else:
        satellite_file = None

    if satellite_file and satellite_file.exists():
        satellite_upscaled_file = PYRAMIDS_BASE_DIR / "temp_satellite_upscaled.tif"
        upscale_image(satellite_file, satellite_upscaled_file, upscale_factor=3)

        viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport_id
        viewport_pyramids_dir.mkdir(parents=True, exist_ok=True)
        satellite_dir = viewport_pyramids_dir / "satellite"
        create_pyramids_for_image(satellite_upscaled_file, satellite_dir, "Satellite RGB", upscale_factor=1)
        satellite_upscaled_file.unlink()

        total_size = sum(f.stat().st_size for f in satellite_dir.rglob("*.tif"))
        total_mb = total_size / (1024 * 1024)
        print(f"\nSatellite pyramid size: {total_mb:.1f} MB")
        progress.complete(f"Created satellite pyramids: {total_mb:.1f} MB")
    else:
        print(f"\nSatellite RGB file not found: {satellite_file}")
        progress.complete("No satellite file found")

    print("\n" + "=" * 70)
    print("Satellite pyramid generation complete!")


if __name__ == "__main__":
    main()
