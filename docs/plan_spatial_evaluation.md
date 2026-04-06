# Spatial Evaluation: Train/Test/Map Bounding Boxes

Implementation plan for replacing random train/test splits with user-drawn
geographic bounding boxes, plus a classification map (GeoTIFF) generator.

---

## Overview

| Component | What changes |
|-----------|-------------|
| **UI (viewer.html)** | Bbox drawing toolbar on satellite panel, area type selector, three action buttons |
| **UI (evaluation.js)** | Bbox state management, serialization, three run paths, GeoTIFF download |
| **UI (maps.js)** | Leaflet.Draw rectangle handler, bbox layer groups per type |
| **Server (server.py)** | Spatial filtering of sample points, new `/create-map` endpoint |
| **Engine (evaluate.py)** | Accept pre-split train/test arrays (no internal splitting) |
| **Config** | Bounding boxes in config JSON, hyperparameter variants |

---

## Step 1: Bounding Box Drawing on the Satellite Map

### Goal
Let the user draw, view, and delete coloured bounding boxes on panel 2 (satellite map). Three types: train (blue), test (red), map (green).

### Files changed

**`public/viewer.html`**
- Add a `<select>` dropdown in panel 2's header, next to the satellite source selector:
  ```html
  <select id="bbox-type-select" style="display:none;">
      <option value="">-- Draw areas --</option>
      <option value="train">Train area (blue)</option>
      <option value="test">Test area (red)</option>
      <option value="map">Map area (green)</option>
  </select>
  <button id="bbox-draw-btn" style="display:none;">Draw</button>
  <button id="bbox-clear-btn" style="display:none;">Clear all</button>
  ```
- These controls are hidden by default; shown only in validation mode via CSS:
  ```css
  body.mode-validation #bbox-type-select,
  body.mode-validation #bbox-draw-btn,
  body.mode-validation #bbox-clear-btn { display: inline-block; }
  ```

**`public/js/maps.js`** (or a new section in `evaluation.js`)

Add a bbox drawing module:

```javascript
// Bounding box state
const spatialBboxes = {
    train: [],  // each: { id, bounds: [[s,w],[n,e]], layer }
    test:  [],
    map:   [],
};

const BBOX_COLORS = { train: '#4363d8', test: '#e6194b', map: '#3cb44b' };
const BBOX_LABELS = { train: 'Train', test: 'Test', map: 'Map' };

// One Leaflet FeatureGroup per type, added to maps.rgb
const bboxLayerGroups = {
    train: L.featureGroup(),
    test:  L.featureGroup(),
    map:   L.featureGroup(),
};
```

Key functions:
- `startBboxDraw(type)` -- activates `L.Draw.Rectangle` on `maps.rgb` with the type's colour. On `L.Draw.Event.CREATED`, pushes the rectangle to `spatialBboxes[type]`, adds it to the layer group, adds a tooltip showing the type label and a delete-on-click handler.
- `deleteBbox(type, id)` -- removes from state and layer group.
- `clearAllBboxes()` -- empties all three arrays and layer groups.
- `getBboxesForServer()` -- returns `{ train: [[s,w,n,e], ...], test: [...], map: [...] }`.

Each rectangle gets:
- Fill colour at 20% opacity, border at full colour, weight 2.
- Tooltip: "Train area 1", "Test area 2", etc.
- Click handler: popup with "Delete this area?" button.

### Conflict with existing Leaflet.Draw
The labelling mode already uses `L.Draw.Polygon` on `maps.rgb` (in `labels.js`). The bbox drawing should only be active in validation mode. Guard: check `window.currentPanelMode === 'validation'` before enabling. The `L.Draw.Rectangle` handler is separate from the polygon handler and can coexist because only one is active at a time.

### Edge cases
- User switches away from validation mode while drawing: cancel the active draw handler in `setPanelLayout()`.
- User resizes browser: Leaflet handles rectangle persistence automatically.

---

## Step 2: Spatial Filtering of Sample Points

### Goal
When bounding boxes exist, filter the sampled points so that train-area points go to training and test-area points go to testing. Points in both train and test areas are assigned to training (training takes priority).

### Files changed

**`packages/tessera-eval/tessera_eval/server.py`**

Add a helper function:

