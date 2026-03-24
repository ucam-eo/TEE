"""Load and dequantize Tessera embeddings from various formats."""

import gzip
import io
import json
from pathlib import Path

import numpy as np


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
