# API reference

Everything below is importable from the top level (`from tessera_eval import …`)
unless noted. Array shapes use `N` = number of pixels/samples, `dim` = 128.

---

## `tessera_eval.data`

#### `dequantize_uint8(quantized, dim_min, dim_max) -> float32`
Per-dim dequantization (TEE format). `quantized` is `uint8 (N, 128)` or
`(H, W, 128)`; `dim_min`/`dim_max` are `(128,)`. Returns same shape as input.

#### `dequantize_int8(quantized, scales) -> float32 (H, W, 128)`
Per-pixel dequantization (GeoTessera format). `quantized` is `int8 (H, W, 128)`;
`scales` is `(H, W)` or `(H, W, 128)`. Returns `int8 * scale`.

#### `load_tee_vectors(vector_dir) -> (vectors, coords, metadata)`
Read a TEE vector directory. Returns `vectors: float32 (N, 128)`,
`coords: int32 (N, 2)` pixel `(x, y)`, `metadata: dict`. Raises `FileNotFoundError`
if files are missing.

#### `load_geotessera_tile(embedding_path, scales_path) -> float32 (H, W, 128)`
Load + dequantize one GeoTessera tile from its `.npy` + `_scales.npy` pair.

#### `load_embeddings_for_shapefile(gdf, field, year, gt_instance, callback=None) -> (vectors, labels, class_names, stats)`
Stream embeddings for all pixels under a labelled GeoDataFrame, one GeoTessera tile
at a time (memory-bounded). `gdf` is reprojected per tile; `field` is the label
column. Returns `vectors: float32 (N, 128)`, `labels: int (N,)` (0-indexed),
`class_names: list[str]`, `stats: dict` (`tile_count`, `tiles_with_data`,
`total_pixels`, `n_classes`). `callback(current, total)` reports tile progress.
Raises `ValueError` if no labelled pixels are found.

---

## `tessera_eval.rasterize`

