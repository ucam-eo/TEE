"""Tile server views — serves map tiles from PNG pyramids."""

import io
import json
import hashlib
import logging
from pathlib import Path

import numpy as np
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
from api.helpers import MIN_YEAR, MAX_YEAR

logger = logging.getLogger(__name__)

PYRAMIDS_BASE_DIR = PYRAMIDS_DIR
_VALID_MAP_IDS = {str(y) for y in range(MIN_YEAR, MAX_YEAR + 1)}

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


def export_geotiff(request, viewport, map_id):
    """Download a year's embedding visualisation as a georeferenced GeoTIFF.

    Full resolution (pyramid level 0), RGB, in the pyramid's own CRS
    (EPSG:4326) -- for making maps in QGIS/ArcGIS instead of screen grabs.
    """
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return HttpResponse("Invalid viewport name", status=400)
    if map_id not in _VALID_MAP_IDS:
        return HttpResponse("Invalid map_id", status=400)

    year_dir = PYRAMIDS_BASE_DIR / viewport / map_id
    png_path = year_dir / 'level_0.png'
    meta_path = year_dir / 'pyramid_meta.json'
    if not png_path.exists() or not meta_path.exists():
        return HttpResponse("No embeddings for this viewport/year", status=404)

    try:
        tif_bytes = render_geotiff(png_path, meta_path, viewport, map_id)
    except Exception as e:
        logger.error("GeoTIFF export failed for %s/%s: %s", viewport, map_id, e)
        return HttpResponse(f"Export failed: {e}", status=500)

    resp = HttpResponse(tif_bytes, content_type='image/tiff')
    resp['Content-Disposition'] = (
        f'attachment; filename="{viewport}_{map_id}_embeddings.tif"')
    return resp


def render_geotiff(png_path, meta_path, viewport, map_id):
    """Encode a pyramid level-0 PNG as DEFLATE-compressed GeoTIFF bytes."""
    import rasterio
    from rasterio.io import MemoryFile
    from affine import Affine

    with open(meta_path) as f:
        meta = json.load(f)
    level = meta['levels'][0]
    t = level['transform']
    with Image.open(png_path) as img:
        rgb = np.asarray(img.convert('RGB'))  # (H, W, 3)

    profile = {
        'driver': 'GTiff',
        'width': rgb.shape[1],
        'height': rgb.shape[0],
        'count': 3,
        'dtype': 'uint8',
        'crs': meta.get('crs', 'EPSG:4326'),
        'transform': Affine(t['a'], t['b'], t['c'], t['d'], t['e'], t['f']),
        'compress': 'deflate',
        'photometric': 'RGB',
    }
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(np.transpose(rgb, (2, 0, 1)))
            dst.update_tags(
                viewport=viewport, year=map_id,
                description='Tessera embedding visualisation: embedding dimensions '
                            '0/1/2 as R/G/B, 2-98 percentile stretch')
        return mem.read()


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
    """Health check — returns available years for all viewports."""
    viewports_data = {}

    if PYRAMIDS_BASE_DIR.exists():
        for viewport_dir in PYRAMIDS_BASE_DIR.iterdir():
            if viewport_dir.is_dir():
                viewport_name = viewport_dir.name
                years = sorted([
                    sub.name for sub in viewport_dir.iterdir()
                    if sub.is_dir() and sub.name.isdigit() and (sub / 'level_0.png').exists()
                ])
                if years:
                    viewports_data[viewport_name] = years

    return JsonResponse({'status': 'ok', 'viewports': viewports_data})