```python
def _split_points_by_bboxes(sample_points, sample_labels, train_bboxes, test_bboxes):
    """Split sample points into train/test sets based on bounding boxes.

    Args:
        sample_points: list of (lon, lat)
        sample_labels: list of int
        train_bboxes: list of [south, west, north, east]
        test_bboxes: list of [south, west, north, east]

    Returns:
        (train_points, train_labels, test_points, test_labels)
        Each is a numpy array. Points in both train and test go to train.
        Points in neither are discarded.
    """
    points = np.array(sample_points)  # (N, 2) with lon, lat
    lons, lats = points[:, 0], points[:, 1]

    in_train = np.zeros(len(points), dtype=bool)
    for s, w, n, e in train_bboxes:
        in_train |= (lats >= s) & (lats <= n) & (lons >= w) & (lons <= e)

    in_test = np.zeros(len(points), dtype=bool)
    for s, w, n, e in test_bboxes:
        in_test |= (lats >= s) & (lats <= n) & (lons >= w) & (lons <= e)

    # Training takes priority
    in_test &= ~in_train

    train_idx = np.where(in_train)[0]
    test_idx = np.where(in_test)[0]

    labels = np.array(sample_labels)
    return (
        [sample_points[i] for i in train_idx], labels[train_idx],
        [sample_points[i] for i in test_idx], labels[test_idx],
    )
```

Modify `run-large-area` endpoint:
- Accept new fields in the request body: `train_bboxes`, `test_bboxes`, `map_bboxes` (each a list of `[south, west, north, east]`).
- After generating sample points and before fetching embeddings, if `train_bboxes` is non-empty, call `_split_points_by_bboxes()`.
- Store both `train_points/labels` and `test_points/labels` in the tile cache.
- Pass both to the modified learning curve runner.

### Backward compatibility
If `train_bboxes` is empty or absent, the existing random-split path runs unchanged. This is the critical compatibility constraint -- no bboxes = current behaviour.

---

## Step 3: Modify Learning Curve for Pre-Split Data

### Goal
Allow `run_learning_curve` to accept pre-split train and test sets instead of doing its own random stratified split.

### Files changed

**`packages/tessera-eval/tessera_eval/evaluate.py`**

Add new parameters to `run_learning_curve`:

```python
def run_learning_curve(vectors, labels, classifier_names, training_pcts,
                       repeats=5, classifier_params=None, spatial_vectors=None,
                       spatial_vectors_5x5=None, finish_classifiers=None,
                       unet_patches=None,
                       # New: pre-split spatial evaluation
                       test_vectors=None, test_labels=None,
                       test_spatial_vectors=None, test_spatial_vectors_5x5=None,
                       **kwargs):
```

When `test_vectors` is provided:
- `vectors` and `labels` are the TRAIN set only.
- Learning curve percentages subsample from `vectors`/`labels`.
- Testing always uses the full `test_vectors`/`test_labels` (no random hold-out).
- `repeats` still applies (different random subsamples of the training set).
- The rest of the logic (classifier training, F1 computation, confusion matrices) is identical.

Implementation: in the inner loop, replace this block:

```python
# Current: random stratified split from combined pool
train_idx = ...  # stratified sample
test_mask = np.ones(n_samples, dtype=bool)
test_mask[train_idx] = False
test_idx = np.where(test_mask)[0]
X_train, y_train = vectors[train_idx], labels[train_idx]
X_test, y_test = vectors[test_idx], labels[test_idx]
```

With:
```python
if test_vectors is not None:
    # Spatial mode: subsample train, use full fixed test set
    X_train, y_train = vectors[train_idx], labels[train_idx]
    X_test, y_test = test_vectors, test_labels
else:
    # Random mode: split from combined pool
    ...existing code...
```

### Edge cases
- Train area has only 1 class: yield an error event, don't crash.
- Test area has classes not in train: classifiers predict label 0 for unknown classes; confusion matrix rows will show this. No special handling needed (sklearn handles it).
- Very small test area (< 50 pixels): warn but proceed.

---

## Step 4: Wire Up the UI to Server

### Goal
The "Run Evaluation" button sends bounding boxes to the server. Show a warning popup if no boxes are drawn.

### Files changed

**`public/js/evaluation.js`**

Modify `runLargeAreaEvaluation()`:

```javascript
// Before fetch:
const bboxes = window.getBboxesForServer ? window.getBboxesForServer() : {};
const hasBboxes = (bboxes.train && bboxes.train.length > 0) ||
                  (bboxes.test && bboxes.test.length > 0);

if (!hasBboxes) {
    const ok = confirm(
        'No train/test areas drawn.\n\n' +
        'Default: random stratified split across entire shapefile area.\n\n' +
        'To define spatial train/test areas, cancel and draw bounding boxes on the satellite map.'
    );
    if (!ok) return;
}

// In the fetch body, add:
body: JSON.stringify({
    ...existingFields,
    train_bboxes: bboxes.train || [],
    test_bboxes: bboxes.test || [],
    map_bboxes: bboxes.map || [],
}),
```

