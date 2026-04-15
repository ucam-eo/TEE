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

| Mode | Django (8001) | tee-compute (8002) | User opens | How to start |
|------|:------------:|:------------------:|:----------:|:------------|
| **Local dev** | localhost | localhost | localhost:8001 | `./scripts/deploy-compute.sh --local` |
| **Hosted + local compute** | tee.cl.cam.ac.uk | user's laptop | localhost:8001 (tee-compute proxies to hosted) | `./scripts/deploy-compute.sh` |
| **Hosted + remote compute** | tee.cl.cam.ac.uk | GPU box via SSH tunnel | localhost:8001 | `./scripts/deploy-compute.sh gpu-box` |

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
        { content: 'panel6-label-view',     title: '' },
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
                                    ├── /api/evaluation/upload-shapefile (returns estimated_labelled_pixels, per-class polygon counts)
                                    ├── /api/evaluation/clear-shapefiles
                                    ├── /api/evaluation/run-large-area (streaming NDJSON learning curves)
                                    ├── /api/evaluation/cancel (CORS-enabled for direct access)
                                    ├── /api/evaluation/finish-classifier
                                    ├── /api/evaluation/train-models
                                    ├── /api/evaluation/download-model/<name>
                                    ├── /api/evaluation/create-map (streaming NDJSON, GeoTIFF generation)
                                    ├── /api/evaluation/download-map/<name>
                                    └── /health

                                  Everything else proxied back to Django
```

### Evaluation flow

Pixel classifiers (k-NN, RF, XGBoost, MLP) use `sample_embeddings_at_points` to
fetch embeddings at random point locations — fast and memory-efficient. Spatial
classifiers (Spatial MLP, U-Net) fetch real tiles via `fetch_embeddings` and
extract pixel-aligned 256×256 crops with the tile's native CRS and transform.

```
Upload shapefile(s) → select field + year + classifiers + sampling → Run
    ↓
tee-compute:
    1. field_start event (emitted immediately)
    2. GeoTessera init (cached instance reused across runs)
    3. Generate stratified random points within shapefile polygons (200K max)
       Sampling strategy: equal, sqrt-proportional, or proportional to area
    4. If spatial MLP or U-Net selected → single tile pass:
        a. Fetch tiles via gt.fetch_embeddings (shuffled for geographic
           diversity, up to 5 patches per tile, cancellable per tile)
        b. From each tile: extract point sample values AND random 256×256
           pixel-aligned crops (both from the same tile, tiles fetched once)
        c. Spatial MLP: extract 3×3 or 5×5 neighbourhood features,
           subsampled to 5000 pixels/patch to cap memory
        d. U-Net: receives full 256×256 patches (no subsampling)
        e. Augmentation: 16× per patch (4 rotations × 2 flips × 2 noise
           levels) during U-Net training
       If pixel-only classifiers → use sample_embeddings_at_points (faster)
    5. start event (pixel counts, class info, training percentages,
       estimated total labelled pixels from shapefile polygon areas)
    6. run_learning_curve (% of labels, adaptive repeats, 200K test cap)
        → classifier_status events per model
        → progress events with actual training pixel counts per classifier
    7. confusion_matrices event
    8. done event (or Cancelled if user clicks Cancel)

Download Models (deferred, user-triggered):
   10. POST /api/evaluation/train-models
   11. Train each classifier on full data → model_ready events
   12. User downloads .joblib / .pt files
