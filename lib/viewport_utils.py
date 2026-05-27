"""Viewport utilities for reading, parsing, and validating viewport configurations."""

import re
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import rasterio

logger = logging.getLogger(__name__)

# Tolerance for bounds matching (degrees, approximately 10 meters)
BOUNDS_TOLERANCE = 0.0001

# Strict allowlist: only alphanumeric, underscore, and hyphen
_VIEWPORT_NAME_RE = re.compile(r'^[A-Za-z0-9_-]+$')


def validate_viewport_name(name: str) -> str:
    """Validate and return a safe viewport name.

    Rejects names containing path separators, dots, or any character
    outside [A-Za-z0-9_-] to prevent path traversal attacks.

    Args:
        name: Candidate viewport name

    Returns:
        The validated name (unchanged)

    Raises:
        ValueError: If the name is empty, too long, or contains
                    disallowed characters
    """
    if not name:
        raise ValueError("Viewport name must not be empty")
    if len(name) > 128:
        raise ValueError("Viewport name must be 128 characters or fewer")
    if not _VIEWPORT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid viewport name: {name!r}. "
            "Only letters, digits, underscores, and hyphens are allowed."
        )
    return name


def get_viewport_path() -> Path:
    """Get the path to the active viewport file."""
    viewport_path = Path(__file__).parent.parent / "viewports" / "viewport.txt"
    return viewport_path


def get_active_viewport() -> Dict:
    """
    Read active viewport from symlink and return parsed data.

    Returns:
        Dictionary with keys: viewport_id, center, bounds, bounds_tuple, size_km

    Raises:
        FileNotFoundError: If viewport.txt doesn't exist
        ValueError: If viewport file is malformed
    """
    viewport_path = get_viewport_path()

    # Resolve symlink
    if viewport_path.is_symlink():
        viewport_path = viewport_path.resolve()

    if not viewport_path.exists():
        raise FileNotFoundError(
            f"Viewport file not found: {viewport_path}\n"
            f"Run: python scripts/viewport_manager.py list\n"
            f"Then: python scripts/viewport_manager.py use <viewport_name>"
        )

    with open(viewport_path, 'r') as f:
        content = f.read()

    return parse_viewport_content(content)


def parse_viewport_content(content: str) -> Dict:
    """
    Parse viewport.txt format and extract bounds, center, etc.

    Args:
        content: Raw text content of viewport.txt file

    Returns:
        Dictionary with viewport configuration

    Raises:
        ValueError: If required fields are missing or invalid
    """
    # Extract values with regex
    id_match = re.search(r'Viewport ID:\s*(.+)', content)
    lat_match = re.search(r'Latitude:\s*([-\d.]+)°', content)
    lon_match = re.search(r'Longitude:\s*([-\d.]+)°', content)
    min_lat_match = re.search(r'Min Latitude:\s*([-\d.]+)°', content)
    max_lat_match = re.search(r'Max Latitude:\s*([-\d.]+)°', content)
    min_lon_match = re.search(r'Min Longitude:\s*([-\d.]+)°', content)
    max_lon_match = re.search(r'Max Longitude:\s*([-\d.]+)°', content)
    size_match = re.search(r'Size:\s*([\d.]+)km', content)

    # Validate required fields
    if not all([id_match, lat_match, lon_match, min_lat_match, max_lat_match,
                min_lon_match, max_lon_match]):
        raise ValueError(
            "Viewport file is missing required fields. "
            "Expected: Viewport ID, Center (Latitude/Longitude), Bounds (Min/Max Latitude/Longitude)"
        )

    try:
        center_lat = float(lat_match.group(1))
        center_lon = float(lon_match.group(1))
        min_lat = float(min_lat_match.group(1))
        max_lat = float(max_lat_match.group(1))
        min_lon = float(min_lon_match.group(1))
        max_lon = float(max_lon_match.group(1))
        size_km = float(size_match.group(1)) if size_match else 10.0
    except (ValueError, IndexError) as e:
        raise ValueError(f"Invalid numeric values in viewport file: {e}")

    # Validate coordinate ranges
    if not (-90 <= center_lat <= 90):
        raise ValueError(f"Invalid center latitude: {center_lat}")
    if not (-180 <= center_lon <= 180):
        raise ValueError(f"Invalid center longitude: {center_lon}")
    if not (-90 <= min_lat <= 90) or not (-90 <= max_lat <= 90):
        raise ValueError(f"Invalid latitude bounds: {min_lat} to {max_lat}")
    if not (-180 <= min_lon <= 180) or not (-180 <= max_lon <= 180):
        raise ValueError(f"Invalid longitude bounds: {min_lon} to {max_lon}")
    if min_lat >= max_lat:
        raise ValueError(f"Min latitude ({min_lat}) must be less than max latitude ({max_lat})")
    if min_lon >= max_lon:
        raise ValueError(f"Min longitude ({min_lon}) must be less than max longitude ({max_lon})")

    return {
        'viewport_id': id_match.group(1).strip(),
        'center': [center_lat, center_lon],
        'bounds': {
            'minLon': min_lon,
            'minLat': min_lat,
            'maxLon': max_lon,
            'maxLat': max_lat
        },
        'bounds_tuple': (min_lon, min_lat, max_lon, max_lat),
        'size_km': size_km
    }


