"""Rasterize shapefile polygons onto a pixel grid."""

import numpy as np
import rasterio.features
from sklearn.preprocessing import LabelEncoder


def rasterize_shapefile(gdf, field, transform, width, height):
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

    Returns:
        int32 array, shape (height, width) — 0=nodata, 1..N=class IDs
    """
    le = LabelEncoder()
    gdf = gdf.dropna(subset=[field]).copy()
    gdf["_class_id"] = le.fit_transform(gdf[field]) + 1  # 1-based (0 = nodata)

    shapes = list(zip(gdf.geometry, gdf["_class_id"]))

    class_raster = rasterio.features.rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.int32,
        all_touched=True,
    )

    return class_raster
