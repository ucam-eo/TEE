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
    import geotessera as gt
except ImportError as e:
    print(f"IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

try:
    from lib.viewport_utils import get_active_viewport
    from lib.progress_tracker import ProgressTracker
    from lib.config import DATA_DIR, EMBEDDINGS_DIR, PYRAMIDS_DIR, VECTORS_DIR, pyramid_exists
except ImportError as e:
    print(f"LIB IMPORT ERROR: {e}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger(__name__)

DEFAULT_YEARS = range(2018, 2026)
EMBEDDING_DIM = 128
NUM_ZOOM_LEVELS = 6


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
    pyramids_ok = pyramid_exists(year_pyramids_dir)
    vectors_ok = (year_vectors_dir / 'all_embeddings_uint8.npy.gz').exists()
    if pyramids_ok and vectors_ok:
        print(f"  [{year}] Already processed (pyramids + vectors exist), skipping")
        return (year, True, "already exists")

    # --- FETCH MOSAIC ---
    _progress(1, f"[{year}] Connecting to GeoTessera...")
    print(f"  [{year}] Fetching mosaic...")
    t0 = _time.monotonic()

    max_retries = 3
    mosaic = None
    for attempt in range(1, max_retries + 1):
        try:
            # Run fetch in a thread so we can report download progress
            import threading as _threading
            _fetch_result = [None, None, None, None]  # mosaic, transform, crs, error
            _fetch_status = [None]  # latest status message from GeoTessera callback
            def _do_fetch():
                try:
                    def _gt_progress(current, total, status):
                        _fetch_status[0] = f"{status} ({current}/{total})"
                    m, t, c = tessera.fetch_mosaic_for_region(
                        bbox=bounds, year=year,
                        target_crs='EPSG:4326', auto_download=True,
                        progress_callback=_gt_progress,
                    )
                    _fetch_result[:3] = [m, t, c]
                except Exception as ex:
                    _fetch_result[3] = ex

            # Snapshot embeddings dir size before fetch
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
            tick = 0
            data_started = False
            while ft.is_alive():
                ft.join(timeout=2)
                if ft.is_alive():
                    tick += 1
                    downloaded_mb = (_dir_size(str(EMBEDDINGS_DIR)) - size_before) / (1024 * 1024)
                    elapsed = _time.monotonic() - t0
                    # Asymptotic percentage (0→55%) so the bar keeps moving
                    fetch_pct = max(1, int(55 * (1 - 1 / (1 + tick * 0.15))))
                    gt_status = _fetch_status[0]
                    if downloaded_mb > 0.1:
                        if not data_started:
                            data_started = True
                            print(f"  [{year}] Download started after {elapsed:.0f}s")
                        speed_mbs = downloaded_mb / elapsed if elapsed > 0 else 0
                        _progress(fetch_pct, f"[{year}] Downloading tiles ({downloaded_mb:.1f} MB, {speed_mbs:.1f} MB/s)")
                    elif gt_status:
                        _progress(fetch_pct, f"[{year}] {gt_status}")
                    else:
                        _progress(fetch_pct, f"[{year}] Waiting for GeoTessera registry ({elapsed:.0f}s)")
            if _fetch_result[3] is not None:
                raise _fetch_result[3]
            mosaic, transform, crs = _fetch_result[0], _fetch_result[1], _fetch_result[2]
            break
        except Exception as e:
            # Simplify known error messages
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

    height, width = mosaic.shape[:2]
    elapsed = _time.monotonic() - t0
    print(f"  [{year}] Fetched {width}x{height} mosaic ({elapsed:.1f}s)")

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

    # Process years in parallel
    max_workers = min(len(years_to_process), os.cpu_count() or 1)
    args_list = [
        (viewport_id, bounds, year, pyramids_dir, vectors_dir)
        for year in years_to_process
    ]

    progress.update("processing", f"Connecting to GeoTessera...", percent=1)
    print(f"  Initializing GeoTessera...")
    t_init = _time.monotonic()
    tessera = gt.GeoTessera(embeddings_dir=str(EMBEDDINGS_DIR))
    init_secs = _time.monotonic() - t_init
    print(f"  GeoTessera ready ({init_secs:.1f}s)")
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
