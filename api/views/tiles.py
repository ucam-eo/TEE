"""Tile server views — serves map tiles from PNG pyramids."""

import io
import json
import hashlib
import logging
from pathlib import Path

from PIL import Image
from django.http import HttpResponse, JsonResponse

from lib.config import PYRAMIDS_DIR
from lib.viewport_utils import validate_viewport_name
from lib.tile_renderer import (
    tile_to_bbox,
    get_pyramid_path,
    render_tile_png,
    _load_pyramid_meta,
)

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
    Returns (path_str, mtime) tuple, or None if file doesn't exist.
    Delegates path resolution to lib.tile_renderer.get_pyramid_path; caches result."""
    pyramid_level = max(0, min(5, (14 - zoom_level) // 2))
    key = f"{viewport}_{map_id}_{pyramid_level}"

    result = get_pyramid_path(viewport, map_id, zoom_level)
    if not result:
        _readers.pop(key, None)
        return None

    path, mtime = result
    cached = _readers.get(key)
    if not cached or cached[1] != mtime or cached[0] != path:
        _readers[key] = (path, mtime)
    return _readers[key]


def _tile_response(png_bytes, cache_max_age=86400):
    """Return a PNG HttpResponse with cache headers."""
    resp = HttpResponse(png_bytes, content_type='image/png')
    resp['Cache-Control'] = f'public, max-age={cache_max_age}'
    return resp


def _transparent_tile():
    """Return a cached transparent 256x256 PNG (not cached by browser — data may appear later)."""
    return _tile_response(_get_transparent_png(), cache_max_age=0)


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

        path, mtime = reader_info

        # ETag / 304 support (includes mtime so stale cache is busted)
        etag = hashlib.md5(f"{path}:{mtime}:{z}:{x}:{y}".encode()).hexdigest()
        if request.META.get('HTTP_IF_NONE_MATCH') == etag:
            return HttpResponse(status=304)

        try:
            png_bytes = render_tile_png(path, z, x, y, _mtime=mtime)
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

        return JsonResponse({'error': 'Pyramid not found (run migration if .tif files exist)'}, status=404)

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
                        if (yd / 'level_0.png').exists():
                            available_maps.append(year)

                if (viewport_dir / 'satellite' / 'level_0.png').exists():
                    available_maps.append('satellite')

                rgb_dir = viewport_dir / 'rgb' / '2024'
                if (rgb_dir / 'level_0.png').exists():
                    available_maps.append('rgb')

                if available_maps:
                    viewports_data[viewport_name] = available_maps

    return JsonResponse({'status': 'ok', 'viewports': viewports_data})
