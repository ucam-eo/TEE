#!/usr/bin/env python3
"""Generate two synthetic validation shapefiles for sanity-checking the
validation panel.

Outputs (in Cumbria_naddle/):
  random-validation.zip   — shuffled labels  → F1 ≈ 1/16 ≈ 0.06
  perfect-validation.zip  — KMeans clusters  → F1 ≈ 1.0
"""

import gzip
import io
import json
import os
import shutil
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import shape
from sklearn.cluster import KMeans

ROOT = Path(__file__).resolve().parent.parent
VECTORS = Path("/Users/skeshav/data/vectors/Haweswater_test/2024")
OUT_DIR = ROOT / "Cumbria_naddle"
SHP_PATH = OUT_DIR / "UKhabs_Naddle_Swindale_Mardale.shp"


def _load_embeddings():
    """Load and dequantize Haweswater_test embeddings."""
    with open(VECTORS / "quantization.json") as f:
        quant = json.load(f)
    dim_min = np.array(quant["dim_min"], dtype=np.float32)
    dim_max = np.array(quant["dim_max"], dtype=np.float32)

    with gzip.open(VECTORS / "all_embeddings_uint8.npy.gz", "rb") as f:
        quantized = np.load(io.BytesIO(f.read()))

    dim_scale = dim_max - dim_min
    dim_scale[dim_scale == 0] = 1
    embeddings = quantized.astype(np.float32) / 255.0 * dim_scale + dim_min
    return embeddings


def _load_pixel_coords():
    with gzip.open(VECTORS / "pixel_coords.npy.gz", "rb") as f:
        return np.load(io.BytesIO(f.read()))


def _load_metadata():
    with open(VECTORS / "metadata.json") as f:
        return json.load(f)


def _save_gdf_as_zip(gdf, zip_path, stem):
    """Save GeoDataFrame as a zipped shapefile."""
    with tempfile.TemporaryDirectory() as tmp:
        shp = os.path.join(tmp, f"{stem}.shp")
        gdf.to_file(shp)
        # zip all shapefile components
        base = zip_path.replace(".zip", "")
        shutil.make_archive(base, "zip", tmp)
    print(f"  → {zip_path}  ({len(gdf)} polygons)")


# ── random-validation ────────────────────────────────────────────────
def make_random():
    """Per-pixel random labels → polygonize.

    Polygon-level randomisation doesn't work: each polygon's pixels share
    similar embeddings AND the same label, so classifiers memorise the
    polygon→label mapping.  Per-pixel labels break that correlation.
    """
    print("Creating random-validation.zip …")
    meta = _load_metadata()
    height, width = meta["mosaic_height"], meta["mosaic_width"]
    gt = meta["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])
    coords = _load_pixel_coords()

    # Assign each pixel a random class 1..16 (0 = nodata)
    rng = np.random.default_rng(42)
    raster = np.zeros((height, width), dtype=np.int32)
    raster[coords[:, 1], coords[:, 0]] = rng.integers(1, 17, size=len(coords))

    # Polygonize
    print("  Polygonizing …")
    class_names = [f"Class_{i}" for i in range(16)]
    geoms, values = [], []
    for geom_dict, val in shapes(raster, transform=transform):
        if val == 0:
            continue
        geoms.append(shape(geom_dict))
        values.append(class_names[int(val - 1)])

    gdf = gpd.GeoDataFrame(
        {"Group_Name": values, "geometry": geoms},
        crs="EPSG:4326",
    )
    _save_gdf_as_zip(gdf, str(OUT_DIR / "random-validation.zip"), "random-validation")


# ── perfect-validation ───────────────────────────────────────────────
def make_perfect():
    print("Creating perfect-validation.zip …")
    meta = _load_metadata()
    height, width = meta["mosaic_height"], meta["mosaic_width"]
    gt = meta["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])

    print("  Loading embeddings …")
    embeddings = _load_embeddings()
    coords = _load_pixel_coords()

    print("  Running KMeans(16) …")
    km = KMeans(n_clusters=16, random_state=42, n_init=10)
    labels = km.fit_predict(embeddings)  # 0..15

    # Build raster (0 = nodata, clusters 1..16)
    raster = np.zeros((height, width), dtype=np.int32)
    raster[coords[:, 1], coords[:, 0]] = labels + 1

    # Polygonize
    print("  Polygonizing …")
    geoms, values = [], []
    for geom_dict, val in shapes(raster, transform=transform):
        if val == 0:
            continue
        geoms.append(shape(geom_dict))
        values.append(f"Cluster_{int(val - 1)}")

    gdf = gpd.GeoDataFrame(
        {"Group_Name": values, "geometry": geoms},
        crs="EPSG:4326",
    )
    _save_gdf_as_zip(gdf, str(OUT_DIR / "perfect-validation.zip"), "perfect-validation")


if __name__ == "__main__":
    make_random()
    make_perfect()
    print("Done.")
