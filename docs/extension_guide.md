# TEE Extension Guide

Practical recipes for extending the Tessera Embeddings Explorer.  Each recipe
is self-contained with step-by-step instructions and code snippets from the
actual codebase.

See also: [architecture.md](architecture.md) for system overview,
[frontend_api.md](frontend_api.md) for the JavaScript API,
[backend_api.md](backend_api.md) for backend endpoints.

---

## Table of Contents

1. [Adding a New Panel Mode](#1-adding-a-new-panel-mode)
2. [Adding a New Classifier](#2-adding-a-new-classifier)
3. [Adding a New Map Data Source](#3-adding-a-new-map-data-source)
4. [Adding a New Label Type](#4-adding-a-new-label-type)
5. [Adding a New JS Module](#5-adding-a-new-js-module)
6. [Modifying Panel Titles and Layout](#6-modifying-panel-titles-and-layout)
7. [Adding a New Backend Endpoint](#7-adding-a-new-backend-endpoint)
8. [Adding a New Dimensionality Reduction Method](#8-adding-a-new-dimensionality-reduction-method)
9. [Adding a Custom Schema](#9-adding-a-custom-schema)

---

## 1. Adding a New Panel Mode

Panel modes control which panels are visible, their titles, and which layers
are active on Panel 5.  The existing modes are `explore`, `change-detection`,
`labelling`, and `validation`.

**Example:** Adding a `"comparison"` mode that shows two satellite views
side-by-side.

### Step 1: Add CSS class

In `viewer.html`, add CSS rules for the new mode.  Each mode class is applied
to `#map-container` and `body`:

```css
/* In viewer.html <style> section */
.mode-comparison .panel1 { /* grid area rules */ }
.mode-comparison .panel2 { /* grid area rules */ }
/* Hide panels not needed */
.mode-comparison .panel4,
.mode-comparison .panel6 { display: none !important; }
```

### Step 2: Add to Panel 5 layer rules

In `maps.js`, add the new mode to `PANEL5_LAYER_RULES`:

```javascript
// In maps.js, inside the PANEL5_LAYER_RULES object:
const PANEL5_LAYER_RULES = {
    'explore':          { satellite: false, heatmapCanvas: true,  segOverlay: true,  embedding2: false },
    'change-detection': { satellite: false, heatmapCanvas: true,  segOverlay: false, embedding2: true  },
    'labelling':        { satellite: true,  heatmapCanvas: false, segOverlay: true,  embedding2: false },
    'validation':       { satellite: false, heatmapCanvas: false, segOverlay: false, embedding2: false },
    'comparison':       { satellite: true,  heatmapCanvas: false, segOverlay: false, embedding2: true  },  // NEW
};
```

### Step 3: Add panel titles

In `maps.js`, inside `setPanelLayout()`, add titles for the new mode:

```javascript
const titles = {
    'explore':          { p1: 'OpenStreetMap', p3: 'Tessera Embeddings', p4: 'PCA (Embedding Space)', p5: 'Change Heatmap',  p6: 'Tessera Embeddings' },
    'change-detection': { p1: 'OpenStreetMap', p3: 'Tessera Embeddings', p4: 'Change Distribution',    p5: 'Change Heatmap',  p6: 'Tessera Embeddings' },
    'labelling':        { p1: 'OpenStreetMap', p3: 'Tessera Embeddings', p4: 'PCA (Embedding Space)', p5: 'Classification results',    p6: 'Auto-label' },
    'validation':       { p1: 'Classes',       p3: 'Evaluation year',    p4: 'Performance',           p5: 'Confusion Matrix', p6: 'Controls' },
    'comparison':       { p1: 'Satellite A',   p3: 'Satellite B',        p4: 'Statistics',            p5: 'Difference',       p6: 'Controls' },  // NEW
};
```

### Step 4: Add mode-specific setup

In `setPanelLayout()`, add a branch for the new mode after the existing
mode-specific setup:

```javascript
} else if (mode === 'comparison') {
    // Custom setup for comparison mode
    // e.g., load comparison data, configure panels
}
```

### Step 5: Add to valid modes list

In `restorePanelMode()` in `maps.js`:

```javascript
const validModes = ['explore', 'change-detection', 'labelling', 'validation', 'comparison'];
```

### Step 6: Update CSS class removal

In `setPanelLayout()`, update the class removal list:

```javascript
container.classList.remove('mode-explore', 'mode-change-detection', 'mode-labelling', 'mode-validation', 'mode-comparison');
document.body.classList.remove('mode-explore', 'mode-change-detection', 'mode-labelling', 'mode-validation', 'mode-comparison');
```

### Step 7: Add option to mode selector

In `viewer.html`, add an option to the `#panel-layout-select` dropdown:

```html
<option value="comparison">Comparison</option>
```

---

## 2. Adding a New Classifier

The evaluation pipeline supports multiple classifiers.  To add a new one, you
need to update both the backend (Python) and frontend (JavaScript).

**Example:** Adding a Gradient Boosted Trees classifier via LightGBM.

### Step 1: Add to evaluation_engine.py

In `lib/evaluation_engine.py`, update `make_classifier()`:

```python
def make_classifier(name, params=None):
    p = params or {}
    if name == 'nn':
        return KNeighborsClassifier(n_neighbors=p.get('n_neighbors', 5))
    elif name == 'rf':
        return RandomForestClassifier(n_estimators=p.get('n_estimators', 100), ...)
    # ... existing classifiers ...
    elif name == 'lgbm':                                    # NEW
        from lightgbm import LGBMClassifier                # NEW
        return LGBMClassifier(                              # NEW
            n_estimators=p.get('n_estimators', 200),        # NEW
            learning_rate=p.get('learning_rate', 0.1),      # NEW
            num_leaves=p.get('num_leaves', 31),             # NEW
            n_jobs=-1,                                      # NEW
        )                                                   # NEW
    elif name == 'unet':
        return None  # handled separately
    return None
```

The classifier must implement the scikit-learn interface (`fit()`, `predict()`).

### Step 2: Add frontend color and label

In `evaluation.js`, add to the constants:

```javascript
const CLASSIFIER_COLORS = {
    // ... existing ...
    lgbm: { line: 'rgba(139, 195, 74, 1)', fill: 'rgba(139, 195, 74, 0.15)' },  // NEW
};

const CLASSIFIER_LABELS = {
    // ... existing ...
    lgbm: 'LightGBM',  // NEW
};
```

### Step 3: Add checkbox to viewer.html

In the validation panel section of `viewer.html`, add a checkbox:

```html
<label class="val-clf-header">
    <input type="checkbox" value="lgbm" checked> LightGBM
</label>
```

### Step 4: Add hyperparameter controls (optional)

If you want the user to configure parameters:

```html
<div class="val-params" style="display: none;">
    <label>n_estimators: <input data-clf="lgbm" data-param="n_estimators" value="200" style="width: 60px;"></label>
</div>
```

The evaluation.js `runEvaluation()` function automatically reads all
`.val-params input` elements with `data-clf` and `data-param` attributes.

### Step 5: Add Python dependency

Add `lightgbm` to `requirements.txt`.

**Extension Point:** The `run_learning_curve()` generator in
`lib/evaluation_engine.py` handles training sizes, cross-validation splits, and
streaming results.  New classifiers plug in via `make_classifier()` alone --
no changes needed to the evaluation loop itself, unless the classifier needs
spatial features (like `spatial_mlp`).

For spatial classifiers, check the `name.startswith('spatial_')` branch in
`run_learning_curve()` which calls `gather_spatial_features()`.

---

## 3. Adding a New Map Data Source

Satellite imagery sources are defined in `maps.js`.  The existing sources are
ESRI World Imagery and Google Satellite.

**Example:** Adding Mapbox Satellite.

### Step 1: Add to satelliteSources

In `maps.js`, update the `satelliteSources` object:

```javascript
const satelliteSources = {
    esri: {
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Esri World Imagery',
        exportUrl: (z, y, x) => `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`
    },
    google: {
        url: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attribution: 'Google Satellite',
        exportUrl: (z, y, x) => `https://mt1.google.com/vt/lyrs=s&x=${x}&y=${y}&z=${z}`
    },
    mapbox: {                                                               // NEW
        url: 'https://api.mapbox.com/v4/mapbox.satellite/{z}/{x}/{y}@2x.png?access_token=YOUR_TOKEN',
        attribution: 'Mapbox Satellite',                                    // NEW
        exportUrl: (z, y, x) => `https://api.mapbox.com/v4/mapbox.satellite/${z}/${x}/${y}@2x.png?access_token=YOUR_TOKEN`
    }                                                                       // NEW
};
```

### Step 2: Add to HTML selector

In `viewer.html`, find the `#satellite-source-selector` dropdown:

```html
<select id="satellite-source-selector">
    <option value="esri">ESRI</option>
    <option value="google">Google</option>
    <option value="mapbox">Mapbox</option>  <!-- NEW -->
</select>
```

That's all.  The `change` event listener in `createMaps()` already handles
switching between any sources defined in `satelliteSources`.

**Extension Point:** The `satelliteSources` object is exposed on
`window.satelliteSources` (read-only via `Object.defineProperty`).  If you
need to add sources at runtime, modify the module-private variable directly
inside `maps.js`.

---

## 4. Adding a New Label Type

TEE supports three label types: `point`, `similarity`, and `polygon`.  Each
type has different data and overlay behaviour.

**Example:** Adding a `circle` label type that draws a circle of a given radius
and labels all pixels inside it.

### Step 1: Define the entry shape

In `labels.js`, the `addManualLabel(entry)` function accepts any entry object
with the standard fields.  Create your entry with `type: 'circle'`:

```javascript
const entry = {
    name: window.currentManualLabel.name,
    color: window.currentManualLabel.color,
    code: window.currentManualLabel.code || null,
    type: 'circle',           // NEW type
    lat: centerLat,
    lon: centerLon,
    radiusMeters: 50,         // NEW field
    embedding: centroid,       // mean embedding of interior pixels
    threshold: classThreshold,
    matchCount: matchCount,
};
window.addManualLabel(entry);
```

### Step 2: Handle in rebuildClassOverlay

In `labels.js`, update `rebuildClassOverlay(className)` to handle the new type.
Find the section that processes polygon outlines and add a branch:

```javascript
// After the polygon outline drawing section:
if (label.type === 'circle' && label.radiusMeters) {
    const circle = L.circle([label.lat, label.lon], {
        radius: label.radiusMeters,
        color: label.color,
        fillColor: label.color,
        fillOpacity: 0.15,
        weight: 2
    });
    layerGroup.addLayer(circle);
}
```

### Step 3: Add rasterization

If the circle needs to contribute pixels to the overlay, add a rasterization
function similar to `rasterizePolygon()`:

```javascript
function rasterizeCircle(centerLat, centerLon, radiusMeters) {
    if (!window.localVectors) return [];
    const gt = window.localVectors.metadata.geotransform;
    const grid = window.localVectors.gridLookup;

    const centerPx = Math.round((centerLon - gt.c) / gt.a);
    const centerPy = Math.round((centerLat - gt.f) / gt.e);
    const radiusPx = Math.round(radiusMeters / 10);  // 10m pixel size

    const matches = [];
    for (let py = centerPy - radiusPx; py <= centerPy + radiusPx; py++) {
        for (let px = centerPx - radiusPx; px <= centerPx + radiusPx; px++) {
            const dx = px - centerPx, dy = py - centerPy;
            if (dx * dx + dy * dy <= radiusPx * radiusPx) {
                const idx = window.gridLookupIndex(grid, px, py);
                if (idx >= 0) {
                    matches.push({
                        lat: gt.f + py * gt.e,
                        lon: gt.c + px * gt.a,
                        vectorIndex: idx
                    });
                }
            }
        }
    }
    return matches;
}
```

### Step 4: Handle in export

In `doExportManualLabels()`, the JSON export already includes all entry fields.
For GeoJSON, add a branch in the geometry generation:

```javascript
if (l.type === 'circle') {
    // GeoJSON doesn't have a native circle; export as Point with radius property
    return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [l.lon, l.lat] },
        properties: { name: l.name, color: l.color, type: 'circle', radiusMeters: l.radiusMeters }
    };
}
```

### Step 5: Update renderManualLabelsList (optional)

Add a distinct icon for circle labels in the label list:

```javascript
const childIcon = label.type === 'polygon' ? '\u2b20'
    : label.type === 'circle' ? '\u25ef'     // NEW
    : '\ud83d\udccd';
```

---

## 5. Adding a New JS Module

To add a new JS module to the TEE viewer:

### Step 1: Create the module file

Create `public/js/mymodule.js`:

```javascript
// mymodule.js -- Description of what this module does
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──

let myState = null;

Object.defineProperty(window, 'myState', {
    get: () => myState,
    set: (v) => { myState = v; },
    configurable: true,
});

// ── Core Functions ──

function myFunction(param1, param2) {
    // Implementation using window.localVectors, window.maps, etc.
    console.log('[MYMODULE] doing work...');
}

// ── Expose on window for onclick handlers and cross-module access ──

window.myFunction = myFunction;
```

**Key patterns to follow:**
- Use `Object.defineProperty` for state that other modules need to read/write
- Expose functions on `window` at the bottom of the file
- Use `window.*` to access state and functions from other modules
- Prefix console logs with `[MYMODULE]` for easy filtering

### Step 2: Load in viewer.html

Add a `<script>` tag in `viewer.html`.  The load order matters -- place your
module after its dependencies:

```html
<!-- Near the end of viewer.html, after existing module scripts -->
<script src="js/mymodule.js"></script>
```

If your module uses ES `import` (like `dimreduction.js` does for Three.js),
use `type="module"`:

```html
<script type="module" src="js/mymodule.js"></script>
```

### Step 3: Wire into the dependency system (optional)

If your module needs to initialise after vectors are downloaded, add a
dependency entry in `app.js`:

```javascript
// In the dependencyRegistry array in app.js:
{
    id: 'mymodule-init',
    test: (s) => s.vectors_downloaded && !myModuleInitialised,
    onReady: async () => {
        console.log('[DEP] mymodule-init: initialising');
        await window.myFunction();
        myModuleInitialised = true;
    },
    satisfied: false
},
```

### Step 4: Add HTML elements (optional)

If your module needs UI, add HTML in the appropriate panel in `viewer.html`.
Panel IDs follow the pattern:

- Panel 1: `#map-osm` (Leaflet map)
- Panel 2: `#map-rgb` (Leaflet map)
- Panel 3: `#map-embedding` (Leaflet map)
- Panel 4: `#map-umap` (Three.js container)
- Panel 5: `#map-panel5` (Leaflet map)
- Panel 6: `#map-embedding2` (Leaflet map) + HTML controls

---

## 6. Modifying Panel Titles and Layout

### Static Panel Titles

Panel titles are set per-mode in `setPanelLayout()` in `maps.js`.  The titles
object maps mode names to per-panel titles:

```javascript
const titles = {
    'explore': {
        p1: 'OpenStreetMap',
        p3: 'Tessera Embeddings',
        p4: 'PCA (Embedding Space)',
        p5: 'Change Heatmap',
        p6: 'Tessera Embeddings'
    },
    // ... other modes ...
};
```

The title elements are:
- `#panel1-title`
- `#panel2-title` (set separately by label mode)
- `#panel3-title`
- `#panel4-title`
- `#panel5-title`
- `#panel6-header-text`

### Dynamic Panel Titles

Some titles are set dynamically outside `setPanelLayout()`:

- **Panel 4 title** is updated by `loadDimReduction()` in `dimreduction.js` to
  show the current method (PCA or UMAP)
- **Panel 2 and 5 titles** are updated by `setLabelMode()` in `labels.js`
  when switching between auto-label and manual modes

### Grid Layout

The 6-panel grid is defined in `viewer.html` CSS.  Each mode has a CSS class
(`mode-explore`, etc.) that controls which panels are visible and their grid
placement:

```css
#map-container {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    grid-template-rows: 1fr 1fr;
}

/* Example: hide panels in validation mode */
.mode-validation .panel2 { display: none !important; }
```

To change the grid layout (e.g., make Panel 4 span two columns), modify the
CSS grid rules for the relevant mode class.

### Adding a Panel

To add a 7th panel:

1. Add a new `<div>` in the `#map-container` in `viewer.html`
2. Create a Leaflet map or HTML container inside it
3. Update the CSS grid to accommodate the new panel
4. If it's a Leaflet map, add it to `window.maps` and include it in `syncMaps()`
5. Add click/dblclick handlers in `createMaps()` if needed

---

## 7. Adding a New Backend Endpoint

### Step 1: Create the view function

In `api/views/` (either in an existing file or a new one):

```python
# api/views/myfeature.py
import json
from django.http import JsonResponse

def my_endpoint(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    body = json.loads(request.body)
    # ... process ...
    return JsonResponse({"result": "ok"})
```

### Step 2: Register the URL

In `api/urls.py`, add the route:

```python
from .views.myfeature import my_endpoint

urlpatterns = [
    # ... existing routes ...
    path('myfeature/do-thing', my_endpoint),
]
```

All paths in `api/urls.py` are prefixed with `/api/` by `tee_project/urls.py`.

### Step 3: Add auth requirement (if needed)

If the endpoint modifies data, add it to `WRITE_ENDPOINTS` in
`api/middleware.py`:

```python
WRITE_ENDPOINTS = {
    # ... existing ...
    '/api/myfeature/do-thing',
}
```

### Step 4: Call from JavaScript

```javascript
const resp = await fetch('/api/myfeature/do-thing', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ param1: 'value' }),
});
const data = await resp.json();
```

### Step 5: Run tests

```bash
venv/bin/pytest validation/ -v
```

---

## 8. Adding a New Dimensionality Reduction Method

### Step 1: Add to `dimreduction.js`

In `loadDimReduction()`, add a branch for the new method alongside `pca` and
`umap`:

```javascript
} else if (dimMethod === 'tsne') {
    // Compute t-SNE (e.g., via a Web Worker)
    umapContainer.innerHTML = '<div ...>Computing t-SNE...</div>';
    await new Promise(resolve => setTimeout(resolve, 50));
    points = computeTSNE(window.localVectors);
}
```

The function must return an array of `{x, y, z, lat, lon}` objects.  The
`UMAPScene` class renders any 3D point cloud regardless of the reduction method.

### Step 2: Add to the selector dropdown

In `viewer.html`, add an option to `#dim-reduction-selector`:

```html
<option value="tsne">t-SNE</option>
```

The change listener in `dimreduction.js` already calls
`loadDimReduction(selectedValue)` for any selected value.

---

## 9. Adding a Custom Schema

Schemas define hierarchical label ontologies for structured labelling.  See
[frontend_api.md §8](frontend_api.md#8-schemajs) for the full format reference.

### Option A: Add a built-in schema

1. Create a JSON file in `public/schemas/` (e.g. `public/schemas/corine.json`):

```json
{
  "name": "CORINE Land Cover",
  "tree": [
    {
      "code": "1",
      "name": "Artificial surfaces",
      "children": [
        { "code": "1.1", "name": "Urban fabric", "children": [
          { "code": "1.1.1", "name": "Continuous urban fabric", "children": [] },
          { "code": "1.1.2", "name": "Discontinuous urban fabric", "children": [] }
        ]}
      ]
    },
    {
      "code": "3",
      "name": "Forest and semi-natural areas",
      "children": []
    }
  ]
}
```

2. In `schema.js`, add an entry to the `builtinSchemas` lookup in `loadSchema()`:

```javascript
const builtinSchemas = {
    ukhab: { url: '/schemas/ukhab-v2.json', label: 'UKHab' },
    hotw:  { url: '/schemas/hotw.json',      label: 'HOTW' },
    corine: { url: '/schemas/corine.json',   label: 'CORINE' },  // NEW
};
```

3. In `viewer.html`, add an option to the schema dropdown menu (search for
   `schema-dropdown-menu`):

```html
<button onclick="loadSchema('corine')">CORINE Land Cover</button>
```

### Option B: User-uploaded custom schema

No code changes needed — users can already upload custom schemas via the
"Custom..." option in the Schema dropdown.  Supported formats:

- **JSON** — `{name, tree}` with nested `{code, name, children}` nodes
- **Tab-indented text** — one label per line, optional code prefix:

```
1 Artificial surfaces
    1.1 Urban fabric
        1.1.1 Continuous urban fabric
3 Forest and semi-natural areas
```

---

## Common Extension Points

| Extension | Primary file | Key function/object |
|---|---|---|
| New panel mode | `maps.js` | `setPanelLayout()`, `PANEL5_LAYER_RULES` |
| New classifier | `lib/evaluation_engine.py` | `make_classifier()` |
| New satellite source | `maps.js` | `satelliteSources` object |
| New label type | `labels.js` | `rebuildClassOverlay()`, `doExportManualLabels()` |
| New JS module | `viewer.html` + new file | `window.*` exports, `dependencyRegistry` |
| New backend endpoint | `api/urls.py` + `api/views/*.py` | Django view function |
| New schema format | `schema.js` | `loadCustomSchema()`, `parseTabIndentedSchema()` |
| New dim reduction method | `dimreduction.js` | `loadDimReduction()` |
| New export format | `labels.js` | `doExportManualLabels()` |
| Custom tile overlay | `maps.js` | `L.pixelatedTileLayer()` or `L.tileLayer()` |

---

## Checklist: Before Submitting Any Change

1. **Run tests:** `venv/bin/pytest validation/ -v` — all tests must pass
2. **Check JS syntax:** browser console should show no errors on load
3. **Verify `window.*` exports:** if you add a function other modules call,
   expose it at the bottom of the file with `window.myFunction = myFunction`
4. **Update docs:** if you added a new `window.*` export, add it to
   `frontend_api.md`.  If you added a backend endpoint, add it to
   `backend_api.md`
5. **Test on multiple browsers:** Safari can behave differently (especially with
   Web Workers when dev console is open — see architecture.md §9)