def check_cache(bounds: Tuple[float, float, float, float],
                data_type: str = 'embeddings') -> Optional[Path]:
    """
    Check if bounds match existing mosaic in cache.

    Args:
        bounds: (min_lon, min_lat, max_lon, max_lat)
        data_type: Type of data ('embeddings', 'satellite', etc.)

    Returns:
        Path to matching mosaic file, or None if no match found

    Note:
        Uses BOUNDS_TOLERANCE (±0.0001°, approximately ±10 meters)
    """
    min_lon, min_lat, max_lon, max_lat = bounds
    mosaics_dir = Path(__file__).parent.parent / "mosaics"

    if not mosaics_dir.exists():
        return None

    logger.info(f"Checking cache for bounds: {bounds}")

    for mosaic_file in mosaics_dir.glob("*.tif"):
        try:
            with rasterio.open(mosaic_file) as src:
                cached_bounds = src.bounds

                # Check if bounds match within tolerance
                if (abs(cached_bounds.left - min_lon) < BOUNDS_TOLERANCE and
                    abs(cached_bounds.bottom - min_lat) < BOUNDS_TOLERANCE and
                    abs(cached_bounds.right - max_lon) < BOUNDS_TOLERANCE and
                    abs(cached_bounds.top - max_lat) < BOUNDS_TOLERANCE):

                    logger.info(
                        f"✓ Cache hit! Found matching mosaic: {mosaic_file}\n"
                        f"  Cached bounds: ({cached_bounds.left:.6f}, {cached_bounds.bottom:.6f}, "
                        f"{cached_bounds.right:.6f}, {cached_bounds.top:.6f})"
                    )
                    return mosaic_file

        except Exception as e:
            logger.warning(f"Error reading {mosaic_file}: {e}")

    logger.info(f"No matching mosaic found in cache")
    return None


def list_viewports() -> list:
    """
    List all saved viewport files in viewports/ directory.

    Returns:
        List of viewport names (without .txt extension)
    """
    viewports_dir = Path(__file__).parent.parent / "viewports"

    viewports = []
    for viewport_file in sorted(viewports_dir.glob("*.txt")):
        if viewport_file.name != "viewport.txt":  # Skip the symlink
            viewports.append(viewport_file.stem)  # stem = filename without .txt

    return viewports


def get_active_viewport_name() -> str:
    """
    Get the name of the currently active viewport.

    Returns:
        Viewport name (e.g., 'tile_aligned')
    """
    active_file = Path(__file__).parent.parent / "viewports" / ".active"

    if active_file.exists():
        return active_file.read_text().strip()

    # Fallback: try to read symlink target
    viewport_path = get_viewport_path()
    if viewport_path.is_symlink():
        return viewport_path.resolve().stem

    return "unknown"


def read_viewport_file(viewport_name: str) -> Dict:
    """
    Read and parse a specific viewport file by name.

    Args:
        viewport_name: Viewport name (without .txt extension)

    Returns:
        Dictionary with viewport configuration

    Raises:
        ValueError: If viewport name contains unsafe characters
        FileNotFoundError: If viewport file doesn't exist
    """
    validate_viewport_name(viewport_name)
    viewport_path = Path(__file__).parent.parent / "viewports" / f"{viewport_name}.txt"

    if not viewport_path.exists():
        raise FileNotFoundError(f"Viewport file not found: {viewport_path}")

    with open(viewport_path, 'r') as f:
        content = f.read()

    return parse_viewport_content(content)


def get_viewport_vq_config(viewport_name: str) -> Optional[Dict]:
    """Return the viewport's VQ fast-path config, or None for plain GeoTessera.

    Reads ``viewports/{viewport_name}_config.json``. Returns None for viewports
    without a config file (legacy) or with ``fast_path != True``. Otherwise
    returns ``{"t", "k", "k2", "m"}`` — the kwargs needed by
    :class:`tessera_vq.client.VQTessera`. ``k2`` is None for single-stage VQ;
    ``m='cosine'`` always implies single-stage (the bolt-on does not support
    RVQ with cosine).

    Kept Django-free so ``process_viewport.py`` (a subprocess) can call it.
    """
    import json
    config_file = Path(__file__).parent.parent / "viewports" / f"{viewport_name}_config.json"
    if not config_file.exists():
        return None
    try:
        with open(config_file) as f:
            cfg = json.load(f)
    except Exception:
        return None
    if not cfg.get('fast_path'):
        return None
    vq = cfg.get('vq') or {}
    m = str(vq.get('m') or 'euclidean').lower()
    k2 = vq.get('k2')
    # RVQ is L2-only; force single-stage under cosine.
    if m == 'cosine':
        k2 = None
    return {
        't':  int(vq.get('t', 64)),
        'k':  int(vq.get('k', 256)),
        'k2': int(k2) if k2 is not None else None,
        'm':  m,
    }
