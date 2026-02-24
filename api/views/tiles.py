"""Tile server views — serves map tiles from pyramid GeoTIFFs."""

import io
import math
import logging

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
YEARS = [str(y) for y in range(2017, 2026)] + ['satellite']
_VALID_MAP_IDS = {str(y) for y in range(2017, 2026)} | {'satellite', 'rgb'}

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
    """Get or create a reader path for a specific viewport, map, and zoom level."""
    pyramid_level = max(0, min(5, (14 - zoom_level) // 2))
    key = f"{viewport}_{map_id}_{pyramid_level}"

    if key not in _readers:
        viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport

        if map_id == 'satellite':
            tif_path = viewport_pyramids_dir / 'satellite' / f'level_{pyramid_level}.tif'
        elif map_id == 'rgb':
            tif_path = viewport_pyramids_dir / 'rgb' / '2024' / f'level_{pyramid_level}.tif'
        else:
            tif_path = viewport_pyramids_dir / map_id / f'level_{pyramid_level}.tif'

        if tif_path.exists():
            _readers[key] = str(tif_path)
        else:
            return None

    return _readers[key]


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
    """Return a cached transparent 256x256 PNG."""
    return _tile_response(_get_transparent_png())


def get_tile(request, viewport, map_id, z, x, y):
    """Serve a map tile for a specific viewport."""
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return HttpResponse("Invalid viewport name", status=400)
    if map_id not in _VALID_MAP_IDS:
        return HttpResponse("Invalid map_id", status=400)

    TILE_SIZE = 256

    try:
        tif_path = _get_reader(viewport, map_id, z)

        if not tif_path:
            return _transparent_tile()

        bbox = _tile_to_bbox(x, y, z)

        try:
            with rasterio.open(tif_path) as src:
                window = rasterio.windows.from_bounds(
                    bbox[0], bbox[1], bbox[2], bbox[3], src.transform
                )

                col_off = int(round(window.col_off))
                row_off = int(round(window.row_off))
                width = int(round(window.width))
                height = int(round(window.height))

                if width <= 0 or height <= 0:
                    return _transparent_tile()

                read_col_off = max(0, col_off)
                read_row_off = max(0, row_off)
                read_col_end = min(src.width, col_off + width)
                read_row_end = min(src.height, row_off + height)
                read_width = read_col_end - read_col_off
                read_height = read_row_end - read_row_off

                if read_width <= 0 or read_height <= 0:
                    return _transparent_tile()

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
                return _tile_response(buf.getvalue())

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
            tif_path = viewport_pyramids_dir / 'satellite' / 'level_0.tif'
        elif map_id == 'rgb':
            tif_path = viewport_pyramids_dir / 'rgb' / '2024' / 'level_0.tif'
        else:
            tif_path = viewport_pyramids_dir / map_id / 'level_0.tif'

        if tif_path.exists():
            with Reader(str(tif_path)) as src:
                bounds = src.bounds
                return JsonResponse({
                    'bounds': bounds,
                    'center': [(bounds[1] + bounds[3]) / 2, (bounds[0] + bounds[2]) / 2],
                })
        else:
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
                        if (viewport_dir / year / 'level_0.tif').exists():
                            available_maps.append(year)

                if (viewport_dir / 'satellite' / 'level_0.tif').exists():
                    available_maps.append('satellite')

                if (viewport_dir / 'rgb' / '2024' / 'level_0.tif').exists():
                    available_maps.append('rgb')

                if available_maps:
                    viewports_data[viewport_name] = available_maps

    return JsonResponse({'status': 'ok', 'viewports': viewports_data})
