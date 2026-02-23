"""Shared helpers extracted from web_server.py."""

import sys
import json
import time
import logging
import subprocess
from pathlib import Path

from django.http import JsonResponse

from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, FAISS_DIR, EMBEDDINGS_DIR, VIEWPORTS_DIR
from lib.viewport_utils import list_viewports, read_viewport_file

logger = logging.getLogger(__name__)

# Aliases
FAISS_INDICES_DIR = FAISS_DIR

# Get venv Python path for subprocess calls
PROJECT_ROOT = Path(__file__).parent.parent
VENV_PYTHON = PROJECT_ROOT / "venv" / "bin" / "python3"
if not VENV_PYTHON.exists():
    VENV_PYTHON = sys.executable
logger.info(f"Using Python: {VENV_PYTHON}")

# Year range constant (used in multiple views)
MIN_YEAR = 2017
MAX_YEAR = 2025

# Per-user disk quota (2 GB default)
USER_QUOTA_MB = 2048


def cleanup_viewport_embeddings(viewport_name, viewport_bounds):
    """Clean up cached embeddings tiles for a deleted/cancelled viewport."""
    import shutil

    deleted_items = []
    if not EMBEDDINGS_DIR.exists():
        return deleted_items

    # Collect bounds of all OTHER existing viewports
    other_bounds = []
    for vp_name in list_viewports():
        if vp_name == viewport_name:
            continue
        try:
            vp = read_viewport_file(vp_name)
            other_bounds.append(vp['bounds'])
        except Exception:
            continue

    def tile_overlaps_bounds(lon, lat, bounds):
        tile_min_lon = lon
        tile_max_lon = lon + 0.1
        tile_min_lat = lat
        tile_max_lat = lat + 0.1
        return (tile_max_lon > bounds['minLon'] and tile_min_lon < bounds['maxLon'] and
                tile_max_lat > bounds['minLat'] and tile_min_lat < bounds['maxLat'])

    for representation_dir in EMBEDDINGS_DIR.iterdir():
        if not representation_dir.is_dir():
            continue
        for year_dir in representation_dir.iterdir():
            if not year_dir.is_dir():
                continue
            empty_after_cleanup = True
            for grid_dir in list(year_dir.iterdir()):
                if not grid_dir.is_dir() or not grid_dir.name.startswith('grid_'):
                    if grid_dir.exists():
                        empty_after_cleanup = False
                    continue

                try:
                    coord_str = grid_dir.name[5:]  # strip 'grid_'
                    split_idx = None
                    for i in range(len(coord_str) - 1, 0, -1):
                        if coord_str[i] == '_' and coord_str[i-1].isdigit():
                            split_idx = i
                            break
                    if split_idx is None:
                        empty_after_cleanup = False
                        continue
                    grid_lon = float(coord_str[:split_idx])
                    grid_lat = float(coord_str[split_idx + 1:])
                except (ValueError, IndexError):
                    empty_after_cleanup = False
                    continue

                if not tile_overlaps_bounds(grid_lon, grid_lat, viewport_bounds):
                    empty_after_cleanup = False
                    continue

                shared = False
                for ob in other_bounds:
                    if tile_overlaps_bounds(grid_lon, grid_lat, ob):
                        shared = True
                        break
                if shared:
                    empty_after_cleanup = False
                    continue

                try:
                    shutil.rmtree(grid_dir)
                    deleted_items.append(f"embeddings: {representation_dir.name}/{year_dir.name}/{grid_dir.name}")
                    logger.info(f"[CLEANUP] Deleted embeddings tile: {grid_dir}")
                except Exception as e:
                    logger.warning(f"[CLEANUP] Could not delete {grid_dir}: {e}")
                    empty_after_cleanup = False

            if empty_after_cleanup:
                try:
                    year_dir.rmdir()
                    deleted_items.append(f"embeddings: {representation_dir.name}/{year_dir.name}/ (empty)")
                    logger.info(f"[CLEANUP] Removed empty year dir: {year_dir}")
                except OSError:
                    pass

    if deleted_items:
        logger.info(f"[CLEANUP] Cleaned up {len(deleted_items)} embeddings items for '{viewport_name}'")
    else:
        logger.info(f"[CLEANUP] No embeddings tiles to clean up for '{viewport_name}'")

    return deleted_items


