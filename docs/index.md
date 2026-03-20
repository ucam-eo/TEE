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
| [frontend_api.md](frontend_api.md) | JavaScript API reference for all 8 ES modules, data structures, frontend→backend call map |
| [backend_api.md](backend_api.md) | Python HTTP endpoints, library modules, standalone scripts |
| [extension_guide.md](extension_guide.md) | Recipes for adding panels, classifiers, data sources, endpoints, label types — with checklist |

---

## Key Features

- **Six-panel viewer** with four modes: explore, change-detection, labelling, validation
- **Client-side similarity search** over 128-dim float32 embeddings (~250K vectors per viewport) using brute-force L2
- **PCA and UMAP** dimensionality reduction rendered as interactive 3D scatter plots (Three.js)
- **K-means segmentation** via inline Web Worker with K-means++ initialisation
- **Change-detection heatmap** comparing per-pixel embedding distances across years
- **Manual labelling** with point, polygon, and similarity-expansion label types
- **Hierarchical schema browser** for structured label ontologies (UKHab, HOTW, or custom)
- **ML evaluation pipeline** with streaming NDJSON learning curves (k-NN, RF, MLP, spatial MLP, U-Net)
- **Confusion matrix** with interactive pop-up and percentage toggle
- **Export** labels as JSON, GeoJSON, or ESRI Shapefile; download trained models
- **Label sharing** — contribute to the Tessera global habitat directory (private) or share with other users (public)

---

## Quick Orientation

```
TEE/
  public/js/           8 ES modules loaded by viewer.html
  public/viewer.html   Main viewer (6-panel layout)
  api/views/           Django view modules (HTTP endpoints)
  api/urls.py          URL routing
  lib/                 Pure-function backend libraries
  process_viewport.py  Pipeline script (subprocess)
  create_pyramids.py   Legacy satellite pyramid builder (unused)
  docs/                This documentation
```

The frontend is vanilla JavaScript with no build step.  Modules communicate
through `window.*` properties bridged via `Object.defineProperty`.  The backend
is Django served by Waitress WSGI, with a background pipeline that downloads
embedding tiles via the GeoTessera library and builds pyramids + vector data.