The `start` event from the server should include a `spatial_mode: true/false` flag so the UI can show "Spatial train/test split" in the status.

---

## Step 5: Config File Serialization

### Goal
Save and restore bounding boxes in the evaluation config JSON.

### Files changed

**`public/js/evaluation.js`**

In `generateConfig()`, add:
```javascript
const bboxes = window.getBboxesForServer ? window.getBboxesForServer() : {};
config.train_bboxes = bboxes.train || [];
config.test_bboxes = bboxes.test || [];
config.map_bboxes = bboxes.map || [];
```

In `applyConfig()`, add:
```javascript
// Restore bounding boxes
if (window.clearAllBboxes) window.clearAllBboxes();
for (const type of ['train', 'test', 'map']) {
    const key = type + '_bboxes';
    if (config[key] && Array.isArray(config[key])) {
        for (const bbox of config[key]) {
            if (window.addBboxFromConfig) {
                window.addBboxFromConfig(type, bbox);  // [south, west, north, east]
            }
        }
    }
}
```

Add `addBboxFromConfig(type, [s,w,n,e])` to the bbox module: creates a rectangle on the map and adds it to state, same as if the user drew it.

### Config schema versioning
Bump schema to `tee_evaluate_config_v2`. Old v1 configs (no bboxes) load fine because the bbox fields are simply absent, which means "no bboxes = random split" -- backward compatible.

---

## Step 6: Three Action Buttons

### Goal
Replace the single "Run Evaluation" button with context-aware buttons.

### Design decision: keep ONE button, change semantics based on state
After review, three separate buttons add visual clutter to an already dense panel. Instead:

1. **Keep "Run Evaluation" as is** -- does train+test (learning curve). If bboxes drawn, uses spatial split. If not, uses random split.
2. **Add "Create Map" button** -- only enabled when map bboxes are drawn. Trains on ALL labels, then predicts across map area. Downloads GeoTIFF.

### Files changed

**`public/viewer.html`**
After the existing Run/Cancel button row:
```html
<div style="display:flex; gap:6px; margin-top:4px;">
    <button id="val-create-map-btn" class="validation-run-btn" disabled
        style="flex:1; background:#3cb44b;"
        title="Train on all labels, predict classification map in green areas">
        Create Map
    </button>
</div>
```

**`public/js/evaluation.js`**
- Enable the "Create Map" button only when `spatialBboxes.map.length > 0`.
- On click: call `POST /api/evaluation/create-map` with `{ field, year, classifiers, classifier_params, map_bboxes }`.

---

## Step 7: Map GeoTIFF Generation (Server)

### Goal
Train a classifier on ALL uploaded labels, then predict every pixel in the map bounding box and return a GeoTIFF.

### Files changed

**`packages/tessera-eval/tessera_eval/server.py`**

New endpoint:

```python
@app.route("/api/evaluation/create-map", methods=["POST"])
def create_map():
    """Train on all labels, predict classification across map bounding boxes."""
```

#### Algorithm

1. **Train the classifier** on the full cached `vectors`/`labels` from the last evaluation run (reuse `_tile_cache`). If no evaluation has been run yet, return an error.

2. **For each map bbox**, process one tile at a time:
   - Use `GeoTesseraZarr.read_region(bbox, year)` to get the embedding mosaic `(H, W, 128)`, transform, and CRS.
   - Large bboxes: split into a grid of sub-regions, each at most 1000x1000 pixels (~100M of float32 embeddings + 128 channels = ~500MB). Process and write sequentially.
   - Predict: reshape to `(H*W, 128)`, call `clf.predict()`, reshape to `(H, W)`.
   - Write to a temporary single-band GeoTIFF with class IDs (uint8, since class count < 256 typically), using rasterio with the correct CRS and transform.

3. **Multi-bbox handling**: if multiple map bboxes, produce one GeoTIFF per bbox, zip them together.

4. **Return**: stream the file back, or return a download URL.

#### Memory management (critical)

For a large map bbox like the Austria case:
- Austria at 10m: ~50K x 30K pixels = 1.5 billion pixels. At 128 float32 channels = ~750GB. This is impossible to do at once.
- The zarr `read_region()` call handles the bbox, but we must **tile the prediction**.

