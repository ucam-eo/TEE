# Plan: Extract `tessera-tools` Python Library from TEE

**Status:** Planned (not started)
**Priority:** Future work
**Goal:** Make TEE's backend logic available as a standalone pip-installable
library for use by `geotessera-examples` and other downstream projects.

---

## 1. Motivation

TEE's `lib/` directory contains pure Python functions with no Django dependencies.
These are useful independently:

- Researchers want to run evaluation (learning curves, classifiers) from scripts
- The `geotessera-examples` repo needs viewport processing and classification
- Other tools may want to load vectors, rasterize shapefiles, or build pyramids

Currently these functions are locked inside TEE's repo and can't be imported
without cloning the whole project.

---

## 2. Library Name and Scope

**Package name:** `tessera-tools` (PyPI) / `tessera_tools` (Python import)
**Repo:** `ucam-eo/tessera-tools`

### What goes into the library

| Current location | Library module | Key exports |
|---|---|---|
| `lib/evaluation_engine.py` | `tessera_tools.evaluation` | `load_vectors`, `dequantize`, `rasterize_shapefile`, `gather_spatial_features`, `make_classifier`, `run_learning_curve` |
| `lib/tile_renderer.py` | `tessera_tools.tiles` | `tile_to_bbox`, `render_tile_png` |
| `lib/viewport_utils.py` | `tessera_tools.viewport` | `parse_viewport_content`, `validate_viewport_name`, `list_viewports`, `read_viewport_file` |
| `lib/viewport_writer.py` | `tessera_tools.viewport` | `create_viewport_from_bounds` |
| `lib/viewport_ops.py` | `tessera_tools.viewport` | `check_readiness`, `delete_viewport_data`, `compute_data_size` |
| `lib/config.py` | `tessera_tools.config` | `DATA_DIR`, `VECTORS_DIR`, etc. (configurable, not hardcoded) |
| `lib/pipeline.py` | `tessera_tools.pipeline` | `PipelineRunner` |
| `lib/progress_tracker.py` | `tessera_tools.progress` | `ProgressTracker` |
| `process_viewport.py` | `tessera_tools.process` | `process_viewport`, `process_year` |
| `create_pyramids.py` | `tessera_tools.pyramids` | `create_pyramid_level` (legacy, optional) |

### What stays in TEE

| File | Why |
|---|---|
| `api/views/*.py` | Django HTTP layer — thin wrappers around library functions |
| `api/middleware.py` | Django-specific auth |
| `api/tasks.py` | Django-specific background task management |
| `api/urls.py` | Django URL routing |
| `public/` | Frontend (HTML, JS, CSS) |
| `tee_project/` | Django settings |
| `Dockerfile` | TEE-specific deployment |

---

## 3. Library API Design

### 3.1 `tessera_tools.config`

Configurable paths — no hardcoded defaults. Users pass paths explicitly or
set environment variables.

```python
from tessera_tools.config import Config

# Explicit paths
cfg = Config(
    data_dir="/data",
    vectors_dir="/data/vectors",
    pyramids_dir="/data/pyramids",
)

# Or from environment (same TEE_DATA_DIR convention)
cfg = Config.from_env()
```

### 3.2 `tessera_tools.evaluation`

The main value for downstream users.

```python
from tessera_tools.evaluation import (
    load_vectors,
    rasterize_shapefile,
    make_classifier,
    run_learning_curve,
)

# Load vectors for a viewport/year
vectors, coords, metadata = load_vectors("/data/vectors/cambridge/2024")

# Rasterize a shapefile onto the vector grid
import geopandas as gpd
from affine import Affine
gdf = gpd.read_file("ground_truth.shp")
gt = metadata["geotransform"]
transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])
class_raster = rasterize_shapefile(gdf, "habitat", transform, width, height)

# Run learning curve evaluation
for event in run_learning_curve(vectors, labels, ["nn", "rf", "mlp"], sizes):
    if event["type"] == "progress":
        print(f"  Size {event['size']}: {event['classifiers']}")
```

