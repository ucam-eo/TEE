# TEE Architecture

System architecture for the Tessera Embeddings Explorer.

---

## 1. High-Level Architecture

```
 +-----------------------------+       +-------------------------------+
 |        Browser (JS)         |       |     Django Backend (Python)   |
 |                             |       |                               |
 |  viewer.html                |       |  api/views/                   |
 |    +-- app.js               | HTTP  |    viewports.py  config.py    |
 |    +-- maps.js              |<----->|    tiles.py      pipeline.py  |
 |    +-- vectors.js           |       |    vector_data.py             |
 |    +-- labels.js            |       |    evaluation.py              |
 |    +-- segmentation.js      |       |                               |
 |    +-- dimreduction.js      |       |  lib/                         |
 |    +-- evaluation.js        |       |    config.py                  |
 |    +-- schema.js            |       |    viewport_utils.py          |
 |                             |       |    viewport_writer.py         |
 |  Leaflet maps (5 panels)   |       |    viewport_ops.py            |
 |  Three.js scene (panel 4)  |       |    pipeline.py                |
 |  Chart.js (validation)     |       |    tile_renderer.py           |
 |  IndexedDB (vector cache)  |       |    evaluation_engine.py       |
 +-----------------------------+       |    progress_tracker.py        |
                                       +-------------------------------+
                                              |           |
                                    +---------+           +----------+
                                    |                                |
                              +-----v-----+              +-----------v---------+
                              | Filesystem |              | GeoTessera API      |
                              |            |              | (embedding tiles)   |
                              | viewports/ |              | dl2.geotessera.org  |
                              | pyramids/  |              +---------------------+
                              | vectors/   |
                              | mosaics/   |
                              | progress/  |
                              +------------+
```

---

## 2. Panel Layout

The viewer uses a 6-panel CSS grid.  Which panels are visible and what they
display depends on the current **mode**.

```
  +------------------+------------------+------------------+
  |    Panel 1       |    Panel 2       |    Panel 3       |
  |   (map-osm)      |   (map-rgb)      |  (map-embedding) |
  |   Leaflet OSM    |   Leaflet Sat    |  Leaflet Emb     |
  +------------------+------------------+------------------+
  |    Panel 4       |    Panel 5       |    Panel 6       |
  |   (map-umap)     |   (map-heatmap)  | (map-embedding2) |
  |   Three.js PCA/  |   Leaflet        |  Leaflet / HTML  |
  |   UMAP scatter   |   Heatmap/Seg    |  Labels/Controls |
  +------------------+------------------+------------------+
```

### 2.1 Panel Modes

There are four modes, set via `window.setPanelLayout(mode)`:

| Mode | Panel 1 | Panel 2 | Panel 3 | Panel 4 | Panel 5 | Panel 6 |
|---|---|---|---|---|---|---|
| `explore` | OSM | Satellite | Embeddings | PCA/UMAP | Change Heatmap + Seg overlay | Embeddings (year 2) |
| `change-detection` | OSM | Satellite | Embeddings | Change Distribution | Change Heatmap | Embeddings (year 2) |
| `labelling` | OSM | Satellite | Embeddings | PCA/UMAP | Classification results | Auto-label / Manual Label |
| `validation` | Classes | Evaluation year | Embeddings | Performance chart | Confusion Matrix | Controls |

Mode is stored in `localStorage` and restored on reload via `restorePanelMode()`.

### 2.2 Panel 5 Layer Rules

Panel 5 (`maps.heatmap`) has three optional layers whose visibility is governed
by a declarative rules table in `maps.js`:

```javascript
const HEATMAP_LAYER_RULES = {
    'explore':          { satellite: false, heatmapCanvas: true,  segOverlay: true  },
    'change-detection': { satellite: false, heatmapCanvas: true,  segOverlay: false },
    'labelling':        { satellite: true,  heatmapCanvas: false, segOverlay: true  },
    'validation':       { satellite: false, heatmapCanvas: false, segOverlay: false },
};
```

The function `applyHeatmapLayerRule(layer, shouldShow)` adds or removes a
Leaflet layer from `maps.heatmap` based on these rules.

---

## 3. Module Dependency Graph