Strategy:
```python
CHUNK_SIZE = 512  # pixels per chunk side (512x512 = 262K pixels, ~130MB of embeddings)

def predict_map_tiled(clf, gtz, bbox, year, chunk_size=CHUNK_SIZE):
    """Predict classification map in tiles to cap memory."""
    # Read full mosaic metadata (bounds, shape) without loading data
    full_mosaic, full_transform, crs = gtz.read_region(bbox, year)
    H, W = full_mosaic.shape[:2]

    result = np.zeros((H, W), dtype=np.uint8)

    for r0 in range(0, H, chunk_size):
        for c0 in range(0, W, chunk_size):
            r1 = min(r0 + chunk_size, H)
            c1 = min(c0 + chunk_size, W)
            chunk = full_mosaic[r0:r1, c0:c1]  # (h, w, 128)
            h, w = chunk.shape[:2]
            flat = chunk.reshape(-1, chunk.shape[2])
            # Replace NaN with 0
            nan_mask = np.isnan(flat).any(axis=1)
            flat[nan_mask] = 0
            pred = clf.predict(flat)
            pred[nan_mask] = 0  # nodata
            result[r0:r1, c0:c1] = pred.reshape(h, w).astype(np.uint8)

    return result, full_transform, crs
```

**Problem**: `read_region` loads the entire bbox into memory. For very large areas, this is the bottleneck.

**Better approach**: tile the bbox into smaller geographic sub-bboxes and call `read_region` for each:

```python
def predict_map_chunked(clf, gtz, bbox, year, chunk_deg=0.1):
    """Predict in geographic chunks of chunk_deg x chunk_deg degrees."""
    west, south, east, north = bbox
    chunks = []
    lat = south
    while lat < north:
        lon = west
        while lon < east:
            sub_bbox = (lon, lat, min(lon + chunk_deg, east), min(lat + chunk_deg, north))
            try:
                mosaic, transform, crs = gtz.read_region(sub_bbox, year)
                flat = mosaic.reshape(-1, mosaic.shape[2]).astype(np.float32)
                nan_mask = np.isnan(flat).any(axis=1)
                flat[nan_mask] = 0
                pred = clf.predict(flat).astype(np.uint8)
                pred[nan_mask] = 0
                pred_2d = pred.reshape(mosaic.shape[:2])
                chunks.append((sub_bbox, pred_2d, transform, crs))
            except Exception:
                pass  # no data for this chunk
            lon += chunk_deg
        lat += chunk_deg
    return chunks
```

Then merge chunks into a single rasterio GeoTIFF using `rasterio.merge.merge` or manual placement. This keeps peak memory at one 0.1-degree tile (~1000x1000 pixels, ~50MB).

#### GeoTIFF writing

```python
import rasterio
from rasterio.transform import from_bounds

def write_geotiff(result, transform, crs, class_names, path):
    """Write classification result as a GeoTIFF with class names in metadata."""
    with rasterio.open(path, 'w', driver='GTiff', height=result.shape[0],
                       width=result.shape[1], count=1, dtype='uint8',
                       crs=crs, transform=transform, nodata=0,
                       compress='lz4') as dst:
        dst.write(result, 1)
        # Store class names as tags
        dst.update_tags(**{f'CLASS_{i+1}': name for i, name in enumerate(class_names)})
```

#### Optional: probability raster
Add a checkbox in the UI: "Include class probabilities". If checked, use `clf.predict_proba()` and write an N-band GeoTIFF (one band per class, float32). This is much larger, so warn the user.

Not all classifiers support `predict_proba` (e.g., k-NN does, RF does, but need to check). Fall back to argmax-only if `predict_proba` is unavailable.

**Defer probability raster to a later iteration** -- the single-band class map is the MVP.

#### Streaming progress

The `/create-map` endpoint should stream NDJSON like `/run-large-area`:
```json
{"event": "status", "message": "Training classifier on all labels..."}
{"event": "status", "message": "Predicting chunk 3/12..."}
{"event": "map_ready", "download_url": "/api/evaluation/download-map/latest"}
```

#### New endpoint for download:
```python
@app.route("/api/evaluation/download-map/<name>", methods=["GET"])
def download_map(name):
    path = _map_outputs.get(name)
    if not path:
        return jsonify({"error": "No map available"}), 404
    return send_file(path, as_attachment=True, download_name=f"classification_map.tif")
```

---

## Step 8: Hyperparameter Sweep (Config File)

### Goal
Allow multiple parameter sets per classifier. Each variant gets its own learning curve.

### Design

