"""Tile server views — serves map tiles from pyramid GeoTIFFs and PNGs."""

import io
import json
import math
import hashlib
import logging
import functools
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows
from PIL import Image
from rio_tiler.io import Reader
from django.http import HttpResponse, JsonResponse

from lib.config import PYRAMIDS_DIR
from lib.viewport_utils import validate_viewport_name

logger = logging.getLogger(__name__)

PYRAMIDS_BASE_DIR = PYRAMIDS_DIR
YEARS = [str(y) for y in range(2018, 2026)] + ['satellite']
_VALID_MAP_IDS = {str(y) for y in range(2018, 2026)} | {'satellite', 'rgb'}

# Cache for tile reader paths
_readers = {}

# Pre-compute transparent tile PNG bytes (used for missing/out-of-bounds tiles)
_TRANSPARENT_PNG = None


def _get_transparent_png():
    global _TRANSPARENT_PNG
    if _TRANSPARENT_PNG is None:
        img = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        _TRANSPARENT_PNG = buf.getvalue()
    return _TRANSPARENT_PNG


def _get_reader(viewport, map_id, zoom_level):
    """Get or create a reader path for a specific viewport, map, and zoom level.
    Returns (path_str, mtime, is_png) tuple, or None if file doesn't exist.
    Tries PNG first, falls back to GeoTIFF.
    mtime is included so render-tile cache auto-invalidates when file changes."""
    pyramid_level = max(0, min(5, (14 - zoom_level) // 2))
    key = f"{viewport}_{map_id}_{pyramid_level}"

    viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport
    if map_id == 'satellite':
        year_dir = viewport_pyramids_dir / 'satellite'
    elif map_id == 'rgb':
        year_dir = viewport_pyramids_dir / 'rgb' / '2024'
    else:
        year_dir = viewport_pyramids_dir / map_id

    # Try PNG first, then TIF
    png_path = year_dir / f'level_{pyramid_level}.png'
    tif_path = year_dir / f'level_{pyramid_level}.tif'

    for path, is_png in [(png_path, True), (tif_path, False)]:
        try:
            mtime = int(path.stat().st_mtime)
        except FileNotFoundError:
            continue
        cached = _readers.get(key)
        if not cached or cached[1] != mtime or cached[0] != str(path):
            _readers[key] = (str(path), mtime, is_png)
        return _readers[key]

    _readers.pop(key, None)
    return None


def _tile_to_bbox(x, y, zoom):
    """Convert tile coordinates to bounding box."""
    n = 2.0 ** zoom
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_min_rad = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
    lat_max = math.degrees(lat_max_rad)
    lat_min = math.degrees(lat_min_rad)
    return (lon_min, lat_min, lon_max, lat_max)


def _tile_response(png_bytes, cache_max_age=86400):
    """Return a PNG HttpResponse with cache headers."""
    resp = HttpResponse(png_bytes, content_type='image/png')
    resp['Cache-Control'] = f'public, max-age={cache_max_age}'
    return resp


def _transparent_tile():
    """Return a cached transparent 256x256 PNG (not cached by browser — data may appear later)."""
    return _tile_response(_get_transparent_png(), cache_max_age=0)


@functools.lru_cache(maxsize=2048)
def _render_tile(tif_path, z, x, y, _mtime=0):
    """Render a single tile to PNG bytes.  Cached by (path, coords, mtime)."""
    TILE_SIZE = 256
    bbox = _tile_to_bbox(x, y, z)

    with rasterio.open(tif_path) as src:
        window = rasterio.windows.from_bounds(
            bbox[0], bbox[1], bbox[2], bbox[3], src.transform
        )

        col_off = int(round(window.col_off))
        row_off = int(round(window.row_off))
        width = int(round(window.width))
        height = int(round(window.height))

        if width <= 0 or height <= 0:
            return None

        read_col_off = max(0, col_off)
        read_row_off = max(0, row_off)
        read_col_end = min(src.width, col_off + width)
        read_row_end = min(src.height, row_off + height)
        read_width = read_col_end - read_col_off
        read_height = read_row_end - read_row_off

        if read_width <= 0 or read_height <= 0:
            return None

        pixel_window = rasterio.windows.Window(
            read_col_off, read_row_off, read_width, read_height
        )
        data = src.read(window=pixel_window)

        if data.shape[0] == 1:
            rgb = np.stack([data[0], data[0], data[0]], axis=0)
        else:
            rgb = data[:3]

        tile_x_start = max(0, -col_off)
        tile_y_start = max(0, -row_off)

        full_data = np.zeros((3, height, width), dtype=np.uint8)
        full_data[
            :,
            tile_y_start:tile_y_start + read_height,
            tile_x_start:tile_x_start + read_width,
        ] = rgb

        rgb_t = np.transpose(full_data, (1, 2, 0))
        img = Image.fromarray(rgb_t.astype(np.uint8), mode='RGB')
        img = img.resize((TILE_SIZE, TILE_SIZE), Image.NEAREST)

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()


@functools.lru_cache(maxsize=64)
def _load_pyramid_meta(meta_path, _mtime=0):
    """Load and cache pyramid_meta.json.  Keyed by (path, mtime)."""
    with open(meta_path) as f:
        return json.load(f)


@functools.lru_cache(maxsize=2048)
def _render_tile_png(png_path, z, x, y, _mtime=0):
    """Render a single tile from a PNG pyramid level.  Cached by (path, coords, mtime)."""
    TILE_SIZE = 256
    bbox = _tile_to_bbox(x, y, z)

    # Derive meta path and level index from png_path
    png_p = Path(png_path)
    meta_path = str(png_p.parent / 'pyramid_meta.json')
    level_idx = int(png_p.stem.split('_')[1])

    try:
        meta_mtime = int(Path(meta_path).stat().st_mtime)
    except FileNotFoundError:
        return None

    meta = _load_pyramid_meta(meta_path, _mtime=meta_mtime)
    level = meta['levels'][level_idx]
    t = level['transform']
    img_width = level['width']
    img_height = level['height']

    # Compute pixel window (same math as rasterio.windows.from_bounds)
    col_off = (bbox[0] - t['c']) / t['a']
    row_off = (bbox[3] - t['f']) / t['e']
    col_end = (bbox[2] - t['c']) / t['a']
    row_end = (bbox[1] - t['f']) / t['e']

    col_off_i = int(round(col_off))
    row_off_i = int(round(row_off))
    width = int(round(col_end - col_off))
    height = int(round(row_end - row_off))

    if width <= 0 or height <= 0:
        return None

    read_col_off = max(0, col_off_i)
    read_row_off = max(0, row_off_i)
    read_col_end = min(img_width, col_off_i + width)
    read_row_end = min(img_height, row_off_i + height)
    read_width = read_col_end - read_col_off
    read_height = read_row_end - read_row_off

    if read_width <= 0 or read_height <= 0:
        return None

    # Read region from PNG
    with Image.open(png_path) as img:
        cropped = np.array(img.crop((read_col_off, read_row_off, read_col_end, read_row_end)))

    # Place into full-size canvas
    tile_x_start = max(0, -col_off_i)
    tile_y_start = max(0, -row_off_i)

    full_data = np.zeros((height, width, 3), dtype=np.uint8)
    full_data[
        tile_y_start:tile_y_start + read_height,
        tile_x_start:tile_x_start + read_width,
    ] = cropped

    img_out = Image.fromarray(full_data, mode='RGB')
    img_out = img_out.resize((TILE_SIZE, TILE_SIZE), Image.NEAREST)

    buf = io.BytesIO()
    img_out.save(buf, format='PNG')
    return buf.getvalue()


def get_tile(request, viewport, map_id, z, x, y):
    """Serve a map tile for a specific viewport."""
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return HttpResponse("Invalid viewport name", status=400)
    if map_id not in _VALID_MAP_IDS:
        return HttpResponse("Invalid map_id", status=400)

    try:
        reader_info = _get_reader(viewport, map_id, z)

        if not reader_info:
            return _transparent_tile()

        path, mtime, is_png = reader_info

        # ETag / 304 support (includes mtime so stale cache is busted)
        etag = hashlib.md5(f"{path}:{mtime}:{z}:{x}:{y}".encode()).hexdigest()
        if request.META.get('HTTP_IF_NONE_MATCH') == etag:
            return HttpResponse(status=304)

        try:
            if is_png:
                png_bytes = _render_tile_png(path, z, x, y, _mtime=mtime)
            else:
                png_bytes = _render_tile(path, z, x, y, _mtime=mtime)
            if png_bytes is None:
                return _transparent_tile()
            resp = _tile_response(png_bytes)
            resp['ETag'] = etag
            return resp

        except Exception as e:
            logger.error("Error reading tile %s/%s/%s/%s: %s", map_id, z, x, y, e)
            return _transparent_tile()

    except Exception as e:
        logger.error("Error serving tile: %s", e)
        return HttpResponse(f"Error: {e}", status=500)


def get_bounds(request, viewport, map_id):
    """Get bounds for a map in a specific viewport."""
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return JsonResponse({'error': 'Invalid viewport name'}, status=400)
    if map_id not in _VALID_MAP_IDS:
        return JsonResponse({'error': 'Invalid map_id'}, status=400)
    try:
        viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport

        if map_id == 'satellite':
            year_dir = viewport_pyramids_dir / 'satellite'
        elif map_id == 'rgb':
            year_dir = viewport_pyramids_dir / 'rgb' / '2024'
        else:
            year_dir = viewport_pyramids_dir / map_id

        # Try PNG meta first
        meta_path = year_dir / 'pyramid_meta.json'
        if meta_path.exists():
            with open(meta_path) as f:
                meta = json.load(f)
            level = meta['levels'][0]
            t = level['transform']
            w, h = level['width'], level['height']
            bounds = (t['c'], t['f'] + t['e'] * h, t['c'] + t['a'] * w, t['f'])
            return JsonResponse({
                'bounds': bounds,
                'center': [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2],
            })

        # Fall back to GeoTIFF
        tif_path = year_dir / 'level_0.tif'
        if tif_path.exists():
            with Reader(str(tif_path)) as src:
                bounds = src.bounds
                return JsonResponse({
                    'bounds': bounds,
                    'center': [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2],
                })

        return JsonResponse({'error': 'File not found'}, status=404)

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def tile_health(request):
    """Health check — returns available maps for all viewports."""
    viewports_data = {}

    if PYRAMIDS_BASE_DIR.exists():
        for viewport_dir in PYRAMIDS_BASE_DIR.iterdir():
            if viewport_dir.is_dir():
                viewport_name = viewport_dir.name
                available_maps = []

                for year in YEARS:
                    if year != 'satellite':
                        yd = viewport_dir / year
                        if (yd / 'level_0.png').exists() or (yd / 'level_0.tif').exists():
                            available_maps.append(year)

                if (viewport_dir / 'satellite' / 'level_0.tif').exists():
                    available_maps.append('satellite')

                rgb_dir = viewport_dir / 'rgb' / '2024'
                if (rgb_dir / 'level_0.png').exists() or (rgb_dir / 'level_0.tif').exists():
                    available_maps.append('rgb')

                if available_maps:
                    viewports_data[viewport_name] = available_maps

    return JsonResponse({'status': 'ok', 'viewports': viewports_data})
