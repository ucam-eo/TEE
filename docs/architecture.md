# TEE Architecture

System architecture for the Tessera Embeddings Explorer.

---

## 1. High-Level Architecture

TEE runs as two services locally, or as a hosted server with user-run compute:

```
 +-----------------------------+       +-------------------------------+
 |        Browser (JS)         |       |     Django Backend (Python)   |
 |                             |       |     (port 8001)               |
 |  viewer.html                |       |  api/views/                   |
 |    +-- app.js               | HTTP  |    viewports.py  config.py    |
 |    +-- maps.js              |<----->|    tiles.py      pipeline.py  |
 |    +-- vectors.js           |       |    vector_data.py             |
 |    +-- labels.js            |       |    evaluation.py (proxy)      |
 |    +-- segmentation.js      |       |    share.py     enrolment.py  |
 |    +-- dimreduction.js      |       |                               |
 |    +-- evaluation.js        |       |  Proxies /api/evaluation/*    |
 |    +-- schema.js            |       |  to tee-compute (port 8002)   |
 |                             |       +-------------------------------+
 |  Leaflet maps (5 panels)   |              |           |
 |  Three.js scene (panel 4)  |    +---------+           +----------+
 |  Chart.js (validation)     |    |                                |
 |  IndexedDB (vector cache)  | +--v----------+          +-----------v---------+
 +-----------------------------+ | Filesystem  |          | tee-compute         |
                                 |             |          | (port 8002)         |
                                 | viewports/  |          |                     |
                                 | pyramids/   |          | Flask + waitress    |
                                 | vectors/    |          | tessera_eval lib    |
                                 | mosaics/    |          | GeoTessera tiles    |
                                 | share/      |          +----------+----------+
                                 +-------------+                     |
                                                          +---------v-----------+
                                                          | GeoTessera API      |
                                                          | dl2.geotessera.org  |
                                                          +---------------------+
```

### Deployment Modes

| Mode | Django (8001) | tee-compute (8002) | User opens |
|------|:------------:|:------------------:|:----------:|
| **Local dev** | localhost | localhost | localhost:8001 |
| **Hosted + local compute** | tee.cl.cam.ac.uk | user's laptop | localhost:8001 (tee-compute proxies to hosted) |
| **Hosted + remote compute** | tee.cl.cam.ac.uk | GPU box via SSH tunnel | localhost:8001 |

In all modes, the browser talks only to port 8001. Django proxies
`/api/evaluation/*` requests to tee-compute. All ML runs on tee-compute,
never on the hosted Django server.

### Data Privacy

Ground-truth shapefiles and evaluation results never leave the compute node.
The hosted server only sees map tile requests and explicit label sharing (opt-in).
Similarity searches run entirely in the browser.

---

## 2. Panel Layout (Declarative)

The viewer uses a 6-panel CSS grid. Which content each panel shows is controlled
by a **single declarative table** in `maps.js`:

```javascript
const PANEL_LAYOUT = {
    'explore': [
        { content: null,                    title: 'OpenStreetMap' },
        { content: null,                    title: 'Satellite' },
        { content: null,                    title: 'Tessera Embeddings' },
        { content: null,                    title: 'PCA (Embedding Space)' },
        { content: null,                    title: '' },
        { content: null,                    title: '' },
    ],
    'change-detection': [
        { content: null,                    title: 'OpenStreetMap' },
        { content: null,                    title: 'Satellite' },
        { content: null,                    title: 'Tessera Embeddings' },
        { content: 'change-stats-panel',    title: 'Change Distribution' },
        { content: null,                    title: 'Change Heatmap' },
        { content: null,                    title: 'Tessera Embeddings' },
    ],
    'labelling': [
        { content: null,                    title: 'OpenStreetMap' },
        { content: null,                    title: 'Satellite' },
        { content: null,                    title: 'Tessera Embeddings' },
        { content: null,                    title: 'PCA (Embedding Space)' },
        { content: null,                    title: 'Classification results' },
        { content: 'panel6-label-view',     title: 'Auto-label' },
    ],
    'validation': [
        { content: 'validation-controls',   title: 'Controls',         header: false, flow: true },
        { content: null,                    title: 'Satellite' },
        { content: 'val-class-table-panel', title: 'Ground Truth',     header: false },
        { content: 'val-results-panel',     title: 'Progress',         header: false },
        { content: 'validation-chart-panel',title: 'Learning Curves',  header: false },
        { content: 'val-cm-panel',          title: 'Confusion Matrix', header: false },
    ],
};
```

### How the table works

