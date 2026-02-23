#!/usr/bin/env python3
"""
Tile server for Tessera embeddings
Serves map tiles dynamically from pyramid GeoTIFFs for current viewport
"""

import sys
from flask import Flask, send_file, jsonify
from flask_cors import CORS
from rio_tiler.io import Reader
from rio_tiler.models import ImageData
from pathlib import Path
import io
from PIL import Image
import numpy as np

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))
from lib.config import DATA_DIR, PYRAMIDS_DIR
from lib.viewport_utils import validate_viewport_name
from lib.flask_auth import init_auth

app = Flask(__name__)
CORS(app, supports_credentials=True)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
init_auth(app, DATA_DIR)

PYRAMIDS_BASE_DIR = PYRAMIDS_DIR
YEARS = [str(y) for y in range(2017, 2026)] + ['satellite']

# Allowed map_id values (years + special names)
_VALID_MAP_IDS = {str(y) for y in range(2017, 2026)} | {'satellite', 'rgb'}

# Cache for tile readers
readers = {}

def get_reader(viewport, map_id, zoom_level):
    """Get or create a Reader for a specific viewport, map, and zoom level."""
    # Map web zoom levels to pyramid levels (we have 6 levels: 0-5)
    # With tileSize=2048 and zoomOffset=-3, Leaflet requests z=3 to z=14
    # Map z=14 → level 0 (most detail), z=3 → level 5 (least detail)
    # Use floor division to spread 12 zoom levels across 6 pyramid levels
    pyramid_level = max(0, min(5, (14 - zoom_level) // 2))

    key = f"{viewport}_{map_id}_{pyramid_level}"

    if key not in readers:
        viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport

        if map_id == 'satellite':
            tif_path = viewport_pyramids_dir / 'satellite' / f'level_{pyramid_level}.tif'
        elif map_id == 'rgb':
            tif_path = viewport_pyramids_dir / 'rgb' / '2024' / f'level_{pyramid_level}.tif'
        else:
            # map_id is a year like '2024'
            tif_path = viewport_pyramids_dir / map_id / f'level_{pyramid_level}.tif'

        if tif_path.exists():
            readers[key] = str(tif_path)
        else:
            return None

    return readers[key]

def mercator_to_tile(lon, lat, zoom):
    """Convert lon/lat to tile coordinates at given zoom level."""
    import math
    n = 2.0 ** zoom
    x_tile = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y_tile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x_tile, y_tile

def tile_to_bbox(x, y, zoom):
    """Convert tile coordinates to bounding box."""
    import math
    n = 2.0 ** zoom
    lon_min = x / n * 360.0 - 180.0
    lon_max = (x + 1) / n * 360.0 - 180.0
    lat_max_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat_min_rad = math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n)))
    lat_max = math.degrees(lat_max_rad)
    lat_min = math.degrees(lat_min_rad)
    return (lon_min, lat_min, lon_max, lat_max)

