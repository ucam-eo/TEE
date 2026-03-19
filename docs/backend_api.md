# TEE Backend API Reference

Complete reference for all backend Python modules: HTTP endpoints, library
functions, and standalone scripts.

See also: [architecture.md](architecture.md) for system overview,
[frontend_api.md](frontend_api.md) for the JavaScript API that consumes
these endpoints.

---

## Table of Contents

1. [HTTP API Endpoints](#1-http-api-endpoints)
   - [Authentication](#11-authentication--apiauthviewspy)
   - [Viewports](#12-viewports--apiviewsviewportspy)
   - [Pipeline & Progress](#13-pipeline--progress--apiviewspipelinepy)
   - [Tiles](#14-tiles--apiviewstilespy)
   - [Evaluation](#15-evaluation--apiviewsevaluationpy)
   - [Vector Data](#16-vector-data--apiviewsvector_datapy)
   - [Config & Health](#17-config--health--apiviewsconfigpy)
2. [Library Modules](#2-library-modules)
   - [lib/config.py](#21-libconfigpy--paths--directories)
   - [lib/viewport_utils.py](#22-libviewport_utilspy--viewport-readingvalidation)
   - [lib/viewport_writer.py](#23-libviewport_writerpy--viewport-creation)
   - [lib/viewport_ops.py](#24-libviewport_opspy--viewport-operations)
   - [lib/pipeline.py](#25-libpipelinepy--pipeline-orchestration)
   - [lib/progress_tracker.py](#26-libprogress_trackerpy--progress-persistence)
   - [lib/tile_renderer.py](#27-libtile_rendererpy--tile-rendering)
   - [lib/evaluation_engine.py](#28-libevaluation_enginepy--ml-evaluation)
3. [Standalone Scripts](#3-standalone-scripts)
   - [process_viewport.py](#31-process_viewportpy)
   - [create_pyramids.py](#32-create_pyramidspy)
4. [Supporting Modules](#4-supporting-modules)
   - [api/helpers.py](#41-apihelperspy)
   - [api/tasks.py](#42-apitaskspy)
   - [api/middleware.py](#43-apimiddlewarepy)
5. [Import Dependency Map](#5-import-dependency-map)

---

## 1. HTTP API Endpoints

### 1.1 Authentication — `api/auth_views.py`

**Overview:** Session-based authentication. Auth is optional — it activates when at least one Django `User` exists. Unauthenticated users get read-only access (demo mode).

#### `POST /api/auth/login`

Authenticate with username and password.

```json
// Request
{ "username": "alice", "password": "secret" }

// Response 200
{ "success": true, "user": "alice" }

// Response 400
{ "error": "Invalid credentials" }
```

#### `POST /api/auth/logout`

Destroy current session.

```json
// Response 200
{ "success": true }
```

#### `POST /api/auth/change-password`

Change password for the authenticated user.

```json
// Request
{ "current_password": "old", "new_password": "new" }

// Response 200
{ "success": true }
```

#### `GET /api/auth/status`

Check current auth state.

```json
// Response 200
{ "auth_enabled": true, "logged_in": true, "user": "alice" }
```

---

### 1.2 Viewports — `api/views/viewports.py`

**Overview:** CRUD for viewports (geographic areas of interest). Each viewport is a named bounding box that owns pyramids, vectors, and label data. Viewports are owned by Django users (when authentication is enabled); ownership is checked on write operations and disk quotas are enforced per-user.

**Depends on:** `lib.viewport_utils`, `lib.viewport_writer`, `lib.viewport_ops`, `lib.pipeline`, `lib.config`

#### `GET /api/viewports/list`

List all viewports with metadata. Private viewports are filtered based on ownership.

```json
// Response 200
{
  "success": true,
  "viewports": [
    {
      "name": "cambridge",
      "bounds": { "minLon": 0.08, "minLat": 52.18, "maxLon": 0.16, "maxLat": 52.22 },
      "center": [52.20, 0.12],
      "years": [2023, 2024],
      "data_size_mb": 142.3,
      "owner": "alice",
      "private": false
    }
  ],
  "active": "cambridge"
}
```

#### `GET /api/viewports/current`

Get the currently active viewport.

```json
// Response 200
{
  "success": true,
  "viewport": {
    "name": "cambridge",
    "bounds": { "minLon": 0.08, "minLat": 52.18, "maxLon": 0.16, "maxLat": 52.22 },
    "center": [52.20, 0.12]
  }
}
```

#### `POST /api/viewports/switch`

Switch the active viewport.

```json
// Request
{ "name": "cambridge" }

// Response 200
{
  "success": true,
  "message": "Switched to viewport 'cambridge'",
  "viewport": { "name": "cambridge", "bounds": {...}, "center": [...] },
  "data_ready": true,
  "pyramids_ready": true,
  "vectors_ready": true
}
```

#### `POST /api/viewports/create`

Create a new viewport from geographic bounds. Triggers the processing pipeline automatically. Enforces disk quota for non-admin users.

```json
// Request
{
  "bounds": "0.08,52.18,0.16,52.22",
  "name": "cambridge",
  "description": "Central Cambridge",
  "years": [2023, 2024],
  "private": false
}

// Response 200
{
  "success": true,
  "message": "Viewport 'cambridge' created — pipeline started",
  "viewport": { "name": "cambridge", "bounds": {...}, "center": [...] },
  "data_preparing": true
}
```

#### `POST /api/viewports/delete`

Delete a viewport and all associated data (mosaics, pyramids, vectors, labels, progress files).

```json
// Request
{ "name": "cambridge" }

// Response 200
{
  "success": true,
  "message": "Deleted viewport 'cambridge' and 5 data items",
  "deleted": [
    "pyramids directory: cambridge/",
    "vectors directory: cambridge/",
    "viewport: cambridge.txt"
  ]
}
```

#### `POST /api/viewports/<viewport_name>/add-years`

Add years to an existing viewport and re-trigger the pipeline for the new years.

```json
// Request
{ "years": [2022] }

// Response 200
{ "success": true, "message": "Added years [2022]", "years": [2022, 2023, 2024] }
```

#### `GET /api/viewports/<viewport_name>/available-years`

List years that have pyramid data ready for viewing.

```json
// Response 200
{ "success": true, "years": [2023, 2024] }
```

#### `GET /api/viewports/<viewport_name>/is-ready`

Check if a viewport has enough data to open the viewer. Returns `ready: true` once pyramids exist for at least one requested year. Auto-triggers the pipeline if data is incomplete and no pipeline is running.

```json
// Response 200
{
  "ready": true,
  "message": "Ready to view (2023, 2024)",
  "has_embeddings": true,
  "has_pyramids": true,
  "has_vectors": true,
  "has_umap": true,
  "years_available": ["2023", "2024"],
  "years_processing": [],
  "years_unavailable": ["2019"]
}
```

---

### 1.3 Pipeline & Progress — `api/views/pipeline.py`

**Overview:** Monitor and control the background data-processing pipeline. The pipeline downloads embedding tiles, creates PNG pyramids, and extracts vectors.

**Depends on:** `lib.pipeline`, `lib.viewport_utils`, `lib.viewport_writer`, `lib.config`

#### `GET /api/operations/progress/<operation_id>`

Read progress from the JSON file written by `ProgressTracker`. The `operation_id` is typically `<viewport_name>_pipeline`.

```json
// Response 200
{
  "success": true,
  "status": "processing",
  "message": "[2024] Fetching mosaic (12.4 MB, 35s)",
  "percent": 28,
  "current_file": "",
  "start_time": "2025-03-18T10:00:00Z",
  "last_update": "2025-03-18T10:00:35Z"
}
```

Possible `status` values: `"starting"`, `"processing"`, `"complete"`, `"error"`, `"not_started"`.

#### `GET /api/operations/pipeline-status/<viewport_name>`

Get task-level status from the in-memory `tasks` dict (not the progress file).

```json
// Response 200
{
  "success": true,
  "operation_id": "cambridge_full_pipeline",
  "status": "starting",
  "current_stage": "initialization",
  "error": null
}
```

#### `POST /api/viewports/<viewport_name>/cancel-processing`

Cancel a running pipeline. Sends SIGTERM to the subprocess, then cleans up all generated files.

```json
// Response 200
{
  "success": true,
  "message": "Processing cancelled and data cleaned up",
  "deleted_items": ["pyramids directory: cambridge/", "vectors directory: cambridge/"],
  "task_was_active": true
}
```

---

### 1.4 Tiles — `api/views/tiles.py`

**Overview:** Slippy-map tile server. Serves 256x256 PNG tiles from pre-built pyramid GeoTIFFs or PNGs. Supports ETag/304 caching and returns transparent tiles for missing data.

**Depends on:** `lib.tile_renderer`, `lib.viewport_utils`, `lib.config`

**Note:** Tile endpoints are mounted at the root (`/tiles/...`), not under
`/api/`.  They bypass all middleware via `TileShortcircuitMiddleware` for
performance.

#### `GET /tiles/<viewport>/<map_id>/<z>/<x>/<y>.png`

Serve a single map tile.

- `map_id`: year string (`"2017"` .. `"2025"`). Satellite imagery is served by external providers (ESRI, Google), not this tile server.
- `z`, `x`, `y`: standard slippy-map tile coordinates

```
GET /tiles/cambridge/2024/12/2048/1362.png

Response: 256x256 PNG image
Headers:
  Content-Type: image/png
  Cache-Control: public, max-age=86400
  ETag: "a1b2c3..."
```

Returns a transparent 256x256 PNG (with `max-age=0`) for missing or out-of-bounds tiles. Returns HTTP 304 if the client sends a matching `If-None-Match` header.

#### `GET /bounds/<viewport>/<map_id>`

Get geographic bounds and center for a map layer.

```json
// Response 200
{
  "bounds": [0.08, 52.18, 0.16, 52.22],
  "center": [52.20, 0.12]
}
```

#### `GET /tiles/health`

List all viewports and their available map layers.

```json
// Response 200
{
  "status": "ok",
  "viewports": {
    "cambridge": ["2023", "2024", "satellite"],
    "oxford": ["2024", "rgb"]
  }
}
```

---

### 1.5 Evaluation — `api/views/evaluation.py`

**Overview:** Upload shapefiles with ground-truth habitat classes, then run learning-curve evaluations against the embedding vectors. Supports multiple classifiers (k-NN, Random Forest, MLP, spatial MLP, U-Net). Results are streamed as NDJSON so the frontend can display progress in real-time.

**Depends on:** `lib.evaluation_engine`

#### `POST /api/evaluation/upload-shapefile`

Upload a `.zip` containing a shapefile (`.shp`, `.dbf`, `.shx`, `.prj`). Returns field metadata for class selection.

```
POST /api/evaluation/upload-shapefile
Content-Type: multipart/form-data
Body: file=<shapefile.zip>
```

```json
// Response 200
{
  "fields": [
    { "name": "habitat", "unique_count": 12, "samples": ["woodland", "grassland", "heath"] },
    { "name": "id", "unique_count": 340, "samples": [1, 2, 3] }
  ],
  "geojson": { "type": "FeatureCollection", "features": [...] }
}
```

#### `POST /api/evaluation/class-counts`

Get pixel counts per class without running ML. Uses rasterization to count how many pixels fall in each shapefile class.

```json
// Request
{ "viewport": "cambridge", "year": "2024", "field": "habitat" }

// Response 200
{
  "classes": [
    { "name": "woodland", "pixels": 4521 },
    { "name": "grassland", "pixels": 8932 }
  ]
}
```

#### `POST /api/evaluation/run`

Run a learning-curve evaluation. Returns a streaming NDJSON response — one JSON object per line.

```json
// Request
{
  "viewport": "cambridge",
  "year": "2024",
  "field": "habitat",
  "classifiers": ["nn", "rf", "mlp"],
  "params": { "rf": { "n_estimators": 200 } },
  "max_train": 5000
}
```

```
// Response 200 (streaming NDJSON)
{"event":"start","classifiers":["nn","rf","mlp"],"training_sizes":[50,100,200,500,1000,2000,5000],"n_classes":12}
{"event":"progress","size":50,"classifiers":{"nn":{"f1":0.42,"per_class":{...}},"rf":{"f1":0.38,...}}}
{"event":"progress","size":100,"classifiers":{"nn":{"f1":0.55,...},...}}
...
{"event":"confusion_matrices","confusion_matrices":{"nn":[[12,3],[1,45]],...}}
{"event":"model_ready","classifier":"nn"}
{"event":"model_ready","classifier":"rf"}
{"event":"done","elapsed_seconds":34.2,"models_available":["nn","rf","mlp"]}
```

#### `POST /api/evaluation/finish-classifier`

Tell the server to stop evaluating a classifier early (it will skip remaining training sizes).

```json
// Request
{ "classifier": "rf" }

// Response 200
{ "ok": true }
```

#### `GET /api/evaluation/download-model/<classifier>`

Download a trained model file. Returns `.pt` for U-Net, `.joblib` for sklearn models.

```
GET /api/evaluation/download-model/rf

Response: application/octet-stream (joblib file)
Content-Disposition: attachment; filename="rf.joblib"
```

---

### 1.6 Vector Data — `api/views/vector_data.py`

**Overview:** Serve raw vector data files for client-side operations (similarity search, UMAP, etc.). Files are served from the `vectors/<viewport>/<year>/` directory.

**Depends on:** `lib.viewport_utils`, `lib.config`

#### `GET /api/vector-data/<viewport>/<year>/<filename>`

Serve a vector data file. Allowed filenames:

| Filename | Description | Content-Type |
|---|---|---|
| `all_embeddings_uint8.npy.gz` | Quantised uint8 embeddings | `application/gzip` |
| `all_embeddings.npy` | Float32 embeddings (legacy) | `application/octet-stream` |
| `pixel_coords.npy` / `.npy.gz` | Pixel coordinates | `application/gzip` or `octet-stream` |
| `metadata.json` | Mosaic dimensions, transform, stats | `application/json` |
| `quantization.json` | Per-dimension min/max for dequantization | `application/json` |

```
GET /api/vector-data/cambridge/2024/all_embeddings_uint8.npy.gz

Response: application/gzip (binary file)
```

---

### 1.7 Config & Health — `api/views/config.py`

**Overview:** Static file serving, health check, and client configuration.

**Depends on:** `lib.config`

#### `GET /` — Serve `viewport_selector.html`

#### `GET /public/<path>` — Serve static files from `public/` with path-traversal protection.

#### `GET /health`

Docker/monitoring health check.

```json
// Response 200
{ "status": "healthy", "service": "TEE", "version": "v1.2.3-abc1234" }
```

#### `GET /api/config`

Client configuration (currently empty, reserved for future use).

```json
// Response 200
{}
```

---

## 2. Library Modules

### 2.1 `lib/config.py` — Paths & Directories

**Overview:** Single source of truth for all filesystem paths. Paths are configurable via environment variables `TEE_DATA_DIR` and `TEE_APP_DIR`.

```python
from lib.config import (
    DATA_DIR,        # Base data dir (default ~/data)
    MOSAICS_DIR,     # DATA_DIR / 'mosaics'
    PYRAMIDS_DIR,    # DATA_DIR / 'pyramids'
    VECTORS_DIR,     # DATA_DIR / 'vectors'
    EMBEDDINGS_DIR,  # DATA_DIR / 'embeddings' (tile cache)
    PROGRESS_DIR,    # DATA_DIR / 'progress'
    APP_DIR,         # Project root
    VIEWPORTS_DIR,   # APP_DIR / 'viewports'
)

# Check if a pyramid level exists (PNG or TIF)
pyramid_exists(year_dir: Path) -> bool

# Create all required directories
ensure_dirs() -> None
```

**Used by:** Nearly every module in the project.

---

### 2.2 `lib/viewport_utils.py` — Viewport Reading/Validation

**Overview:** Read-only viewport operations: parse viewport files, validate names, list available viewports, check the embedding tile cache.

```python
from lib.viewport_utils import (
    validate_viewport_name,
    get_active_viewport,
    get_active_viewport_name,
    list_viewports,
    read_viewport_file,
    parse_viewport_content,
    check_cache,
)
```

#### `validate_viewport_name(name: str) -> str`

Validates a viewport name is safe for filesystem use. Rejects path separators, dots, and non-alphanumeric characters (except `_` and `-`). Returns the name if valid.

```python
validate_viewport_name("cambridge")      # "cambridge"
validate_viewport_name("../etc/passwd")  # raises ValueError
```

#### `get_active_viewport() -> dict`

Read the active viewport from the symlink and return parsed data.

```python
vp = get_active_viewport()
# {
#   "viewport_id": "cambridge",
#   "center": [52.20, 0.12],
#   "bounds": {"minLon": 0.08, "minLat": 52.18, "maxLon": 0.16, "maxLat": 52.22},
#   "bounds_tuple": (0.08, 52.18, 0.16, 52.22),
#   "size_km": 5.4
# }
```

#### `read_viewport_file(viewport_name: str) -> dict`

Read and parse a specific viewport file by name. Same return format as `get_active_viewport()`.

#### `list_viewports() -> list[str]`

Return names of all saved viewport files in `viewports/`.

```python
list_viewports()  # ["cambridge", "oxford", "london"]
```

#### `check_cache(bounds: tuple, data_type: str = "embeddings") -> Path | None`

Check if the given bounds match an existing mosaic in the cache directory. Returns `Path` to matching file or `None`.

---

### 2.3 `lib/viewport_writer.py` — Viewport Creation

**Overview:** Write-side viewport operations: create viewport files and manage the active-viewport symlink.

```python
from lib.viewport_writer import (
    create_viewport_from_bounds,
    set_active_viewport,
    clear_active_viewport,
)
```

#### `create_viewport_from_bounds(viewport_name, bounds, description="") -> Path`

Create a new viewport file from geographic bounds (WGS84).

```python
path = create_viewport_from_bounds(
    "cambridge",
    bounds=(0.08, 52.18, 0.16, 52.22),
    description="Central Cambridge"
)
# Returns: Path("viewports/cambridge.txt")
# Raises: ValueError (invalid bounds), FileExistsError (already exists)
```

#### `set_active_viewport(viewport_name: str) -> None`

Set the active viewport by updating the symlink `viewports/viewport.txt` and writing `.active`.

#### `clear_active_viewport() -> None`

Remove the active viewport symlink and `.active` file.

---

### 2.4 `lib/viewport_ops.py` — Viewport Operations

**Overview:** Pure functions for viewport readiness checks, data size calculation, and data deletion. Extracted from `api/views/viewports.py`.

```python
from lib.viewport_ops import check_readiness, delete_viewport_data, compute_data_size
```

#### `check_readiness(viewport_name, years_requested=None) -> dict`

Check whether a viewport has the data needed to open the viewer.

```python
status = check_readiness("cambridge", years_requested=[2023, 2024])
# {
#   "has_embeddings": True,
#   "has_pyramids": True,
#   "has_vectors": True,
#   "has_umap": True,
#   "years_available": ["2023", "2024"]
# }
```

Checks:
- **vectors:** `vectors/<name>/<year>/metadata.json` + `all_embeddings_uint8.npy.gz` (or `.npy`)
- **mosaics:** `mosaics/<name>_embeddings_*.tif`
- **pyramids:** `pyramids/<name>/<year>/level_0.{png,tif}`

#### `delete_viewport_data(viewport_name, bounds=None) -> list[str]`

Delete all data files associated with a viewport. Returns a list of human-readable descriptions of what was deleted.

```python
deleted = delete_viewport_data("cambridge", bounds={"minLon": 0.08, ...})
# [
#   "pyramids directory: cambridge/",
#   "vectors directory: cambridge/",
#   "labels JSON: cambridge_labels.json",
#   "viewport: cambridge.txt"
# ]
```

Deletes: mosaics, RGB mosaics, pyramids, vectors, embedding tile cache, labels JSON, config JSON, progress files, and the viewport file itself.

#### `compute_data_size(viewport_name) -> float`

Calculate total data size for a viewport in MB. Scans mosaics, RGB mosaics, vectors, and pyramids directories.

```python
compute_data_size("cambridge")  # 142.3
```

---

### 2.5 `lib/pipeline.py` — Pipeline Orchestration

**Overview:** Pipeline for viewport data processing: download embedding tiles, create PNG pyramids, extract vectors. Supports cancellation via SIGTERM. The subprocess writes progress directly to `{viewport}_pipeline_progress.json` (single source of truth); the pipeline handles cancellation, error detection, and final "complete" status.

```python
from lib.pipeline import PipelineRunner, cancel_pipeline, is_pipeline_cancelled
```

#### `cancel_pipeline(viewport_name: str) -> bool`

Cancel a running pipeline by killing its subprocess (SIGTERM, then SIGKILL after 2s).

```python
cancel_pipeline("cambridge")  # True if was running, False otherwise
```

#### `PipelineRunner`

```python
runner = PipelineRunner(project_root=Path("/app"), venv_python=Path("/app/venv/bin/python"))

success, error = runner.run_full_pipeline(
    viewport_name="cambridge",
    years_str="2023,2024",
    cancel_check=lambda: False,  # optional cancellation callback
)
# success: True/False
# error: None or error message string
```

**Progress allocation:**

| Stage | Range | Description |
|---|---|---|
| `process` | 0–100% | Download tiles + pyramids + vectors |

**Key methods:**

```python
# Run a subprocess with cancellation, timeout, and real-time log streaming
runner.run_script("process_viewport.py", "--years", "2024", timeout=1800)
    # -> subprocess.CompletedProcess

# Update pipeline progress (monotonically increasing)
runner.update_progress("process", stage_percent=50, message="Fetching mosaic...")

# Wait for a file to appear on disk
runner.wait_for_file(Path("pyramids/cambridge/2024/level_0.png"), min_size_bytes=1024)
```

**Progress model:** The subprocess (`process_viewport.py`) writes directly to `{viewport}_pipeline_progress.json` — no forwarding layer. The pipeline only writes the final "complete" or "error" status after the subprocess exits.

---

### 2.6 `lib/progress_tracker.py` — Progress Persistence

**Overview:** Write progress to a JSON file in `PROGRESS_DIR` for frontend polling. Each operation gets its own file: `<operation_id>_progress.json`.

```python
from lib.progress_tracker import ProgressTracker

progress = ProgressTracker("cambridge_pipeline")
progress.update("processing", "Fetching mosaic...", percent=25)
progress.complete("Pipeline complete")
progress.error("Download failed: timeout")
progress.cleanup()  # delete the progress file
```

**JSON file format:**

```json
{
  "operation_id": "cambridge_pipeline",
  "status": "processing",
  "message": "Fetching mosaic (12.4 MB, 35s)",
  "percent": 25,
  "current_value": 0,
  "total_value": 0,
  "current_file": "",
  "start_time": "2025-03-18T10:00:00+00:00",
  "last_update": "2025-03-18T10:00:35+00:00"
}
```

---

### 2.7 `lib/tile_renderer.py` — Tile Rendering

**Overview:** Pure tile rendering functions for the map tile server. Handles coordinate math, GeoTIFF and PNG pyramid rendering, and pyramid path resolution. All rendering functions are LRU-cached keyed by `(path, z, x, y, mtime)`.

```python
from lib.tile_renderer import (
    tile_to_bbox,
    get_pyramid_path,
    render_tile,
    render_tile_png,
)
```

#### `tile_to_bbox(x, y, zoom) -> tuple`

Convert slippy-map tile coordinates to geographic bounding box.

```python
tile_to_bbox(2048, 1362, 12)
# (0.087890625, 52.1874047..., 0.175781..., 52.2414...)
# Returns: (lon_min, lat_min, lon_max, lat_max)
```

#### `get_pyramid_path(viewport, map_id, zoom_level) -> tuple | None`

Resolve the pyramid file path for a viewport/map/zoom. Tries PNG first, falls back to GeoTIFF.

```python
get_pyramid_path("cambridge", "2024", 12)
# ("/data/pyramids/cambridge/2024/level_0.png", 1710500000, True)
# Returns: (path_str, mtime, is_png) or None
```

**Pyramid level mapping:** `level = max(0, min(5, (14 - zoom) // 2))`

| Zoom | Level |
|---|---|
| 14-15 | 0 (full resolution) |
| 12-13 | 1 |
| 10-11 | 2 |
| 8-9 | 3 |
| 6-7 | 4 |
| 0-5 | 5 (most downsampled) |

#### `render_tile(tif_path, z, x, y, _mtime=0) -> bytes | None`

Render a 256x256 PNG tile from a GeoTIFF pyramid level. Returns `None` for out-of-bounds tiles. Cached (LRU, maxsize=2048).

```python
png_bytes = render_tile("/data/pyramids/cambridge/2024/level_0.tif", 12, 2048, 1362)
```

#### `render_tile_png(png_path, z, x, y, _mtime=0) -> bytes | None`

Render a 256x256 PNG tile from a PNG pyramid level. Reads `pyramid_meta.json` for the affine transform. Cached (LRU, maxsize=2048).

---

### 2.8 `lib/evaluation_engine.py` — ML Evaluation

**Overview:** Pure ML functions for the evaluation pipeline. Loads quantised vectors, rasterizes shapefiles, builds spatial features, creates classifiers, and runs learning curves. Extracted from `api/views/evaluation.py`.

```python
from lib.evaluation_engine import (
    dequantize,
    load_vectors,
    rasterize_shapefile,
    gather_spatial_features,
    augment_spatial,
    make_classifier,
    run_learning_curve,
)
```

#### `dequantize(quantized, dim_min, dim_max) -> np.ndarray`

Convert uint8 embeddings back to float32.

```python
embeddings_f32 = dequantize(quantized_uint8, dim_min, dim_max)
# quantized: (N, 128) uint8
# dim_min, dim_max: (128,) float64 from quantization.json
# Returns: (N, 128) float32
```

#### `load_vectors(viewport, year) -> tuple`

Load dequantised float32 embeddings, pixel coordinates, and metadata for a viewport/year.

```python
embeddings, coords, metadata = load_vectors("cambridge", "2024")
# embeddings: (N, 128) float32
# coords: (N, 2) int32 — (x, y) pixel coordinates
# metadata: dict with "mosaic_width", "mosaic_height", "transform", etc.
```

Internally loads `all_embeddings_uint8.npy.gz`, `quantization.json`, `pixel_coords.npy.gz`, and `metadata.json` from `vectors/<viewport>/<year>/`.

#### `rasterize_shapefile(gdf, field, transform, width, height) -> np.ndarray`

Rasterise a GeoPandas GeoDataFrame onto the pixel grid.

```python
label_grid = rasterize_shapefile(gdf, "habitat", affine_transform, 1000, 800)
# Returns: (H, W) int array — 1-based class IDs, 0 = nodata
```

#### `gather_spatial_features(embeddings, coords, width, height, radius=1, subset_mask=None) -> np.ndarray`

Build spatial neighbourhood features: for each pixel, concatenate embeddings from a `(2r+1) x (2r+1)` window around it.

```python
spatial_3x3 = gather_spatial_features(embeddings, coords, 1000, 800, radius=1)
# Returns: (N, 9*128) = (N, 1152) float32 for 3x3 window

spatial_5x5 = gather_spatial_features(embeddings, coords, 1000, 800, radius=2)
# Returns: (N, 25*128) = (N, 3200) float32 for 5x5 window
```

#### `augment_spatial(X, y, window, dim) -> tuple`

4x data augmentation via horizontal and vertical flips of spatial feature patches.

```python
X_aug, y_aug = augment_spatial(X_spatial, y_labels, window=3, dim=128)
# X_aug: (4*N, window*window*dim) — original + 3 flips
# y_aug: (4*N,)
```

#### `make_classifier(name, params=None) -> object | None`

Create a classifier instance by name.

```python
clf = make_classifier("rf", params={"n_estimators": 200})
# Returns: RandomForestClassifier(n_estimators=200, ...)

clf = make_classifier("nn")        # KNeighborsClassifier(n_neighbors=5)
clf = make_classifier("mlp")       # MLPClassifier(hidden_layer_sizes=(256, 128), ...)
clf = make_classifier("unet")      # Returns None (U-Net handled separately)
```

| Name | Classifier | Features |
|---|---|---|
| `"nn"` | k-NN (k=5) | 128-dim embeddings |
| `"rf"` | Random Forest | 128-dim embeddings |
| `"mlp"` | MLP | 128-dim embeddings |
| `"xgboost"` | XGBoost | 128-dim embeddings |
| `"spatial_mlp"` | MLP | 3x3 spatial (1152-dim) |
| `"spatial_mlp_5x5"` | MLP | 5x5 spatial (3200-dim) |
| `"unet"` | U-Net (PyTorch) | 2D embedding grid |

#### `run_learning_curve(embeddings, labels, classifier_names, training_sizes, ...) -> Generator`

Generator that yields progress events as each training size completes. Runs 5 repeats (stratified shuffle-split) per size and averages F1 scores.

```python
for event in run_learning_curve(embeddings, labels, ["nn", "rf"], [50, 100, 500]):
    if event["type"] == "progress":
        print(f"Size {event['size']}: nn={event['classifiers']['nn']['f1']:.2f}")
    elif event["type"] == "confusion_matrices":
        print(f"Confusion matrices: {event['confusion_matrices'].keys()}")
```

**Yielded events:**

```python
# After each training size:
{"type": "progress", "size": 100, "classifiers": {
    "nn": {"f1": 0.55, "per_class": {"woodland": 0.62, "grassland": 0.48}},
    "rf": {"f1": 0.51, ...}
}}

# After the largest training size (full confusion matrices):
{"type": "confusion_matrices", "confusion_matrices": {
    "nn": [[12, 3], [1, 45]],
    "rf": [[10, 5], [2, 44]]
}}
```

---

## 3. Standalone Scripts

### 3.1 `process_viewport.py`

**Overview:** Single-script pipeline that downloads embedding tiles via the GeoTessera library, creates RGB pyramids, and extracts quantised vectors for each requested year. Runs as a subprocess launched by `PipelineRunner`.

**Usage:**

```bash
python process_viewport.py --years 2023,2024
python process_viewport.py              # all years 2018-2025
```

**Task flow per year:**

1. **Fetch mosaic** (0-55% of year): Download embedding tiles via `geotessera.fetch_mosaic_for_region()`. Reports progress using an asymptotic formula during download.
2. **Create pyramids** (60%): Stack bands 0-2 as RGB, percentile-normalise to uint8, write 6-level PNG pyramid.
3. **Extract vectors** (70%): Quantise all 128 bands to uint8, save compressed embeddings + coordinates + metadata.
4. **UMAP** (80-95%): Compute 2D UMAP projection from the embedding vectors.

**Key functions:**

```python
def write_pyramid_levels(rgb, transform, crs, output_dir):
    """Write 6-level PNG pyramid with pyramid_meta.json."""

def save_vectors(quantized, coords, dim_min, dim_max, transform, height, width,
                 viewport_id, year, output_dir):
    """Save quantised uint8 embeddings, pixel coords, and metadata."""

def process_year(tessera, viewport_id, bounds, year, pyramids_dir, vectors_dir,
                 progress=None, year_idx=0, num_years=1):
    """Process a single year: fetch -> pyramids -> vectors -> UMAP.
    Returns: (year, success: bool, message: str)"""

def main():
    """Entry point: read active viewport, filter to unprocessed years,
    process in parallel (multi-year) or sequentially (single year)."""
```

**Output structure:**

```
pyramids/<viewport>/<year>/
  level_0.png, level_1.png, ..., level_5.png
  pyramid_meta.json

vectors/<viewport>/<year>/
  all_embeddings_uint8.npy.gz
  pixel_coords.npy.gz
  quantization.json
  metadata.json
```

---

### 3.2 `create_pyramids.py`

**Overview:** Legacy script for creating satellite RGB pyramids from GeoTIFF files. No longer used in the main pipeline — satellite imagery is now served by external providers (ESRI, Google). Kept for potential offline use.

**Key function:**

```python
def create_pyramid_level(input_file, output_file, scale_factor,
                        target_width, target_height, use_nearest=True):
    """Create one pyramid level with 2x2 averaging between levels.
    Uses nearest-neighbor resampling for crisp 10m boundaries."""
```

---

## 4. Supporting Modules

### 4.1 `api/helpers.py`

**Overview:** Shared helpers used across views. Disk quota estimation, viewport ownership checks, embedding tile cache cleanup.

```python
from api.helpers import (
    VENV_PYTHON,        # Path to venv Python
    PROJECT_ROOT,       # Project root Path
    MIN_YEAR, MAX_YEAR, # 2018, 2025
    DEFAULT_QUOTA_MB,   # 2048

    parse_json_body,               # Parse request JSON → (dict, None) | (None, JsonResponse)
    check_viewport_owner,          # Auth check → (True, None) | (False, 403 response)
    cleanup_viewport_embeddings,   # Delete cached tiles for a viewport
    get_viewport_data_size,        # Total data size in MB
    get_user_viewports,            # List viewports owned by user
    get_user_total_data_size,      # Sum data size for user
    estimate_viewport_size,        # Estimate disk usage from bounds + years
)
```

#### `parse_json_body(request) -> tuple`

```python
data, err = parse_json_body(request)
if err:
    return err  # JsonResponse 400
viewport = data["viewport"]
```

#### `check_viewport_owner(request, viewport_name) -> tuple`

```python
ok, err = check_viewport_owner(request, "cambridge")
if not ok:
    return err  # JsonResponse 403
```

#### `estimate_viewport_size(bounds, num_years) -> float`

Estimate disk usage in MB based on area and number of years, before downloading.

```python
estimate_viewport_size((0.08, 52.18, 0.16, 52.22), num_years=3)
# ~85.0 (MB estimate)
```

---

### 4.2 `api/tasks.py`

**Overview:** Background task management. Starts the processing pipeline in a daemon thread and tracks task status in a module-level dict.

```python
from api.tasks import trigger_data_download_and_processing, tasks, tasks_lock
```

#### `trigger_data_download_and_processing(viewport_name, years=None)`

Spawn background thread that runs the full pipeline. Creates initial progress file immediately so the frontend doesn't read stale data.

```python
trigger_data_download_and_processing("cambridge", years=[2023, 2024])

# Check status:
with tasks_lock:
    status = tasks.get("cambridge_full_pipeline", {}).get("status")
    # "starting" | "in_progress" | "success" | "failed" | "cancelled"
```

---

### 4.3 `api/middleware.py`

**Overview:** Two middleware classes plus auth helpers.

#### `TileShortcircuitMiddleware`

Performance optimisation: tile and bounds requests (`/api/tiles/`) skip all other middleware and go directly to the view.

#### `DemoModeMiddleware`

When auth is enabled, allows unauthenticated read access (GET on non-write endpoints) but requires login for write/destructive operations (POST to create, delete, evaluate, etc.).

```python
from api.middleware import auth_enabled, get_user_quota

auth_enabled()        # True if any Django User exists
get_user_quota(user)  # MB quota (float('inf') for superusers)
```

---

## 5. Import Dependency Map

```
api/views/viewports.py
  ├── lib.config
  ├── lib.viewport_utils
  ├── lib.viewport_writer
  ├── lib.viewport_ops      ← extracted in refactoring
  ├── lib.pipeline
  └── api.helpers, api.tasks

api/views/evaluation.py
  └── lib.evaluation_engine  ← extracted in refactoring

api/views/tiles.py
  ├── lib.config
  ├── lib.viewport_utils
  └── lib.tile_renderer      ← extracted in refactoring

api/views/pipeline.py
  ├── lib.config
  ├── lib.viewport_utils
  ├── lib.viewport_writer
  └── lib.pipeline

api/views/vector_data.py
  ├── lib.config
  └── lib.viewport_utils

api/views/config.py
  └── lib.config

api/tasks.py
  ├── lib.pipeline
  ├── lib.progress_tracker
  └── lib.viewport_writer

api/helpers.py
  ├── lib.config
  └── lib.viewport_utils
```
