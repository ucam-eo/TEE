"""Shared zarr utilities for GeoTessera tile access.

Provides cached zarr instance, coverage probing, and chunked region reading.
Used by both the evaluation server (server.py) and viewport processing
(process_viewport.py).
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

# ── Singleton zarr instance ──

_zarr_instance = None  # None = not tried, False = tried and failed


def get_zarr():
    """Return a cached GeoTesseraZarr instance, or None if unavailable.

    Only attempts the import once; caches the result (including failure).
    """
    global _zarr_instance
    if _zarr_instance is None:
        try:
            from geotessera.store import GeoTesseraZarr
            _zarr_instance = GeoTesseraZarr()
            logger.info("GeoTesseraZarr available: %s", _zarr_instance.url)
        except Exception:
            _zarr_instance = False
            logger.info("GeoTesseraZarr not available")
    return _zarr_instance if _zarr_instance is not False else None


def probe_zarr_coverage(gtz, bounds, year):
    """Probe zarr store for coverage at the centre of bounds.

    Returns True if zarr has non-NaN data for (year, centre-of-bounds).
    """
    try:
        cx = (bounds[0] + bounds[2]) / 2
        cy = (bounds[1] + bounds[3]) / 2
        probe = gtz.sample_at(cx, cy, year)
        return not np.isnan(probe).all()
    except Exception:
        return False


# ── Chunked region reading ──

CHUNK_THRESHOLD = 0.2  # degrees — regions larger than this get split
CHUNK_SIZE = 0.1       # degrees per chunk


def read_region_chunked(gtz, bounds, year):
    """Read a region via zarr, chunking if larger than CHUNK_THRESHOLD.

    Args:
        gtz: GeoTesseraZarr instance
        bounds: (west, south, east, north) in EPSG:4326
        year: int

    Returns:
        (mosaic, transform, crs) where mosaic is (H, W, 128) float32.
        Returns (None, None, None) if no data available.
    """
    west, south, east, north = bounds
    lon_span = east - west
    lat_span = north - south

    # Small region — single read
    if lon_span <= CHUNK_THRESHOLD and lat_span <= CHUNK_THRESHOLD:
        mosaic, transform, crs = gtz.read_region(bounds, year)
        return mosaic, transform, crs

    # Large region — split into chunks and merge
    chunk_lons = []
    lon = west
    while lon < east:
        chunk_lons.append((lon, min(lon + CHUNK_SIZE, east)))
        lon += CHUNK_SIZE
    chunk_lats = []
    lat = south
    while lat < north:
        chunk_lats.append((lat, min(lat + CHUNK_SIZE, north)))
        lat += CHUNK_SIZE

    total_chunks = len(chunk_lons) * len(chunk_lats)
    logger.info("Reading %d zarr chunks (%d x %d)", total_chunks, len(chunk_lons), len(chunk_lats))

    # Collect chunks — merge manually using coordinate offsets
    first_transform = None
    first_crs = None
    chunks = []

    for lat_start, lat_end in chunk_lats:
        for lon_start, lon_end in chunk_lons:
            chunk_bbox = (lon_start, lat_start, lon_end, lat_end)
            try:
                emb, tfm, crs = gtz.read_region(chunk_bbox, year)
            except Exception as e:
                logger.warning("Zarr chunk (%.3f,%.3f)-(%.3f,%.3f) failed: %s",
                               lon_start, lat_start, lon_end, lat_end, e)
                continue
            if emb is None or emb.size == 0:
                continue
            if first_transform is None:
                first_transform = tfm
                first_crs = crs
            chunks.append((emb, tfm))

    if not chunks:
        return None, None, None

    # Merge: compute the full mosaic size from the first and last transforms
    import rasterio.transform
    px = first_transform.a  # pixel size in CRS units
    all_rows = []
    all_cols = []
    for emb, tfm in chunks:
        col_off = round((tfm.c - first_transform.c) / px)
        row_off = round((first_transform.f - tfm.f) / px)
        all_rows.append(row_off + emb.shape[0])
        all_cols.append(col_off + emb.shape[1])

    total_h = max(all_rows)
    total_w = max(all_cols)
    n_bands = chunks[0][0].shape[2]
    mosaic = np.full((total_h, total_w, n_bands), np.nan, dtype=np.float32)

    for emb, tfm in chunks:
        col_off = round((tfm.c - first_transform.c) / px)
        row_off = round((first_transform.f - tfm.f) / px)
        h, w = emb.shape[:2]
        mosaic[row_off:row_off + h, col_off:col_off + w] = emb

    return mosaic, first_transform, first_crs