ES modules (ECMAScript modules) are the browser's native module system, loaded
with `<script type="module">` instead of plain `<script>`.  Each module has its
own scope — variables declared in one file are not visible in another unless
explicitly exported or attached to `window`.

All 8 modules are loaded as ES modules in `viewer.html`.  They communicate
through `window.*` properties bridged via `Object.defineProperty`.  There is no
import/export between modules; `dimreduction.js` is the only module that uses
`import` (for Three.js and OrbitControls).

```
  app.js
    |
    +--- maps.js        (map creation, sync, click routing, panel layout)
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

**Load order in viewer.html:**

1. `app.js` -- init, dependency system, progress tracking
2. `maps.js` -- map creation, sync, click handlers
3. `vectors.js` -- vector download, search, explorer viz
4. `labels.js` -- manual labels, saved labels, polygon drawing
5. `segmentation.js` -- k-means clustering
6. `dimreduction.js` -- PCA/UMAP, heatmap, Three.js (ES module import)
7. `evaluation.js` -- validation panel
8. `schema.js` -- schema dropdown, tree browser

---

## 4. Data Flow

```
  Viewport creation                    Viewer usage
  ─────────────────                    ────────────

  User draws bounds on map
        │
        ▼
  POST /api/viewports/create
  (api/views/viewports.py)
        │
        ▼
  Pipeline (background thread)         User opens viewer.html?viewport=X
  (lib/pipeline.py)                        │
    ├─ download embedding tiles            ▼
    │   via GeoTessera library         Tile server serves /tiles/{vp}/{year}/{z}/{x}/{y}.png
    ├─ create PNG pyramids ──────────► (api/views/tiles.py → lib/tile_renderer.py)
    │   (create_pyramids.py)
    ├─ extract uint8 vectors ────────► /api/vector-data/{vp}/{year}/*.npy.gz
    │   (lib/viewport_ops.py)              │
    └─ write metadata                      ▼
        (lib/viewport_writer.py)      vectors.js downloads to IndexedDB
                                           │
                                      ┌────┴────┐
                                      │         │
                                      ▼         ▼
                              Similarity   PCA/UMAP in
                              search       dimreduction.js
                              (client-     (client-side)
                               side L2)
                                      │
                                      ▼
                              Label creation (explore/labelling mode)
                              (labels.js)
                                      │
                                      ▼
                              Export as JSON/GeoJSON/Shapefile
                              (labels.js → exportManualLabelsShapefile)
                                      │
                                      ▼
                              Upload ground-truth shapefile
                              for evaluation (validation mode)
                              (api/views/evaluation.py → upload_shapefile)
                                      │
                                      ▼
                              POST /api/evaluation/run
                              (api/views/evaluation.py → lib/evaluation_engine.py)
                              (streaming NDJSON results)
```

---

## 5. State Management

### 5.1 Window Property Bridges

Each module declares private state variables and exposes them on `window` via
`Object.defineProperty` with getter/setter pairs.  This allows cross-module
communication without ES imports:

```javascript
// In vectors.js
let localVectors = null;
Object.defineProperty(window, 'localVectors', {
    get: () => localVectors,
    set: (v) => { localVectors = v; },
    configurable: true,
});
```

Other modules read/write `window.localVectors` as if it were a global, but the
actual storage is module-private.

**Key bridged properties by module:**

| Module | Properties on `window` |
|---|---|
| `app.js` | `maps`, `currentViewportName`, `currentEmbeddingYear`, `viewportStatus`, `currentPanelMode`, `TILE_SERVER`, `labels`, `markers`, `isLoggedIn`, `definedLabels`, `embeddingLabels`, `labelColors` |
| `maps.js` | `viewportBounds`, `satelliteSources`, `currentSatelliteSource`, `TRIANGLE_ICON`, `HEATMAP_LAYER_RULES`, `persistentLabelMarkers` |
| `vectors.js` | `localVectors`, `explorerResults` |
| `labels.js` | `manualLabels`, `currentManualLabel`, `savedLabels`, `currentSearchCache`, `manualClassOverlays`, `_classMatchCache`, `isPolygonDrawing`, `labelMode` |
| `segmentation.js` | `segAssignments`, `segOverlay`, `segLabels`, `segRunning`, `segVectors`, `segK`, `SEG_PALETTE` |
| `dimreduction.js` | `currentEmbeddingYear2`, `umapCanvasLayer`, `currentDimReduction`, `heatmapCanvasLayer`, `_dimReductionCache` |
| `evaluation.js` | `lastEvalData` |
| `schema.js` | `activeSchema`, `activeSchemaMode` |

### 5.2 Dependency System

`app.js` contains a declarative dependency system that manages the viewer's
initialisation sequence.  It consists of:

1. **`viewportStatus`** -- an object tracking server readiness flags
   (`has_pyramids`, `has_vectors`, `has_umap`, `years_available`) and client-side
   flags (`vectors_downloaded`, `pca_loaded`, `umap_loaded`).

2. **`dependencyRegistry`** -- an array of dependency entries, each with:
   - `id`: string identifier
   - `test(status)`: predicate function
   - `onReady(status)`: callback when test transitions false-to-true
   - `onNotReady()`: callback when test transitions true-to-false (optional)
   - `satisfied`: boolean tracking current state

3. **`evaluateDependencies()`** -- iterates the registry and fires callbacks on
   state transitions.  Called after each poll response and after manual state
   changes.

4. **`pollViewportStatus()`** -- polls `GET /api/viewports/{name}/is-ready`
   every 2s (server busy) or 30s (server idle), updates `viewportStatus`, and
   calls `evaluateDependencies()`.

**Registered dependencies:**

| ID | Triggers when | Action |
|---|---|---|
| `panel3-tiles` | pyramids ready | Create/refresh embedding tile layer |
| `panel6-tiles` | pyramids ready | Create/refresh panel 6 tile layer |
| `year-selectors` | >1 year available | Populate year dropdowns |
| `year-selector-2-visibility` | change-detection mode + >1 year | Show panel 6 year selector |
| `vectors-download` | vectors ready on server, not yet downloaded | Download vectors to IndexedDB |
| `label-controls` | vectors downloaded | Enable similarity slider, seg controls |
| `panel4-pca` | vectors downloaded, PCA not loaded | Compute PCA, render Three.js |
| `panel4-umap` | vectors downloaded, UMAP not loaded | Compute UMAP in Web Worker |
| `panel5-heatmap` | vectors ready + 2+ years | Compute change heatmap |

### 5.3 Persistence

| What | Where | Key pattern |
|---|---|---|
| Panel mode | `localStorage` | `panelMode` |
| Label sub-mode | `localStorage` | `labelMode` |
| Schema mode | `localStorage` | `schemaMode` |
| Current manual label | `localStorage` | `currentManualLabel_{viewport}` |
| Manual labels | `localStorage` | `manualLabels_{viewport}` |
| Saved (auto) labels | `localStorage` | `tee_labels_{viewport}` |
| Vector data | `IndexedDB` | `tee_vector_cache` store, key `{viewport}/{year}` |
| Dim reduction cache | In-memory object | `_dimReductionCache[viewport/year/method]` |

---

## 6. File/Directory Structure

```
TEE/
├── public/
│   ├── js/
│   │   ├── app.js              Application init, dependency system (47K)
│   │   ├── maps.js             Map creation, sync, click handlers (39K)
│   │   ├── vectors.js          Vector download, search, canvas layers (36K)
│   │   ├── labels.js           Manual + saved labels, polygon, export (108K)
│   │   ├── segmentation.js     K-means clustering, seg overlay (23K)
│   │   ├── dimreduction.js     PCA, UMAP, heatmap, Three.js (52K)
│   │   ├── evaluation.js       Validation pipeline UI (37K)
│   │   └── schema.js           Schema browser, label selection (13K)
│   ├── viewer.html             6-panel viewer layout
│   ├── viewport_selector.html  Viewport list / creation page
│   └── login.html              Authentication page
│
├── api/
│   ├── urls.py                 URL routing (all /api/* endpoints)
│   ├── auth_views.py           Login/logout/status
│   ├── helpers.py              Shared utilities (quota, ownership)
│   ├── tasks.py                Background task management
│   ├── middleware.py            Tile shortcircuit, demo mode
│   └── views/
│       ├── viewports.py        Viewport CRUD
│       ├── pipeline.py         Pipeline progress/cancel
│       ├── tiles.py            Tile server
│       ├── vector_data.py      Vector file serving
│       ├── evaluation.py       ML evaluation endpoints
│       └── config.py           Static files, health, config
│
├── lib/
│   ├── config.py               Filesystem path constants
│   ├── viewport_utils.py       Viewport reading/validation
│   ├── viewport_writer.py      Viewport creation/symlink
│   ├── viewport_ops.py         Readiness checks, data deletion
│   ├── pipeline.py             Two-stage pipeline runner
│   ├── progress_tracker.py     JSON progress file writer
│   ├── tile_renderer.py        GeoTIFF/PNG tile rendering
│   └── evaluation_engine.py    ML classifiers, learning curves
│
├── tee_project/
│   └── settings/
│       └── base.py             Django settings
│
├── process_viewport.py         Pipeline subprocess (fetch + pyramids + vectors)
├── create_pyramids.py          Satellite pyramid builder
├── Dockerfile                  Multi-stage Docker build
└── docs/
    ├── index.md                This documentation home
    ├── architecture.md         This file
    ├── frontend_api.md         JavaScript API reference
    ├── backend_api.md          Python API reference
    └── extension_guide.md      How to extend TEE
```

---

## 7. Map Synchronization

All five geographic Leaflet maps (`osm`, `embedding`, `rgb`, `heatmap`,
`embedding2`) are synchronized via `syncMaps()` in `maps.js`.  When any panel
fires a `move` or `zoom` event, all other panels are updated to the same center
and zoom level:

```javascript
function syncMaps() {
    let syncing = false;
    const geoPanels = ['osm', 'embedding', 'rgb', 'heatmap', 'embedding2'];

    function doSync(sourcePanel) {
        if (syncing) return;
        syncing = true;
        const center = window.maps[sourcePanel].getCenter();
        const zoom = window.maps[sourcePanel].getZoom();
        geoPanels.forEach(panel => {
            if (panel !== sourcePanel) {
                window.maps[panel].setView(center, zoom, {animate: false});
            }
        });
        syncing = false;
    }

    geoPanels.forEach(panel => {
        window.maps[panel].on('move zoom', () => doSync(panel));
    });
}
```

Panel 4 (Three.js) is not synchronized with geographic maps but supports
bidirectional click interaction: clicking a point in the 3D scatter plot triggers
`handleUnifiedClick()` on all map panels, and clicking a map panel highlights
the nearest point in the scatter plot.

---

## 8. Click Routing

All map panels share unified click and double-click handlers installed in
`createMaps()`:

- **Single click** (250ms delay to distinguish from double-click):
  - Default: `handleUnifiedClick(lat, lon)` -- places triangle markers on all panels, highlights nearest UMAP point
  - Ctrl/Cmd+click in manual label mode: `handleManualPinDrop(lat, lon)` -- drops a colored pin

- **Double click**:
  - Default: `handleSimilaritySearch(lat, lon)` -- runs client-side similarity search, shows results on Panel 2 + Panel 4
  - Ctrl/Cmd+double-click in manual label mode: `startPolygonDrawing(latlng)` -- begins Leaflet.Draw polygon

The 250ms delay is necessary because Leaflet fires `click` before `dblclick`.
The timeout is cancelled if a second click arrives within the window.

---

## 9. Third-Party Dependencies

All frontend dependencies are loaded from CDNs — there is no build step, no
`node_modules`, no bundler.

| Library | Version | Source | Used for |
|---|---|---|---|
| Leaflet | 1.9.4 | unpkg CDN (`<script>`) | All geographic map panels (1-3, 5-6) |
| Leaflet.Draw | 1.0.4 | unpkg CDN (`<script>`) | Polygon drawing on Panel 2 |
| Three.js | 0.163.0 | jsdelivr CDN (ES module via `importmap`) | 3D scatter plot on Panel 4 |
| OrbitControls | 0.163.0 | jsdelivr CDN (ES module via `importmap`) | Pan/rotate/zoom on Panel 4 |
| Chart.js | latest | jsdelivr CDN (`<script>`) | Learning curve charts in validation |
| shp-write | 0.3.2 | unpkg CDN (loaded dynamically on export) | ESRI Shapefile export |

The Three.js imports use an `importmap` in `viewer.html`:

```html
<script type="importmap">
{
    "imports": {
        "three": "https://cdn.jsdelivr.net/npm/three@0.163.0/build/three.module.js",
        "three/addons/controls/OrbitControls.js": "https://cdn.jsdelivr.net/npm/three@0.163.0/examples/jsm/controls/OrbitControls.js"
    }
}
</script>
```

This allows `dimreduction.js` to use bare `import * as THREE from 'three'`.

---

## 10. HTML Panel Structure

The 6-panel grid lives inside `#map-container` in `viewer.html`.  Each panel
is a `<div class="panel">` containing a header and a content area.  Key element
IDs an agent needs to know:

```
#map-container (CSS grid, 3x2)
  ├── Panel 1: .panel
  │     ├── #panel1-title (span)
  │     ├── #map-osm (Leaflet map div)
  │     └── #val-class-table-panel (validation mode only)
  │
  ├── Panel 2: .panel
  │     ├── #panel2-title (span)
  │     ├── #satellite-source-selector (dropdown: esri/google)
  │     └── #map-rgb (Leaflet map div)
  │
  ├── Panel 3: .panel
  │     ├── #panel3-title (span)
  │     ├── #embedding-year-selector (year dropdown)
  │     └── #map-embedding (Leaflet map div)
  │
  ├── Panel 4: .panel
  │     ├── #panel4-title (span)
  │     ├── #dim-reduction-selector (PCA/UMAP dropdown)
  │     ├── #map-umap (Three.js container div)
  │     └── #change-stats-panel (change-detection mode only)
  │
  ├── Panel 5: .panel
  │     ├── #panel5-title (span)
  │     ├── #map-heatmap (Leaflet map div)
  │     ├── #heatmap-waiting-message (shown when vectors not ready)
  │     ├── #heatmap-same-year-message (shown when years match)
  │     └── #val-cm-panel (confusion matrix, validation mode only)
  │
  └── Panel 6: .panel
        ├── #panel6-header-text (span, note: not #panel6-title)
        ├── #embedding-year-selector-2 (year dropdown, change-detection)
        ├── #map-embedding2 (Leaflet map div)
        ├── #panel6-autolabel-view (auto-label sub-view)
        │     ├── #seg-controls (k-means k=, -/+, Go, Clear)
        │     ├── #panel6-seg-list (cluster list)
        │     └── #panel6-promote-all-btn
        ├── #panel6-manual-view (manual label sub-view)
        │     ├── #manual-label-name (input)
        │     ├── #manual-label-color (color picker)
        │     └── #manual-labels-list (label list)
        └── #val-controls-panel (validation mode only)
              ├── #val-dropzone (shapefile upload)
              ├── #val-field-select (field selector)
              ├── classifier checkboxes (.val-clf-header)
              └── #val-run-btn / #val-cancel-btn
```

**Important:** Panel 6 header uses `#panel6-header-text`, not `#panel6-title`.
All other panels use `#panelN-title`.

---

## 11. Geotransform (Lat/Lon ↔ Pixel Conversion)

The geotransform is a 6-parameter affine transformation stored in
`localVectors.metadata.geotransform`:

```
{ a: pixelWidth,   b: 0,   c: originLon,
  d: 0,            e: pixelHeight (negative),  f: originLat }
```

**Pixel → Geographic:**
```javascript
lon = c + px * a     // px = pixel column (x)
lat = f + py * e     // py = pixel row (y), e is negative so lat decreases
```

**Geographic → Pixel:**
```javascript
px = Math.round((lon - c) / a)
py = Math.round((lat - f) / e)
```

**Embedding extraction** (in `vectors.js` `localExtract`):
```javascript
const gt = localVectors.metadata.geotransform;
const px = Math.round((lon - gt.c) / gt.a);
const py = Math.round((lat - gt.f) / gt.e);
const idx = gridLookupIndex(localVectors.gridLookup, px, py);
if (idx >= 0) {
    return localVectors.values.subarray(idx * 128, (idx + 1) * 128);
}
```

Note: `a` is typically ~0.00009 (about 10m in degrees at UK latitudes).
`e` is negative (latitude decreases going down the raster).

---

## 12. Terminology: Embeddings vs Vectors

The codebase enforces a consistent naming convention:

| Term | Format | Meaning |
|---|---|---|
| **Embeddings** | uint8 quantized | Raw storage/transfer format from the Tessera model |
| **Vectors** | float32 dequantized | What all computation uses (similarity search, PCA, k-means, etc.) |

**Dequantization** converts embeddings to vectors:
```
vector[i] = embedding[i] / 255.0 * (dim_max[i] - dim_min[i]) + dim_min[i]
```

In code: `localVectors.values` is the Float32Array of dequantized vectors.
The file on disk `all_embeddings_uint8.npy.gz` contains the uint8 embeddings.

---

## 13. Vector Data Pipeline

When vectors are downloaded to the browser, they go through these steps:

1. **Server stores** uint8 quantized embeddings + quantization params + pixel
   coordinates in `viewports/{name}/vectors/{year}/`:
   - `all_embeddings_uint8.npy.gz` — shape (N, 128), dtype uint8
   - `quantization.json` — `{dim_min: float32[128], dim_max: float32[128]}`
   - `pixel_coords.npy.gz` — shape (N, 2), dtype int32 (px, py pairs)
   - `metadata.json` — geotransform, mosaic dimensions

2. **`vectors.js` downloads** all four files via `/api/vector-data/{viewport}/{year}/`

3. **Client-side dequantization:**
   ```
   float32_value = uint8_value / 255.0 * (dim_max - dim_min) + dim_min
   ```

4. **Stored in IndexedDB** cache (`tee_vector_cache` store) to avoid re-downloading

5. **Set as `window.localVectors`** — triggers dependency cascade
   (PCA computation, label controls enable, etc.)

---

## 14. Testing

TEE has static analysis tests in `validation/` that verify the frontend HTML and
JS haven't regressed during refactoring.  **Run after any frontend change:**

```bash
cd /path/to/TEE && venv/bin/pytest validation/ -v
```

### Test files

| File | What it checks |
|---|---|
| `test_viewer_html.py` | HTML structure (panel IDs, no duplicate IDs), JS syntax (all script blocks parse), feature presence (polygon drawing, schema system, keyboard shortcuts) |
| `test_refactoring_guards.py` | Backend: all view functions exist, URL routing complete, lib modules export expected functions, `window.*` exports present in JS files |

### Key test classes in `test_viewer_html.py`

- `TestExploreRename` — no residual "simple" mode references
- `TestManualLabelMode` — manual label UI elements exist
- `TestClassificationOverlay` — classify button and overlay elements
- `TestSchemaSystem` — schema functions present in JS
- `TestPolygonDrawing` — Leaflet.Draw CDN, polygon state vars, click handlers
- `TestJSSyntax` — all `<script>` blocks parse without syntax errors
- `TestHTMLStructure` — starts with DOCTYPE, no duplicate IDs, panel structure

### When tests fail

If a test fails after a code change, it usually means:
- A `window.*` function was renamed/removed without updating the test
- An HTML element ID was changed
- A JS syntax error was introduced

Fix the code or update the test assertion — never skip tests.

---

## 15. Deployment

### Local development

```bash
python3 manage.py runserver 8001   # Django dev server
```

### Docker build and push (for production)

```bash
docker buildx build --platform linux/amd64 \
    --build-arg GIT_VERSION="$(git describe --tags --always)" \
    -t sk818/tee:stable --push .
```

### Production server (michael)

```bash
ssh michael
sudo ./manage.sh   # option 5: pull sk818/tee:stable, restart, health-check
```

Docker volumes on production: `-v /data:/data -v /data/viewports:/app/viewports`

The Dockerfile runs: migrate → collectstatic → waitress on port 8001.
Waitress is configured with `--send-bytes=1` to support streaming responses
(evaluation NDJSON).