#### `rasterize_shapefile(gdf, field, transform, width, height, label_encoder=None) -> int32 (height, width)`
Burn polygons onto a pixel grid using `field` as the class label. Output is
**1-based** (`0` = nodata, `1..K` = classes). Pass a pre-fitted
`sklearn.preprocessing.LabelEncoder` to keep class IDs consistent across tiles
(`all_touched=True` is used so thin polygons aren't dropped).

---

## `tessera_eval.classify`

#### `available_classifiers() -> list[str]`
`["nn", "rf", "mlp", "spatial_mlp", "spatial_mlp_5x5"]`, plus `"xgboost"` if
installed.

#### `make_classifier(name, params=None) -> estimator`
scikit-learn-compatible classifier by name. Names may carry a variant suffix
(`mlp_v2` → `mlp`). Recognized: `nn` (k-NN), `rf` (random forest), `xgboost`,
`mlp`, `spatial_mlp`, `spatial_mlp_5x5`. `params` overrides hyperparameters
(e.g. `{"n_estimators": 300}`, `{"hidden_layers": "256,128"}`). Estimators use
`random_state=42`. Raises `ValueError` (unknown) / `ImportError` (xgboost missing).

#### `available_regressors() -> list[str]` / `make_regressor(name, params=None)`
As above for regression: `nn_reg`, `rf_reg`, `mlp_reg`, `xgboost_reg`.

#### `gather_spatial_features(vectors, coords, width, height, radius=1, subset_mask=None) -> float32 (M, w·w·dim)`
For each pixel, concatenate its embedding with its `(2·radius+1)²` grid neighbours
(missing neighbours zero-filled). `radius=1`→3×3, `2`→5×5. `subset_mask` restricts
output to selected pixels. Operates on a sparse `(coords)` grid.

#### `gather_spatial_features_2d(tile_emb, radius=1, mask=None) -> float32`
Same idea for a contiguous `(H, W, dim)` tile (edge-padded). Returns
`(H, W, w·w·dim)`, or `(M, w·w·dim)` when a `(H, W)` boolean `mask` is given.

#### `augment_spatial(X, y, window, dim) -> (X_aug, y_aug)`
4× augmentation of spatial patches via horizontal/vertical flips.

---

## `tessera_eval.evaluate`

#### `run_learning_curve(vectors, labels, classifier_names, training_pcts, repeats=5, ...) -> generator`
Yields events as it sweeps `training_pcts` (percentages), with stratified sampling
and `repeats` random restarts:

- `{"type": "classifier_status", "message": str}`
- `{"type": "progress", "pct": float, "classifiers": {name: {mean_f1, std_f1, mean_f1w, std_f1w}}, "pixel_train_count", "total_pixels", ...}`
- `{"type": "confusion_matrices", "confusion_matrices": {name: [[int]]}}` (at the largest pct)

Spatial hold-out: pass `test_vectors` + `test_labels` (a fixed, separate test set;
`vectors`/`labels` become the train-only pool). Spatial-MLP variants take
`spatial_vectors` / `spatial_vectors_5x5`; U-Net takes `unet_patches`.

#### `evaluate(vectors, labels, classifiers=None, training_sizes=None, max_train=10000, repeats=5, ...) -> Results`
Non-streaming convenience wrapper. `Results` has `.summary()` (formatted string),
`.to_dict()`, `.confusion_matrices`, `.training_sizes`, `.progress`.

#### `run_kfold_cv(vectors, labels, model_names, k=5, task="classification", model_params=None, max_training_samples=None, seed=42) -> generator`
Stratified k-fold (classification) or k-fold (regression). Yields:

- `{"type": "fold_result", "fold": int, "models": {name: metrics}}`
- `{"type": "aggregate", "models": {name: {mean_f1, std_f1, mean_f1w, std_f1w}}}`
  (regression: `mean_r2/std_r2/mean_rmse/.../mean_mae/...`)
- `{"type": "confusion_matrices", ...}` (classification only)

#### `regression_metrics(y_true, y_pred) -> {"r2", "rmse", "mae"}`

#### `detect_field_type(gdf, field_name, threshold=20) -> "classification" | "regression"`
Numeric with `> threshold` unique values → regression; otherwise classification.

---

## `tessera_eval.unet` (requires `torch`)

#### `extract_labelled_patches(tile_emb, class_raster, patch_size=256, min_labelled=10) -> list[(emb_patch, label_patch)]`
Connected-component patch extraction centred on label clusters (edge-zero-padded).

#### `train_unet_on_patches(patches, n_classes, params=None, progress_callback=None) -> model`
Train a `TinyUNet` on `(emb_patch, label_patch)` pairs. `params` e.g.
`{"epochs": 15}`.

#### `predict_unet_tile(model, tile_emb, patch_size=256, overlap=32) -> int (H, W)`
Sliding-window prediction over a tile; output classes are 1-based (`0` = ignore).

`TinyUNet` (class) and `_HAS_TORCH` (bool) are also exposed. If torch is missing,
the training/predict functions raise `RuntimeError`.

---

## `tessera_eval.zarr_utils` (requires `geotessera`)

#### `get_zarr() -> GeoTesseraZarr | None`
Cached zarr handle (import attempted once; failure cached).

#### `probe_zarr_coverage(gtz, bounds, year) -> bool`
True if the zarr store has non-NaN data at the centre of `bounds`.

#### `read_region_chunked(gtz, bounds, year) -> (mosaic, transform, crs)`
Read a region (`bounds = (west, south, east, north)`, EPSG:4326), chunking large
areas. Returns a `(H, W, 128) float32` mosaic **reprojected to EPSG:4326**
(GeoTessera returns native UTM), or `(None, None, None)` if no data.

---

## `tessera_eval.server`

`tee-compute` console entry point. See [compute-server.md](compute-server.md).