- `content: null` = show the panel's default map
- `content: 'element-id'` = hide the map, show the named overlay element (positioned absolutely to fill the panel)
- `content: 'hidden'` = hide the entire panel
- `header: false` = hide the panel header bar
- `flow: true` = don't position absolutely (element flows normally, panel scrolls)
- `also: ['id']` = show additional elements in the same panel

### Architecture rules

1. **PANEL_LAYOUT is the single source of truth** for what each panel shows in each mode
2. **maps.js** owns panel visibility (show/hide panels, position overlays, set headers)
3. **evaluation.js** owns content within panels (text, table rows, chart data, sub-element toggles)
4. evaluation.js must **never** set `style.display` on PANEL_LAYOUT-controlled elements
5. All switchable overlay elements start with `display: none` in CSS
6. Elements use `data-display-mode="flex"` when they need flex instead of block

### Switchable content elements

Each content element must live in the same physical panel it's displayed in:

| Panel | Default map | Switchable overlays |
|-------|-------------|--------------------|
| 1 | `#map-osm` | `#validation-controls` |
| 2 | `#map-rgb` | (none) |
| 3 | `#map-embedding` | `#val-class-table-panel` |
| 4 | `#map-umap` | `#val-results-panel`, `#change-stats-panel` |
| 5 | `#map-panel5` | `#validation-chart-panel` |
| 6 | `#map-embedding2` | `#val-cm-panel`, `#panel6-label-view` |

### Adding a new mode

To add a new mode (e.g., `'comparison'`):
1. Add a 6-entry array to `PANEL_LAYOUT` in `maps.js`
2. Add any new overlay element IDs to the `SWITCHABLE` array
3. Add the element to the correct panel's HTML in `viewer.html`
4. Set `display: none` in CSS for the new element
5. Add `data-display-mode` attribute if it needs flex

No other changes needed. No CSS rules. No JS logic.

---

## 3. Evaluation Architecture

### Compute server (tee-compute)

All ML evaluation runs on `tee-compute` (a Flask app in `tessera_eval/server.py`).
Django proxies `/api/evaluation/*` requests to it.

```
Browser → Django :8001 → proxy → tee-compute :8002
                                    ├── /api/evaluation/upload-shapefile
                                    ├── /api/evaluation/clear-shapefiles
                                    ├── /api/evaluation/run-large-area
                                    ├── /api/evaluation/finish-classifier
                                    ├── /api/evaluation/download-model/<name>
                                    └── /health

                                  Everything else proxied back to Django
```

### Evaluation flow

```
Upload shapefile(s) → select field + year + classifiers → Run
    ↓
tee-compute:
    1. field_start event (emitted immediately, before GeoTessera init)
    2. GeoTessera init (cached instance reused across runs)
    3. load_blocks_for_region(bbox, year)
    4. Reproject GDF to tile CRS once (cached per CRS)
    5. For each tile (lazy loading — one at a time):
        a. download_progress event (shows "cached" or "downloading")
        b. Load from disk cache or download from GeoTessera
        c. Filter GDF to tile bbox using spatial index (R-tree)
        d. Rasterize shapefile onto tile grid
        e. Extract labelled pixel embeddings
        f. Compute spatial features for labelled pixels only (if spatial_mlp)
        g. Extract U-Net patches (if unet selected)
    6. Memory check (abort with message if > 80% of available RAM)
    7. start event (with pixel counts, class info, training percentages)
    8. run_learning_curve (% of labels, adaptive repeats, capped test set)
        → progress events per percentage
    9. confusion_matrices event
   10. Retrain all models on full data → model_ready events
   11. done event
```

### NDJSON streaming

Events are streamed as newline-delimited JSON. Each line is padded to 18KB
to force Waitress to flush immediately. The Django proxy uses matching chunk
size and `Content-Encoding: identity` to prevent GZip buffering. Timeouts
are set to 7200s (2 hours) on both Waitress and the proxy.

### Caching (three layers)

| Layer | Key | Scope | Contents |
|-------|-----|-------|----------|
| **Disk tile cache** | `(year, lon, lat)` | `~/.cache/tessera-eval/tiles/` | Uncompressed `.npz` per tile (~488MB each). Survives restarts. |
| **In-memory tile cache** | `(field, year)` | `_tile_cache` global | Concatenated vectors, labels, spatial features, U-Net patches. Lost on restart. |
| **GeoTessera instance** | singleton | `_geotessera_instance` | Loaded registry parquet. Avoids 10-30s HTTP check per run. |

