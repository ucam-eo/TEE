# TEE Frontend JavaScript API Reference

Complete reference for all 8 ES modules in `public/js/`.  Every function and
property exposed on `window.*` is documented here.

---

## Table of Contents

1. [app.js](#1-appjs)
2. [maps.js](#2-mapsjs)
3. [vectors.js](#3-vectorsjs)
4. [labels.js](#4-labelsjs)
5. [segmentation.js](#5-segmentationjs)
6. [dimreduction.js](#6-dimreductionjs)
7. [evaluation.js](#7-evaluationjs)
8. [schema.js](#8-schemajs)
9. [Key Data Structures](#9-key-data-structures)
10. [Frontend → Backend API Calls](#10-frontend--backend-api-calls)
11. [Cross-Module Communication Patterns](#11-cross-module-communication-patterns)

---

## 1. app.js

**Purpose:** Application initialisation, declarative dependency system, embedding
label management, progress tracking, and the `window.onload` entry point.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.currentViewportName` | `string` | Active viewport name (e.g. `"cambridge"`) |
| `window.currentEmbeddingYear` | `string` | Year for Panel 3 embeddings (e.g. `"2025"`) |
| `window.viewportStatus` | `object` | Server + client readiness flags (see [data structures](#91-viewportstatus)) |
| `window.currentPanelMode` | `string` | `"explore"` / `"change-detection"` / `"labelling"` / `"validation"` |
| `window.TILE_SERVER` | `string` | Tile server base URL (default: `window.location.origin`) |
| `window.heatmapSatelliteLayer` | `L.TileLayer \| null` | Satellite base layer on Panel 5 |
| `window.isLoggedIn` | `boolean` | Whether user is authenticated |

### Functions

#### `window.evaluateDependencies()`
Re-evaluate all dependency predicates, firing `onReady`/`onNotReady` callbacks
on state transitions.  Safe to call multiple times; reentrant calls are queued.

#### `window.startPoller()`
Begin polling `GET /api/viewports/{name}/is-ready` and updating `viewportStatus`.

#### `window.stopPoller()`
Stop the viewport status poller.

#### `window.refreshEmbeddingTileLayer(panelName, year)`
- **panelName** `string` -- `"embedding"` (Panel 3) or `"embedding2"` (Panel 6)
- **year** `string` -- year to display (e.g. `"2024"`)

Replace the embedding tile layer on the specified panel with tiles for the given
year.  Called when pyramids become ready or when the user switches years.

#### `window.makeDraggable(panel, handle)`
- **panel** `HTMLElement` -- element to make draggable
- **handle** `HTMLElement` -- drag handle element

Adds mousedown/mousemove/mouseup listeners for drag behaviour.

#### `window.doLogout()`
POST to `/api/auth/logout` and redirect to login page.

#### `window.showProgressModal(title)` / `window.hideProgressModal()`
Show/hide the full-screen progress overlay.

#### `window.pollOperationProgress(operationId, title)`
- **operationId** `string` -- e.g. `"cambridge_pipeline"`
- **title** `string` -- display title

Poll `/api/operations/progress/{id}` every 500ms and update the progress bar.

#### `window.checkAndPollOperations(viewportId)`
- **viewportId** `string`

Check for ongoing pipeline operations and start polling if found.

---

## 2. maps.js

**Purpose:** Create and synchronise all Leaflet maps, handle clicks, manage
panel layout and mode switching, provide satellite source selection.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.viewportBounds` | `[[latMin, lonMin], [latMax, lonMax]]` | Viewport geographic bounds |
| `window.satelliteSources` | `{esri: {...}, google: {...}}` | Satellite tile URL templates |
| `window.currentSatelliteSource` | `string` | `"esri"` or `"google"` |
| `window.TRIANGLE_ICON` | `L.divIcon` | Yellow triangle marker icon |
| `window.HEATMAP_LAYER_RULES` | `object` | Layer visibility rules per mode |
| `window.persistentLabelMarkers` | `array` | Array of persistent label markers |

### Functions

#### `window.updateMapViewport()` -> `Promise<void>`
Fetch current viewport from `/api/viewports/current`, update `center`, `zoom`,
and `viewportBounds`.

#### `window.createMaps()` -> `L.TileLayer`
Create all six Leaflet maps, install click/dblclick/mousemove handlers,
synchronise maps, initialise polygon drawing.  Returns the Panel 3 embedding
tile layer.

#### `window.switchEmbeddingYear(year)` -> `Promise<void>`
- **year** `number`

Switch Panel 3 to a different year.  Re-downloads vectors, refreshes labels,
re-runs active search, updates heatmap.

#### `window.refreshLabelsForYear(year)` -> `Promise<void>`
- **year** `number`

Recompute all saved label pixel sets using the new year's embeddings.

#### `window.switchEmbeddingYear2(year)`
- **year** `string`

Switch Panel 6 to a different year and update the heatmap.

#### `window.syncMaps()`
Install move/zoom listeners to keep all 5 geographic panels in sync.

#### `window.makeColoredTriangleIcon(fillColor)` -> `L.divIcon`
- **fillColor** `string` -- hex color
- **Returns:** triangle icon with the given fill color

#### `window.setCrossPanelMarker(mapKey, lat, lon)`
Place a yellow triangle marker on a single map panel.

#### `window.clearCrossPanelMarkers()`
Remove all cross-panel triangle markers.

#### `window.handleUnifiedClick(lat, lon)`
Place triangle markers on all panels and highlight nearest UMAP point.

#### `window.highlightLabelAtPixel(lat, lon)`
In labelling mode, highlight the matching saved label or seg cluster row.

#### `window.handleSimilaritySearch(lat, lon)` -> `Promise<void>`
Run client-side similarity search at the given location.  Calls
`explorerClick()` and highlights results on Panel 2 + Panel 4.

#### `window.handleManualSimilaritySearch(lat, lon)` -> `Promise<void>`
In manual label mode, run similarity search and add result as a manual label.

#### `window.handleManualPinDrop(lat, lon)`
In manual label mode, drop a colored point label at the given location.

#### `window.calculatePixelBounds(lat, lon)` -> `[[sw_lat, sw_lon], [ne_lat, ne_lon]]`
Calculate geographic bounds of a 10m x 10m pixel centered at the given lat/lon.

#### `window.applyHeatmapLayerRule(layer, shouldShow)`
Add or remove a Leaflet layer from `maps.heatmap` based on the boolean flag.

#### `window.setPanelLayout(mode)`
- **mode** `string` -- `"explore"`, `"change-detection"`, `"labelling"`, or `"validation"`

Switch the viewer to the given mode.  Updates CSS classes, panel titles, layer
visibility, and fires `evaluateDependencies()`.

#### `window.restorePanelMode()`
Restore saved panel mode from localStorage.

---

## 3. vectors.js

**Purpose:** Download, cache, and search 128-dim embedding vectors client-side.
Provides the `DirectCanvasLayer` class for rendering pixel-level overlays.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.localVectors` | `object \| null` | Currently loaded vector data (see [data structures](#92-localvectors)) |
| `window.explorerResults` | `object \| null` | Cached similarity search results |

### Functions

#### `window.downloadVectorData(viewport, year)` -> `Promise<object>`
- **viewport** `string`
- **year** `string|number`
- **Returns:** `localVectors` object

Download embedding vectors (uint8 quantized), pixel coordinates, and metadata.
Uses IndexedDB cache.  Shows progress overlay during download.

#### `window.localExtract(lat, lon)` -> `Float32Array | null`
- **lat** `number`, **lon** `number`
- **Returns:** 128-dim embedding at the nearest pixel, or `null`

Extract a single embedding from loaded vector data using affine geotransform.
Tries exact pixel match first, then 8-neighbourhood.

#### `window.localSearchSimilar(embedding, threshold)` -> `Array<{lat, lon, distance}>`
- **embedding** `Float32Array` -- 128-dim query vector
- **threshold** `number` -- L2 distance threshold
- **Returns:** array of matching pixels

Brute-force L2 search over all loaded vectors.  Uses loop unrolling (4 dims at
a time) for performance.

#### `window.localSearchSimilarMulti(embeddings, threshold)` -> `Array<{lat, lon, distance}>`
- **embeddings** `Float32Array[]` -- array of 128-dim query vectors
- **threshold** `number`
- **Returns:** array of pixels matching ANY query (union search)

#### `window.searchMultiInVectorData(data, searches)` -> `number`
- **data** `object` -- vector data object (like `localVectors`)
- **searches** `Array<{embedding, threshSq}>` -- search queries
- **Returns:** count of matching pixels

Single-pass count of pixels matching any search (used for timelines).

#### `window.loadVectorDataOnly(viewport, year)` -> `Promise<object>`
Load vector data for a given viewport/year without setting `localVectors`.
Preserves the current `localVectors` after download.

#### `window.extractFromData(data, lat, lon)` -> `Float32Array | null`
Like `localExtract` but operates on an arbitrary vector data object.

#### `window.clearExplorerResults()`
Clear explorer visualization from Panel 2, reset UMAP highlighting.

#### `window.explorerClick(lat, lon)` -> `Promise<void>`
Main explorer entry point.  Extracts embedding, runs similarity search with
wide cache threshold (35.0), visualises results, caches for threshold slider.

#### `window.updateExplorerVisualization()`
Re-filter cached explorer results by current threshold and update the canvas
overlay + Panel 4 highlighting.

#### `window.DirectCanvasLayer`
Leaflet canvas layer class for rendering pixel-level overlays.

```javascript
const layer = new DirectCanvasLayer(matches, map, '#3cb44b');
// matches: [{lat, lon, distance}, ...]
// map: L.Map instance
// color: hex string (optional, default yellow)

layer.updateMatches(newMatches);  // Update with new match array
```

#### `window.calculateAverageEmbedding(embeddings)` -> `number[] | null`
- **embeddings** `number[][]` -- array of 128-dim vectors
- **Returns:** element-wise mean

#### `window.buildGridLookup(coordsData, numVectors)` -> `{minX, minY, w, h}`
Build O(1) grid lookup structure from pixel coordinates.

#### `window.gridLookupIndex(grid, px, py)` -> `number`
- **Returns:** vector index at pixel (px, py), or -1 if out of bounds

#### `window.VectorCache`
IndexedDB cache manager for vector data.

```javascript
await VectorCache.get(viewport, year);      // -> cached data or null
await VectorCache.put(viewport, year, data); // store data
await VectorCache.delete(viewport, year);    // remove entry
```

---

## 4. labels.js

**Purpose:** Manual labelling (point, polygon, similarity), saved label
persistence, overlay rendering, import/export, polygon drawing via Leaflet.Draw.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.manualLabels` | `array` | Array of manual label entries (see [data structures](#93-manuallabel-entry)) |
| `window.currentManualLabel` | `{name, color, code} \| null` | Currently active manual label |
| `window.savedLabels` | `array` | Array of saved (auto) label entries |
| `window.currentSearchCache` | `object \| null` | Cached search results for label saving |
| `window.manualClassOverlays` | `{className: {layerGroup, layer}}` | Per-class overlay layers |
| `window._classMatchCache` | `{className: [{lat, lon}]}` | Cached match coords for Panel 4 |
| `window.isPolygonDrawing` | `boolean` | Whether polygon drawing is active |
| `window.labelMode` | `string` | `"autolabel"` or `"manual"` |

### Functions -- Manual Labels

#### `window.setLabelMode(mode)`
- **mode** `string` -- `"autolabel"` or `"manual"`

Switch between auto-label and manual label sub-modes in labelling mode.

#### `window.setCurrentManualLabel()`
Read name/color from the label input fields and set `currentManualLabel`.

#### `window.updateManualLabelColor(color)` / `window.updateActiveLabelColor(color)`
- **color** `string` -- hex color

Update the active label's color and sync to all labels with the same name.

#### `window.addManualLabel(entry)`
- **entry** `object` -- manual label entry (id is auto-assigned)

Add a new manual label, save to localStorage, re-render list.

#### `window.removeManualLabel(id)` / `window.removeManualClass(className)`
Remove a single label by ID or all labels with a given class name.

#### `window.getClassLabels(className)` -> `array`
Return all manual labels with the given name.

#### `window.getClassThreshold(className)` -> `number`
Return the similarity threshold for a class (from the first member with threshold > 0).

#### `window.rebuildClassOverlay(className)`
Recompute and redraw the RGB overlay for a label class (union of similarity
search + polygon rasterization + point markers).

#### `window.rebuildManualOverlays()`
Clear and rebuild overlays for all label classes.

#### `window.toggleClassExpand(className)` / `window.toggleClassVisibility(className)`
Expand/collapse or show/hide a label class in the list.

#### `window.toggleAllManualLabels()`
Toggle visibility of all manual labels.

#### `window.updateManualClassThreshold(className, newThreshold)`
- **className** `string`
- **newThreshold** `number`

Update threshold for all labels in a class.  Debounced via `requestAnimationFrame`.

#### `window.renderManualLabelsList()`
Rebuild the manual labels list HTML in Panel 6.

#### `window.triggerManualClassification()` / `window.renderManualClassification()`
Render a nearest-centroid classification overlay on Panel 5 (heatmap map)
using all visible manual label embeddings.

#### `window.activateManualClass(className)`
Set a label class as the active manual label.

### Functions -- Polygon Drawing

#### `window.initPolygonDrawing()`
Initialise Leaflet.Draw on `maps.rgb`.

#### `window.startPolygonDrawing(latlng)`
- **latlng** `L.LatLng` -- starting point (first vertex)

Begin polygon drawing mode.

#### `window.cancelPolygonDrawing()`
Cancel active polygon drawing (Escape key handler).

#### `window.handlePolygonComplete(latLngs)`
Process a completed polygon: rasterize interior pixels, compute embedding
(mean or union mode), add as manual label.

#### `window.pointInPolygon(px, py, polygon)` -> `boolean`
Ray-casting point-in-polygon test.

#### `window.rasterizePolygon(pixelVertices)` -> `Array<{lat, lon, px, py, vectorIndex}>`
Rasterize polygon interior into pixel coordinates using the vector grid.

### Functions -- Export/Import

#### `window.exportManualLabels()`
Show export dropdown menu (JSON, GeoJSON, Shapefile, Map JPG).

#### `window.doExportManualLabels(format)`
- **format** `string` -- `"json"`, `"geojson"`, or `"shapefile"`

#### `window.importManualLabels(file)`
Import labels from JSON, GeoJSON, or Shapefile ZIP.

#### `window.downloadFile(content, filename, mimeType)`
Generic file download helper.

### Functions -- Saved Labels (Auto-label)

#### `window.persistLabels()`
Save `savedLabels` metadata to localStorage (without pixel arrays).

#### `window.loadSavedLabels()` -> `Promise<void>`
Load saved labels from localStorage, then recompute pixel coverage from vectors.

#### `window.recomputeLabelPixels()` -> `Promise<void>`
Recompute pixel coverage for all saved labels from the current vector data.

#### `window.renderLabelsInto(container)`
Render saved labels list HTML into a container element.

#### `window.updateLabelsUI()`
Update both floating popup and Panel 6 label lists.

#### `window.confirmSaveLabel()`
Save the current explorer search as a persistent label.

#### `window.closeSaveLabelModal()`
Close the save label dialog.

#### `window.deleteLabelGroup(name)`
Delete all saved labels with a given name.

#### `window.toggleAllOverlays()`
Toggle visibility of all saved labels.

#### `window.exportSavedLabels()` / `window.exportSavedLabelsGeoJSON()`
Export saved labels as JSON or GeoJSON.

#### `window.importSavedLabels(file)` -> `Promise<void>`
Import saved labels from a JSON file.

#### `window.updateOverlay()`
Rebuild the `PersistentLabelOverlay` on `maps.rgb` from all visible saved labels.

#### `window.exportMapAsJPG()`
Export the current map view as a JPG image.

#### `window.updateThresholdDisplay()`
Update the threshold display and re-filter explorer results.

### Functions -- Timeline

#### `window.showLabelTimeline(labelName)` -> `Promise<void>`
Show a modal with year-by-year pixel count for a saved label.

#### `window.showManualLabelTimeline(className)` -> `Promise<void>`
Show timeline for a manual label class.

#### `window.getAvailableYears()` -> `number[]`
Return sorted list of years from the year selector dropdown.

---

## 5. segmentation.js

**Purpose:** Client-side K-means clustering via inline Web Worker, cluster list
UI, segmentation overlay on Panel 5.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.segAssignments` | `Int32Array \| null` | Per-pixel cluster assignments (length N) |
| `window.segOverlay` | `L.ImageOverlay \| null` | Segmentation overlay on `maps.heatmap` |
| `window.segLabels` | `array` | Cluster metadata: `[{id, color, hex, name, count, embedding, sourcePixel, threshold, centroid}]` |
| `window.segRunning` | `boolean` | Whether segmentation is in progress |
| `window.segVectors` | `object \| null` | Vector data used for current segmentation |
| `window.segK` | `number` | Current k value (default 5) |
| `window.SEG_PALETTE` | `string[]` | 20 maximally-distinct colors |

### Functions

#### `window.runKMeans(k)` -> `Promise<void>`
- **k** `number` -- number of clusters (2-20)

Run K-means with K-means++ initialisation in a Web Worker.  Subsamples if
N > max(5000, k*500).  Updates `segAssignments`, `segLabels`, overlay.

#### `window.showSegmentationOverlay()`
Render the segmentation overlay on Panel 5 from `segAssignments` and `segLabels`.

#### `window.clearSegmentation()`
Remove segmentation overlay and reset all seg state.

#### `window.renderSegListInto(container)`
Render cluster list HTML into a container (floating popup or Panel 6).

#### `window.showSegmentationPanel()`
Render cluster list into both the floating popup and Panel 6.

#### `window.saveClusterAsLabel(clusterId)`
Promote a segmentation cluster to a manual label.  Copies pixels, embedding,
and color; rebuilds the overlay.

#### `window.saveAllClustersAsLabels()`
Promote all remaining clusters to manual labels.

#### `window.buildSample(N, size)` -> `Uint32Array | null`
Fisher-Yates partial shuffle for subsampling.

---

## 6. dimreduction.js

**Purpose:** Dimensionality reduction (PCA client-side, UMAP via Web Worker),
Three.js 3D scatter plot (Panel 4), change-detection heatmap (Panel 5).

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.currentEmbeddingYear2` | `string` | Year for Panel 6 (e.g. `"2024"`) |
| `window.umapCanvasLayer` | `UMAPScene \| null` | Active Three.js scene instance |
| `window.currentDimReduction` | `string` | `"pca"` or `"umap"` |
| `window.heatmapCanvasLayer` | `HeatmapCanvasLayer \| null` | Active heatmap layer |
| `window._dimReductionCache` | `object` | Cache of computed points by `viewport/year/method` |

### Functions

#### `window.loadDimReduction(method)` -> `Promise<void>`
- **method** `string | null` -- `"pca"` or `"umap"` (null = current selection)

Compute or restore cached dimensionality reduction, create Three.js scene.

#### `window.loadUMAP()` -> `Promise<void>`
Backward-compatible alias for `loadDimReduction(currentDimReduction)`.

#### `window.loadHeatmap()` -> `Promise<void>`
Compute change-detection heatmap (Euclidean distance between two years'
embeddings).  Only runs in `change-detection` mode.

#### `window.highlightUMAPPoint(lat, lon)`
Find and highlight the nearest point in the 3D scatter plot.

#### `window.showDistanceAtPoint(lat, lon)`
Show the embedding distance at a point in the heatmap (alert dialog).

#### `window.updateUMAPColorsFromLabels()`
Recolour the scatter plot using saved label colors.

#### `window.updatePanel4ManualLabels()`
Recolour the scatter plot using manual label class colors.

#### `window.computePCAFromLocal(localVectors)` -> `Array<{lat, lon, x, y, z}>`
Client-side PCA via power iteration with deflation (top 3 eigenvectors).
Subsamples to 40,000 points max.

#### `window.populateChangeStats(sorted, n, stats)`
Build the change-detection statistics table (stable/minor/moderate/major change bins).

### Classes

#### `window.UMAPScene`

Three.js 3D scatter plot for Panel 4.

```javascript
const scene = new UMAPScene('map-umap', points);
// points: [{x, y, z, lat, lon}, ...]
```

**Methods:**
- `setHighlight(point)` -- move 3D crosshair to point (or hide if null)
- `updateLabelColors(savedLabels)` -- recolour by saved label pixels
- `highlightSimilarPoints(matches)` -- colour matching points yellow
- `colorByManualLabels(colourMap)` -- colour by Map<key, [r,g,b]>
- `clearSimilarityHighlight()` -- reset all points to grey
- `resize()` -- handle container resize
- `dispose()` -- release GPU and DOM resources

#### `window.HeatmapCanvasLayer`

Leaflet canvas layer for per-pixel distance heatmap (Viridis colormap).

```javascript
const layer = new HeatmapCanvasLayer(distances, stats);
// distances: [{lat, lon, distance}, ...]
// stats: {max_distance, median_distance, mean_distance}
```

**Methods:**
- `updateDistances(newDistances)` -- replace distance data and redraw

---

## 7. evaluation.js

**Purpose:** Validation panel: shapefile upload, NDJSON streaming for learning
curves, confusion matrix rendering, model download.

### Bridged State

| Property | Type | Description |
|---|---|---|
| `window.lastEvalData` | `object \| null` | Last evaluation results |

### Functions

#### `window.uploadShapefile(file)` -> `Promise<void>`
Upload a ZIP shapefile to `/api/evaluation/upload-shapefile`.  Populates field
selector and shows GeoJSON outline on Panel 2.

#### `window.runEvaluation()` -> `Promise<void>`
Run the evaluation pipeline with selected classifiers and parameters.
Streams NDJSON results, updates Chart.js learning curves in real-time.

#### `window.renderConfusionMatrix(data)`
Render confusion matrix from evaluation results.

#### `window.exportEvalResults()`
Export evaluation results as JSON file download.

#### `window.openCMPopup(classifierName, data)`
Open confusion matrix in a separate browser window (for large matrices).

### Constants

```javascript
const CLASSIFIER_COLORS = {
    nn:              { line: 'rgba(255, 159, 64, 1)',  fill: '...' },
    rf:              { line: 'rgba(75, 192, 192, 1)',  fill: '...' },
    xgboost:         { line: 'rgba(153, 102, 255, 1)', fill: '...' },
    mlp:             { line: 'rgba(255, 99, 132, 1)',  fill: '...' },
    spatial_mlp:     { line: 'rgba(54, 162, 235, 1)',  fill: '...' },
    spatial_mlp_5x5: { line: 'rgba(255, 206, 86, 1)', fill: '...' },
    unet:            { line: 'rgba(0, 200, 83, 1)',    fill: '...' },
};

const CLASSIFIER_LABELS = {
    nn: 'k-NN', rf: 'Random Forest', xgboost: 'XGBoost',
    mlp: 'MLP', spatial_mlp: 'Spatial MLP (3x3)',
    spatial_mlp_5x5: 'Spatial MLP (5x5)', unet: 'U-Net'
};
```

---

## 8. schema.js

**Purpose:** Schema dropdown, floating tree browser for structured label
ontologies (e.g. UKHab), label selection for both manual labels and seg clusters.

### Bridged State (Object.defineProperty)

| Property | Type | Description |
|---|---|---|
| `window.activeSchema` | `{name, tree} \| null` | Loaded schema object |
| `window.activeSchemaMode` | `string` | `"none"`, `"ukhab"`, or `"custom"` |

### Functions

#### `window.toggleSchemaDropdown()`
Toggle the schema selection dropdown menu.

#### `window.loadSchema(mode)` -> `Promise<void>`
- **mode** `string` -- `"none"`, `"ukhab"`, or `"custom"`

Load a schema.  For UKHab, fetches `/schemas/ukhab-v2.json`.

#### `window.loadCustomSchema(file)`
- **file** `File` -- JSON or tab-indented text file

Parse and load a custom schema file.

#### `window.parseTabIndentedSchema(text, filename)` -> `{name, tree}`
Parse tab-indented text into a schema tree.

#### `window.renderSchemaSelector()`
Update the schema button label and re-render the manual label selector.

#### `window.selectSchemaLabel(code, name, event)`
Select a label from the schema tree.  If targeting a seg cluster input, fills
the input.  Otherwise, sets `currentManualLabel`.

#### `window.filterSchemaTree(query)`
Filter visible nodes in the schema tree by search query.

#### `window.openSchemaForCluster(inputEl)`
Open the schema float window targeting a specific cluster input element.

#### `window.openSchemaFloat()` / `window.closeSchemaFloat()`
Show/hide the floating schema browser window.

#### `window.toggleSchemaNode(caretEl)`
Expand/collapse a node in the schema tree.

#### `window.renderSchemaTreeHTML(nodes, depth)` -> `string`
Render schema tree as HTML string (recursive).

---

## 9. Key Data Structures

### 9.1 viewportStatus

```javascript
{
    // Server-side flags (updated by poller)
    has_embeddings: boolean,
    has_pyramids: boolean,
    has_vectors: boolean,
    has_umap: boolean,
    years_available: string[],     // e.g. ["2023", "2024", "2025"]

    // Client-side flags (set by JS code)
    vectors_downloaded: boolean,
    pca_loaded: boolean,
    umap_loaded: boolean
}
```

### 9.2 localVectors

```javascript
{
    values: Float32Array,           // N * 128 flat array (dequantized float32 vectors)
    coords: Int32Array,            // N * 2 flat array [px0, py0, px1, py1, ...]
    metadata: {
        geotransform: {
            a: number,             // pixel width (lon per pixel)
            b: number,             // rotation (usually 0)
            c: number,             // origin longitude
            d: number,             // rotation (usually 0)
            e: number,             // pixel height (negative, lat per pixel)
            f: number              // origin latitude
        },
        mosaic_width: number,
        mosaic_height: number
    },
    gridLookup: {minX, minY, w, h},  // O(1) grid lookup structure
    numVectors: number,
    dim: 128,
    viewport: string,
    year: string
}
```

### 9.3 manualLabel Entry

```javascript
{
    id: number,                    // auto-incremented
    name: string,                  // class name (e.g. "Woodland")
    color: string,                 // hex color (e.g. "#3cb44b")
    code: string | null,           // schema code (e.g. "w1a")
    type: 'point' | 'similarity' | 'polygon',
    lat: number,                   // source latitude
    lon: number,                   // source longitude
    embedding: number[] | null,    // 128-dim embedding (mean for polygon)
    embeddings: number[][] | null, // individual embeddings (union polygon mode)
    vertices: [number, number][] | null,  // polygon vertices [[lat, lon], ...]
    threshold: number,             // L2 distance threshold
    visible: boolean,
    matchCount: number,            // total matched pixels for the class
    pixelCount: number | null,     // polygon interior pixel count
    polygonMode: 'mean' | 'union' | null
}
```

### 9.4 savedLabel Entry

```javascript
{
    id: string,                    // e.g. "label_1710500000000"
    name: string,
    color: string,
    threshold: number,
    mean_distance: number,
    min_distance: number,
    max_distance: number,
    source_pixel: {lat, lon},
    embedding: number[],           // 128-dim
    pixel_count: number,
    pixel_coords: number[] | null, // flat [px0, py0, px1, py1, ...]
    created: string,               // ISO date
    visible: boolean,
    pixels: [{lat, lon, distance}] // recomputed from vectors on load
}
```

### 9.5 segLabel Entry

```javascript
{
    id: number,                    // cluster index
    color: string,
    hex: string,
    name: string,                  // "Cluster 1" or user-provided
    count: number,                 // pixel count
    embedding: number[],           // nearest-centroid embedding (128-dim)
    sourcePixel: {lat, lon},
    threshold: number,             // max distance from centroid
    centroid: number[]             // cluster centroid (128-dim)
}
```

### 9.6 explorerResults

```javascript
{
    sourcePixel: {lat, lon},
    sourceEmbedding: number[],     // 128-dim
    allMatches: [{lat, lon, distance}],  // cached at wide threshold
    queryTime: number,             // ms
    cacheThreshold: number         // threshold used for caching (default 35.0)
}
```

---

## 10. Frontend → Backend API Calls

Which JS modules call which backend endpoints:

| JS Module | HTTP Call | Backend Endpoint |
|---|---|---|
| `app.js` | `GET` | `/api/viewports/{name}/is-ready` (poller) |
| `app.js` | `GET` | `/api/operations/progress/{id}` (pipeline progress) |
| `app.js` | `POST` | `/api/auth/logout` |
| `maps.js` | `GET` | `/api/viewports/current` |
| `vectors.js` | `GET` | `/api/vector-data/{viewport}/{year}/all_embeddings_uint8.npy.gz` |
| `vectors.js` | `GET` | `/api/vector-data/{viewport}/{year}/quantization.json` |
| `vectors.js` | `GET` | `/api/vector-data/{viewport}/{year}/pixel_coords.npy.gz` |
| `vectors.js` | `GET` | `/api/vector-data/{viewport}/{year}/metadata.json` |
| `evaluation.js` | `POST` | `/api/evaluation/upload-shapefile` (FormData) |
| `evaluation.js` | `POST` | `/api/evaluation/run` (streaming NDJSON response) |
| `evaluation.js` | `POST` | `/api/evaluation/finish-classifier` |
| `evaluation.js` | `GET` | `/api/evaluation/download-model/{name}` |
| `evaluation.js` | `POST` | `/api/evaluation/class-pixel-counts` |
| `schema.js` | `GET` | `/schemas/ukhab-v2.json` (static file) |
| (viewer.html inline) | `GET` | `/api/auth/status` |
| (viewer.html inline) | `GET` | `/api/config` |

Tile requests bypass the Django middleware stack via `TileShortcircuitMiddleware`:

| Source | URL Pattern |
|---|---|
| Leaflet tile layers | `/tiles/{viewport}/{year}/{z}/{x}/{y}.png` |
| Viewport bounds | `/bounds/{viewport}/{year}` |

---

## 11. Cross-Module Communication Patterns

### 11.1 Explorer Flow (vectors.js -> maps.js -> dimreduction.js)

1. User double-clicks on any map panel
2. `maps.js` calls `window.explorerClick(lat, lon)` (from `vectors.js`)
3. `vectors.js` extracts embedding, runs `localSearchSimilar()`, creates `DirectCanvasLayer` on `maps.rgb`
4. `vectors.js` calls `window.umapCanvasLayer.highlightSimilarPoints(matches)` (from `dimreduction.js`)

### 11.2 Label Save Flow (labels.js -> vectors.js -> dimreduction.js)

1. User clicks "Save Label" in the modal
2. `labels.js` reads `window.currentSearchCache` and `window.explorerResults` (from `vectors.js`)
3. `labels.js` calls `window.clearExplorerResults()` (from `vectors.js`)
4. `labels.js` calls `window.updateUMAPColorsFromLabels()` (from `dimreduction.js`)

### 11.3 Segmentation Promote Flow (segmentation.js -> labels.js -> vectors.js)

1. User clicks promote arrow on a cluster
2. `segmentation.js` calls `window.addManualLabel(entry)` (from `labels.js`)
3. `segmentation.js` creates `window.DirectCanvasLayer(pixels, ...)` (from `vectors.js`)
4. `segmentation.js` calls `window.updatePanel4ManualLabels()` (from `dimreduction.js`)

### 11.4 Dependency Cascade (app.js orchestrates all modules)

1. Poller detects `has_vectors: true`
2. `evaluateDependencies()` fires `vectors-download` -> downloads vectors
3. Sets `vectors_downloaded: true`, re-evaluates
4. Fires `label-controls` (enables UI), `panel4-pca` (computes PCA), `panel5-heatmap` (loads heatmap)

### 11.5 Year Switch (maps.js -> vectors.js -> labels.js -> dimreduction.js)

1. User changes year in dropdown
2. `maps.js` `switchEmbeddingYear()` removes old tile layer, creates new one
3. Downloads vectors for new year via `window.downloadVectorData()`
4. Calls `window.refreshLabelsForYear()` to recompute label pixels
5. Re-runs active explorer search via `window.explorerClick()`
6. Calls `window.loadHeatmap()` to update change heatmap