Config format:
```json
{
    "classifiers": {
        "mlp": [
            {"hidden_layers": "64,32"},
            {"hidden_layers": "256,128,64"}
        ],
        "rf": {"n_estimators": 100}
    }
}
```

If the value is a list, each element is a variant. If it's an object, it's a single variant (backward compatible).

### Files changed

**`packages/tessera-eval/tessera_eval/server.py`**

In `run-large-area`, when parsing `classifier_params`:
```python
# Expand hyperparameter variants
expanded_models = []
expanded_params = {}
for name in model_names:
    params = classifier_params.get(name, {})
    if isinstance(params, list):
        for i, p in enumerate(params):
            variant_name = f"{name}_v{i+1}"
            expanded_models.append(variant_name)
            expanded_params[variant_name] = p
            # Map variant name back to base classifier for make_classifier()
    else:
        expanded_models.append(name)
        expanded_params[name] = params
```

**`packages/tessera-eval/tessera_eval/classify.py`**

Add a mapping from variant names to base classifiers:
```python
def make_classifier(name, params=None):
    # Strip variant suffix: "mlp_v2" → "mlp"
    base_name = re.sub(r'_v\d+$', '', name)
    ...use base_name for classifier lookup...
```

**`public/js/evaluation.js`**

- In the chart and confusion matrix, show variant labels like "MLP (v1: 64,32)" and "MLP (v2: 256,128,64)".
- Add `CLASSIFIER_COLORS` entries for variants: derive from base colour with lighter/darker shades.

### UI for adding variants

In `viewer.html`, add a small "+" button next to each classifier's parameters section. Clicking it duplicates the parameter fields with a "(v2)" label. Clicking "-" removes the variant.

**Defer this UI to a later iteration.** For now, variants are config-file only. The UI always sends a single parameter set per classifier. Users who want sweeps edit the config JSON manually.

---

## Implementation Order

| Phase | Steps | Effort | Dependencies |
|-------|-------|--------|-------------|
| **Phase 1** | Steps 1-2 | 2 days | None |
| **Phase 2** | Steps 3-4 | 2 days | Phase 1 |
| **Phase 3** | Step 5 | 0.5 day | Phase 2 |
| **Phase 4** | Steps 6-7 | 3 days | Phase 2 |
| **Phase 5** | Step 8 | 1 day | Phase 2 (server changes) |

Phase 1+2 are the core feature. Phase 4 (map generation) is the most complex and can be done independently after Phase 2.

---

## Risks and Mitigations

### 1. Memory blowup on large map areas
**Risk**: User draws a map bbox covering all of Austria (500km x 400km, ~5B pixels).
**Mitigation**: Geographic chunking (Step 7). Process 0.1-degree tiles (~1000x1000px) sequentially. Peak memory: ~50MB per chunk. Also add a client-side warning if the map bbox exceeds 10,000 km2.

### 2. Zarr read_region not available
**Risk**: User runs without zarr (NPY-only mode). Map generation needs to read arbitrary regions.
**Mitigation**: Require zarr for map generation. Show "Map generation requires zarr store" error if unavailable. The train/test bbox feature works with NPY since it only filters existing sample points.

### 3. Spatial autocorrelation still present within bounding boxes
**Risk**: User draws overlapping train/test boxes, or adjacent boxes with only a few pixels gap.
**Mitigation**: Document that train takes priority for overlapping points. Add a visual warning (orange border flash) if train and test boxes overlap. Add a note in the UI: "For valid spatial cross-validation, ensure train and test areas are geographically separated."

### 4. Tiny test area / class imbalance
**Risk**: Test area contains only 20 pixels of 1 class.
**Mitigation**: Server checks minimum: at least 2 classes in test, at least 10 pixels per class. Emit warning events streamed to the UI.

### 5. Leaflet.Draw conflicts with labelling mode
**Risk**: Both validation and labelling mode use Leaflet.Draw on the same map.
**Mitigation**: Disable bbox drawing handler when leaving validation mode. The labelling polygon handler is already guarded by mode. Both use separate `L.Draw` instances (Rectangle vs Polygon) on `maps.rgb`.

### 6. Backward compatibility with existing configs
**Risk**: Old configs (v1) break.
**Mitigation**: Absence of `train_bboxes`/`test_bboxes`/`map_bboxes` fields means "no spatial split" -- falls through to existing random-split code. Config schema check: accept both v1 and v2.

