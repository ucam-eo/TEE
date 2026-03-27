"""Load and dequantize Tessera embeddings from various formats."""

import gzip
import io
import json
from pathlib import Path

import numpy as np
from rasterio.transform import array_bounds as _array_bounds
from shapely.geometry import box as _box


def dequantize_uint8(quantized, dim_min, dim_max):
    """Convert uint8 embeddings to float32 vectors using per-dimension min/max.

    This is TEE's quantization format: uint8 values in [0, 255] mapped to
    [dim_min, dim_max] per dimension.

    Args:
        quantized: uint8 array, shape (N, 128) or (H, W, 128)
        dim_min: float32 array, shape (128,)
        dim_max: float32 array, shape (128,)

    Returns:
        float32 array, same shape as quantized
    """
    dim_min = np.asarray(dim_min, dtype=np.float32)
    dim_max = np.asarray(dim_max, dtype=np.float32)
    dim_scale = dim_max - dim_min
    dim_scale[dim_scale == 0] = 1
    return quantized.astype(np.float32) / 255.0 * dim_scale + dim_min


def dequantize_int8(quantized, scales):
    """Convert int8 embeddings to float32 vectors using per-pixel scales.

    This is GeoTessera's quantization format: int8 values multiplied by
    float32 scale factors.

    Args:
        quantized: int8 array, shape (H, W, 128)
        scales: float32 array, shape (H, W) or (H, W, 128)

    Returns:
        float32 array, shape (H, W, 128)
    """
    if scales.ndim == 2 and quantized.ndim == 3:
        scales = scales[..., np.newaxis]
    return quantized.astype(np.float32) * scales


def load_tee_vectors(vector_dir):
    """Load dequantized float32 vectors from TEE's vector directory format.

    Reads: all_embeddings_uint8.npy.gz, quantization.json, pixel_coords.npy.gz,
    metadata.json from the given directory.

    Args:
        vector_dir: Path to directory containing TEE vector files
            (e.g., '/data/vectors/cambridge/2024')

    Returns:
        Tuple of (vectors, coords, metadata):
        - vectors: float32 array, shape (N, 128)
        - coords: int32 array, shape (N, 2) — pixel (x, y) coordinates
        - metadata: dict with geotransform, mosaic dimensions, etc.

    Raises:
        FileNotFoundError: If required files are missing
    """
    vector_dir = Path(vector_dir)

    emb_path = vector_dir / "all_embeddings_uint8.npy.gz"
    quant_path = vector_dir / "quantization.json"
    coords_path = vector_dir / "pixel_coords.npy.gz"
    meta_path = vector_dir / "metadata.json"

    for p in [emb_path, quant_path, coords_path, meta_path]:
        if not p.exists():
            raise FileNotFoundError(f"Missing vector file: {p}")

    with open(quant_path) as f:
        quant = json.load(f)
    dim_min = np.array(quant["dim_min"], dtype=np.float32)
    dim_max = np.array(quant["dim_max"], dtype=np.float32)

    with gzip.open(emb_path, "rb") as f:
        quantized = np.load(io.BytesIO(f.read()))

    vectors = dequantize_uint8(quantized, dim_min, dim_max)

    with gzip.open(coords_path, "rb") as f:
        coords = np.load(io.BytesIO(f.read()))

    with open(meta_path) as f:
        metadata = json.load(f)

    return vectors, coords, metadata


def load_geotessera_tile(embedding_path, scales_path):
    """Load a single GeoTessera tile and dequantize.

    Args:
        embedding_path: Path to .npy file (int8, shape H×W×128)
        scales_path: Path to _scales.npy file (float32)

    Returns:
        float32 array, shape (H, W, 128)
    """
    quantized = np.load(embedding_path)
    scales = np.load(scales_path)
    return dequantize_int8(quantized, scales)


def load_embeddings_for_shapefile(gdf, field, year, gt_instance, callback=None):
    """Load embeddings tile-by-tile for all pixels overlapping a shapefile.

    Memory-bounded: processes one GeoTessera tile at a time, only accumulates
    labelled pixels. Suitable for large-area (county/country) shapefiles.

    Args:
        gdf: GeoDataFrame with geometry and the target field (EPSG:4326)
        field: Name of the attribute column to use as labels
        year: Year of embeddings to load
        gt_instance: GeoTessera instance (with registry and embeddings_dir)
        callback: Optional function(current_tile, total_tiles) for progress

    Returns:
        Tuple of (vectors, labels, class_names, stats) where:
        - vectors: float32 array, shape (N, 128)
        - labels: int array, shape (N,) — 0-indexed class labels
        - class_names: list of str — class name for each label index
        - stats: dict with tile_count, total_pixels, etc.

    Raises:
        ValueError: If no labelled pixels found
    """
    from sklearn.preprocessing import LabelEncoder
    from tessera_eval.rasterize import rasterize_shapefile

    bounds = gdf.total_bounds  # (minx, miny, maxx, maxy)
    bbox = (bounds[0], bounds[1], bounds[2], bounds[3])

    tiles = gt_instance.registry.load_blocks_for_region(bbox, year)
    total_tiles = len(tiles)
    if total_tiles == 0:
        raise ValueError(f"No GeoTessera tiles found for bbox {bbox}, year {year}")

    # Fit label encoder on the full shapefile
    le = LabelEncoder()
    le.fit(gdf[field].dropna().unique())
    class_names = le.classes_.tolist()

    all_vectors = []
    all_labels = []
    tiles_with_data = 0

    for tile_idx, (yr, tile_lon, tile_lat, tile_emb, tile_crs, tile_transform) in enumerate(
        gt_instance.fetch_embeddings(tiles)
    ):
        if callback:
            callback(tile_idx + 1, total_tiles)

        h, w, dim = tile_emb.shape

        # Reproject GDF to tile CRS, then filter to tile bbox
        tile_bounds = _array_bounds(h, w, tile_transform)
        gdf_proj = gdf.to_crs(tile_crs) if gdf.crs != tile_crs else gdf
        tile_gdf = gdf_proj[gdf_proj.intersects(_box(*tile_bounds))]
        if tile_gdf.empty:
            continue

        # Rasterize shapefile onto this tile's grid
        class_raster = rasterize_shapefile(tile_gdf, field, tile_transform, w, h, label_encoder=le)

        # Extract labelled pixels
        labelled_mask = class_raster > 0
        n_labelled = int(labelled_mask.sum())
        if n_labelled == 0:
            continue

        tiles_with_data += 1
        # class_raster is 1-based (from rasterize_shapefile), convert to 0-based
        tile_labels = class_raster[labelled_mask] - 1
        tile_vectors = tile_emb[labelled_mask]  # (n_labelled, 128)

        all_vectors.append(tile_vectors)
        all_labels.append(tile_labels)

    if not all_vectors:
        raise ValueError("No labelled pixels found across any tiles")

    vectors = np.concatenate(all_vectors, axis=0).astype(np.float32)
    labels = np.concatenate(all_labels, axis=0).astype(np.int32)

    stats = {
        "tile_count": total_tiles,
        "tiles_with_data": tiles_with_data,
        "total_pixels": len(labels),
        "n_classes": len(class_names),
    }

    return vectors, labels, class_names, stats