```

### NDJSON streaming

Events are streamed as newline-delimited JSON. Each line is padded to 18KB
to force Waitress to flush immediately. The Django proxy uses matching chunk
size and `Content-Encoding: identity` to prevent GZip buffering. Timeouts
are set to 7200s (2 hours) on both Waitress and the proxy.

### Caching (three layers)

| Layer | Key | Scope | Contents |
|-------|-----|-------|----------|
| **Disk result cache** | `(field, year, sampling, gdf_hash)` | `~/.cache/tessera-eval/` | Compressed `.npz` with vectors + labels (~100MB for 200K pixels). Survives restarts. |
| **In-memory cache** | `(field, year, sampling)` | `_tile_cache` global | Vectors, labels, spatial features, U-Net patches, model params. Lost on restart. |
| **GeoTessera instance** | singleton | `_geotessera_instance` | Loaded registry parquet. Avoids 10-30s HTTP check per run. |

Re-running with different classifiers: uses in-memory cache (instant).
Re-running after restart: uses disk result cache (<1s load).
Changing field or year: both caches miss, full point sampling required.
Uploading a new shapefile: in-memory cache not invalidated (keyed by field+year).

### Performance optimizations (country-scale)

The pipeline is optimized for large evaluations (e.g., Austria: 40 tiles,
42K features, 1M labelled pixels, 37 classes):

**Data loading phase (point sampling):**

| Optimization | What it avoids | Savings |
|---|---|---|
| Point sampling via `sample_embeddings_at_points` | Loading full tiles (~450MB each) for pixel classifiers | Memory bounded at ~100MB |
| Configurable sampling (equal/sqrt/proportional) | Equal sampling making weighted F1 meaningless | Meaningful weighted F1 scores |
| GeoTessera instance caching | Registry HTTP check + parquet read per run | 10-30s per run |
| Disk result cache (vectors + labels + sampling) | Re-downloading on restart | <1s vs minutes |
| Single tile pass for point + patch extraction | Two separate tile fetches (point sampling then patches) | Tiles fetched once, halves fetch time |
| Real tile patches for U-Net/spatial MLP | Point-grid patches with no spatial coherence | Pixel-aligned patches, U-Net F1 comparable to viewport mode |
| Spatial feature subsampling (5K px/patch) | 65K×1152 features per patch (~300MB) | ~11.5GB total for 500 patches |
| Tile shuffling for patch extraction | Geographic clustering of patches | Diverse coverage across study area |
| 16× augmentation (rotations + flips + noise) | Limited U-Net training data | Effective 8000 training images from 500 patches |
| Server-side cancellation via cancel flag | Zombie computations after user cancels | Cancel checked per tile, per learning curve step |
| Deferred model training (user-triggered) | 45+ minute U-Net training blocking results | Results in ~1 min |

**Learning curve phase:**

| Optimization | What it avoids | Savings |
|---|---|---|
| Test set capped at 200K | KNN predict on 990K pixels (30-60s per call) | 10-20 min |
| Pre-computed per-class indices | Scanning 1M labels × 37 classes × 40 iterations | ~5s |
| Boolean mask for test indices | `np.setdiff1d` O(N log N) per repeat | ~1.5s |
| Adaptive repeats (fewer at high %) | 5 repeats at 80% where variance is negligible | ~25% of high-% time |

**When extending the pipeline**, keep these principles:
- Use `sample_embeddings_at_points` for pixel-only classifiers (fast, no tile loading)
- Use `fetch_embeddings` for spatial/U-Net — pixel-aligned tile crops, single pass for both points and patches
- Cap total pixel samples at 200K — diminishing returns above that for learning curves
- Cap spatial features at 5K pixels per patch to prevent memory blow-up on dense labels
- Defer expensive operations (model training) behind user-triggered endpoints
- Every phase >2s must emit a status event to the browser
- Check `cancel_flag` in any loop that processes tiles or learning curve steps

### tessera_eval library

The ML library (`packages/tessera-eval/tessera_eval/`) is framework-independent:

| Module | Purpose |
|--------|---------|
| `evaluate.py` | `run_learning_curve`, `run_kfold_cv`, `detect_field_type` |
| `classify.py` | `make_classifier`, `make_regressor`, `gather_spatial_features_2d` |
| `rasterize.py` | `rasterize_shapefile` (with optional pre-fitted LabelEncoder) |
| `data.py` | `load_embeddings_for_shapefile` (tile-by-tile with CRS reprojection) |
| `unet.py` | `extract_labelled_patches`, `TinyUNet`, `train_unet_on_patches` |
| `zarr_utils.py` | Singleton zarr store instance, coverage probing, chunked region reading |
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
./scripts/deploy-compute.sh --local
# Starts Django on :8001 + tee-compute on :8002
# Open http://localhost:8001
```

### Production (tee.cl.cam.ac.uk)

```bash
docker buildx build --platform linux/amd64 \
    --build-arg GIT_VERSION="$(git describe --tags --always)" \
    -t sk818/tee:stable --push .

ssh tee.cl.cam.ac.uk
sudo ./manage.sh   # option 7: pull, restart, health-check
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
│   ├── schemas/
│   │   ├── ukhab-v2.json       UKHab v2 classification schema
│   │   ├── hotw.json           Habitats of the World schema
│   │   └── eunis.json          EUNIS terrestrial habitat schema
│   ├── viewer.html             6-panel viewer
│   ├── viewport_selector.html  Viewport list / creation
│   └── user_guide.md           User documentation
│
├── api/views/
│   ├── viewports.py            Viewport CRUD + embedding coverage
│   ├── evaluation.py           Proxy to tee-compute (no ML here)
│   ├── pipeline.py             Pipeline progress + cancellation
│   ├── tiles.py                Tile server
│   ├── share.py                Label sharing
│   ├── enrolment.py            User creation + management
│   ├── vector_data.py          Serve raw vector files
│   ├── compute.py              Projection loading, year lookups
│   └── config.py               Health, config, static files
│
├── lib/
│   ├── config.py               Paths & directory constants
│   ├── viewport_utils.py       Viewport reading & validation
│   ├── viewport_writer.py      Viewport creation & symlink
│   ├── viewport_ops.py         Readiness checks, data deletion
│   ├── pipeline.py             Pipeline orchestration
│   ├── progress_tracker.py     Progress JSON persistence
│   ├── tile_renderer.py        Slippy map tile rendering
│   └── evaluation_engine.py    Shim — re-exports from tessera_eval
│
├── packages/tessera-eval/tessera_eval/
│   ├── evaluate.py             Learning curves, k-fold CV
│   ├── classify.py             Classifiers, regressors, spatial features
│   ├── rasterize.py            Shapefile rasterization
│   ├── data.py                 Tile-by-tile embedding loading
│   ├── unet.py                 U-Net patches, training, prediction
│   ├── zarr_utils.py           Zarr store singleton, coverage, chunked reads
│   └── server.py               tee-compute Flask server
│
├── scripts/
│   ├── deploy-compute.sh       Start tee-compute (--local for Django too)
│   └── tee_evaluate.py         Standalone CLI for batch evaluation
├── process_viewport.py         Pipeline: fetch tiles → pyramids → vectors
├── Dockerfile                  Production Docker build
├── docker-compose.yml          Docker Compose for production
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
