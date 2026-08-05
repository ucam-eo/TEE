# TEE Documentation

Tessera Embeddings Explorer (TEE) is a Django + vanilla JavaScript web application
for interactive exploration of satellite embedding vectors produced by the
[Tessera](https://github.com/ucam-eo/tessera) foundation model (accessed via
the [GeoTessera](https://github.com/ucam-eo/geotessera) Python library).  Users
define geographic viewports, browse PCA/UMAP projections of 128-dimensional
embeddings, create land-cover labels via similarity search, run change-detection
across years, and evaluate classifiers against ground-truth shapefiles.

---

## Table of Contents

| Document | Description |
|---|---|
| [architecture.md](architecture.md) | System architecture, panel layout, module graph, state management, dependencies, testing, deployment |
| [features.md](features.md) | Complete feature catalogue, grouped by functional area |
| [frontend_api.md](frontend_api.md) | JavaScript API reference for all 8 ES modules, data structures, frontend→backend call map |
| [backend_api.md](backend_api.md) | Python HTTP endpoints, library modules, standalone scripts |
| [tessera-packages.md](tessera-packages.md) | How TEE's four external Tessera-family dependencies (`geotessera`, `tessera-zarr-utils`, `tessera-vq`, `tessera-eval`) relate to each other, what each provides, and how their requirements actually resolve |
| [extension_guide.md](extension_guide.md) | Recipes for adding panels, classifiers, data sources, endpoints, label types — with checklist |
| [label-format.md](label-format.md) | Label serialisation spec: JSON + GeoJSON export formats, CRS conventions, localStorage schema |
| [examples/](examples/README.md) | Ready-to-edit custom classification-schema templates (JSON + tab-indented text) |

---

## Key Features

- **Six-panel viewer** with four modes: explore, change-detection, labelling, validation
- **Client-side similarity search** over 128-dim float32 embeddings (~250K vectors per viewport) using brute-force L2
- **PCA and UMAP** dimensionality reduction rendered as interactive 3D scatter plots (Three.js)
- **K-means segmentation** via inline Web Worker with K-means++ initialisation
- **Change-detection heatmap** comparing per-pixel embedding distances across years
- **Manual labelling** with point, polygon, and similarity-expansion label types
- **Hierarchical schema browser** for structured label ontologies (UKHab v2, HOTW, EUNIS, or custom)
- **ML evaluation pipeline** with streaming NDJSON learning curves (k-NN, RF, XGBoost, MLP, spatial MLP, U-Net)
- **Confusion matrix** with interactive pop-up and percentage toggle
- **Create Map** — generate a GeoTIFF classification raster from any trained classifier
- **Export** labels as JSON, GeoJSON, or ESRI Shapefile (pixel labels vectorized to polygons via d3-contour); download trained models
- **Label sharing** — contribute to the Tessera global habitat directory (private) or share with other users (public)
- **VQ embeddings client** — `tessera-vq` (the "VQ bolt-on") is the standard way TEE fetches embeddings for every viewport, replacing per-tile GeoTessera/zarr downloads; see [tessera-packages.md](tessera-packages.md)

---

## Quick Orientation

```
TEE/
  public/js/                 8 ES modules loaded by viewer.html
  public/schemas/            Built-in classification schemas (UKHab, HOTW, EUNIS)
  public/viewer.html         Main viewer (6-panel layout)
  api/views/                 Django view modules (HTTP endpoints)
  api/urls.py                URL routing
  lib/                       Pure-function backend libraries (paths, viewport ops, tile rendering, pipeline)
  process_viewport.py        Pipeline script (subprocess) — fetches tiles → pyramids → vectors
  scripts/deploy-compute.sh  Start tee-compute (use --local for Django + tee-compute on localhost)
  docs/                      This documentation
```

The frontend is vanilla JavaScript with no build step.  Modules communicate
through `window.*` properties bridged via `Object.defineProperty`.  The backend
is Django served by Waitress WSGI; ML evaluation runs on a separate Flask
service (`tee-compute`) which Django proxies on `/api/evaluation/*`.  A
background pipeline downloads embedding tiles via `tessera-vq` (falling back
to a plain per-pixel fetch on failure) and builds pyramids + vector data —
see [tessera-packages.md](tessera-packages.md) for how that and the other
three Tessera-family dependencies fit together.
