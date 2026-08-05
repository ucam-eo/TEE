# TEE Features

Complete feature catalogue for the Tessera Embeddings Explorer, grouped by
functional area.

**New to TEE?** In one sentence: TEE lets you explore, label, and classify
land cover using [Tessera](https://geotessera.org) embeddings — a
128-dimensional numerical fingerprint computed for every 10m × 10m pixel on
Earth's surface, from Sentinel-2 satellite imagery. Pixels with similar
embeddings tend to be the same kind of land cover, even when they're far
apart geographically, so most of what TEE does boils down to comparing
these fingerprints in different ways: finding similar pixels, clustering
them into labels, or training a classifier on them. A **viewport** is the
5km × 5km (or 10km × 10km for Postcard, see §17) area a user is currently
working with; TEE downloads and processes embeddings for one viewport at a
time. See the top-level [README](../README.md) for a quick start and the
[User Guide](../public/user_guide.md) for step-by-step, end-user-facing
instructions — this document is the deeper, developer-facing reference.

---

## 1. Viewer

**Six-panel synchronised viewer.** All panels show the same geographic area;
panning or zooming one panel moves the others. A declarative
`PANEL_LAYOUT` table (in `maps.js`) decides what each panel shows in each
mode — the single source of truth for panel configuration.

**Four modes**, switchable from a header dropdown (except Validation, which
is entered from the Viewport Manager):

| Mode | Panels |
|------|--------|
| **Explore** | OSM, Satellite, Tessera embeddings (RGB), PCA 3D scatter, (blank), (blank) |
| **Change Detection** | OSM, Satellite, Embeddings (Y1), change statistics, change heatmap, Embeddings (Y2) |
| **Labelling** | OSM, Satellite, Embeddings, PCA, classification-results heatmap, label/cluster list |
| **Validation** | Controls, Satellite, Ground-truth class table, Progress log, Learning curves, Confusion matrix |

**Embedding visualisation.** Each pixel's 128-dimensional embedding is
mapped to an RGB colour via percentile normalisation of the first three
PCA bands, giving an immediate visual summary of landscape structure.

**Map sources.** OpenStreetMap; ESRI World Imagery and Google satellite
selectable in Panel 2; acquisition date displayed when available.

**Year switching.** Years 2018–2025 selectable per panel; in Change
Detection the two embedding panels are independent.

**Cross-panel markers.** Single click places a yellow-triangle crosshair on
every panel; useful for comparing position across map, satellite and
embedding views without triggering a search.

---

## 2. Similarity Search

**Instant client-side search.** Double-click any pixel and all matches
across the viewport (~250 000 pixels) are returned in tens of
milliseconds. Brute-force L2 over 128 dims with manual 4-dim loop
unrolling.

**Threshold slider.** Results are cached at a wide threshold (35.0) on the
first search; subsequent slider movements re-filter the cached results
without re-running the search.

**Cross-panel highlighting.** Matches are drawn as a canvas overlay on the
satellite panel and simultaneously coloured in the PCA/UMAP 3D scatter.

**Multi-query union search.** `localSearchSimilarMulti` and
`searchMultiInVectorData` enable searches that union matches across
multiple query embeddings (used by polygon "union" mode and year
timelines).

---

## 3. Dimensionality Reduction

**PCA.** Client-side power iteration with deflation computes the top three
eigenvectors. Subsampled to 40 000 points maximum.

**UMAP.** Runs in a Web Worker; precomputed server-side for each processed
year and cached client-side.

**Three.js 3D scatter.** Interactive rotation/zoom, highlighted
nearest-point crosshair, recolouring by saved labels or manual-label
classes.

---

## 4. Change Detection

**Per-pixel embedding distance heatmap.** Two years compared directly in
embedding space using Euclidean distance, rendered with the Viridis
colormap on Panel 5.

**Distribution statistics.** Stable / minor / moderate / major change bins
shown in Panel 4.

**Interactive distance readout.** Click any pixel to see its embedding
distance between the two selected years.

---

## 5. Labelling

### Three ways to create labels

| Method | How | Best for |
|--------|-----|----------|
| **Auto-label (K-means)** | Set *k*, click *Go*, name and promote clusters | Rapid overview labelling |
| **Manual pin** | Ctrl-click a pixel | Point locations, single fields |
| **Polygon** | Ctrl-double-click to begin, click to add vertices | Large contiguous areas |
| **Similarity expansion** | Adjust a label's threshold slider | Growing a seed pin into a class |

K-means uses K-means++ initialisation; runs in an inline Web Worker;
subsamples when N > max(5 000, k × 500).

### Polygon search modes

- **Mean** — one representative embedding from the polygon interior
- **Union** — every interior pixel embedding used as an independent query

### Hierarchical classification schemas

Three built-in schemas plus user-supplied:

| Schema | Source |
|--------|--------|
| **UKHab v2** | UK Habitat Classification |
| **EUNIS** | European Nature Information System |
| **Habitats of the World** (HOTW) | Global classification |
| **Custom** | User-supplied JSON or tab-indented text |

The schema browser is a draggable floating window with search; selecting
an entry sets the active label's code, name and colour.

### Label management

- Per-class visibility toggle (eye icon)
- Per-class delete
- Per-class threshold slider (debounced via `requestAnimationFrame`)
- **Classify** button — nearest-centroid overlay using all visible
  manual-label embeddings
- **Timeline modal** — year-by-year pixel count for any label
- Cross-year label re-computation when switching embedding year

---

## 6. Export, Import, Sharing

### Export formats

| Format | Mode | Content |
|--------|------|---------|
| **JSON** | Any | Full round-trip (embeddings, vertices, metadata, embedding year, Tessera dataset version) |
| **GeoJSON** | Any | Vector polygons (d3-contour vectorisation of pixel sets) |
| **ESRI Shapefile (ZIP)** | Manual labelling | GIS-compatible polygons |
| **CSV (points)** | Manual labelling | One row per label (name, code, type, lat, lon, pixel count) — for GEE/ArcGIS point import or a spreadsheet |
| **KML** | Manual labelling | One styled Placemark per label (Point or Polygon, matching label colour) — export-only |
| **Map (JPG)** | Explore / auto-label | Screenshot of satellite view with overlays |

Export button turns red when there are unsaved changes.

### Import

JSON, GeoJSON, CSV, or ESRI Shapefile ZIP. Polygons are re-rasterised to
the current viewport's pixel grid on import. CSV import expects a header
row with lat/lon columns (`lat`/`lon`, `latitude`/`longitude`, or `x`/`y`)
plus optional `name`/`code` columns; rows sharing a name are grouped into
one label. KML is export-only.

### Label sharing

Two distinct modes, selected at share time:

- **Private** — embedding + label pairs only, no coordinates; contributes
  anonymised training data to the Tessera global habitat directory.
- **Public** — full ESRI Shapefile with locations; visible to other users
  of the same viewport on the same server.

An import badge on the label toolbar shows the count of shared label sets
available for the current viewport.

---

## 7. Validation (ML Evaluation)

### Supported classifiers

| Name | Features | Implementation |
|------|----------|----------------|
| `nn` | 128-dim embeddings | k-NN (k=5) |
| `rf` | 128-dim embeddings | Random Forest |
| `xgboost` | 128-dim embeddings | XGBoost |
| `mlp` | 128-dim embeddings | sklearn MLP |
| `spatial_mlp` | 3×3 neighbourhood (1152-dim) | sklearn MLP |
| `spatial_mlp_5x5` | 5×5 neighbourhood (3200-dim) | sklearn MLP |
| `unet` | 2D embedding grid patches | PyTorch TinyUNet |

**Regressors** — the same models (k-NN, RF, MLP, XGBoost) are available
as regressors when the ground-truth field is numeric. Auto-detected from
the field dtype, overridable via the `task` parameter.

### Sampling strategies

Stratified point sampling within shapefile polygons, with class weighting
chosen by the user:

- **equal** — same count per class (unbiased but yields meaningless
  weighted-F1)
- **sqrt** — counts proportional to √area (default — balances unbiased
  and area-weighted metrics)
- **proportional** — counts proportional to polygon area

### Shapefile upload

- Upload `.zip` containing `.shp` + `.dbf` + `.shx` + `.prj`
- Automatic reprojection to EPSG:4326
- Multi-field support — user picks which attribute column to use as the
  classification schema
- Estimated labelled-pixel count returned from polygon area
- GeoJSON overlay rendered on Panel 2, capped at 10 000 features
- **No shapefile handy?** TEE ships `austria.zip` — Austrian INVEKOS crop
  field data (42,789 polygons, 17 crop classes) — downloadable from the
  Validation tab or directly at `/sample-data/austria.zip`. Use field
  `HabUK` (not `Crop`, `Habitat`, or `NVC` — see the [User
  Guide](../public/user_guide.md#worked-example-austriazip) for a full
  worked example including a spatial train/test split).

### Streaming evaluation

Learning curves stream as newline-delimited JSON; events include
`field_start`, `start`, `classifier_status`, `progress`,
`confusion_matrices`, `model_ready`, `done`. Each line padded to 18 KB to
defeat WSGI buffering.

### Learning curve machinery

- Test set capped at 200 000 pixels (KNN predict is the bottleneck)
- Adaptive repeats — more at low training fractions, fewer at high
- Pre-computed per-class indices (avoids rescan per iteration)
- Boolean mask for test indices (avoids `np.setdiff1d`)
- Training pixel counts reported per classifier per step

### Hyperparameter variants

Passing a list for `classifier_params[name]` creates separate named
variants (`mlp_v1`, `mlp_v2`, …) evaluated side-by-side on the learning
curves.

### Confusion matrix

Per-classifier matrix; click to pop out in a larger window for large
class counts; percentage-vs-count toggle.

### Spatial train/test splits

User draws training and test bounding boxes on the map; points are
partitioned accordingly before sampling — prevents spatial
auto-correlation from inflating scores.

### Model download

Trained models can be downloaded as `.joblib` (sklearn) or `.pt`
(PyTorch); training is deferred and user-triggered so results appear in
~1 minute even when U-Net training would take 45+ minutes.

### Create Map

Any trained classifier can be run over the entire viewport to produce a
GeoTIFF classification raster with the correct CRS and transform. Streams
progress and downloads automatically on completion.

### Cancellation

A global cancel flag is checked per tile fetch and per learning-curve
step; the browser can abort mid-run without leaving zombie work on the
compute server. Cancel is posted directly to `tee-compute` with CORS so
it still fires if the Django proxy hangs.

### Caching

| Layer | Key | Lifetime |
|-------|-----|----------|
| **Disk NPZ result cache** | (field, year, sampling, gdf_hash) | Persistent |
| **In-memory cache** | (field, year, sampling) | Process |
| **GeoTessera singleton** | — | Process |

Re-running with different classifiers uses the in-memory cache
(instantaneous); after a restart the disk cache loads in under a second.

---

## 8. Data Pipeline

**Viewports** — 5 km × 5 km areas of interest; user-defined bounding box.
Each viewport is processed independently, one year at a time, by a
standalone subprocess (`process_viewport.py`) — not part of the Django
request cycle, so a slow or failed fetch can't tie up a web worker.

**Embeddings client.** `VQTessera` (the `tessera-vq` "bolt-on" package,
see [architecture.md](architecture.md) for the full picture) is the
standard client for every viewport — it replaced plain `GeoTessera` as the
origin-fetch path entirely (previously an opt-in "fast path"; now
unconditional). It talks HTTP to `TESSERA_VQ_URL`: the public,
per-IP-rate-limited nginx gateway by default (so local dev and self-hosted
Docker deployments reach it with no SSH tunnel needed), or a direct
unthrottled backdoor when running co-located with the bolt-on on the
production host. `GeoTessera` itself is still used, deliberately, in two
places: the coverage-probe endpoint (a lightweight registry lookup, no
embedding fetch) and ML evaluation on `tee-compute` (full precision, no
compression — evaluation quality matters more there than fetch speed).

**Per-year processing:**

1. **Fetch mosaic** — request a "quantized structure" (per-tile codebooks
   + index maps, ~5-6x smaller than raw embeddings) from the bolt-on via
   `fetch_quantized_structure`, then reconstruct it locally into a full
   `(H, W, 128)` float32 mosaic. Falls back to a plain per-pixel NPY fetch
   (still via the same `VQTessera` client) if the quantized-structure
   endpoint has no coverage for that bbox/year.
2. **Create pyramids** — percentile-normalise bands 0–2 to uint8, write
   a 6-level PNG pyramid with affine-transform metadata. This is what the
   viewer's satellite-style basemap panels actually show.
3. **Extract vectors** — quantise all 128 bands to uint8 with
   per-dimension min/max, save compressed embeddings + pixel coords +
   metadata. When the quantized-structure fetch succeeded, *also* save the
   compact codebooks+indices bundle directly (no re-quantisation) — the
   browser prefers this smaller format when present, falling back to the
   uint8 mosaic otherwise.
4. **Compute UMAP** — 2D UMAP projection from the embeddings, cached for
   viewer use.

**Progress tracking.** Single JSON progress file per viewport written by
the subprocess (`ProgressTracker`); polled by the frontend every 500 ms;
monotonically increasing percent; no forwarding layer. The orchestrator
(`lib/pipeline.py`) guarantees a terminal `complete`/`error` status even if
the subprocess wait itself fails (e.g. it hangs and hits its own timeout) —
failures always surface as a visible error rather than an indefinite
"processing" spinner.

**Concurrency.** Pipeline subprocesses are capped at 5 concurrent runs
(`api/tasks.py`, a `ThreadPoolExecutor`) — importing many viewports at once
queues rather than launching dozens of subprocesses simultaneously, which
previously could overwhelm the origin fetch and starve every import at
once.

**Cancellation.** SIGTERM to the subprocess, SIGKILL fallback after 2 s;
followed by automatic cleanup of partial output directories.

**Add years.** Existing viewports can have additional years added
incrementally; the pipeline only re-processes the new years.

**Coverage probe.** `POST /api/viewports/embedding-coverage` returns which
years have GeoTessera coverage for a bounding box, shown in the create
form so unavailable years are greyed out.

---

## 9. Tile Server

**Slippy-map PNG tile server** with ETag / 304 caching and transparent
tiles for missing areas. Tile endpoints bypass Django middleware via
`TileShortcircuitMiddleware` for performance; rendered tiles are
LRU-cached (maxsize 2048) keyed by (path, z, x, y, mtime).

Pyramid level mapping collapses six pyramid levels across zooms 0–15.

---

## 10. User Management

**Optional authentication.** Auth activates automatically as soon as one
Django user exists; with no users, TEE runs in demo mode (read-only).

**Landing chooser.** First-time, unauthenticated visitors hitting `/` see
a three-way choice — Sign in / Continue in demo mode / Postcard (§17) —
rather than dropping straight into the full Viewport Manager, which would
otherwise show every existing viewport before a new visitor has any
context for what they're looking at. Once a session exists (logged in, or
auth disabled entirely), `/` skips the chooser and goes straight to the
Viewport Manager, same as before.

**Roles:**

- **Admin** — full control, no quota
- **Enroller** — can create new users (up to the 50-user limit)
- **Regular user** — owns their viewports, subject to disk quota

**Disk quotas** — per-user quota in megabytes, enforced on viewport
creation; estimated viewport size computed from area and number of years
before download starts.

**Private viewports** — visible only to the owner; other users see the
hosting server's public viewports only.

**User management shell** — `scripts/manage.sh` provides an interactive
menu on the hosting server for list / add / remove / quota / grant-enroller
/ revoke-enroller / update-container operations.

---

## 11. Command-Line Interface

**`scripts/tee_evaluate.py`** — standalone CLI for headless batch
evaluation. No Django dependency; uses `tessera_eval` directly.

Features:

- JSON config file (shapefile, fields, classifiers, regressors, year,
  sampling, max_training_samples, spatial splits, …)
- Per-field evaluation with auto-detection of classification vs regression
- `--dry-run` to preview dataset stats and class counts without running ML
- `--stdout` to emit NDJSON directly (for scripting / Python parsing)
- Same classifier and regressor catalogue as the web UI

---

## 12. Deployment

### Three modes (via `scripts/deploy-compute.sh`)

| Mode | Command |
|------|---------|
| **All local** | `./scripts/deploy-compute.sh --local` |
| **Local UI + remote GPU** | `./scripts/deploy-compute.sh --local gpu-box` |
| **Remote GPU (hosted UI)** | `./scripts/deploy-compute.sh gpu-box` |

The browser always opens `http://localhost:8001`. In the remote-GPU mode,
tee-compute on the GPU box proxies non-eval requests back to the hosted
Django server so the user sees a single origin.

### Docker image

`sk818/tee:stable` — single image; Waitress WSGI on port 8001;
collectstatic + migrations on start; env-var-configurable data
directories.

### Version banner

Git version baked into the image at build time via `GIT_VERSION` build
arg; shown in the `/health` JSON and in the viewport-selector header.

---

## 13. Frontend Architecture (for developers)

**Vanilla JavaScript, no build step.** Eight ES modules loaded directly
by `viewer.html`:

| Module | Responsibility |
|--------|----------------|
| `app.js` | Initialisation, dependency system, pollers |
| `maps.js` | Leaflet maps, sync, `PANEL_LAYOUT`, mode switching |
| `vectors.js` | Download, cache, similarity search, canvas overlays |
| `labels.js` | Manual labels, polygons, export, sharing |
| `segmentation.js` | K-means, cluster list |
| `dimreduction.js` | PCA, UMAP, heatmap, Three.js |
| `evaluation.js` | Validation streaming, confusion matrix, Create Map |
| `schema.js` | Schema browser, label selection |

**Cross-module communication** via `window.*` properties bridged with
`Object.defineProperty`. No import/export between modules.

**Dependency cascade** in `app.js` orchestrates: viewport status →
vectors download → PCA compute → heatmap load → UI enable.

**Client-side IndexedDB cache** for vector data, keyed by (viewport,
year).

---

## 14. Backend Architecture (for developers)

**Django + Waitress** serves all HTTP; module decomposition:

| Module | Purpose |
|--------|---------|
| `api/views/viewports.py` | Viewport CRUD + coverage probe |
| `api/views/evaluation.py` | Pure proxy to tee-compute |
| `api/views/pipeline.py` | Progress polling + cancellation |
| `api/views/tiles.py` | Slippy-map tile server |
| `api/views/share.py` | Label sharing |
| `api/views/enrolment.py` | User creation |
| `api/views/vector_data.py` | Raw vector file serving |
| `api/views/config.py` | Static files, health, config, landing-chooser routing |
| `api/views/postcard.py` | Postcard: unauthenticated, rate-limited "fun demo" endpoint (§17) |

**`lib/`** — pure-function backend libraries (config paths, viewport
utils, pipeline orchestration, progress tracker, tile rendering).

### External Tessera-family packages

None of these are vendored in this repo — all four are installed as
ordinary pip/git dependencies (`requirements.txt`), each with its own
GitHub repository and independent version tag. Understanding what each
one is for makes it much easier to tell, when something breaks, whether
the bug is in TEE's own code or in one of these:

| Package | Repo | What it does | Used by |
|---------|------|---------------|---------|
| **`geotessera`** | `ucam-eo/geotessera` | Foundational Python client for Tessera embeddings — downloads/serves the raw 128-dim per-pixel embedding tiles from Tessera's public data store, full precision, no compression. | ML evaluation (`tee-compute`, deliberately full-precision); the coverage-probe endpoint; the historical origin of TEE's pipeline, now superseded there by `tessera-vq` (see §8) |
| **`tessera-zarr-utils`** | `ucam-eo/tessera-zarr-utils` | Fast zarr-based region reader for GeoTessera data (`get_zarr`, `probe_zarr_coverage`, `read_region_chunked`), with cross-UTM-zone EPSG:4326 merging — extracted out of `tessera-eval` into its own package. | Actively used inside `tee-compute`'s evaluation server. **Not** reachable from TEE's own viewport pipeline any more — `process_viewport.py` still imports it, but since `VQTessera` became the unconditional embeddings client there, the code path that would call it (`gt.GeoTessera(...)`) never runs; left in place as known dead code rather than removed |
| **`tessera-vq`** | `sk818/tessera-vq` | The "VQ bolt-on": compresses Tessera embeddings by replacing raw 128-dim floats with small per-tile codebooks + index maps (vector quantization). `VQTessera`, its plug-compatible client (same `fetch_mosaic_for_region`-shaped interface as `GeoTessera`), is the standard way TEE fetches embeddings for every viewport (see §8). The server half runs on `tee.cl.cam.ac.uk`, reachable via a public rate-limited nginx gateway or, co-located, a direct unthrottled backdoor. |  `process_viewport.py`, `api/embeddings_provider.py`, `api/views/postcard.py` |
| **`tessera-eval`** | `ucam-eo/tessera-eval` | Framework-independent ML library: classifier/regressor factories, learning-curve machinery, spatial train/test splitting, and the `tee-compute` Flask server itself. Deliberately fetches embeddings via plain `geotessera` (full precision) rather than `tessera-vq` — evaluation quality matters more than fetch speed, and there's no responsiveness requirement for a batch training run. | The whole of §7 (Validation / ML Evaluation), `scripts/tee_evaluate.py` |

**Testing:**

- `validation/` — refactoring guards (API contracts, DOM elements, CSS
  mode rules, `PANEL_LAYOUT` coverage, NDJSON event schema,
  `tessera_eval` self-containment, HTML structure and JS syntax, label
  export/import round-trip)
- `tests/` — unit tests for CLI, k-fold CV, spatial splits, rasteriser,
  tile-disk cache, upload proxy

---

## 15. Privacy Guarantees

| Data | Where it lives |
|------|----------------|
| Similarity searches | Browser only |
| Manual labels (draft) | Browser localStorage |
| Saved labels (unshared) | Browser localStorage |
| Ground-truth shapefiles | `tee-compute` process memory + cache (user-controlled host) |
| Evaluation results | `tee-compute` process (user-controlled host) |
| Trained models | `tee-compute` disk (user-controlled host) |
| Viewport bounds and year | Hosted Django server |
| Map tiles and satellite | Hosted Django server + external providers |
| Shared labels (opt-in) | Hosted Django server |

The hosted server never sees ground-truth data, model parameters, or
evaluation outputs unless the user explicitly shares a label set.

---

## 16. Extensibility

### Adding a classifier

1. Factory function in `tessera_eval/classify.py`
2. Add to `available_classifiers()` / `available_regressors()`
3. Add checkbox in `#validation-controls` in `viewer.html`

No `server.py` changes needed — classifier names are read from the
request.

### Adding a panel mode

1. New 6-entry array in `PANEL_LAYOUT` (`maps.js`)
2. Entry in `PANEL5_LAYER_RULES` if any Leaflet layer toggles are needed
3. Overlay element IDs added to `SWITCHABLE`
4. Element added to correct panel's HTML
5. `display: none` CSS rule for new element
6. Option in the layout dropdown

### Adding an evaluation endpoint

1. Endpoint in `tessera_eval/server.py`
2. Proxy function in `api/views/evaluation.py`
3. URL pattern in `api/urls.py`
4. Call from `evaluation.js`

### Adding an NDJSON event type

1. Yield in `server.py` stream generator
2. Handler in `evaluation.js` `handleStreamEvent()`
3. Add to `TestNDJSONEventSchema.BACKEND_EVENTS` in
   `test_refactoring_guards.py`

---

## 17. Postcard

A narrow, deliberately "just for fun" feature: pick anywhere on Earth and
get a downloadable image of that patch of land, generated from its real
Tessera embeddings — something to use as a wallpaper or print. It exists
partly to make TEE more approachable (no account, no viewport-creation
concepts needed) and partly to show off what embeddings look like without
any explanation required.

**Where it lives.** `public/postcard.html` — a fully standalone page
(its own Leaflet map, its own Nominatim city search, no dependency on
`viewport_selector.html`). Reachable from the landing chooser (§10), a
header link on the main Viewport Manager page, and directly at
`/postcard.html`.

**How it works:**

1. Search for a city (same Nominatim geocoding as viewport creation) or
   click the map directly. A 10km × 10km frame follows the cursor and
   locks in on click — click elsewhere to reposition, same interaction
   as positioning a new viewport.
2. "Generate Postcard" posts the frame's center point to
   `POST /api/postcard/generate`, which fetches one year of embeddings for
   a 10km area around it, crops the result down to exactly that area
   (the bolt-on generally returns more than requested — rounded out to
   whole tiles — so this crop matters; see `_center_crop` in
   `api/views/postcard.py`), and renders the first 3 embedding dimensions
   directly as an RGB JPEG (percentile-stretched per channel — the same
   technique the viewer's own satellite pyramid tiles use). No PCA: it
   was tried and read as garish, arbitrary color.
3. The 1000×1000px result (10km at Tessera's native 10m/pixel resolution)
   is returned directly — nothing is written to disk, no viewport object
   is created, nothing persists after the request completes.

**Unauthenticated by design.** `/api/postcard/generate` is deliberately
*not* in `WRITE_ENDPOINTS` (`api/middleware.py`), so demo-mode users can
reach it without logging in — unlike real viewport creation, which is
gated. Per-IP rate limited instead (in-memory, 5 requests / 10 minutes by
default, `POSTCARD_RATE_LIMIT_MAX` / `POSTCARD_RATE_LIMIT_WINDOW_SECONDS`)
since it's reachable by anyone.

**Known limitation.** The reconstructed mosaic isn't perfectly aligned to
true north — source tiles reprojected onto an axis-aligned canvas leave
real content as a very slightly rotated parallelogram with a thin
nodata-padding sliver at one edge. This is inherent to
`reconstruct_from_structure()` in `tessera-vq` (confirmed present in
regular viewports' own satellite tiles too, not specific to Postcard) —
not something the crop step can fix on its own.
