# TEE User Guide

## What is TEE?

TEE (Tessera Embeddings Explorer) is a web-based tool for exploring, labelling, and evaluating Sentinel-2 satellite embeddings. With TEE you can:

- **Explore** any 5km × 5km area on Earth using 128-dimensional Tessera embeddings (2018–2025)
- **Find similar pixels** instantly — double-click anywhere to highlight similar locations across the viewport
- **Label habitats** using K-means clustering, manual pins, or polygon drawing
- **Evaluate classifiers** on ground-truth shapefiles at any scale — from a single viewport to an entire country
- **Compare years** side by side to detect land-use change

> **Privacy by design:** Similarity searches and labelling run entirely in your browser. ML evaluation runs on your own machine. Ground-truth data never leaves your desktop. The hosted server only serves map tiles and satellite imagery.

---

## Quick Start

### Path 1: Explore a location

1. Open TEE → **Viewports** tab → **Create New Viewport**
2. Search for a place or click the map → select years → **Create**
3. When processing completes, click **Open** → double-click any pixel to find similar locations

### Path 2: Label habitats

1. Open a viewport → switch to **Labelling** mode (header dropdown)
2. Click **Segment** to run K-means → review clusters → **Promote** good ones to labels
3. Fine-tune with manual pins (Ctrl+click) and polygons (Ctrl+double-click)
4. **Export** as Shapefile for use in GIS or as ground truth for validation

### Path 3: Evaluate classifiers

1. In the Viewport Manager → **Validation** tab → **Open Validation**
2. Upload a ground-truth `.zip` shapefile → select a class field and year
3. Check classifiers (k-NN, RF, XGBoost, MLP) → **Run Evaluation**
4. View learning curves, confusion matrix, and download trained models

---

## The Viewport Manager

The **Viewport Manager** is the home page. It has three tabs:

| Tab | Purpose |
|-----|---------|
| **Viewports** | Create, manage, and open viewports |
| **Validation** | Evaluate classifiers on ground-truth shapefiles |
| **Users** | Manage user accounts (admin only) |

<!-- Screenshot: viewport_manager.png — Viewport Manager showing the three tabs, viewport list grouped by creator, and the map -->

### Viewports Tab

A **viewport** is a 5km × 5km area for which TEE downloads and processes Sentinel-2 embeddings. The tab shows:

1. **Search** — filter viewports by name
2. **Active viewport** — the currently selected viewport
3. **Create New Viewport** — click to expand the creation form
4. **Export / Import** — bulk operations on your viewports
5. **Viewport list** — grouped by creator; your viewports show all actions (Open, +Year, Export, Delete); others' viewports show only Open

> **Note:** Only the creator of a viewport can delete it. Years without GeoTessera coverage for your area are greyed out in the year selector.

### Validation Tab