@app.route('/tiles/<viewport>/<map_id>/<int:z>/<int:x>/<int:y>.png')
def get_tile(viewport, map_id, z, x, y):
    """Serve a map tile for a specific viewport."""
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return "Invalid viewport name", 400
    if map_id not in _VALID_MAP_IDS:
        return "Invalid map_id", 400

    # Standard tile size - no browser scaling needed
    TILE_SIZE = 256

    try:
        tif_path = get_reader(viewport, map_id, z)

        if not tif_path:
            # Return transparent tile if file doesn't exist
            img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            return send_file(buf, mimetype='image/png')

        # Get tile bounds (lon_min, lat_min, lon_max, lat_max)
        bbox = tile_to_bbox(x, y, z)

        # Read tile from GeoTIFF using direct rasterio (no resampling blur)
        import rasterio
        from rasterio.windows import from_bounds

        try:
            with rasterio.open(tif_path) as src:
                # Convert bbox to pixel window
                window = from_bounds(bbox[0], bbox[1], bbox[2], bbox[3], src.transform)

                # Get original requested window dimensions (before clamping)
                orig_col_off = window.col_off
                orig_row_off = window.row_off
                orig_width = window.width
                orig_height = window.height

                # Round to integer pixels
                col_off = int(round(orig_col_off))
                row_off = int(round(orig_row_off))
                width = int(round(orig_width))
                height = int(round(orig_height))

                if width <= 0 or height <= 0:
                    # Zero-size window - return transparent
                    img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    buf.seek(0)
                    return send_file(buf, mimetype='image/png')

                # Calculate clamped read window (what we can actually read)
                read_col_off = max(0, col_off)
                read_row_off = max(0, row_off)
                read_col_end = min(src.width, col_off + width)
                read_row_end = min(src.height, row_off + height)
                read_width = read_col_end - read_col_off
                read_height = read_row_end - read_row_off

                if read_width <= 0 or read_height <= 0:
                    # Completely outside bounds - return transparent
                    img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    buf.seek(0)
                    return send_file(buf, mimetype='image/png')

                # Read the valid portion
                pixel_window = rasterio.windows.Window(read_col_off, read_row_off, read_width, read_height)
                data = src.read(window=pixel_window)

                # Convert to RGB
                if data.shape[0] == 1:
                    rgb = np.stack([data[0], data[0], data[0]], axis=0)
                else:
                    rgb = data[:3]

                # Calculate where to place data in the full tile
                # If original col_off was negative, data starts at offset in tile
                tile_x_start = max(0, -col_off)
                tile_y_start = max(0, -row_off)

                # Create full-size array for the requested window, filled with black
                full_data = np.zeros((3, height, width), dtype=np.uint8)

                # Place the read data at the correct position
                full_data[:, tile_y_start:tile_y_start+read_height, tile_x_start:tile_x_start+read_width] = rgb

                # Transpose to (H, W, C) for PIL
                rgb_t = np.transpose(full_data, (1, 2, 0))

                # Create PIL image and upscale to tile size with NEAREST (crisp pixels)
                img = Image.fromarray(rgb_t.astype(np.uint8), mode='RGB')
                img = img.resize((TILE_SIZE, TILE_SIZE), Image.NEAREST)

                # Save to buffer
                buf = io.BytesIO()
                img.save(buf, format='PNG')
                buf.seek(0)

                return send_file(buf, mimetype='image/png')

        except Exception as e:
            # Return transparent tile on error
            print(f"Error reading tile {map_id}/{z}/{x}/{y}: {e}")
            img = Image.new('RGBA', (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
            buf = io.BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            return send_file(buf, mimetype='image/png')

    except Exception as e:
        print(f"Error serving tile: {e}")
        return f"Error: {e}", 500

@app.route('/bounds/<viewport>/<map_id>')
def get_bounds(viewport, map_id):
    """Get bounds for a map in a specific viewport."""
    try:
        validate_viewport_name(viewport)
    except ValueError:
        return jsonify({'error': 'Invalid viewport name'}), 400
    if map_id not in _VALID_MAP_IDS:
        return jsonify({'error': 'Invalid map_id'}), 400
    try:
        viewport_pyramids_dir = PYRAMIDS_BASE_DIR / viewport

        if map_id == 'satellite':
            tif_path = viewport_pyramids_dir / 'satellite' / 'level_0.tif'
        elif map_id == 'rgb':
            tif_path = viewport_pyramids_dir / 'rgb' / '2024' / 'level_0.tif'
        else:
            # map_id is a year like '2024'
            tif_path = viewport_pyramids_dir / map_id / 'level_0.tif'

        if tif_path.exists():
            with Reader(str(tif_path)) as src:
                bounds = src.bounds
                return jsonify({
                    'bounds': bounds,
                    'center': [(bounds[1] + bounds[3])/2, (bounds[0] + bounds[2])/2]
                })
        else:
            return jsonify({'error': 'File not found'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health():
    """Health check endpoint - returns available maps for all viewports."""
    viewports_data = {}

    if PYRAMIDS_BASE_DIR.exists():
        # Scan all viewport directories
        for viewport_dir in PYRAMIDS_BASE_DIR.iterdir():
            if viewport_dir.is_dir():
                viewport_name = viewport_dir.name
                available_maps = []

                # Check for year directories (2017-2025)
                for year in YEARS:
                    if year != 'satellite':
                        year_dir = viewport_dir / year / 'level_0.tif'
                        if year_dir.exists():
                            available_maps.append(year)

                # Check for satellite
                if (viewport_dir / 'satellite' / 'level_0.tif').exists():
                    available_maps.append('satellite')

                # Check for RGB
                if (viewport_dir / 'rgb' / '2024' / 'level_0.tif').exists():
                    available_maps.append('rgb')

                if available_maps:
                    viewports_data[viewport_name] = available_maps

    return jsonify({
        'status': 'ok',
        'viewports': viewports_data
    })

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Tessera Tile Server')
    parser.add_argument('--prod', action='store_true', help='Disable Flask debug mode for production use')
    parser.add_argument('--port', type=int, default=5125, help='Port to listen on (default: 5125)')
    parser.add_argument('--host', default='127.0.0.1', help='Host to bind to (default: 127.0.0.1)')
    args = parser.parse_args()

    debug = not args.prod

    print("Starting Tessera Tile Server...")
    print(f"Serving tiles from: {PYRAMIDS_BASE_DIR.absolute()}")
    print("Available endpoints:")
    print(f"  - http://localhost:{args.port}/tiles/<viewport>/<map_id>/<z>/<x>/<y>.png")
    print(f"  - http://localhost:{args.port}/bounds/<viewport>/<map_id>")
    print(f"  - http://localhost:{args.port}/health")
    print("\nMap IDs: 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, satellite, rgb")
    if debug:
        print("\nDebug mode enabled (use --prod to disable)")
    print(f"\nStarting server on http://localhost:{args.port}")
    app.run(debug=debug, host=args.host, port=args.port, threaded=True)
