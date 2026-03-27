# TEE User Guide

A guide to using the Tessera Embeddings Explorer (TEE) web interface.

## Creating a Viewport

A **viewport** is a 5km x 5km geographic area for which TEE downloads and processes Sentinel-2 embeddings.

1. Open the **Viewport Manager** (the home page)
2. Click **+ Create New Viewport**
3. Choose a location using one of three methods:
   - **Search** — type a place name (e.g. "Cambridge") or coordinates (e.g. "52.2, 0.12") in the search bar
   - **Click the map** — a 5km preview box follows the cursor; click to lock it in place
   - **Reposition** — after clicking, click again to move the box
4. Enter a **viewport name** and select which **years** to process (2018-2025)
5. Click **Create** — processing runs in the background with a progress bar
6. The viewer opens automatically when processing completes

**Deleting a viewport:** Click the trash icon next to any viewport in the list. All associated data (mosaics, pyramids, vectors) is cleaned up automatically.

## The Viewer

The viewer has four modes, selected from the **layout dropdown** in the header. All modes share panels 1–3 across the top row and differ in the bottom row (panels 4–6). All six panels are synchronized — panning or zooming one pans/zooms all.

### Explore (default)

A 3-panel overview for browsing embeddings and running similarity searches.

| Panel | Title | Content |
|-------|-------|---------|
| 1 | **OpenStreetMap** | Geographic reference |
| 2 | **Satellite** | Esri or Google satellite imagery (toggle in header) |
| 3 | **Tessera Embeddings** | Embedding visualization with year selector |
| 4 | **PCA (Embedding Space)** | 3D scatter plot of embeddings (PCA or UMAP) |
| 5 | | (available in other modes) |
| 6 | | (available in other modes) |

Double-click any pixel to run a similarity search. Adjust the threshold slider and save results as labels.

### Change Detection

For comparing two years side by side and identifying areas of change.

| Panel | Title | Content |
|-------|-------|---------|
| 1 | **OpenStreetMap** | Geographic reference |
| 2 | **Satellite** | Satellite imagery |
| 3 | **Tessera Embeddings** | Y1 embeddings |
| 4 | **Change Distribution** | Histogram and statistics of embedding distance between Y1 and Y2 |
| 5 | **Change Heatmap** | Pixel-by-pixel temporal distance — bright = changed, dark = stable |
| 6 | **Tessera Embeddings** | Y2 embeddings |

Select different years for Y1 and Y2 using the year dropdowns. The heatmap and change distribution update automatically.

### Labelling

For building label sets — either automatically via K-means or manually with pins and polygons.

| Panel | Title | Content |
|-------|-------|---------|
| 1 | **OpenStreetMap** | Geographic reference |
| 2 | **Satellite** / **Labels** | Satellite imagery with label overlays (titled "Labels" in manual sub-mode) |
| 3 | **Tessera Embeddings** | Embeddings with similarity search |
| 4 | **PCA (Embedding Space)** | 3D scatter plot, colored by label classes in manual sub-mode |
| 5 | **Segmentation** / **Classification** | K-means overlay in auto-label; nearest-centroid classification in manual |
| 6 | **Auto-label** / **Manual Label** | Label management (see below) |

Panel 6 has a **sub-mode dropdown** at the top:

- **Auto-label** — left half shows K-means clusters with promote buttons; right half shows saved labels with timeline, visibility toggle, and rename
- **Manual Label** — set a label name/color, then Ctrl+click to pin or Ctrl+double-click to draw polygons. Per-class similarity sliders expand coverage. See [Manual Labelling](#manual-labelling) for full details.

### Validation

For evaluating classifier performance on expert ground-truth polygons. Supports two sub-modes: **Viewport** (existing single-viewport learning curves) and **Large Area** (k-fold cross-validation across GeoTessera tiles for county/country-scale shapefiles).

| Panel | Title | Content |
|-------|-------|---------|
| 1 | **Classes** | Table of ground-truth classes with pixel counts |
| 2 | **Satellite** | Satellite imagery with ground-truth polygon outlines in red |
| 3 | **Evaluation year** | Embeddings for the year being evaluated |
| 4 | **Performance** | Learning-curve chart (Viewport) or R² bar chart (Large Area regression) |
| 5 | **Confusion Matrix / Regression Metrics** | Confusion matrix (classification) or metrics table (regression) |
| 6 | **Controls** | Mode toggle, shapefile upload, class field selector, classifier checkboxes, hyperparameters |

Upload a zipped shapefile, select a class field and classifiers, then click Run Evaluation. See [Validation](#validation) for full details.

### Switching Years

Use the **year dropdown** above the embedding panels to switch between processed years. In Change Detection and Explore modes, Y1 and Y2 can be set to different years for temporal comparison.

## Similarity Search

**Double-click** anywhere on the map to trigger a similarity search. TEE extracts the 128-dimensional embedding at that pixel and finds all similar locations across the viewport. All computation runs **locally in your browser** — no data is sent to the server.

### How to Use

1. **Double-click any pixel** on any map panel — similar locations are highlighted across all panels as colored dots
2. Adjust the **similarity slider** to control how similar a match must be (lower = more strict)
3. Click **Save as Label** to name and color-code the current search results

The first time you run a search for a viewport+year, vector data (~20-50MB) is downloaded and cached in your browser's IndexedDB. Subsequent searches are instant.

### Single-Click

A **single click** on any panel places a synchronized marker across all panels, useful for cross-referencing a location between OSM, satellite, and embedding views without triggering a search.

## Labels

Labels are named, colored collections of similar pixels found through similarity search.

### Managing Labels

- **Save** — after a similarity search, click "Save as Label", choose a name and color
- **Toggle visibility** — click a label name to show/hide it
- **Delete** — click the X next to a label
- **Labels persist** across page reloads (stored in browser localStorage)

### Cross-Year Timeline

Click **Timeline** on any saved label to see how its coverage changes across all available years:

- A bar chart shows pixel counts per year
- A percentage change summary compares the first and last years
- Each year's vector data is loaded automatically from cache (or downloaded in background)

### Importing and Exporting Labels

Labels are portable — they use embedding distance rather than coordinates, so they work across different viewports.

- **Import** — use the Import button to load a previously exported JSON file
- **Export** — see Export Options below

## Manual Labelling

Manual labelling mode lets you build label classes by hand — placing individual pins, drawing polygons, or combining both. Each label entry captures an embedding, which enables similarity-based expansion, cross-year timeline analysis, and classification in Panel 5.

### Entering Manual Mode

1. Switch the layout to **Labelling** (header dropdown)
2. In Panel 6, change the mode dropdown from **Auto-label** to **Manual Label**

Panel headings update: Panel 2 becomes **Labels** (shows your labels on the satellite image), Panel 5 becomes **Classification**, and Panel 6 shows the manual label controls.

### Setting a Label

Before placing any labels, you must set the active label class:

1. Type a **label name** in the text field (e.g. "Woodland")
2. Click the **color swatch** to choose a color
3. Click **Set**

The **Active** bar appears below, showing the current label name, color, and instructions.

**Schema support:** Click **Schema** in the header to load a classification scheme (UKHab v2, HOTW, or a custom JSON/text). A floating tree window opens — click any entry to set it as the active label with its code and color pre-filled.

### Placing Point Labels (Pins)

**Ctrl+click** (or Cmd+click on Mac) anywhere on the map to drop a pin at that location. The pin:

- Appears as a colored circle marker on the satellite panel (Panel 2)
- Extracts the 128D embedding at that pixel (if vectors are loaded)
- Inherits the similarity threshold from other labels in the same class
- Is immediately listed in the Manual Labels panel (Panel 6)

**Double-click** (without Ctrl) also drops a pin — this is a shortcut for quick labelling.

### Drawing Polygon Labels

**Ctrl+double-click** (or Cmd+double-click) to start drawing a polygon:

1. A polygon drawing tool activates — click to place vertices on the map
2. Click the first vertex (or double-click) to close the polygon
3. Press **Escape** to cancel a polygon in progress

The polygon:
- Appears as a colored filled shape on the satellite panel
- All pixels inside the polygon are rasterized at 10m resolution and included in the class overlay
- Is listed in the Manual Labels panel with a "polygon" type indicator

#### Polygon Search Mode

When the active label bar is visible, two radio buttons let you choose how the polygon embedding is used for similarity expansion:

- **Mean** (default) — computes the average of all pixel embeddings inside the polygon and stores a single centroid embedding. The similarity slider then finds pixels similar to this average. Best for homogeneous areas where you want to capture the "typical" signature.
- **Union** — stores every individual pixel embedding inside the polygon. The similarity slider finds pixels similar to *any* of those embeddings. Best for heterogeneous areas (e.g. a polygon spanning mixed habitat types) where you want to capture the full variety.

The mode is chosen *before* drawing the polygon and is saved per label entry. Mean mode is faster for large polygons; union mode can be slow if the polygon contains thousands of pixels, but gives broader coverage.

### Understanding Label Entries

Each pin or polygon creates a **label entry** under a class name. A class can contain multiple entries (e.g. several pins for "Grassland"). The Manual Labels panel groups entries by class:

- **Multi-entry classes** show a collapsible group with an expand/collapse arrow
- **Single-entry classes** show a flat row

Each row displays:
- **Set-active icon** (target, leftmost) — click to make this the active label class for quick re-selection
- **Color swatch** — the class color
- **Label code** (if using a schema) — e.g. "[u1b5]"
- **Label name** — e.g. "Grassland"
- **Similarity slider** — adjusts the embedding distance threshold (see below)
- **Pixel count** — number of matched pixels
- **Timeline icon** (clock) — see coverage across years (only shown when an embedding is available)
- **Delete icon** (trash) — remove the entry or entire class
- **Visibility toggle** (eye) — show/hide the class on the map

### Similarity Threshold

Each label entry has a **similarity slider** that controls how far the embedding search extends from the pin or polygon embedding (centroid in mean mode, or any pixel in union mode):

- **Threshold = 0** — only the pin pixel itself (or polygon interior) is shown
- **Higher threshold** — more pixels are included based on embedding distance
- The slider ranges from 0 to 500 (L2 distance in embedding space)

Adjusting the threshold for any entry in a class rebuilds the entire class overlay — the union of all entries' similarity matches.

### Classification (Panel 5)

Click the **Classify** button in the Panel 5 header to generate a full-viewport classification:

- Each pixel is assigned to the nearest label class based on embedding distance
- Only labels with a **non-zero threshold** contribute to classification
- A pixel must fall within a label's threshold to be classified (pixels outside all thresholds remain unclassified)
- Results appear as a colored overlay on Panel 5
- Classification updates automatically when you add, remove, or adjust labels

### Panel 4 Coloring

Manual labels are reflected in the PCA/UMAP scatter plot (Panel 4):

- Pixels matching any visible label class are colored with that class's color
- Unmatched pixels appear as gray
- Colors update when labels change, are toggled, or the scene loads

### Cross-Year Timeline

Click the **clock icon** on any class row to see how that class's coverage changes over all available years:

- A bar chart shows pixel counts per year
- A percentage change summary compares the first and last years
- Each year's vector data is loaded from cache or downloaded automatically

### Bulk Actions

- **Hide All / Show All** — toggle visibility of all manual label classes at once
- Set-active icon allows quick switching between classes without re-entering the name

### Tips

- **Combine pins and polygons** — use polygons for large homogeneous areas, pins for scattered features
- **Start with threshold = 0** — place several pins across a class, then gradually increase the threshold to expand coverage
- **Check Panel 4** — the PCA/UMAP view shows whether your labels form coherent clusters in embedding space
- **Use Classify** — the Panel 5 classification gives a quick visual check of your label coverage
- **Labels persist** across page reloads (stored in browser localStorage)

## Auto-Labelling (K-Means Clustering)

TEE can automatically segment the viewport into distinct clusters using K-means clustering on the embedding space. Segmentation clusters are **temporary previews** — they appear in a floating panel but are not saved until you promote them.

### Running Segmentation

1. Set the number of clusters using the **k slider** in the toolbar (2–20)
2. Click **Segment** — clustering runs in a Web Worker and results appear as a colored overlay on the heatmap panel
3. The **Segmentation (temporary preview)** panel lists each cluster with its color, pixel count, and percentage

### Promoting Clusters to Labels

Segmentation clusters are temporary — they disappear when you re-segment or clear. To make them permanent:

- **Promote one cluster** — click the **↗** button on a cluster row. It moves from the seg panel to your saved labels.
- **Promote all** — click **Promote All to Labels** to save every cluster at once.
- **Name before promoting** — type a label name in the text field next to a cluster before promoting; otherwise it defaults to "Cluster N".

Promoted labels are fully functional saved labels:
- They have an embedding and source pixel, so they support **Timeline** analysis across years
- They can be **re-matched** on other viewports via import/export
- They appear in the **Labels** panel and persist across page reloads

### Clearing Segmentation

Click **Clear** to remove the seg overlay and panel. This does not affect any already-promoted labels.

### Suggested Labelling Workflow

A practical workflow for building a complete land-cover label set:

1. **Auto-label with k-means** — run segmentation with **k = 5** or higher. Set k a bit higher than the number of classes you expect — it is easier to merge clusters than to split them later.
2. **Review on the heatmap** — click on clusters in **Panel 5** (heatmap) to see where each cluster falls, then inspect the corresponding area in **Panel 6** (embeddings Y2). When a cluster looks correct, give it a name in the text field and click **Promote** to save it as a label.
3. **Merge related clusters** — if two auto-labelled clusters represent the same land cover (e.g. two shades of grassland), type the **same label name** for both in Panel 6 before promoting. TEE merges them into a single label automatically.
4. **Fine-tune with pins** — double-click to place a pin on a location that was missed or misclassified, then adjust the **similarity slider** until the highlighted area matches what you want. Save the result as a new label or extend an existing one.

## Validation (Learning Curves & Large-Area Evaluation)

The **Validation** panel lets you evaluate how well classifiers can distinguish habitat classes using Tessera embeddings as features, given expert-labelled ground-truth polygons.

Two modes are available, toggled by buttons at the top of Panel 6:

- **Viewport** — evaluates classifiers within the current viewport (~5×5 km) using learning curves with repeated random splits. This is the original mode.
- **Large Area** — evaluates classifiers on shapefiles covering much larger areas (county or country scale). Embeddings are loaded tile-by-tile from GeoTessera, then the same learning curve is run. Supports both classification and regression.

### Setup

1. Switch the layout dropdown to **Validation**
2. The bottom row changes to: a controls panel (left), a learning-curve chart (centre), and a confusion matrix panel (right)
3. Choose **Viewport** or **Large Area** mode using the toggle at the top of Panel 6

### Preparing a Ground-Truth Shapefile

TEE accepts a **zipped ESRI shapefile**. The `.zip` must contain at least four files with the same base name:

| Extension | Purpose |
|-----------|---------|
| `.shp` | Geometry (polygons or multipolygons) |
| `.dbf` | Attribute table |
| `.shx` | Spatial index |
| `.prj` | Coordinate reference system |

The shapefile should cover (or overlap) the current viewport area. Each polygon needs an attribute column whose values represent the class labels (e.g. a `Habitat` field with values like "Woodland", "Grassland", "Urban"). Any coordinate reference system is accepted — TEE reprojects to EPSG:4326 automatically.

**Tip:** You can produce a suitable shapefile from QGIS, ArcGIS, or any GIS tool. Export your labelled polygons as a shapefile and zip the four files together:
```bash
zip ground_truth.zip polygons.shp polygons.dbf polygons.shx polygons.prj
```

### Uploading Ground Truth

1. Drag and drop the `.zip` onto the upload zone (or click to browse)
2. The shapefile polygons appear as **red outlines** on the satellite panel (panel 2) with hover tooltips showing class labels
3. The **Class field** dropdown is populated with the shapefile's attribute columns — each shows the number of unique values and sample entries to help you pick the right one

### Running an Evaluation (Viewport Mode)

1. Select a **Class field** — this determines what's being classified (e.g. broad habitat groups vs fine-grained types). The summary shows the number of classes and sample values.
2. Check the **classifiers** you want to compare:
   - **k-NN** — k-Nearest Neighbours (default k=5, Euclidean distance)
   - **RF** — Random Forest (default 100 trees)
   - **XGBoost** — Gradient boosted trees (default 100 rounds, max depth 6)
   - **MLP** — Multi-layer perceptron (default 64-32 hidden layers)
   - **Spatial MLP (3x3)** — MLP that uses a 3x3 neighbourhood of embeddings as input, capturing local spatial context
   - **Spatial MLP (5x5)** — same idea with a 5x5 neighbourhood for wider spatial context
   - **U-Net (GPU)** — a convolutional U-Net trained on the full 2D embedding grid (requires PyTorch/GPU)
3. *(Optional)* Click the **`...`** button next to any classifier to expand its hyperparameters:
   - **k-NN**: k (1–50), weights (uniform or distance-weighted)
   - **Random Forest**: number of trees (10–500), max depth (leave empty for unlimited)
   - **XGBoost**: boosting rounds (10–500), max depth (1–15), learning rate (0.01–1.0)
   - **MLP**: hidden layer architecture (64,32 / 128,64 / 256,128,64), max iterations (50–1000)
   - **Spatial MLP**: hidden layer architecture, max iterations (same options as MLP)
   - **U-Net**: epochs (5–500), learning rate (0.0001–0.1), depth (3/4/5), base filters (16/32/64)
4. *(Optional)* Adjust **Max training pixels** (default 10,000). Increase this if your ground truth has dense coverage — training sizes are log-spaced from 10 up to this value (e.g. 30,000 gives sizes 10, 30, 100, 300, 1000, 3000, 10000, 30000).
5. Click **Run Evaluation** — the server trains each classifier at each training size with 5 random repeats. An elapsed timer shows progress. Typical runtime is 60–120 seconds for 4 classifiers at the default max.

### Running an Evaluation (Large Area Mode)

Large Area mode is designed for shapefiles that cover areas much larger than a single viewport — counties, national parks, or even entire countries. Instead of using pre-extracted viewport vectors, it loads embeddings tile-by-tile from GeoTessera and then runs the same learning curve as Viewport mode.

1. Click **Large Area** at the top of Panel 6
2. Upload a shapefile as usual (drag-and-drop or click)
3. Select a **Class field** — TEE automatically detects whether the field is classification (text or few unique values) or regression (numeric with many unique values)
4. Check the **classifiers** you want to use. Spatial classifiers (3×3, 5×5) and U-Net are not available in Large Area mode — only pixel-level classifiers: k-NN, RF, XGBoost, MLP
5. Select the **year** for the embeddings
6. *(Optional)* Adjust **Max training samples** — caps the largest training size in the learning curve
7. Click **Run Evaluation** — progress shows tile download count, then the learning curve streams results as in Viewport mode

**Additional buttons:**

- **Generate Config** — downloads a JSON config file you can use with the CLI script for headless batch evaluation on a compute node
- **Load Results** — load a pre-computed `.ndjson` results file (e.g. from a CLI run) and replay the events into the viewer panels

### Regression Support

When the selected field is numeric with more than 20 unique values, TEE automatically switches to regression mode:

- Panel 4 shows a **bar chart of R² scores** per model (instead of learning curves)
- Panel 5 shows a **regression metrics table** with R² ± std, RMSE ± std, and MAE ± std for each model (instead of a confusion matrix)
- Available regressors: k-NN Regressor, Random Forest Regressor, XGBoost Regressor, MLP Regressor

### CLI for Headless Evaluation

For batch processing on compute nodes (no browser needed), use the standalone CLI:

```bash
python scripts/tee_evaluate.py --config eval_config.json
```

The CLI reads a JSON config file specifying the shapefile, fields, classifiers/regressors, years, and k-fold settings. It outputs NDJSON progress events to stdout and writes result files to the output directory.

**Dry run** (print stats without downloading or evaluating):
```bash
python scripts/tee_evaluate.py --config eval_config.json --dry-run
```

**Config file format:**
```json
{
  "$schema": "tee_evaluate_config_v1",
  "shapefile": "/path/to/ground_truth.zip",
  "fields": [
    { "name": "UKHab_L2", "type": "auto" },
    { "name": "carbon_tCO2", "type": "auto" }
  ],
  "classifiers": { "nn": {}, "rf": { "n_estimators": 100 } },
  "regressors": { "rf_reg": {}, "mlp_reg": { "hidden_layers": "64,32" } },
  "years": [2024],
  "max_training_samples": 30000,
  "output_dir": "./eval_output",
  "seed": 42
}
```

Fields with `"type": "auto"` are automatically detected as classification or regression. You can also set `"type": "classification"` or `"type": "regression"` explicitly. Multiple fields are evaluated independently in one run.

**Three usage modes:**

| Mode | Description |
|------|-------------|
| **Fully headless** | `python scripts/tee_evaluate.py --config eval.json` — NDJSON to stdout, results to output_dir |
| **Viewer-launched** | Click "Run" in the viewer — backend streams NDJSON to the browser |
| **Load pre-computed** | Run CLI on a remote node, then click "Load Results" in the viewer to replay the `.ndjson` file |

### Learning Curve Chart

Results appear as a line chart in the centre panel:

- **X axis**: number of training pixels (log scale)
- **Y axis**: F1 score (0–1)
- One line per classifier with shaded ±1 standard deviation bands
- Use the **Macro F1 / Weighted F1** dropdown (top-right of chart) to switch metrics:
  - **Macro F1** — unweighted average across classes (treats all classes equally)
  - **Weighted F1** — weighted by class frequency (reflects overall accuracy better when classes are imbalanced)
- Expect F1 to rise from ~0.1 at 10 pixels to ~0.5–0.7 at 10,000 pixels

### Confusion Matrix

After the evaluation finishes, the right panel shows a **confusion matrix** for the largest training size:

- Rows are the **true** class, columns are the **predicted** class
- Cells are colour-coded: diagonal (correct predictions) in blue, off-diagonal (errors) in red
- Use the **classifier dropdown** to switch between classifiers
- Click the **%** button to toggle between raw pixel counts and row-normalised percentages
- A note at the top lists any classes that were excluded for having fewer than 50 labelled pixels

### Exporting Results

Two export options are available in the confusion matrix header:

- **Export Results** — downloads the full evaluation data as a JSON file (training sizes, F1 scores, confusion matrices, class info)
- **Download Models** — downloads `.joblib` files for each trained classifier. After the evaluation, each classifier is retrained on **all** labelled data and saved. Each `.joblib` file contains a dict with:
  - `model` — the fitted sklearn/xgboost classifier object
  - `class_names` — list of class name strings (matching the prediction order)

  You can load a downloaded model in Python:
  ```python
  import joblib
  d = joblib.load("rf_model.joblib")
  clf = d["model"]          # the trained classifier
  names = d["class_names"]  # e.g. ["Grassland", "Urban", "Woodland"]
  predictions = clf.predict(embeddings)  # embeddings shape: (N, 128)
  predicted_names = [names[i] for i in predictions]
  ```

### Interpreting Results

- **Steeper curves** indicate embeddings that separate classes well even with few training samples
- **RF and XGBoost** typically outperform k-NN at small sample sizes
- **MLP** may need more training data to converge (it has more parameters to learn)
- Classes with fewer than 50 labelled pixels are automatically excluded
- At large training sizes, rare classes contribute fewer samples (capped at 80% of their count) to ensure test data remains available

### Notes

- **Viewport mode** uses the current viewport's pre-extracted embeddings as features. **Large Area mode** loads embeddings directly from GeoTessera tiles
- All computation runs server-side; runtime scales with max training pixels and number of classifiers
- Changing the class field updates the polygon hover tooltips on the satellite panel
- Re-running with different hyperparameters or a different field does not require re-uploading the shapefile
- In Viewport mode, downloaded models are trained on **all** labelled pixels (not a train/test split), so they represent the best possible fit for deployment
- Large Area mode uses the same learning curve as Viewport mode — train on N pixels, test on the remainder
- Spatial classifiers (3×3, 5×5) are not available in Large Area mode because neighbourhood context doesn't work across tile boundaries — only pixel-level classifiers are supported
- Large Area tile-by-tile processing is memory-bounded: only one tile's embeddings are in memory at a time, with labelled pixels accumulated incrementally

## Running Evaluation on Your Own Machine

By default, all ML evaluation runs on the hosted server (tee.cl.cam.ac.uk). As the number of users grows, you may want to run compute locally on your laptop or on a dedicated GPU server. The `tee-compute` command makes this easy — it runs the evaluation locally and proxies everything else (UI, tiles, label sharing) to the hosted server.

### How It Works

```
Browser → http://localhost:8001
              │
              ├── /api/evaluation/*  → handled locally (ML on your machine)
              └── everything else    → proxied to tee.cl.cam.ac.uk
```

From the browser's perspective, nothing changes — you just open `localhost:8001` instead of `tee.cl.cam.ac.uk`. The compute server handles ML evaluation locally and transparently forwards all other requests (map tiles, satellite imagery, label sharing, viewport configs) to the hosted server.

### Deployment Modes

| Mode | Description | What you run |
|------|-------------|-------------|
| **Hosted (default)** | Everything on tee.cl.cam.ac.uk | Nothing — just open the website |
| **Local compute** | ML on your laptop, data from hosted server | `tee-compute` on your laptop |
| **Remote compute** | ML on a GPU server, data from hosted server | `tee-compute` on GPU box + SSH tunnel from laptop |
| **All local** | Everything on your laptop (Django server) | `python manage.py runserver` (developer mode) |

### Local Compute Setup

Run ML evaluation on your laptop while using the hosted server for everything else.

**One-time setup:**

```bash
pip install tessera-eval[server]
```

This installs the `tee-compute` command along with all ML dependencies (scikit-learn, geopandas, geotessera, etc.).

**Each session:**

```bash
tee-compute
```

Then open `http://localhost:8001` in your browser. You'll see the same TEE interface, but evaluation runs on your machine.

By default, `tee-compute` proxies to `https://tee.cl.cam.ac.uk`. To use a different hosted server:

```bash
tee-compute --hosted https://your-tee-server.example.com
```

### Remote Compute Setup (GPU Server)

Run ML evaluation on a remote GPU server while browsing from your laptop.

**One-time setup on the GPU server:**

1. Copy your SSH public key to the server (if not already done):
   ```bash
   ssh-copy-id gpu-box
   ```

2. Install tessera-eval on the server:
   ```bash
   ssh gpu-box 'pip install tessera-eval[server]'
   ```

**Each session (one command from your laptop):**

```bash
ssh -L 8001:localhost:8001 gpu-box 'tee-compute'
```

This starts `tee-compute` on the GPU server and creates an SSH tunnel so `localhost:8001` on your laptop reaches it. Open `http://localhost:8001` in your browser.

**Tip:** Create an alias in your `~/.zshrc` or `~/.bashrc`:

```bash
alias tee='ssh -L 8001:localhost:8001 gpu-box "tee-compute"'
```

Then just type `tee` to start a session.

### What Runs Where

| Component | Hosted server | Your machine |
|-----------|:------------:|:------------:|
| Map tiles (OSM, satellite) | ✓ | |
| Embedding tile images | ✓ | |
| Image pyramids | ✓ | |
| Label sharing | ✓ | |
| Viewport management | ✓ | |
| Shapefile upload | | ✓ |
| Embedding download (GeoTessera) | | ✓ |
| ML training + evaluation | | ✓ |
| Model download | | ✓ |

Embeddings are fetched directly from GeoTessera (`dl2.geotessera.org`) by the compute server — they do not pass through the hosted TEE server.

### Command Reference

```
tee-compute [OPTIONS]

Options:
  --hosted URL    Hosted TEE server URL (default: https://tee.cl.cam.ac.uk)
  --port PORT     Local port to serve on (default: 8001)
  --host HOST     Host to bind to (default: 127.0.0.1)
  --debug         Enable Flask debug mode with auto-reload
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Connection refused` on localhost:8001 | Is `tee-compute` running? Check the terminal for errors. |
| `Cannot reach hosted server` | Check your internet connection. Try `curl https://tee.cl.cam.ac.uk/health`. |
| `No GeoTessera tiles found` | The selected year may not have coverage for your area. Try year 2025 (has wider coverage). |
| `ModuleNotFoundError: geotessera` | Install with server extras: `pip install tessera-eval[server]` |
| SSH tunnel disconnects | Add `-o ServerAliveInterval=60` to your SSH command. |
| Port 8001 already in use | Either stop the other process, or use `--port 8002` (and open `localhost:8002`). |

## Export Options

### Header Export (Explore / Auto-label modes)

The **Export** dropdown in the header provides three formats for saved labels:

| Format | Description |
|--------|-------------|
| **Labels (JSON)** | Compact metadata for re-importing into TEE |
| **Labels (GeoJSON)** | FeatureCollection with 10m polygons per pixel, compatible with QGIS and other GIS tools. Properties include label name, color, distance, and threshold. |
| **Map (JPG)** | High-resolution satellite image with label overlays and legend, rendered at zoom level 18 |

### Manual Label Export (Labelling mode)

In manual label mode, the **Export** button in the labelling toolbar provides:

| Format | Description |
|--------|-------------|
| **JSON** | Compact metadata including embeddings and thresholds, for re-importing into TEE |
| **GeoJSON** | Points and polygons as a FeatureCollection, compatible with QGIS and GIS tools |
| **ESRI Shapefile (ZIP)** | Zipped `.shp`/`.dbf`/`.shx`/`.prj` — the standard GIS interchange format. Can be shared with others and also uploaded back into TEE's Validation mode as ground-truth training data. |

## Sharing Labels

TEE supports two sharing modes, accessed via the **Share** button in the manual label toolbar:

- **Private** — sends only embedding vectors and label metadata (no geographic coordinates) to the Tessera global habitat directory. This contributes to improving Tessera's habitat classification without revealing your study site locations. Private shares are invisible to other users.
- **Public** — uploads a full ESRI Shapefile (with geolocations) that other users on the same server can browse and import for the same viewport. Public shares appear in the **Import** dropdown for anyone viewing that viewport.

To share labels, set up your manual labels, click **Share**, choose Private or Public, fill in your name/email/organization, and click Submit. To import shared labels from other users, click **Import** and select from the list of available public shares.

## PCA / UMAP Visualization (Panel 4)

In the 6-panel layout, Panel 4 shows a 3D projection of the embedding space:

- **PCA** — computed instantly in your browser from the downloaded vectors (no server round-trip). Available as soon as vectors are downloaded.
- **UMAP** — richer structure, computed server-side on first load (~1-2 min)
- Points are colored using satellite RGB values
- Single-click a point to place a marker on all map panels; double-click to trigger a similarity search from that point
- Toggle between PCA and UMAP using the dropdown
- Right-click drag to rotate the 3D view

## Heatmap (Panel 5)

The temporal distance heatmap shows pixel-by-pixel embedding differences between Y1 and Y2:

- Bright areas indicate large changes between the two years
- Dark areas indicate stability
- Useful for detecting land use change, deforestation, construction, etc.

## Mouse Controls

| Action | Control |
|--------|---------|
| Pan | Click and drag |
| Zoom | Scroll wheel, or +/- buttons |
| Place marker | Single-click on any panel |
| Similarity search | Double-click on any panel |
| Adjust similarity | Drag the similarity slider |
| Drop pin (manual mode) | Ctrl+click (or double-click) |
| Draw polygon (manual mode) | Ctrl+double-click, then click vertices |
| Cancel polygon | Escape |
| Rotate (PCA/UMAP) | Right-click drag |

## Tips

- **Processing time** is roughly the same whether you select 1 year or 8 years — all years download and process in parallel
- **Features appear incrementally** — the viewer becomes usable as soon as pyramids are built, even before vector extraction completes
- **Privacy** — all similarity search and labeling runs locally in your browser. Only tile images are fetched from the server
- **Storage** — each viewport uses ~5GB depending on the number of years processed
