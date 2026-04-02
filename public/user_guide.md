# TEE User Guide

A guide to using the Tessera Embeddings Explorer (TEE) web interface.

**Privacy by design:** Your ground-truth labels and evaluation results stay private. All ML evaluation runs on your own machine (or a compute server you control) — never on the hosted TEE server. Similarity searches and labelling run entirely in your browser. The only data shared with the server is map tile requests and explicit label sharing (opt-in).

## The Viewport Manager

The **Viewport Manager** is the home page of TEE. It has three tabs:

- **Viewports** — create, manage, and open viewports
- **Validation** — evaluate classifiers on ground-truth shapefiles at any scale
- **Users** — manage user accounts (admin only)

### Viewports Tab

A **viewport** is a 5km x 5km geographic area for which TEE downloads and processes Sentinel-2 embeddings.

The Viewports tab shows:
1. **Search** — filter viewports by name
2. **Active viewport** — the currently selected viewport
3. **Create New Viewport** — click to expand the creation form
4. **Export / Import** — bulk export all your viewports or import from a file
5. **Viewport list** — grouped by creator. Your viewports show all actions (Open, + Year, Export, Delete). Other users' public viewports show only Open.

### Creating a Viewport

1. Click **+ Create New Viewport** to expand the form
2. Choose a location:
   - **Search** — type a place name (e.g. "Cambridge") or coordinates (e.g. "52.2, 0.12")
   - **Click the map** — a 5km preview box follows the cursor; click to place it
   - **Reposition** — click again to move the box
3. Enter a **viewport name** and select which **years** to process (2018–2025). Years without GeoTessera coverage for your area are greyed out.
4. Click **Create** — processing runs in the background with a progress bar
5. The viewer opens automatically when processing completes

Only the creator of a viewport can delete it.

### Validation Tab