Re-running with different classifiers: uses in-memory cache (instant).
Re-running after restart: uses disk cache (tiles load in ~0.15s each vs ~100s download).
Changing field or year: in-memory cache misses, disk cache still valid.
Uploading a new shapefile: caches are NOT invalidated (tiles don't depend on shapefile).

### Performance optimizations (country-scale)

The pipeline is optimized for large evaluations (e.g., Austria: 40 tiles,
42K features, 1M labelled pixels, 37 classes):

**Tile loading phase:**

| Optimization | What it avoids | Savings |
|---|---|---|
| Lazy tile loading (one at a time) | Loading all 40 tiles into memory at once (~10GB) | Prevents OOM |
| Uncompressed disk cache | zlib decompression (2-3s/tile) | 60-80s per cached run |
| GeoTessera instance caching | Registry HTTP check + parquet read per run | 10-30s per run |
| GDF reproject once per CRS | Reprojecting 42K features per tile (40x) | 4-20s |
| Spatial index (R-tree) for bbox | Testing all 42K features per tile | 2-9s |
| Masked spatial features | Computing features for full tile then indexing | 96% memory reduction |
| Memory check before concatenation | Silent OOM kill | Clear error with advice |

**Learning curve phase:**

| Optimization | What it avoids | Savings |
|---|---|---|
| Test set capped at 200K | KNN predict on 990K pixels (30-60s per call) | 10-20 min |
| Pre-computed per-class indices | Scanning 1M labels × 37 classes × 40 iterations | ~5s |
| Boolean mask for test indices | `np.setdiff1d` O(N log N) per repeat | ~1.5s |
| Adaptive repeats (fewer at high %) | 5 repeats at 80% where variance is negligible | ~25% of high-% time |

**When extending the pipeline**, keep these principles:
- Never allocate H×W×dim arrays for the full tile — always mask to labelled pixels first
- Never reproject or filter the full GDF per tile — cache reprojections, use spatial index
- Never load all tiles into memory — process one at a time, accumulate only labelled pixels
- Free intermediate lists (`del all_vectors`) after concatenation

### tessera_eval library

The ML library (`packages/tessera-eval/tessera_eval/`) is framework-independent:

| Module | Purpose |
|--------|---------|
| `evaluate.py` | `run_learning_curve`, `run_kfold_cv`, `detect_field_type` |
| `classify.py` | `make_classifier`, `make_regressor`, `gather_spatial_features_2d` |
| `rasterize.py` | `rasterize_shapefile` (with optional pre-fitted LabelEncoder) |
| `data.py` | `load_embeddings_for_shapefile` (tile-by-tile with CRS reprojection) |
| `unet.py` | `extract_labelled_patches`, `TinyUNet`, `train_unet_on_patches` |
| `server.py` | Flask compute server (`tee-compute` CLI) |

The library has **no Django imports**. It can be installed standalone:
```bash
pip install tessera-eval[server]
tee-compute --hosted https://tee.cl.cam.ac.uk
```

---

## 4. Module Dependency Graph

All 8 modules are loaded as ES modules in `viewer.html`. They communicate
through `window.*` properties. No import/export between modules.

```
  app.js
    |
    +--- maps.js        (map creation, sync, PANEL_LAYOUT, mode switching)
    |       |
    |       +--- vectors.js    (download, cache, search, DirectCanvasLayer)
    |       |       |
    |       |       +--- labels.js    (manual labels, overlays, polygon, export)
    |       |       |       |
    |       |       |       +--- schema.js    (schema browser, label selection)
    |       |       |
    |       |       +--- segmentation.js  (k-means, cluster list, seg overlay)
    |       |
    |       +--- dimreduction.js  (PCA, UMAP, heatmap, Three.js scene)
    |
    +--- evaluation.js   (validation: shapefile upload, learning curves, CM)
```

---

## 5. State Management

### 5.1 Responsibility Split

| System | Owns | Example |
|--------|------|---------|
| `PANEL_LAYOUT` (maps.js) | Panel visibility, overlay positioning, headers | Which panel shows what in each mode |
| `PANEL5_LAYER_RULES` (maps.js) | Leaflet layer visibility per mode | Heatmap shown in change-detection, hidden in validation |
| `evaluation.js` | Content within panels | Text updates, table rows, chart data |
| `app.js` | Dependency cascade, polling | When to download vectors, compute PCA |
| CSS | Initial hidden state, styling | `#validation-controls { display: none; }` |

### 5.2 Window Property Bridges

Each module declares private state and exposes it on `window`:

| Module | Properties on `window` |
|---|---|
| `app.js` | `currentViewportName`, `currentEmbeddingYear`, `viewportStatus`, `currentPanelMode` |
| `maps.js` | `viewportBounds`, `PANEL5_LAYER_RULES`, `persistentLabelMarkers` |
| `vectors.js` | `localVectors`, `explorerResults` |
| `labels.js` | `manualLabels`, `currentManualLabel`, `savedLabels` |
| `evaluation.js` | `lastEvalData` |

---

## 6. Testing

```bash
venv/bin/pytest validation/ tests/ -v
```

| File | What it checks |
|------|---------------|
| `validation/test_refactoring_guards.py` | API endpoints in JS, critical functions exist, state variables, DOM elements, CSS mode rules, PANEL_LAYOUT table, backend libraries, NDJSON event schema, tessera_eval self-containment |
| `validation/test_viewer_html.py` | HTML structure, JS syntax, mode classes, panel layout has all modes, large-area validation elements |
| `tests/test_kfold.py` | K-fold CV, regression metrics, regressor factory |
| `tests/test_cli.py` | CLI config validation, auto-type detection, dry-run |
| `tests/test_rasterize_encoder.py` | Rasterize with pre-fitted LabelEncoder |
| `tests/test_dry_run_field_validation.py` | Dry-run with bad field name |
| `tests/test_upload_proxy.py` | End-to-end upload through Django proxy (requires servers running) |

---

## 7. Deployment

### Local development

```bash
./restart.sh
# Starts Django on :8001 + tee-compute on :8002
# Open http://localhost:8001
```

### Production (tee.cl.cam.ac.uk)

```bash
docker buildx build --platform linux/amd64 \
    --build-arg GIT_VERSION="$(git describe --tags --always)" \
    -t sk818/tee:stable --push .

ssh tee.cl.cam.ac.uk
sudo ./manage.sh   # option 5: pull, restart, health-check
```

Production runs Django only (no tee-compute). Users run their own `tee-compute`
pointing `--hosted` at `https://tee.cl.cam.ac.uk`.

### Version tags

Always create an annotated git tag on version bumps:
```bash
git tag -a alpha-3.10 -m "description"
git push origin alpha-3.10
```
The viewport selector header shows the version from `git describe --tags --always`.

---

## 8. File Structure

```
TEE/
├── public/
│   ├── js/
│   │   ├── app.js              Application init, dependency system
│   │   ├── maps.js             Maps, sync, PANEL_LAYOUT, mode switching
│   │   ├── vectors.js          Vector download, search, canvas layers
│   │   ├── labels.js           Manual labels, polygon, sharing, export
│   │   ├── segmentation.js     K-means clustering, seg overlay
│   │   ├── dimreduction.js     PCA, UMAP, heatmap, Three.js
│   │   ├── evaluation.js       Validation panel content (NOT layout)
│   │   └── schema.js           Schema browser, label selection
│   ├── viewer.html             6-panel viewer
│   ├── viewport_selector.html  Viewport list / creation
│   └── user_guide.md           User documentation
│
├── api/views/
│   ├── viewports.py            Viewport CRUD + embedding coverage
│   ├── evaluation.py           Proxy to tee-compute (no ML here)
│   ├── tiles.py                Tile server
│   ├── share.py                Label sharing
│   └── config.py               Health, config, static files
│
├── packages/tessera-eval/tessera_eval/
│   ├── evaluate.py             Learning curves, k-fold CV
│   ├── classify.py             Classifiers, regressors, spatial features
│   ├── rasterize.py            Shapefile rasterization
│   ├── data.py                 Tile-by-tile embedding loading
│   ├── unet.py                 U-Net patches, training, prediction
│   └── server.py               tee-compute Flask server
│
├── scripts/tee_evaluate.py     Standalone CLI for batch evaluation
├── restart.sh                  Start Django + tee-compute locally
├── Dockerfile                  Production Docker build
└── docs/                       This documentation
```

---

## 9. Extending TEE

### Adding a new classifier

1. Add factory function in `tessera_eval/classify.py`
2. Add to `available_classifiers()` or `available_regressors()`
3. Add checkbox HTML in panel 1's `#validation-controls` in `viewer.html`
4. No server.py changes needed (it reads classifier names from the request)

### Adding a new panel mode

1. Add entry to `PANEL_LAYOUT` in `maps.js` (6 panel specs)
2. Add entry to `PANEL5_LAYER_RULES` if needed
3. Add any new overlay elements to HTML (in the correct panel)
4. Add element IDs to `SWITCHABLE` array in `maps.js`
5. Add `display: none` CSS rule for new elements
6. Add option to the layout dropdown in `viewer.html`

### Adding a new evaluation endpoint

1. Add the endpoint in `tessera_eval/server.py`
2. Add a proxy function in `api/views/evaluation.py`
3. Add the URL pattern in `api/urls.py`
4. Call it from `evaluation.js`

### Adding a new NDJSON event type

1. Yield the event in `server.py`'s stream generator
2. Add handler in `evaluation.js`'s `handleStreamEvent()`
3. Add to `TestNDJSONEventSchema.BACKEND_EVENTS` in `test_refactoring_guards.py`
