# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [1.0.0]

First public release.

### Added
- `data`: load + dequantize Tessera embeddings — GeoTessera int8 × per-pixel
  scale (`dequantize_int8`), TEE per-dim uint8 vector directories
  (`dequantize_uint8`, `load_tee_vectors`), and tile-by-tile loading for
  shapefile labels (`load_embeddings_for_shapefile`).
- `rasterize`: burn shapefile polygons onto a pixel grid with stable class IDs.
- `classify`: classifier/regressor factory (k-NN, random forest, MLP, spatial
  MLP, optional XGBoost) and spatial neighbourhood feature extraction.
- `evaluate`: streaming learning curves, k-fold cross-validation, spatial
  hold-out splits, classification (F1, confusion) and regression (R²/RMSE/MAE)
  metrics, and field-type detection.
- `unet`: optional PyTorch U-Net for sparse-label tile segmentation.
- `zarr_utils`: cached GeoTessera zarr access with chunked reads + EPSG:4326
  reprojection.
- `server` (`tee-compute`): local Flask compute server that runs ML locally and
  proxies data/UI to a hosted TEE server.
- README, data-format reference, API reference, tutorial, and compute-server
  guide; test suite; MIT license.
