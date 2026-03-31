"""Rasterize shapefile polygons onto a pixel grid."""

import numpy as np
import rasterio.features
from sklearn.preprocessing import LabelEncoder


def rasterize_shapefile(gdf, field, transform, width, height, label_encoder=None):
    """Rasterize a shapefile field onto a pixel grid.

    Each polygon in the GeoDataFrame is burned into a raster using the
    specified attribute field as the class label. Class IDs are 1-based
    (0 = nodata).

    Args:
        gdf: GeoDataFrame with geometry and attribute columns
        field: Name of the attribute column to use as class labels
        transform: Affine transform mapping pixel coords to geographic coords
        width: Raster width in pixels
        height: Raster height in pixels
        label_encoder: Optional pre-fitted LabelEncoder. When provided,
            uses transform() instead of fit_transform(), ensuring consistent
            class IDs across tiles.

    Returns:
        int32 array, shape (height, width) — 0=nodata, 1..N=class IDs
    """
    valid = gdf.dropna(subset=[field])
    if label_encoder is not None:
        class_ids = label_encoder.transform(valid[field]) + 1  # 1-based (0 = nodata)
    else:
        le = LabelEncoder()
        class_ids = le.fit_transform(valid[field]) + 1  # 1-based (0 = nodata)

    shapes = list(zip(valid.geometry, class_ids))

    class_raster = rasterio.features.rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.int32,
        all_touched=True,
    )

    return class_raster