### 3.3 `tessera_tools.viewport`

```python
from tessera_tools.viewport import (
    create_viewport_from_bounds,
    check_readiness,
    list_viewports,
)

# Create a viewport
path = create_viewport_from_bounds(
    "cambridge", (0.08, 52.18, 0.16, 52.22),
    viewports_dir="/data/viewports"
)

# Check if data is ready
status = check_readiness("cambridge", config=cfg)
```

### 3.4 `tessera_tools.process`

```python
from tessera_tools.process import process_viewport

# Process a viewport (download + pyramids + vectors)
results = process_viewport(
    viewport_name="cambridge",
    bounds=(0.08, 52.18, 0.16, 52.22),
    years=[2023, 2024],
    config=cfg,
    progress_callback=lambda pct, msg: print(f"{pct}% {msg}"),
)
```

### 3.5 `tessera_tools.pipeline`

```python
from tessera_tools.pipeline import PipelineRunner

runner = PipelineRunner(project_root="/app", venv_python="/app/venv/bin/python3")
success, error = runner.run_full_pipeline("cambridge", years_str="2023,2024")
```

### 3.6 `tessera_tools.tiles`

```python
from tessera_tools.tiles import tile_to_bbox, render_tile_png

bbox = tile_to_bbox(2048, 1362, 12)
png_bytes = render_tile_png("/data/pyramids/cambridge/2024/level_0.png", 12, 2048, 1362)
```

---

## 4. Dependencies

The library should have minimal dependencies:

```
# Required
numpy>=1.24
geopandas>=0.14
rasterio>=1.3
scikit-learn>=1.3
Pillow>=10.0
affine>=2.4
geotessera>=0.7.5

# Optional (for specific classifiers)
xgboost  # for XGBoost classifier
torch    # for U-Net classifier
```

---

## 5. Package Structure

```
tessera-tools/
├── pyproject.toml          # Package metadata, dependencies
├── README.md               # Library documentation with examples
├── LICENSE                  # MIT
├── tessera_tools/
│   ├── __init__.py         # Version, top-level imports
│   ├── config.py           # Configurable paths (from lib/config.py)
│   ├── evaluation.py       # ML evaluation (from lib/evaluation_engine.py)
│   ├── viewport.py         # Viewport CRUD (from lib/viewport_utils.py + writer + ops)
│   ├── process.py          # Viewport processing (from process_viewport.py)
│   ├── pipeline.py         # Pipeline runner (from lib/pipeline.py)
│   ├── progress.py         # Progress tracking (from lib/progress_tracker.py)
│   ├── tiles.py            # Tile rendering (from lib/tile_renderer.py)
│   └── pyramids.py         # Pyramid building (from create_pyramids.py, legacy)
├── tests/
│   ├── test_evaluation.py
│   ├── test_viewport.py
│   └── test_tiles.py
└── examples/
    ├── run_evaluation.py   # Standalone evaluation script
    ├── process_viewport.py # Standalone viewport processing
    └── classify_region.py  # End-to-end classification example
```

---

## 6. Migration Strategy (Incremental, Not Big-Bang)

**Phase 1: Create the package with copies (no TEE changes)**

1. Create `ucam-eo/tessera-tools` repo
2. Copy `lib/*.py` and `process_viewport.py` into `tessera_tools/`
3. Refactor `config.py` to use explicit paths instead of env vars
4. Add `pyproject.toml`, tests, README
5. Publish to PyPI as `tessera-tools`
6. Add examples to `geotessera-examples`

**Phase 2: TEE uses the library (replace lib/)**

7. Add `tessera-tools>=0.1.0` to TEE's `requirements.txt`
8. Replace `from lib.evaluation_engine import ...` with `from tessera_tools.evaluation import ...`
9. Replace `from lib.config import ...` with TEE-specific config that wraps `tessera_tools.config`
10. Remove `lib/` directory from TEE
11. Keep `api/views/*.py` as thin Django wrappers

