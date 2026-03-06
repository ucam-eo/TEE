"""Compute utilities: projection loading, year lookup."""

import json
import logging

import numpy as np

from lib.config import VECTORS_DIR

logger = logging.getLogger(__name__)


def _load_projection(vector_dir, coords_filename):
    """Load projection coordinates and compute geo coords.

    Returns (coords, lons, lats) on success, or raises on failure.
    """
    coords = np.load(str(vector_dir / coords_filename))
    pixel_coords = np.load(str(vector_dir / 'pixel_coords.npy'))

    with open(vector_dir / 'metadata.json') as f:
        metadata = json.load(f)

    gt = metadata['geotransform']
    lons = gt['c'] + gt['a'] * pixel_coords[:, 0] + gt['b'] * pixel_coords[:, 1]
    lats = gt['f'] + gt['d'] * pixel_coords[:, 0] + gt['e'] * pixel_coords[:, 1]

    return coords, lons, lats


def _projection_to_points(coords, lons, lats):
    """Convert projection arrays to list of point dicts."""
    has_z = coords.shape[1] >= 3
    points = []
    for i in range(len(lats)):
        point = {
            'lat': float(lats[i]),
            'lon': float(lons[i]),
            'x': float(coords[i, 0]),
            'y': float(coords[i, 1]),
        }
        if has_z:
            point['z'] = float(coords[i, 2])
        points.append(point)
    return points


def _find_year_with_file(viewport_name, filename, preferred_year=None):
    """Find a year directory containing the given file.

    Checks preferred_year first, then falls back to any available year.
    Returns (year_str, vector_dir) or (None, None).
    """
    vectors_root = VECTORS_DIR / viewport_name
    if not vectors_root.exists():
        return None, None

    # Try preferred year first
    if preferred_year:
        d = vectors_root / str(preferred_year)
        if d.is_dir() and (d / filename).exists():
            return str(preferred_year), d

    # Fall back to any year (newest first)
    for year_dir in sorted(vectors_root.iterdir(), reverse=True):
        if year_dir.is_dir() and (year_dir / filename).exists():
            return year_dir.name, year_dir

    return None, None
