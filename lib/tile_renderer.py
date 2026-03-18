"""Pure tile rendering helpers: coordinate math, PNG/TIF rendering, pyramid path resolution."""

import io
import json
import math
import functools
from pathlib import Path

import numpy as np
from PIL import Image

from lib.config import PYRAMIDS_DIR


def tile_to_bbox(x, y, zoom):
    """Convert tile coordinates to bounding box (lon_min, lat_min, lon_max, lat_max)."""
    n = 2.0 ** zoom
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_min_rad = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
    lat_max = math.degrees(lat_max_rad)
    lat_min = math.degrees(lat_min_rad)
    return (lon_min, lat_min, lon_max, lat_max)


def get_pyramid_path(viewport, map_id, zoom_level):
    """Resolve the pyramid file path for a viewport/map/zoom.

    Returns (path_str, mtime, is_png) or None if not found.
    Tries PNG first, falls back to GeoTIFF.
    """
    pyramid_level = max(0, min(5, (14 - zoom_level) // 2))
    viewport_pyramids_dir = PYRAMIDS_DIR / viewport

    if map_id == 'satellite':
        year_dir = viewport_pyramids_dir / 'satellite'
    elif map_id == 'rgb':
        year_dir = viewport_pyramids_dir / 'rgb' / '2024'
    else:
        year_dir = viewport_pyramids_dir / map_id

    png_path = year_dir / f'level_{pyramid_level}.png'
    tif_path = year_dir / f'level_{pyramid_level}.tif'

    for path, is_png in [(png_path, True), (tif_path, False)]:
        try:
            mtime = int(path.stat().st_mtime)
        except FileNotFoundError:
            continue
        return (str(path), mtime, is_png)

    return None


@functools.lru_cache(maxsize=2048)
def render_tile(tif_path, z, x, y, _mtime=0):
    """Render a single tile from a GeoTIFF to PNG bytes.  Cached by (path, coords, mtime)."""
    import rasterio
    import rasterio.windows

    TILE_SIZE = 256
    bbox = tile_to_bbox(x, y, z)

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
def render_tile_png(png_path, z, x, y, _mtime=0):
    """Render a single tile from a PNG pyramid level.  Cached by (path, coords, mtime)."""
    TILE_SIZE = 256
    bbox = tile_to_bbox(x, y, z)

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

    with Image.open(png_path) as img:
        cropped = np.array(img.crop((read_col_off, read_row_off, read_col_end, read_row_end)))

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