def run_script(script_name, *args, timeout=1800):
    """Run a Python script using the venv Python interpreter."""
    cmd = [str(VENV_PYTHON), str(PROJECT_ROOT / script_name)] + list(args)
    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        logger.error(f"Script {script_name} failed with code {result.returncode}: {result.stderr[:500]}")
    return result


def wait_for_file(file_path, min_size_bytes=1024, max_retries=30, retry_interval=1.0):
    """Wait for a file to exist and reach minimum size."""
    file_path = Path(file_path)
    for attempt in range(max_retries):
        if file_path.exists():
            try:
                file_size = file_path.stat().st_size
                if file_size >= min_size_bytes:
                    logger.info(f"[WAIT] File ready after {attempt} retries: {file_path.name} ({file_size / (1024*1024):.1f} MB)")
                    return True
                else:
                    logger.debug(f"[WAIT] File exists but too small ({file_size} bytes), retrying...")
            except OSError as e:
                logger.debug(f"[WAIT] Could not stat file: {e}, retrying...")

        if attempt < max_retries - 1:
            time.sleep(retry_interval)

    logger.error(f"[WAIT] Timeout waiting for file: {file_path}")
    return False


def check_viewport_mosaics_exist(viewport_name):
    """Check if embeddings mosaic exists for a viewport (checks for ANY year available)."""
    if not MOSAICS_DIR.exists():
        return False
    embeddings_files = list(MOSAICS_DIR.glob(f"{viewport_name}_embeddings_*.tif"))
    return len(embeddings_files) > 0


def check_viewport_pyramids_exist(viewport_name):
    """Check if pyramid tiles exist for a viewport (checks for ANY year available)."""
    viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
    if not viewport_pyramids_dir.exists():
        return False
    for year_dir in viewport_pyramids_dir.glob("*"):
        if year_dir.is_dir() and year_dir.name not in ['satellite', 'rgb']:
            level_0_file = year_dir / "level_0.tif"
            if level_0_file.exists():
                return True
    return False


def get_viewport_data_size(viewport_name, active_viewport_name):
    """Calculate total data size for a viewport in MB."""
    total_size = 0

    if MOSAICS_DIR.exists():
        for mosaic_file in MOSAICS_DIR.glob(f'{viewport_name}_*.tif'):
            if mosaic_file.is_file():
                total_size += mosaic_file.stat().st_size

    faiss_dir = FAISS_INDICES_DIR / viewport_name
    if faiss_dir.exists():
        for item in faiss_dir.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size

    viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
    if viewport_pyramids_dir.exists():
        for item in viewport_pyramids_dir.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size

    return round(total_size / (1024 * 1024), 1)


def get_user_viewports(username):
    """Return list of viewport names owned by username (from *_config.json files)."""
    viewports = []
    if not VIEWPORTS_DIR.exists():
        return viewports
    for config_file in VIEWPORTS_DIR.glob('*_config.json'):
        try:
            with open(config_file) as f:
                config = json.load(f)
            if config.get('created_by') == username:
                name = config_file.stem.replace('_config', '')
                viewports.append(name)
        except Exception:
            pass
    return viewports


def get_user_total_data_size(username):
    """Sum get_viewport_data_size() for all viewports owned by username. Returns MB."""
    total = 0.0
    for vp_name in get_user_viewports(username):
        total += get_viewport_data_size(vp_name, None)
    return total


def estimate_viewport_size(bounds, num_years):
    """Estimate disk usage (MB) for a viewport from its bounds and year count."""
    import math
    min_lon, min_lat, max_lon, max_lat = bounds

    center_lat = (min_lat + max_lat) / 2
    meters_per_deg_lon = 111_320 * math.cos(math.radians(center_lat))
    meters_per_deg_lat = 110_540

    width_m = (max_lon - min_lon) * meters_per_deg_lon
    height_m = (max_lat - min_lat) * meters_per_deg_lat

    width_px = width_m / 10
    height_px = height_m / 10
    pixels = width_px * height_px

    embeddings_mb = pixels * 128 * 4 * 0.4 / (1024 * 1024)
    total_mb = embeddings_mb * 3 * num_years
    return total_mb


def parse_json_body(request):
    """Parse JSON from request body. Returns (data_dict, error_response).

    On success: returns (dict, None)
    On failure: returns (None, JsonResponse with 400 status)
    """
    if not request.body:
        return {}, None
    try:
        return json.loads(request.body), None
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({'success': False, 'error': 'Invalid JSON in request body'}, status=400)