### 7. GeoTIFF file size
**Risk**: A 10,000 x 10,000 pixel uint8 GeoTIFF with LZ4 compression is ~20-50MB. Probability raster (float32, N bands) could be 500MB+.
**Mitigation**: Default to class-only (uint8). Probability raster is opt-in and deferred. Compress with LZ4 (fast) or DEFLATE (smaller).

### 8. Classifier choice for map generation
**Risk**: Some classifiers (U-Net, spatial MLP) need spatial features, not point embeddings. Map generation reads raw embeddings.
**Mitigation**: For the map endpoint, only support pixel-based classifiers (k-NN, RF, XGBoost, MLP). Spatial MLP and U-Net map generation would require extracting 3x3/5x5 neighborhoods at every pixel, which is expensive. Document this limitation. Spatial MLP map support can be added later.

---

## Detailed File Change Summary

### `public/viewer.html`
- Add bbox type selector and draw/clear buttons to panel 2 header
- Add "Create Map" button below Run Evaluation
- Add CSS to show bbox controls only in validation mode
- Add CSS for bbox type selector styling

### `public/js/evaluation.js`
- Import/use bbox state from maps module
- Modify `runLargeAreaEvaluation()` to include bboxes in request body
- Add warning popup if no bboxes drawn
- Add `runCreateMap()` function for map generation
- Modify `generateConfig()` to include bboxes
- Modify `applyConfig()` to restore bboxes
- Add `handleStreamEvent` cases for `map_ready` event
- Enable/disable "Create Map" button based on map bbox state
- Bump config schema to v2

### `public/js/maps.js`
- Add `spatialBboxes` state, layer groups, colour constants
- Add `startBboxDraw()`, `deleteBbox()`, `clearAllBboxes()`, `getBboxesForServer()`, `addBboxFromConfig()`
- Cancel active draw handler in `setPanelLayout()` when leaving validation mode
- Expose functions on `window` for cross-module access

### `packages/tessera-eval/tessera_eval/server.py`
- Add `_split_points_by_bboxes()` helper function
- Modify `run-large-area` to accept and use `train_bboxes`/`test_bboxes`
- Add `create-map` endpoint with chunked prediction
- Add `download-map` endpoint
- Add `_map_outputs` state dict for temporary GeoTIFF paths

### `packages/tessera-eval/tessera_eval/evaluate.py`
- Add `test_vectors`, `test_labels`, `test_spatial_vectors`, `test_spatial_vectors_5x5` parameters to `run_learning_curve()`
- When test data provided: subsample from train only, test on full fixed set
- Report `spatial_mode: true` in progress events

### `packages/tessera-eval/tessera_eval/classify.py`
- Handle variant name suffix stripping in `make_classifier()` (Step 8)

---

## Testing Plan

### Manual tests
1. **No bboxes**: Upload shapefile, run evaluation. Confirm identical behaviour to current system.
2. **Train+test bboxes**: Draw 2 train boxes, 1 test box. Run evaluation. Verify confusion matrix is computed on test-area pixels only. Verify status shows "Spatial train/test split".
3. **Overlapping boxes**: Draw overlapping train and test box. Verify no points are double-counted (training takes priority).
4. **Map generation**: Draw a small map bbox (~5km x 5km). Click Create Map. Verify GeoTIFF downloads, opens in QGIS, has correct CRS and class values.
5. **Large map area**: Draw a 50km x 50km box. Monitor memory usage during prediction. Verify it stays under 2GB.
6. **Config round-trip**: Generate config with bboxes. Clear all. Upload config. Verify bboxes reappear on the map.
7. **Mode switching**: Switch to labelling mode and back to validation. Verify bboxes persist.

### Automated tests (`tests/test_cli.py`)
- Test `_split_points_by_bboxes()` with known points and boxes.
- Test `run_learning_curve()` with pre-split data (test_vectors provided).
- Test variant name stripping in `make_classifier()`.

---

## Non-goals (explicitly deferred)

1. **Probability raster**: Multi-band float32 GeoTIFF with per-class probabilities. Add later as a checkbox.
2. **Spatial MLP / U-Net map generation**: Requires spatial feature extraction at every pixel. Expensive. Defer.
3. **UI for hyperparameter variants**: The "+" button for adding variant parameter rows. Config-file only for now.
4. **Map area preview on satellite panel**: Rendering a preview of the predicted map as a tile overlay. Would be nice but complex (needs server-side tile serving of the result). Defer.
5. **Polygon-based train/test regions**: Bounding boxes only. Arbitrary polygon train/test regions add complexity with minimal user benefit for ecologists (bboxes are easier to reason about).