**Phase 3: Stabilize and version**

12. Tag `tessera-tools` v1.0.0 when API is stable
13. Document in `geotessera-examples` how to use it

---

## 7. Key Design Decisions

### Config must be explicit, not implicit

Current `lib/config.py` reads `TEE_DATA_DIR` from environment. The library
version should accept paths as constructor arguments, with env var fallback:

```python
# Library: explicit
cfg = Config(data_dir="/my/data")

# TEE: wraps with env vars
cfg = Config.from_env()  # reads TEE_DATA_DIR
```

### No Django dependency

The library must never import Django. All Django-specific code stays in TEE's
`api/` directory. The library's only dependencies are numpy, geopandas,
rasterio, scikit-learn, Pillow, and geotessera.

### Backward compatibility for TEE

During Phase 2, TEE's `lib/` imports change from:
```python
from lib.evaluation_engine import load_vectors
```
to:
```python
from tessera_tools.evaluation import load_vectors
```

This can be done with a compatibility shim during transition:
```python
# lib/evaluation_engine.py (shim)
from tessera_tools.evaluation import *
```

### Progress callback instead of ProgressTracker

The library should use a simple callback pattern instead of file-based
progress tracking:

```python
def run_learning_curve(..., progress_callback=None):
    for event in ...:
        if progress_callback:
            progress_callback(event)
        yield event
```

TEE's `api/views/evaluation.py` can wrap this with its `ProgressTracker`
for file-based progress.

---

## 8. Example: geotessera-examples Usage

```python
"""Evaluate habitat classification accuracy using Tessera embeddings."""

import geotessera as gt
import geopandas as gpd
from tessera_tools.evaluation import (
    load_vectors, rasterize_shapefile, run_learning_curve
)
from affine import Affine

# 1. Download embeddings for a region
tessera = gt.GeoTessera()
mosaic, transform, crs = tessera.fetch_mosaic_for_region(
    bbox=(0.08, 52.18, 0.16, 52.22), year=2024
)

# 2. Load processed vectors (or process from mosaic)
vectors, coords, metadata = load_vectors("./vectors/cambridge/2024")

# 3. Load ground truth
gdf = gpd.read_file("ground_truth.shp")
gt_meta = metadata["geotransform"]
affine = Affine(gt_meta["a"], gt_meta["b"], gt_meta["c"],
                gt_meta["d"], gt_meta["e"], gt_meta["f"])
class_raster = rasterize_shapefile(gdf, "habitat", affine,
                                    metadata["mosaic_width"],
                                    metadata["mosaic_height"])

# 4. Run evaluation
pixel_labels = class_raster[coords[:, 1], coords[:, 0]]
mask = pixel_labels > 0
for event in run_learning_curve(
    vectors[mask], pixel_labels[mask],
    classifier_names=["nn", "rf", "mlp"],
    training_sizes=[100, 1000, 10000],
):
    if event["type"] == "progress":
        for name, scores in event["classifiers"].items():
            print(f"  {name}: F1={scores['mean_f1']:.3f} (n={event['size']})")
```

---

## 9. Estimated Effort

| Phase | Work | Estimate |
|---|---|---|
| Phase 1 (create package) | Copy, refactor config, add tests, pyproject.toml | 1-2 days |
| Phase 2 (TEE migration) | Replace imports, test, remove lib/ | 1 day |
| Phase 3 (stabilize) | Examples, docs, version tag | 0.5 day |
| **Total** | | **2.5-3.5 days** |

---

## 10. Risks

- **Breaking TEE during migration**: Mitigated by Phase 1 being copies (TEE unchanged)
  and Phase 2 using compatibility shims
- **API instability**: The library API should be considered unstable until v1.0.
  Use `0.x` versioning with clear deprecation warnings.
- **Double quantization**: The library inherits TEE's double quantization issue
  (int8 → float32 → uint8). This will be resolved by the Zarr migration, which
  should also be planned as a library feature.