Click **Open Validation** to launch the viewer in validation mode. Evaluation runs locally on your machine — no labels leave your desktop. For GPU servers, see [Remote Compute Setup](#remote-compute-setup-gpu-server).

### Users Tab

Visible to administrators only. Create accounts, set quotas, and manage enrolled users.

## The Viewer

The viewer has three modes, selected from the **layout dropdown** in the header:

- **Explore** (default) — browse embeddings and run similarity searches
- **Change Detection** — compare two years side by side
- **Labelling** — build label sets via K-means or manual pins/polygons

**Validation** mode is accessed from the Viewport Manager's Validation tab, not from the viewer dropdown.

All modes use a 6-panel synchronized grid — panning or zooming one panel pans/zooms all.

### Explore (default)

| Panel | Content |
|-------|---------|
| 1 | **OpenStreetMap** — geographic reference |
| 2 | **Satellite** — Esri or Google imagery |
| 3 | **Tessera Embeddings** — embedding visualization with year selector |
| 4 | **PCA (Embedding Space)** — 3D scatter plot (PCA or UMAP) |
| 5–6 | (available in other modes) |

Double-click any pixel to run a similarity search.

### Change Detection

| Panel | Content |
|-------|---------|
| 1 | **OpenStreetMap** |
| 2 | **Satellite** |
| 3 | **Tessera Embeddings** (Y1) |
| 4 | **Change Distribution** — histogram of embedding distance between Y1 and Y2 |
| 5 | **Change Heatmap** — bright = changed, dark = stable |
| 6 | **Tessera Embeddings** (Y2) |

Select different years for Y1 and Y2 using the year dropdowns.

### Labelling

| Panel | Content |
|-------|---------|
| 1 | **OpenStreetMap** |
| 2 | **Satellite** with label overlays |
| 3 | **Tessera Embeddings** |
| 4 | **PCA (Embedding Space)** — colored by label classes |
| 5 | **Segmentation** / **Classification** |
| 6 | **Auto-label** / **Manual Label** — label management |

Panel 6 has a sub-mode dropdown: **Auto-label** (K-means clusters) or **Manual Label** (pins, polygons, similarity expansion).

### Validation

Accessed from the Viewport Manager's **Validation** tab (opens viewer with `?mode=validation`).

| Panel | Content |
|-------|---------|
| 1 | **Controls** — shapefile upload, field/year/classifier selection, Run button |
| 2 | **Satellite** — with ground-truth polygon overlay (red outlines) |
| 3 | **Ground Truth** — class table with polygon/pixel counts |
| 4 | **Progress** — status messages and results table during evaluation |
| 5 | **Learning Curves** — streaming chart (% labels vs F1 score) |
| 6 | **Confusion Matrix** — with classifier selector and % toggle |

### Switching Years

Use the **year dropdown** above the embedding panels. In Change Detection mode, Y1 and Y2 can be set to different years.

## Similarity Search

**Double-click** anywhere on the map to trigger a similarity search. TEE extracts the 128-dimensional embedding at that pixel and finds all similar locations across the viewport. All computation runs **locally in your browser** — no data is sent to the server.

### How to Use

1. **Double-click any pixel** — similar locations are highlighted as colored dots
2. Adjust the **similarity slider** to control match strictness
3. Click **Save as Label** to name and color-code the results

The first time you search for a viewport+year, vector data (~20–50MB) is downloaded and cached in your browser's IndexedDB. Subsequent searches are instant.

### Single-Click

A **single click** places a synchronized marker across all panels, useful for cross-referencing a location without triggering a search.

## Labels

Labels are named, colored collections of similar pixels found through similarity search.

### Managing Labels

- **Save** — after a similarity search, click "Save as Label"
- **Toggle visibility** — click a label name to show/hide
- **Delete** — click the X next to a label
- **Labels persist** across page reloads (stored in browser localStorage)

### Cross-Year Timeline

Click **Timeline** on any saved label to see how coverage changes across years:
- Bar chart of pixel counts per year
- Percentage change summary between first and last years
- Each year's vector data loads from cache or downloads automatically

### Importing and Exporting Labels

Labels are portable — they use embedding distance rather than coordinates, so they work across different viewports.

- **Import** — use the Import button to load a previously exported JSON file
- **Export** — see [Export Options](#export-options)

## Manual Labelling

Manual labelling lets you build label classes by hand — placing pins, drawing polygons, or combining both.

### Entering Manual Mode

1. Switch the layout to **Labelling** (header dropdown)
2. In Panel 6, change the mode from **Auto-label** to **Manual Label**

### Setting a Label

1. Type a **label name** (e.g. "Woodland")
2. Click the **color swatch** to choose a color
3. Click **Set**

**Schema support:** Click **Schema** in the header to load a classification scheme (UKHab v2, HOTW, or custom).

### Placing Point Labels (Pins)

**Ctrl+click** (Cmd+click on Mac) to drop a pin. The pin extracts the 128D embedding and appears in the Manual Labels panel.

### Drawing Polygon Labels

**Ctrl+double-click** to start drawing a polygon. Click to place vertices, close by clicking the first vertex or double-clicking. Press **Escape** to cancel.

#### Polygon Search Mode

- **Mean** (default) — stores the average embedding of all pixels inside the polygon
- **Union** — stores every individual pixel embedding (better for heterogeneous areas)

### Similarity Threshold

Each label entry has a **similarity slider** (0–500) controlling how far the search extends from the pin/polygon embedding.

### Classification (Panel 5)

Click **Classify** to generate a full-viewport classification — each pixel is assigned to the nearest label class based on embedding distance.

### Tips

- **Combine pins and polygons** — polygons for large homogeneous areas, pins for scattered features
- **Start with threshold = 0** — place pins, then increase the slider to expand coverage
- **Check Panel 4** — the PCA/UMAP view shows whether labels form coherent clusters
- **Labels persist** across page reloads

## Auto-Labelling (K-Means Clustering)

TEE segments the viewport into clusters using K-means on the embedding space. Clusters are **temporary previews** until promoted to saved labels.

### Running Segmentation

1. Set **k** using the slider (2–20)
2. Click **Segment** — results appear as a colored overlay
3. The cluster list shows each cluster's color, pixel count, and percentage

### Promoting Clusters to Labels

- **Promote one** — click the ↗ button on a cluster row
- **Promote all** — click **Promote All to Labels**
- **Name before promoting** — type a label name next to the cluster first

### Suggested Labelling Workflow

1. **Auto-label** with k = 5 or higher
2. **Review** clusters on the heatmap and embedding panels
3. **Merge** related clusters by giving them the same name before promoting
4. **Fine-tune** with manual pins and similarity sliders

## Validation (Learning Curves)

The Validation mode evaluates how well classifiers distinguish habitat classes using Tessera embeddings, given expert ground-truth polygons. It works at any scale — from a single viewport to an entire country.

All ML evaluation runs on a **compute server** (`tee-compute`), not on the hosted TEE server. See [Running Evaluation on Your Own Machine](#running-evaluation-on-your-own-machine) for setup.

### Accessing Validation

Click the **Validation** tab in the Viewport Manager, then click **Open Validation**. This opens the viewer in validation mode.

### Preparing a Ground-Truth Shapefile

TEE accepts a **zipped ESRI shapefile** (`.zip` containing `.shp`, `.dbf`, `.shx`, `.prj`). Each polygon needs an attribute column with class labels (e.g. "Woodland", "Grassland"). Any CRS is accepted — TEE reprojects automatically.

```bash
zip ground_truth.zip polygons.shp polygons.dbf polygons.shx polygons.prj
```

### Uploading Ground Truth

1. Drag and drop the `.zip` onto the upload zone in Panel 1 (or click to browse)
2. Polygons appear as **red outlines** on the satellite panel
3. The **Class field** dropdown shows available columns with class counts
4. The satellite map zooms to the shapefile's extent

### Running an Evaluation

1. Select a **Class field** — TEE auto-detects classification vs regression
2. Select the **year** for embeddings (years without coverage are greyed out)
3. Check the **classifiers** to compare:
   - **k-NN** — k-Nearest Neighbours
   - **RF** — Random Forest
   - **XGBoost** — Gradient boosted trees
   - **MLP** — Multi-layer perceptron
   - **Spatial MLP (3×3 / 5×5)** — MLP with neighbourhood context
   - **U-Net (GPU)** — convolutional U-Net on 256×256 patches (requires PyTorch)
4. *(Optional)* Expand hyperparameters with the **`...`** button
5. *(Optional)* Adjust **Max training samples**
6. Click **Run Evaluation**

TEE samples up to 200,000 random points within the shapefile polygons (stratified by class), fetches embeddings from GeoTessera, and runs the learning curve. For spatial classifiers and U-Net, 256×256 2D patches are sampled via point grids.

### Learning Curve Chart

Results appear as a line chart in Panel 5:

- **X axis**: % of labelled area used for training (1–80%)
- **Y axis**: F1 score (0–1)
- One line per classifier with shaded ±1 std bands
- Use the **Macro F1 / Weighted F1** dropdown to switch metrics

### Confusion Matrix

Panel 6 shows a confusion matrix for the largest training percentage:

- Rows = true class, columns = predicted class
- Color-coded: green diagonal (correct), red off-diagonal (errors)
- **%** button toggles counts vs percentages
- **View** button opens a full-size modal for large matrices
- **Classifier dropdown** to switch between classifiers

### Downloading Trained Models

Click **Download Models** in the header bar. This triggers model training on your compute server (deferred from the evaluation run for speed), then downloads `.joblib` files for each classifier.

```python
import joblib
d = joblib.load("rf_model.joblib")
clf = d["model"]          # the trained classifier
names = d["class_names"]  # e.g. ["Grassland", "Urban", "Woodland"]
predictions = clf.predict(embeddings)  # embeddings shape: (N, 128)
```

### Regression Support

When the selected field is numeric with >20 unique values, TEE switches to regression mode — showing R², RMSE, and MAE instead of F1 and confusion matrices.

### CLI for Headless Evaluation

For batch processing without a browser:

```bash
python scripts/tee_evaluate.py --config eval_config.json
```

**Dry run** (stats only, no evaluation):
```bash
python scripts/tee_evaluate.py --config eval_config.json --dry-run
```

### Notes

- All evaluation runs on your compute server, never the hosted server
- Embeddings are sampled via GeoTessera's point API — no full tiles loaded
- Re-running with different classifiers reuses cached data (instant)
- The Back button is disabled during evaluation to prevent data loss
- Spatial MLP and U-Net use 2D patches sampled as point grids (~33MB each vs ~450MB for full tiles)

## Running Evaluation on Your Own Machine

All ML evaluation runs on a compute server (`tee-compute`). The hosted TEE server does not run ML.

### How It Works

```
Browser → http://localhost:8001
              │
              ├── /api/evaluation/*  → handled locally (ML on your machine)
              └── everything else    → proxied to tee.cl.cam.ac.uk
```

You open `localhost:8001`. The compute server handles ML evaluation locally and forwards all other requests to the hosted server.

### Deployment Modes

| Mode | What you run |
|------|-------------|
| **Hosted (default)** | Nothing — just open the website |
| **Local compute** | `tee-compute` on your laptop |
| **Remote compute** | `tee-compute` on GPU box + SSH tunnel |
| **All local** | Django + tee-compute via `restart.sh` |

### Local Compute Setup

**One-time setup:**

```bash
pip install tessera-eval[server]
```

**Each session:**

```bash
tee-compute
```

Open `http://localhost:8001`. By default, proxies to `https://tee.cl.cam.ac.uk`.

### Remote Compute Setup (GPU Server)

Run ML on a remote GPU server while browsing from your laptop.

**One-time setup on the GPU server:**

1. Copy your SSH public key:
   ```bash
   ssh-copy-id gpu-box
   ```

2. Install tessera-eval:
   ```bash
   ssh gpu-box 'pip install tessera-eval[server]'
   ```

**Each session (one command from your laptop):**

```bash
ssh -L 8001:localhost:8001 gpu-box 'tee-compute'
```

Open `http://localhost:8001`.

**Tip:** Create an alias:

```bash
alias tee='ssh -L 8001:localhost:8001 gpu-box "tee-compute"'
```

### What Runs Where

| Component | Hosted server | Local compute | GPU server (via SSH) |
|-----------|:------------:|:------------:|:-------------------:|
| Map tiles, satellite imagery | ✓ | | |
| Embedding tile images | ✓ | | |
| Label sharing | ✓ | | |
| Viewport management | ✓ | | |
| Shapefile upload | | ✓ | ✓ |
| Embedding sampling (GeoTessera) | | ✓ | ✓ |
| ML training + evaluation | | ✓ | ✓ |
| Model download | | ✓ | ✓ |

### Command Reference

```
tee-compute [OPTIONS]

Options:
  --hosted URL    Hosted TEE server URL (default: https://tee.cl.cam.ac.uk)
  --port PORT     Local port (default: 8001)
  --host HOST     Bind address (default: 127.0.0.1)
  --debug         Flask debug mode with auto-reload
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Connection refused` on localhost:8001 | Is `tee-compute` running? |
| `Cannot reach hosted server` | Check internet. Try `curl https://tee.cl.cam.ac.uk/health`. |
| `No GeoTessera tiles found` | Try year 2025 (wider coverage). |
| `ModuleNotFoundError: geotessera` | `pip install tessera-eval[server]` |
| SSH tunnel disconnects | Add `-o ServerAliveInterval=60` to SSH command. |
| Port 8001 in use | Use `--port 8002`. |

## Export Options

### Header Export (Explore / Auto-label modes)

| Format | Description |
|--------|-------------|
| **Labels (JSON)** | Compact metadata for re-importing into TEE |
| **Labels (GeoJSON)** | FeatureCollection with 10m polygons, compatible with QGIS |
| **Map (JPG)** | High-resolution satellite image with label overlays and legend |

### Manual Label Export (Labelling mode)

| Format | Description |
|--------|-------------|
| **JSON** | Compact metadata with embeddings, for re-importing |
| **GeoJSON** | Points and polygons as a FeatureCollection |
| **ESRI Shapefile (ZIP)** | Standard GIS format — can be uploaded back as ground truth |

## Sharing Labels

Two sharing modes via the **Share** button in the labelling toolbar:

- **Private** — sends only embedding vectors (no coordinates) to the Tessera global habitat directory
- **Public** — uploads a full ESRI Shapefile that other users can browse and import

## PCA / UMAP Visualization (Panel 4)

- **PCA** — computed instantly in your browser
- **UMAP** — richer structure, computed server-side (~1–2 min)
- Points colored using satellite RGB values
- Click to place markers; double-click for similarity search
- Right-click drag to rotate

## Heatmap (Panel 5)

Temporal distance heatmap showing pixel-by-pixel embedding differences between Y1 and Y2. Bright = changed, dark = stable.

## Data Privacy

| What | Where it runs | Server sees |
|------|--------------|-------------|
| Similarity search | Your browser | Nothing |
| Labelling | Your browser | Nothing |
| ML evaluation | Your compute server | Nothing |
| Model download | Your compute server | Nothing |
| Label sharing | Hosted server | Only when you click Share |
| Map tiles | Hosted server | Standard tile requests |

Ground-truth shapefiles and evaluation results **never** leave your machine.

## Mouse Controls

| Action | Control |
|--------|---------|
| Pan | Click and drag |
| Zoom | Scroll wheel, or +/- buttons |
| Place marker | Single-click |
| Similarity search | Double-click |
| Adjust similarity | Drag the similarity slider |
| Drop pin (manual mode) | Ctrl+click (or double-click) |
| Draw polygon (manual mode) | Ctrl+double-click, then click vertices |
| Cancel polygon | Escape |
| Rotate (PCA/UMAP) | Right-click drag |

## Tips

- **Processing time** is roughly the same for 1 year or 8 — all download in parallel
- **Features appear incrementally** — the viewer becomes usable as soon as pyramids are built
- **Privacy** — similarity search and labelling run in your browser; evaluation runs on your compute server
- **Storage** — each viewport uses ~5GB depending on years processed