Click **Open Validation** to launch the viewer in validation mode. Evaluation runs locally — no labels leave your machine. For GPU servers, see [Remote Compute Setup](#remote-compute-setup-gpu-server).

### Users Tab

Visible to administrators only. Create accounts, set quotas, and manage enrolled users.

---

## The Viewer

The viewer uses a **6-panel synchronized grid** — panning or zooming one panel pans/zooms all. Three modes are available from the **layout dropdown** in the header. **Validation** mode is accessed from the Viewport Manager's Validation tab.

<!-- Screenshot: viewer_explore.png — Viewer in Explore mode showing all 6 panels -->

### Panel Layout by Mode

```
┌──────────────┬──────────────┬──────────────┐
│   Panel 1    │   Panel 2    │   Panel 3    │
│              │              │              │
├──────────────┼──────────────┼──────────────┤
│   Panel 4    │   Panel 5    │   Panel 6    │
│              │              │              │
└──────────────┴──────────────┴──────────────┘
```

| Panel | Explore | Change Detection | Labelling | Validation |
|:-----:|---------|-----------------|-----------|------------|
| 1 | OpenStreetMap | OpenStreetMap | OpenStreetMap | **Controls** |
| 2 | Satellite | Satellite | Satellite + labels | Satellite + polygons |
| 3 | Embeddings | Embeddings (Y1) | Embeddings | **Ground Truth classes** |
| 4 | PCA / UMAP | Change Distribution | PCA / UMAP | **Progress / results** |
| 5 | — | Change Heatmap | Classification | **Learning Curves** |
| 6 | — | Embeddings (Y2) | Label management | **Confusion Matrix** |

### Switching Modes

Use the **layout dropdown** in the header for Explore, Change Detection, and Labelling. Validation is accessed from the Viewport Manager's **Validation** tab.

### Switching Years

Use the **year dropdown** above the embedding panels. In Change Detection mode, Y1 and Y2 can be set independently.

---

## Similarity Search

<!-- Screenshot: similarity_search.png — Showing colored dots across panels after a double-click search -->

**Double-click** anywhere on any panel to find similar pixels across the viewport. TEE extracts the 128-dimensional embedding at that pixel and computes distances to all other pixels — entirely in your browser.

| Step | Action |
|------|--------|
| 1 | **Double-click** any pixel |
| 2 | Adjust the **similarity slider** — lower = stricter matching |
| 3 | Click **Save as Label** to keep the results |

> **First search** downloads vector data (~20–50MB) and caches it in your browser. Subsequent searches are instant.

A **single click** (without double) places a synchronized marker across all panels — useful for cross-referencing locations without triggering a search.

---

## Labels

Labels are named, colored collections of similar pixels.

| Action | How |
|--------|-----|
| Save | After a search, click **Save as Label** |
| Show/hide | Click a label name |
| Delete | Click the **×** next to a label |
| Timeline | Click the **clock icon** — see coverage across all years |
| Import | Click **Import** → select a JSON file |
| Export | See [Export Options](#export-options) |

Labels persist across page reloads (stored in browser localStorage) and are portable across viewports.

---

## Manual Labelling

Build label classes by hand — placing pins, drawing polygons, or combining both.

### Getting Started

1. Switch to **Labelling** mode (header dropdown)
2. In Panel 6, select **Manual Label** from the sub-mode dropdown
3. Type a **label name**, pick a **color**, click **Set**

> **Tip:** Click **Schema** in the header to load a classification scheme (UKHab v2, HOTW, or custom) — click any entry to set it as the active label.

### Placing Labels

| Method | Action | Best for |
|--------|--------|----------|
| **Pin** | Ctrl+click (Cmd+click on Mac) | Scattered features, point samples |
| **Polygon** | Ctrl+double-click → click vertices → close | Large homogeneous areas |
| **Similarity slider** | Drag slider on any label entry (0–500) | Expanding coverage from a seed |

### Polygon Search Mode

When drawing a polygon, choose how its embedding is stored:

- **Mean** (default) — average of all pixel embeddings inside (best for homogeneous areas)
- **Union** — every individual pixel embedding (best for heterogeneous areas)

### Classification

Click **Classify** in Panel 5 to generate a full-viewport classification — each pixel is assigned to the nearest label class based on embedding distance.

### Suggested Workflow

```
1. Auto-label (K-means, k=5+)
   ↓
2. Review clusters on heatmap + embedding panels
   ↓
3. Promote good clusters → merge duplicates by giving same name
   ↓
4. Fine-tune with manual pins + similarity sliders
   ↓
5. Export as Shapefile for validation or GIS
```

---

## Auto-Labelling (K-Means)

TEE segments the viewport into clusters using K-means on the embedding space. Clusters are **temporary previews** until promoted to saved labels.

| Step | Action |
|------|--------|
| 1 | Set **k** (2–20) using the slider |
| 2 | Click **Segment** |
| 3 | Review the cluster list (color, pixel count, %) |
| 4 | **Promote** individual clusters (↗) or all at once |

> **Tip:** Name clusters before promoting. Two clusters with the same name are merged automatically.

---

## Validation (Learning Curves)

Evaluate how well classifiers distinguish habitat classes using Tessera embeddings and expert ground-truth polygons. Works at any scale — from a single viewport to an entire country.

<!-- Screenshot: validation_results.png — Showing learning curves in Panel 5, confusion matrix in Panel 6 -->

> All ML evaluation runs on your **compute server** (`tee-compute`), not on the hosted server. See [Running Evaluation on Your Own Machine](#running-evaluation-on-your-own-machine).

### Step-by-Step

| Step | Panel | Action |
|------|-------|--------|
| 1 | — | In Viewport Manager → **Validation** tab → **Open Validation** |
| 2 | 1 | Drag and drop a `.zip` shapefile onto the upload zone |
| 3 | 2 | Verify polygons appear as red outlines on satellite |
| 4 | 3 | Check the class table — polygon counts per class |
| 5 | 1 | Select **Class field**, **Year**, and **Classifiers** |
| 6 | 1 | Click **Run Evaluation** |
| 7 | 4 | Watch progress: status messages + results table |
| 8 | 5 | Learning curve builds as each % completes |
| 9 | 6 | Confusion matrix appears at the end |

### Available Classifiers

| Classifier | Type | Notes |
|-----------|------|-------|
| **k-NN** | Pixel | Fast, good baseline |
| **Random Forest** | Pixel | Strong at all training sizes |
| **XGBoost** | Pixel | Often best accuracy |
| **MLP** | Pixel | Needs more data to converge |
| **Spatial MLP (3×3)** | Neighbourhood | Uses 3×3 embedding context |
| **Spatial MLP (5×5)** | Neighbourhood | Uses 5×5 embedding context |
| **U-Net (GPU)** | Patch-based | Convolutional, 256×256 patches. Requires PyTorch. |

### Understanding the Learning Curve

```
   F1 ↑
  1.0 ┤
      │         ╭───── RF
  0.7 ┤     ╭──╯
      │   ╭─╯──────── k-NN
  0.4 ┤  ╯
      │╭╯
  0.1 ┤
      └──────────────────→ % labels
       1%  5%  10%  30%  80%
```

- **X axis**: % of labelled area used for training (remainder used for testing)
- **Y axis**: F1 score (0–1), with shaded ±1 std bands
- **Steeper curves** = embeddings separate classes well with few labels
- Toggle **Macro F1** / **Weighted F1** with the dropdown

### Confusion Matrix

- Rows = true class, columns = predicted class
- **Green diagonal** = correct, **red off-diagonal** = errors
- **%** button toggles counts vs percentages
- **View** button opens a full-size modal for large matrices
- **Classifier dropdown** switches between classifiers

### Downloading Trained Models

Click **Download Models** in the header bar. This trains each classifier on the full dataset (deferred from the evaluation for speed), then downloads `.joblib` files.

```python
import joblib
d = joblib.load("rf_model.joblib")
clf = d["model"]          # the trained classifier
names = d["class_names"]  # e.g. ["Grassland", "Urban", "Woodland"]
predictions = clf.predict(embeddings)  # shape: (N, 128)
```

### Regression

When the selected field is numeric with >20 unique values, TEE auto-switches to regression — showing R², RMSE, and MAE instead of F1 and confusion matrices.

### CLI for Headless Evaluation

```bash
python scripts/tee_evaluate.py --config eval_config.json
python scripts/tee_evaluate.py --config eval_config.json --dry-run  # stats only
```

---

## Running Evaluation on Your Own Machines

All ML evaluation runs on a compute server (`tee-compute`). The hosted TEE server does not run ML.

### How It Works

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│   Browser    │────▶│  Django (port 8001)  │     │   tee-compute   │
│             │     │  Serves UI, tiles,   │────▶│   (port 8002)   │
│             │     │  proxies /eval/*     │     │  ML evaluation  │
└─────────────┘     └──────────────────────┘     └────────┬────────┘
                                                          │
                                                 ┌────────▼────────┐
                                                 │   GeoTessera    │
                                                 │ dl2.geotessera  │
                                                 └─────────────────┘
```

### Deployment Modes

| Mode | What you run | ML runs on |
|------|-------------|-----------|
| **Hosted** | Nothing — open the website | — (no ML) |
| **Local compute** | `tee-compute` on your laptop | Your laptop |
| **Remote compute** | `tee-compute` on GPU box + SSH tunnel | GPU server |
| **All local** | Django + tee-compute via `restart.sh` | Your laptop |

| Component | Hosted server | Local compute | GPU server |
|-----------|:------------:|:------------:|:----------:|
| Map tiles, satellite imagery | ✓ | | |
| Embedding tile images | ✓ | | |
| Label sharing | ✓ | | |
| Viewport management | ✓ | | |
| Shapefile upload | | ✓ | ✓ |
| Embedding sampling | | ✓ | ✓ |
| ML training + evaluation | | ✓ | ✓ |
| Model download | | ✓ | ✓ |

### Setting Up a GPU Server

Before using either alternative below, set up the GPU server once:

**Step 0: Open a terminal**

- **Mac**: press Cmd+Space, type "Terminal", press Enter
- **Windows**: press Win+X, select "Windows PowerShell", or install [Windows Terminal](https://learn.microsoft.com/en-us/windows/terminal/install)

For a full guide, see [How to open a terminal](https://tutorials.codebar.io/command-line/introduction/tutorial.html).

**Step 1: Get SSH access**

Check if you already have an SSH key:
```bash
cat ~/.ssh/id_rsa.pub
```
If you see "No such file", generate one (press Enter at every prompt):
```bash
ssh-keygen
```
Send the public key to your server admin:
```bash
cat ~/.ssh/id_rsa.pub
```
Ask them to append it to `~/.ssh/authorized_keys` in your home directory on the server.

**Step 2: Configure SSH**

Add the server to `~/.ssh/config` so you can refer to it by a short name:
```
Host gpu-box
    HostName myhost.uk       # replace with your server's DNS name or IP
    User yourname            # replace with your username on the server
```

**Step 3: Verify SSH access**
```bash
ssh gpu-box    # should log in without a password prompt
```

**Step 4: Install tessera-eval on the server**
```bash
ssh gpu-box
git clone -b dev https://github.com/ucam-eo/TEE.git ~/TEE   # first time only
python3 -m venv ~/TEE/venv                                    # first time only
source ~/TEE/venv/bin/activate
pip install -e "$HOME/TEE/packages/tessera-eval[server]"
exit
```

To update later:
```bash
ssh gpu-box
cd ~/TEE && git pull
source venv/bin/activate
pip install -e packages/tessera-eval[server]
exit
```

---

### Alternative A: Local UI + GPU Compute

Your laptop runs the TEE UI, tiles, and data. The GPU server runs only ML evaluation.

```
Browser → localhost:8001 → Django (your laptop)
                               │
                               └── /api/evaluation/* → tunnel → gpu-box (tee-compute)
```

**Each session — open two terminals on your laptop:**

Terminal 1 — start Django:
```bash
cd ~/TEE
./restart.sh
# This starts Django on :8001 and local tee-compute on :8002
```

Terminal 2 — replace local tee-compute with GPU tunnel:
```bash
pkill -f tee-compute                    # stop the local tee-compute
ssh -L 8002:localhost:5050 gpu-box '~/TEE/venv/bin/tee-compute --port 5050'
```

Open `http://localhost:8001`. The UI and tiles come from your laptop. When you click Run Evaluation, the ML runs on the GPU server.

> **Why port 5050?** Port 8001 may already be in use on the GPU server. The tunnel maps your local port 8002 to the server's port 5050. Django automatically proxies evaluation requests to localhost:8002.

---

### Alternative B: Hosted UI + GPU Compute

The hosted TEE server (tee.cl.cam.ac.uk) provides the UI, tiles, and data. The GPU server runs ML evaluation. Nothing runs on your laptop except the browser and SSH tunnel.

```
Browser → localhost:8001 → SSH tunnel → gpu-box (tee-compute)
                                            │
                                            ├── /api/evaluation/* → runs ML on gpu-box
                                            └── everything else   → proxied to tee.cl.cam.ac.uk
```

**Each session — one command from your laptop:**
```bash
ssh -L 8001:localhost:5050 gpu-box '~/TEE/venv/bin/tee-compute --port 5050'
```

Open `http://localhost:8001`. The UI comes from tee.cl.cam.ac.uk (via proxy), evaluation runs on the GPU server.

> **Tip:** Add an alias to `~/.zshrc` or `~/.bashrc`:
> ```bash
> alias tee-gpu='ssh -L 8001:localhost:5050 gpu-box "~/TEE/venv/bin/tee-compute --port 5050"'
> ```
> Then just type `tee-gpu` to start a session.
> Then just type `tee` to start a session.

### Command Reference

```
tee-compute [OPTIONS]

  --hosted URL    Hosted server URL (default: https://tee.cl.cam.ac.uk)
  --port PORT     Local port (default: 8001)
  --host HOST     Bind address (default: 127.0.0.1)
  --debug         Flask debug mode
```

### Troubleshooting

| Problem | Solution |
|---------|----------|
| `Connection refused` | Is `tee-compute` running? |
| `Cannot reach hosted server` | Check internet: `curl https://tee.cl.cam.ac.uk/health` |
| `No GeoTessera tiles found` | Try year 2025 (wider coverage) |
| `ModuleNotFoundError` | `pip install -e "$HOME/TEE/packages/tessera-eval[server]"` |
| SSH disconnects | Add `-o ServerAliveInterval=60` |
| `Address already in use` on the server | Port 8001 is taken by another service. Use a different port: `ssh -L 8001:localhost:5050 gpu-box '~/TEE/venv/bin/tee-compute --port 5050'` |
| `Could not resolve hostname gpu-box` | Replace `gpu-box` with the name from your `~/.ssh/config`, or use the full hostname |
| SSH asks for passphrase every time | Run `ssh-add` to cache your key, or use `ssh-agent` |
| `OpenBLAS: too many memory regions` | Server has >128 CPU cores. Update the code on the server (`cd ~/TEE && git pull`) to get the built-in fix, or run with: `OPENBLAS_NUM_THREADS=64 ~/TEE/venv/bin/tee-compute --port 5050` |

---

## Export Options

### From Explore / Auto-label modes

| Format | Use case |
|--------|----------|
| **Labels (JSON)** | Re-import into TEE |
| **Labels (GeoJSON)** | Open in QGIS or other GIS tools |
| **Map (JPG)** | Presentation — satellite with label overlays |

### From Manual Labelling mode

| Format | Use case |
|--------|----------|
| **JSON** | Re-import into TEE (includes embeddings) |
| **GeoJSON** | GIS-compatible points and polygons |
| **ESRI Shapefile (ZIP)** | Standard GIS interchange — can be used as validation ground truth |

---

## Sharing Labels

Two modes via the **Share** button:

| Mode | What's shared | Who sees it |
|------|--------------|------------|
| **Private** | Embedding vectors only (no coordinates) | Nobody — contributes to Tessera's global model |
| **Public** | Full ESRI Shapefile with locations | Other users on the same server |

---

## Data Privacy

| What | Where it runs | What the server sees |
|------|--------------|---------------------|
| Similarity search | Your browser | Nothing |
| Labelling | Your browser | Nothing |
| ML evaluation | Your compute server | Nothing |
| Trained models | Your compute server | Nothing |
| Label sharing | Hosted server | Only when you click Share |
| Map tiles | Hosted server | Standard tile requests |

> Ground-truth shapefiles and evaluation results **never** leave your machine.

---

## Reference

### Mouse Controls

| Action | Control |
|--------|---------|
| Pan | Click and drag |
| Zoom | Scroll wheel or +/- buttons |
| Place marker | Single-click |
| Similarity search | Double-click |
| Drop pin (labelling) | Ctrl+click |
| Draw polygon (labelling) | Ctrl+double-click → click vertices |
| Cancel polygon | Escape |
| Rotate PCA/UMAP | Right-click drag |

### Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Escape | Cancel polygon drawing |
| Ctrl+click | Drop a pin in manual label mode |
| Ctrl+double-click | Start polygon drawing |

### Tips

- Processing time is roughly the same for 1 year or 8 — all download in parallel
- Features appear incrementally — the viewer is usable as soon as pyramids are built
- Each viewport uses ~5GB of storage depending on years processed
- Similarity search and labelling are completely private — they run in your browser
- Evaluation is private too — it runs on your compute server, not the hosted server
