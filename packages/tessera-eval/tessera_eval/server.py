"""Lightweight compute server for tessera-eval.

Handles ML evaluation locally and proxies everything else (UI, tiles,
label sharing) to a hosted TEE server. This lets users run compute on
their own machine while using the hosted server for data.

Usage:
    tee-compute --hosted https://tee.cl.cam.ac.uk
    tee-compute --hosted https://tee.cl.cam.ac.uk --port 8001
"""

import argparse
import json
import logging
import tempfile
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
from flask import Flask, Response, jsonify, request, send_file

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.after_request
def _add_cors(response):
    """Allow browsers on any origin to talk to tee-compute directly."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ── State (single-user, one process) ──

_uploaded_shapefiles = []  # list of (filename, gdf) tuples
_merged_gdf = None
_trained_models = {}  # classifier name → temp file path
_finish_classifiers = set()
_tile_cache = {"key": None, "vectors": None, "labels": None, "class_names": None,
               "stats": None, "spatial_3x3": None, "spatial_5x5": None}
_hosted_url = None
_tile_disk_cache_dir = None  # set in main()
_geotessera_instance = None  # cached to avoid 10-30s registry init per run
_cancel_flag = None  # threading.Event, set when user cancels

FLUSH_PAD = 18 * 1024  # pad NDJSON lines to force Waitress flush


def _get_cache_dir():
    """Return the cache directory, creating it if needed."""
    global _tile_disk_cache_dir
    if _tile_disk_cache_dir is None:
        _tile_disk_cache_dir = Path.home() / ".cache" / "tessera-eval"
    _tile_disk_cache_dir.mkdir(parents=True, exist_ok=True)
    return _tile_disk_cache_dir


def _result_cache_path(field, year, gdf_hash, sampling="equal"):
    """Return the disk path for cached evaluation results (vectors + labels)."""
    return _get_cache_dir() / f"result_{field}_{year}_{sampling}_{gdf_hash}.npz"


def _gdf_hash(gdf):
    """Quick hash of a GeoDataFrame for cache keying."""
    import hashlib
    h = hashlib.md5()
    h.update(str(len(gdf)).encode())
    h.update(str(sorted(gdf.columns.tolist())).encode())
    bounds = gdf.total_bounds
    h.update(f"{bounds[0]:.4f},{bounds[1]:.4f},{bounds[2]:.4f},{bounds[3]:.4f}".encode())
    return h.hexdigest()[:12]


def _load_cached_result(field, year, gdf, sampling="equal"):
    """Load cached evaluation result. Returns (vectors, labels, class_names, stats) or None."""
    path = _result_cache_path(field, year, _gdf_hash(gdf), sampling)
    if path.exists():
        try:
            data = np.load(path, allow_pickle=True)
            return (data["vectors"], data["labels"],
                    data["class_names"].tolist(), dict(data["stats"].item()))
        except Exception:
            path.unlink(missing_ok=True)
    return None


def _extract_tile_patches(gt, gdf, field_name, year, le, n_classes,
                          patch_size=256, max_patches=500,
                          needs_spatial_3x3=False, needs_spatial_5x5=False,
                          sample_points_lonlat=None,
                          logger=None, progress_cb=None, cancel_flag=None):
    """Extract pixel-aligned 2D patches and optionally point samples from tiles.

    Fetches tiles once and extracts both:
    - Random patch_size × patch_size crops for U-Net / spatial MLP
    - Point sample embeddings (if sample_points_lonlat provided)

    Returns (unet_patches, spatial_3x3, spatial_5x5, point_vectors) where
    point_vectors is a (N, 128) array if sample_points_lonlat was given, else None.
    """
    from tessera_eval.classify import gather_spatial_features_2d
    from tessera_eval.rasterize import rasterize_shapefile
    from rasterio.transform import array_bounds
    from shapely.geometry import box as _box
    import rasterio.transform

    rng = np.random.RandomState(42)

    # Find tiles overlapping the shapefile
    bounds = gdf.total_bounds
    bbox = (bounds[0], bounds[1], bounds[2], bounds[3])
    tiles_to_fetch = gt.registry.load_blocks_for_region(bbox, year)  # returns [(year, lon, lat), ...]
    # Shuffle tiles so patches come from diverse geographic regions
    tiles_to_fetch = list(tiles_to_fetch)
    rng.shuffle(tiles_to_fetch)

    # Pre-group sample points by tile for efficient extraction
    point_vectors = None
    points_by_tile = {}
    if sample_points_lonlat is not None and len(sample_points_lonlat) > 0:
        point_vectors = np.full((len(sample_points_lonlat), 128), np.nan, dtype=np.float32)
        # Group points into 0.1° tile bins
        for pt_idx, (lon, lat) in enumerate(sample_points_lonlat):
            # Snap to tile center (0.05 offset, 0.1 spacing)
            tlon = round((lon - 0.05) / 0.1) * 0.1 + 0.05
            tlat = round((lat - 0.05) / 0.1) * 0.1 + 0.05
            key = (round(tlon, 2), round(tlat, 2))
            if key not in points_by_tile:
                points_by_tile[key] = []
            points_by_tile[key].append(pt_idx)

    if logger:
        pts_info = f", {len(sample_points_lonlat)} sample points" if sample_points_lonlat is not None else ""
        logger.info("Fetching %d tiles (shuffled)%s...", len(tiles_to_fetch), pts_info)

    unet_patches = []
    all_spatial_3x3 = [] if needs_spatial_3x3 else None
    all_spatial_5x5 = [] if needs_spatial_5x5 else None

    patches_per_tile = 5  # cap per tile to ensure geographic diversity
    total_tiles = len(tiles_to_fetch)

    for t_idx, (yr, tlon, tlat, tile_emb, crs, transform) in enumerate(
            gt.fetch_embeddings(tiles_to_fetch)):
        if cancel_flag and cancel_flag.is_set():
            if logger:
                logger.info("Tile extraction cancelled")
            break
        if progress_cb:
            progress_cb(t_idx, total_tiles)

        h, w = tile_emb.shape[:2]
        tile_emb = tile_emb.astype(np.float32)

        # Extract point samples from this tile (if requested)
        tile_key = (round(tlon, 2), round(tlat, 2))
        if tile_key in points_by_tile:
            from pyproj import Transformer
            transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            for pt_idx in points_by_tile[tile_key]:
                lon, lat = sample_points_lonlat[pt_idx]
                x, y = transformer.transform(lon, lat)
                row, col = rasterio.transform.rowcol(transform, x, y)
                if 0 <= row < h and 0 <= col < w:
                    point_vectors[pt_idx] = tile_emb[row, col]

        patches_full = len(unet_patches) >= max_patches
        if patches_full:
            continue  # still iterate for point samples, skip patch extraction

        if h < patch_size or w < patch_size:
            if logger:
                logger.info("  Skipping tile (%d×%d) — smaller than patch size %d", h, w, patch_size)
            continue

        if logger:
            logger.info("Tile %d/%d (%.2f, %.2f): %s, extracting patches...",
                        t_idx + 1, total_tiles, tlon, tlat, tile_emb.shape[:2])

        # Reproject GDF to tile CRS for rasterization
        tile_gdf = gdf.to_crs(crs)
        tile_bounds = array_bounds(h, w, transform)
        tile_gdf = tile_gdf[tile_gdf.intersects(_box(*tile_bounds))]
        if tile_gdf.empty:
            continue

        # Rasterize labels for the full tile
        tile_labels = rasterize_shapefile(tile_gdf, field_name, transform,
                                          h, w, label_encoder=le)

        # Find rows/cols where labels exist, with enough margin for a patch
        labelled_rows, labelled_cols = np.where(tile_labels > 0)
        if len(labelled_rows) == 0:
            continue

        margin = patch_size // 2
        valid = ((labelled_rows >= margin) & (labelled_rows < h - margin) &
                 (labelled_cols >= margin) & (labelled_cols < w - margin))
        valid_rows = labelled_rows[valid]
        valid_cols = labelled_cols[valid]
        if len(valid_rows) == 0:
            continue

        # Pick random centers
        n_pick = min(patches_per_tile, len(valid_rows), max_patches - len(unet_patches))
        idx = rng.choice(len(valid_rows), size=n_pick, replace=False)

        for i in idx:
            r, c = valid_rows[i], valid_cols[i]
            r0, r1 = r - margin, r + margin
            c0, c1 = c - margin, c + margin

            emb_patch = tile_emb[r0:r1, c0:c1]
            label_patch = tile_labels[r0:r1, c0:c1]

            if emb_patch.shape != (patch_size, patch_size, tile_emb.shape[2]):
                continue
            if label_patch.shape != (patch_size, patch_size):
                continue
            if (label_patch > 0).sum() < 10:
                continue

            # Replace NaN with 0
            nan_mask = np.isnan(emb_patch)
            if nan_mask.any():
                emb_patch = emb_patch.copy()
                emb_patch[nan_mask] = 0.0

            unet_patches.append((emb_patch, label_patch.astype(np.int32)))

            # Subsample labelled pixels for spatial features to cap memory
            # (~300MB per full 256×256 patch at 3×3, ~800MB at 5×5)
            labelled_mask = label_patch > 0
            MAX_SPATIAL_PX = 5000  # per patch — 100 patches × 5K = 500K total
            n_labelled = labelled_mask.sum()
            if n_labelled > MAX_SPATIAL_PX and (needs_spatial_3x3 or needs_spatial_5x5):
                # Randomly zero out excess pixels in the mask
                rows, cols = np.where(labelled_mask)
                keep = rng.choice(len(rows), size=MAX_SPATIAL_PX, replace=False)
                labelled_mask = np.zeros_like(labelled_mask)
                labelled_mask[rows[keep], cols[keep]] = True

            if needs_spatial_3x3:
                sf = gather_spatial_features_2d(emb_patch, radius=1, mask=labelled_mask)
                all_spatial_3x3.append(sf)
            if needs_spatial_5x5:
                sf = gather_spatial_features_2d(emb_patch, radius=2, mask=labelled_mask)
                all_spatial_5x5.append(sf)

        if logger:
            logger.info("  %d patches so far (%d from this tile)", len(unet_patches), n_pick)

    spatial_3x3 = np.concatenate(all_spatial_3x3, axis=0).astype(np.float32) if all_spatial_3x3 else None
    spatial_5x5 = np.concatenate(all_spatial_5x5, axis=0).astype(np.float32) if all_spatial_5x5 else None

    if logger:
        s3 = f", spatial_3x3={spatial_3x3.shape}" if spatial_3x3 is not None else ""
        s5 = f", spatial_5x5={spatial_5x5.shape}" if spatial_5x5 is not None else ""
        logger.info("Tile patches: %d total%s%s", len(unet_patches), s3, s5)

    return unet_patches, spatial_3x3, spatial_5x5, point_vectors


def _save_cached_result(field, year, gdf, vectors, labels, class_names, stats, sampling="equal"):
    """Save evaluation result to disk cache."""
    try:
        path = _result_cache_path(field, year, _gdf_hash(gdf), sampling)
        np.savez_compressed(path, vectors=vectors, labels=labels,
                            class_names=np.array(class_names),
                            stats=np.array(stats))
    except Exception as e:
        logger.debug("Failed to save result cache: %s", e)


def _padded(gen):
    """Pad each NDJSON line to exceed Waitress send_bytes buffer."""
    for chunk in gen:
        if len(chunk) < FLUSH_PAD:
            yield chunk + " " * (FLUSH_PAD - len(chunk))
        else:
            yield chunk


def _get_merged_gdf():
    """Return merged GeoDataFrame from all uploaded shapefiles."""
    global _merged_gdf
    if _merged_gdf is not None:
        return _merged_gdf
    if not _uploaded_shapefiles:
        return None
    import pandas as pd
    _merged_gdf = gpd.GeoDataFrame(
        pd.concat([g for _, g in _uploaded_shapefiles], ignore_index=True))
    return _merged_gdf


# ── Local evaluation endpoints ──

@app.route("/api/evaluation/upload-shapefile", methods=["POST"])
def upload_shapefile():
    """Accept a .zip containing .shp/.dbf/.shx/.prj, append to shapefile list."""
    global _merged_gdf
    uploaded = request.files.get("file")
    if not uploaded:
        return jsonify({"error": "No file uploaded"}), 400

    if not uploaded.filename.endswith(".zip"):
        return jsonify({"error": "File must be a .zip"}), 400

    tmp_dir = tempfile.mkdtemp(prefix="tee_eval_")
    zip_path = Path(tmp_dir) / uploaded.filename
    uploaded.save(str(zip_path))

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)
    except zipfile.BadZipFile:
        return jsonify({"error": "Invalid zip file"}), 400

    shp_files = list(Path(tmp_dir).rglob("*.shp"))
    if not shp_files:
        return jsonify({"error": "No .shp file found in zip"}), 400

    try:
        import pandas as pd
        gdfs = [gpd.read_file(shp) for shp in shp_files]
        gdf = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True)) if len(gdfs) > 1 else gdfs[0]
    except Exception as e:
        return jsonify({"error": f"Failed to read shapefile: {e}"}), 400

    if len(gdf) == 0:
        return jsonify({"error": "Shapefile is empty (0 features)"}), 400

    if "geometry" not in gdf.columns or gdf.geometry.is_empty.all():
        return jsonify({"error": "Shapefile has no geometry"}), 400

    # Reproject to EPSG:4326
    if gdf.crs is None:
        logger.warning("Shapefile has no CRS — assuming EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    _uploaded_shapefiles.append((uploaded.filename, gdf))
    _merged_gdf = None  # invalidate merged GDF cache
    # Note: _tile_cache is NOT invalidated here — tiles don't depend on shapefile.
    # The cache key is (field, year) which naturally misses if field changes.
    logger.info("Uploaded '%s': %d features, %d fields",
                uploaded.filename, len(gdf), len([c for c in gdf.columns if c != "geometry"]))

    merged = _get_merged_gdf()

    # Build field info with non-null counts
    fields = []
    for col in merged.columns:
        if col == "geometry":
            continue
        total = len(merged)
        non_null = int(merged[col].notna().sum())
        unique_count = merged[col].nunique()
        samples = merged[col].dropna().head(10).tolist()
        samples = [s if isinstance(s, (str, int, float)) else str(s) for s in samples]
        # Per-class polygon counts (from full GDF, not truncated GeoJSON)
        class_counts = merged[col].dropna().value_counts().to_dict()
        class_counts = {str(k): int(v) for k, v in class_counts.items()}
        fields.append({
            "name": col, "unique_count": int(unique_count),
            "non_null": non_null, "total": total, "samples": samples,
            "class_counts": class_counts,
        })

    # Build GeoJSON for map overlay
    MAX_OVERLAY = 10_000
    if len(merged) > MAX_OVERLAY:
        geojson = json.loads(merged.iloc[:MAX_OVERLAY].to_json())
        geojson["truncated"] = len(merged)
    else:
        geojson = json.loads(merged.to_json())

    # Estimate total labelled pixels from polygon areas at 10m resolution
    try:
        area_crs = merged.estimate_utm_crs()
        total_area_m2 = merged.to_crs(area_crs).geometry.area.sum()
        estimated_labelled_pixels = int(total_area_m2 / 100)  # 10m × 10m per pixel
    except Exception:
        estimated_labelled_pixels = 0

    return jsonify({
        "fields": fields, "geojson": geojson,
        "files": [f for f, _ in _uploaded_shapefiles],
        "estimated_labelled_pixels": estimated_labelled_pixels,
    })


@app.route("/api/evaluation/clear-shapefiles", methods=["POST"])
def clear_shapefiles():
    """Clear all uploaded shapefiles."""
    global _merged_gdf
    _uploaded_shapefiles.clear()
    _merged_gdf = None
    return jsonify({"ok": True})


@app.route("/api/evaluation/cancel", methods=["POST"])
def cancel_evaluation():
    """Cancel the running evaluation."""
    global _cancel_flag
    if _cancel_flag is not None:
        _cancel_flag.set()
        logger.info("Evaluation cancelled by user")
        return jsonify({"ok": True, "message": "Cancellation requested"})
    return jsonify({"ok": False, "message": "No evaluation running"})


@app.route("/api/evaluation/finish-classifier", methods=["POST"])
def finish_classifier():
    """Mark a classifier as finished for early stop."""
    try:
        body = request.get_json()
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400
    name = body.get("classifier")
    if not name:
        return jsonify({"error": "classifier is required"}), 400
    _finish_classifiers.add(name)
    logger.info("Classifier '%s' marked for early finish", name)
    return jsonify({"ok": True})


@app.route("/api/evaluation/run-large-area", methods=["POST"])
def run_large_area():
    """Run evaluation: GeoTessera tile loading + learning curve.

    Supports all classifiers including spatial MLP and U-Net (per-tile).
    """
    try:
        body = request.get_json()
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    field_name = body.get("field")
    year = body.get("year", 2024)
    classifiers = body.get("classifiers", ["nn", "rf"])
    classifier_params = body.get("classifier_params", {})
    max_train = body.get("max_training_samples")
    if max_train is not None:
        max_train = int(max_train)
    sampling = body.get("sampling", "sqrt")  # equal, proportional, sqrt
    max_patches = int(body.get("max_patches", 500))

    if not field_name:
        return jsonify({"error": "field is required"}), 400

    gdf = _get_merged_gdf()
    if gdf is None:
        return jsonify({"error": "No shapefile uploaded. Upload first."}), 400

    if field_name not in gdf.columns:
        return jsonify({"error": f"Field '{field_name}' not found in shapefile"}), 400

    # Auto-detect task type
    from tessera_eval.evaluate import detect_field_type
    task = body.get("task")
    if task is None or task == "auto":
        task = detect_field_type(gdf, field_name)

    is_classification = (task == "classification")
    _CLF_TO_REG = {"nn": "nn_reg", "rf": "rf_reg", "mlp": "mlp_reg", "xgboost": "xgboost_reg"}
    if is_classification:
        model_names = classifiers
        model_params = classifier_params
    else:
        regressors = body.get("regressors", [])
        regressor_params = body.get("regressor_params", {})
        if regressors:
            model_names = regressors
            model_params = regressor_params
        else:
            model_names = [_CLF_TO_REG.get(c, c) for c in classifiers]
            model_params = {_CLF_TO_REG.get(c, c): v for c, v in classifier_params.items()}

    # Determine which spatial features are needed
    needs_spatial_3x3 = "spatial_mlp" in model_names
    needs_spatial_5x5 = "spatial_mlp_5x5" in model_names
    needs_unet = "unet" in model_names

    def stream():
        import threading
        global _cancel_flag
        _cancel_flag = threading.Event()

        from geotessera import GeoTessera
        from rasterio.transform import array_bounds as _array_bounds
        from shapely.geometry import box as _box
        from sklearn.preprocessing import LabelEncoder
        from tessera_eval.rasterize import rasterize_shapefile
        from tessera_eval.evaluate import run_learning_curve
        from tessera_eval.classify import make_classifier, gather_spatial_features_2d

        _finish_classifiers.clear()

        def _cancelled():
            return _cancel_flag is not None and _cancel_flag.is_set()

        # Clean up old models
        for old_path in _trained_models.values():
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        _trained_models.clear()

        t0 = time.time()

        # Check in-memory cache first, then disk cache
        cache_key = (field_name, year, sampling)
        vectors = labels = class_names = stats = None
        spatial_3x3 = spatial_5x5 = unet_patches = None

        if _tile_cache["key"] == cache_key and _tile_cache["vectors"] is not None:
            vectors = _tile_cache["vectors"]
            labels = _tile_cache["labels"]
            class_names = _tile_cache["class_names"]
            stats = _tile_cache["stats"]
            spatial_3x3 = _tile_cache.get("spatial_3x3")
            spatial_5x5 = _tile_cache.get("spatial_5x5")
            unet_patches = _tile_cache.get("unet_patches", [])
            logger.info("In-memory cache hit for %s/%s (%d pixels)", field_name, year, len(labels))

            # If spatial features needed but not cached, must reload
            if (needs_spatial_3x3 and spatial_3x3 is None) or (needs_spatial_5x5 and spatial_5x5 is None):
                logger.info("Spatial features needed but not cached — reloading tiles")
                vectors = None  # force reload

        if vectors is None:
            # Check disk result cache (much smaller than raw tiles)
            cached_result = _load_cached_result(field_name, year, gdf, sampling)
            if cached_result and not needs_spatial_3x3 and not needs_spatial_5x5 and not needs_unet:
                vectors, labels, class_names, stats = cached_result
                logger.info("Disk result cache hit for %s/%s (%d pixels)", field_name, year, len(labels))

        if vectors is not None:
            yield json.dumps({
                "event": "download_progress", "tile": stats.get("tile_count", 0),
                "total": stats.get("tile_count", 0), "cached": True,
            }) + "\n"
            # Update in-memory cache so we skip the GeoTessera fetch below
            _tile_cache.update({
                "key": cache_key, "vectors": vectors, "labels": labels,
                "class_names": class_names, "stats": stats,
                "spatial_3x3": None, "spatial_5x5": None, "unet_patches": [],
            })

        if _tile_cache["key"] != cache_key:
            # Emit early so the browser knows we're working
            yield json.dumps({
                "event": "field_start",
                "field": field_name,
                "type": task,
                "status": "Loading GeoTessera tile index...",
            }) + "\n"

            # Reuse cached GeoTessera instance (avoids 10-30s registry init per run)
            global _geotessera_instance
            logger.info("Initializing GeoTessera...")
            yield json.dumps({"event": "status", "message": "Initializing GeoTessera..."}) + "\n"
            if _geotessera_instance is None:
                _geotessera_instance = GeoTessera()
            gt = _geotessera_instance

            try:
                MAX_SAMPLE_PIXELS = 200_000  # 200K is ample for learning curves

                le = LabelEncoder()
                le.fit(gdf[field_name].dropna().unique())
                class_names = le.classes_.tolist()
                n_classes = len(class_names)

                # Generate random sample points within shapefile polygons
                logger.info("Generating sample points across %d classes...", n_classes)
                yield json.dumps({"event": "status", "message": f"Generating sample points across {n_classes} classes..."}) + "\n"

                valid_gdf = gdf.dropna(subset=[field_name]).copy()
                label_ids = le.transform(valid_gdf[field_name])
                valid_gdf["_label_id"] = label_ids

                # Sampling strategy: equal, proportional, or sqrt-proportional
                MIN_PER_CLASS = 50
                if sampling in ("proportional", "sqrt"):
                    import math
                    area_crs = valid_gdf.estimate_utm_crs()
                    projected = valid_gdf.to_crs(area_crs)
                    projected["_area"] = projected.geometry.area
                    valid_gdf["_area"] = projected["_area"].values
                    class_areas = valid_gdf.groupby("_label_id")["_area"].sum()
                    if sampling == "sqrt":
                        weights = {c: math.sqrt(a) for c, a in class_areas.items()}
                    else:
                        weights = dict(class_areas)
                    total_weight = sum(weights.values())
                    raw_alloc = {c: max(MIN_PER_CLASS, int(MAX_SAMPLE_PIXELS * w / total_weight))
                                 for c, w in weights.items()}
                    # Scale down if total exceeds budget
                    alloc_total = sum(raw_alloc.values())
                    if alloc_total > MAX_SAMPLE_PIXELS:
                        scale = MAX_SAMPLE_PIXELS / alloc_total
                        raw_alloc = {c: max(MIN_PER_CLASS, int(n * scale)) for c, n in raw_alloc.items()}
                else:
                    # Equal per class
                    equal_n = MAX_SAMPLE_PIXELS // n_classes
                    raw_alloc = {c: equal_n for c in range(n_classes)}

                sample_points = []
                sample_labels = []

                for cls_idx in range(n_classes):
                    cls_gdf = valid_gdf[valid_gdf["_label_id"] == cls_idx]
                    if cls_gdf.empty:
                        continue
                    per_class = raw_alloc.get(cls_idx, MIN_PER_CLASS)
                    # sample_points(size=N) generates N points PER ROW.
                    # We want per_class total, so divide by number of rows.
                    n_rows = len(cls_gdf)
                    pts_per_row = max(1, per_class // n_rows)
                    try:
                        import warnings
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore", UserWarning)
                            pts = cls_gdf.sample_points(size=pts_per_row)
                        # sample_points returns a GeoSeries of MultiPoint or Point
                        for mp in pts:
                            if mp is not None and not mp.is_empty:
                                if hasattr(mp, 'geoms'):
                                    for pt in mp.geoms:
                                        sample_points.append((pt.x, pt.y))
                                        sample_labels.append(cls_idx)
                                else:
                                    # Single Point (not MultiPoint) — single-polygon class
                                    sample_points.append((mp.x, mp.y))
                                    sample_labels.append(cls_idx)
                    except Exception as e:
                        logger.warning("sample_points failed for class %d: %s", cls_idx, e)

                n_points = len(sample_points)
                if n_points == 0:
                    yield json.dumps({"event": "error", "message": "No sample points generated from shapefile polygons"}) + "\n"
                    return

                logger.info("Generated %d sample points across %d classes", n_points, n_classes)
                yield json.dumps({"event": "status", "message": f"Generated {n_points:,} sample points across {n_classes} classes"}) + "\n"

                if _cancelled():
                    yield json.dumps({"event": "error", "message": "Cancelled"}) + "\n"
                    return

                import queue, threading
                progress_q = queue.Queue()
                spatial_3x3 = None
                spatial_5x5 = None
                unet_patches = []

                if needs_spatial_3x3 or needs_spatial_5x5 or needs_unet:
                    # Single tile pass: fetch tiles once, extract both point samples AND patches
                    logger.info("Fetching tiles for %d points + patches...", n_points)
                    yield json.dumps({"event": "status", "message": f"Fetching tiles for {n_points:,} points + patches..."}) + "\n"

                    def _tile_progress(current, total):
                        progress_q.put(("tile", current, total))

                    tile_result = [None, None]
                    def _fetch_all():
                        try:
                            tile_result[0] = _extract_tile_patches(
                                gt, gdf, field_name, year, le, n_classes,
                                max_patches=max_patches,
                                needs_spatial_3x3=needs_spatial_3x3,
                                needs_spatial_5x5=needs_spatial_5x5,
                                sample_points_lonlat=sample_points,
                                logger=logger,
                                progress_cb=_tile_progress,
                                cancel_flag=_cancel_flag,
                            )
                        except Exception as e:
                            tile_result[1] = e
                        finally:
                            progress_q.put(None)

                    t = threading.Thread(target=_fetch_all, daemon=True)
                    t.start()

                    while True:
                        if _cancelled():
                            logger.info("Cancelled during tile fetch")
                            yield json.dumps({"event": "error", "message": "Cancelled"}) + "\n"
                            return
                        try:
                            item = progress_q.get(timeout=5)
                        except queue.Empty:
                            yield json.dumps({"event": "heartbeat"}) + "\n"
                            continue
                        if item is None:
                            break
                        if item[0] == "tile":
                            _, cur, tot = item
                            pct = int(100 * cur / tot) if tot else 0
                            msg = f"Fetching tiles: {cur}/{tot} ({pct}%)"
                            logger.info(msg)
                            yield json.dumps({"event": "progress", "pct": pct, "message": msg}) + "\n"

                    t.join()
                    if tile_result[1] is not None:
                        yield json.dumps({"event": "error", "message": f"Tile fetch failed: {tile_result[1]}"}) + "\n"
                        return

                    unet_patches, spatial_3x3, spatial_5x5, vectors = tile_result[0]
                else:
                    # Pixel-only: use sample_embeddings_at_points (faster, no tile loading)
                    logger.info("Fetching embeddings for %d points...", n_points)
                    yield json.dumps({"event": "status", "message": f"Fetching embeddings for {n_points:,} points..."}) + "\n"

                    result_holder = [None, None]
                    def _fetch():
                        try:
                            def _cb(current, total, status):
                                progress_q.put(("tile", current, total))
                            vecs = gt.sample_embeddings_at_points(
                                sample_points, year=year, progress_callback=_cb)
                            result_holder[0] = vecs
                        except Exception as e:
                            result_holder[1] = e
                        finally:
                            progress_q.put(None)

                    t = threading.Thread(target=_fetch, daemon=True)
                    t.start()

                    last_reported = -1
                    while True:
                        if _cancelled():
                            logger.info("Cancelled during pixel fetch")
                            yield json.dumps({"event": "error", "message": "Cancelled"}) + "\n"
                            return
                        try:
                            item = progress_q.get(timeout=5)
                        except queue.Empty:
                            yield json.dumps({"event": "heartbeat"}) + "\n"
                            continue
                        if item is None:
                            break
                        if item[0] == "tile":
                            _, current, total = item
                            if current == last_reported:
                                continue
                            last_reported = current
                            pct = int(100 * current / total) if total else 0
                            msg = f"Fetching embeddings: {current}/{total} tiles ({pct}%)"
                            logger.info(msg)
                            yield json.dumps({"event": "progress", "pct": pct, "message": msg}) + "\n"

                    t.join()
                    if result_holder[1] is not None:
                        yield json.dumps({"event": "error", "message": f"GeoTessera sampling failed: {result_holder[1]}"}) + "\n"
                        return
                    vectors = result_holder[0]

                logger.info("Processing embeddings...")
                yield json.dumps({"event": "status", "message": "Processing embeddings..."}) + "\n"

                labels = np.array(sample_labels, dtype=np.int32)

                # Remove NaN rows (points outside tile coverage)
                valid_mask = ~np.isnan(vectors).any(axis=1)
                if valid_mask.sum() < len(vectors):
                    n_removed = len(vectors) - valid_mask.sum()
                    n_remaining = valid_mask.sum()
                    logger.info("Removed %d points outside coverage (%d remaining)", n_removed, n_remaining)
                    yield json.dumps({"event": "status", "message": f"Removed {n_removed:,} points outside coverage ({n_remaining:,} remaining)"}) + "\n"
                    vectors = vectors[valid_mask].astype(np.float32)
                    labels = labels[valid_mask]
                else:
                    vectors = vectors.astype(np.float32)

                if len(vectors) == 0:
                    yield json.dumps({"event": "error", "message": "No valid embeddings found at sample points"}) + "\n"
                    return

                # Count tiles used
                bounds = gdf.total_bounds
                bbox = (bounds[0], bounds[1], bounds[2], bounds[3])
                tiles = gt.registry.load_blocks_for_region(bbox, year)
                total_tiles = len(tiles)

                stats = {
                    "tile_count": total_tiles,
                    "tiles_with_data": total_tiles,
                    "total_pixels": len(labels),
                    "n_classes": n_classes,
                }

                # Cache in memory and on disk
                _tile_cache.update({
                    "key": cache_key, "vectors": vectors, "labels": labels,
                    "class_names": class_names, "stats": stats,
                    "spatial_3x3": None, "spatial_5x5": None,
                    "unet_patches": [],
                })
                _save_cached_result(field_name, year, gdf, vectors, labels, class_names, stats, sampling)

                logger.info("Point sampling complete: %d pixels, %.1fMB",
                            len(labels), vectors.nbytes / 1e6)

            except Exception as e:
                yield json.dumps({"event": "error", "message": str(e)}) + "\n"
                return
        else:
            spatial_3x3 = _tile_cache.get("spatial_3x3")
            spatial_5x5 = _tile_cache.get("spatial_5x5")
            unet_patches = _tile_cache.get("unet_patches", [])

        total_labelled = len(vectors)

        # Training percentages (% of labelled area)
        training_pcts = [1, 3, 5, 10, 20, 30, 50, 80]
        if max_train:
            max_pct = min(80, int(100 * max_train / total_labelled))
            training_pcts = [p for p in training_pcts if p <= max_pct]
            if not training_pcts:
                training_pcts = [max_pct]

        # Class info
        unique_labels, counts = np.unique(labels, return_counts=True)
        class_info = []
        for lbl, cnt in zip(unique_labels, counts):
            name = class_names[lbl] if lbl < len(class_names) else f"Class {lbl}"
            class_info.append({"name": str(name), "pixels": int(cnt)})

        # Filter classifiers that the user hasn't installed deps for
        active_models = []
        for name in model_names:
            if name == "unet":
                try:
                    from tessera_eval.unet import _HAS_TORCH
                    if not _HAS_TORCH:
                        logger.warning("Skipping U-Net: PyTorch not installed")
                        yield json.dumps({"event": "status", "message": "U-Net skipped — PyTorch not installed"}) + "\n"
                        continue
                except ImportError:
                    continue
                if not unet_patches:
                    yield json.dumps({"event": "status", "message": "U-Net skipped — no labelled patches found"}) + "\n"
                    continue
            if name in ("spatial_mlp", "spatial_mlp_5x5"):
                if (name == "spatial_mlp" and spatial_3x3 is None) or (name == "spatial_mlp_5x5" and spatial_5x5 is None):
                    yield json.dumps({"event": "status", "message": f"{name} skipped — no spatial features"}) + "\n"
                    continue
            active_models.append(name)

        yield json.dumps({
            "event": "start",
            "classifiers": active_models,
            "classes": class_info if is_classification else [],
            "total_labelled_pixels": total_labelled,
            "confusion_matrix_labels": class_names if is_classification else [],
            "training_pcts": training_pcts,
            "stats": stats,
        }) + "\n"

        # Run learning curve (all classifiers including U-Net)
        for event in run_learning_curve(
            vectors, labels, active_models, training_pcts,
            repeats=5, classifier_params=model_params,
            spatial_vectors=spatial_3x3, spatial_vectors_5x5=spatial_5x5,
            finish_classifiers=_finish_classifiers,
            unet_patches=unet_patches,
        ):
            if _cancelled():
                logger.info("Evaluation cancelled during learning curve")
                yield json.dumps({"event": "error", "message": "Cancelled"}) + "\n"
                return
            if event["type"] == "progress":
                yield json.dumps({
                    "event": "progress",
                    "pct": event["pct"],
                    "classifiers": event["classifiers"],
                    "pixel_train_count": event.get("pixel_train_count", 0),
                    "unet_train_count": event.get("unet_train_count", 0),
                    "total_pixels": event.get("total_pixels", 0),
                    "total_unet_pixels": event.get("total_unet_pixels", 0),
                }) + "\n"
            elif event["type"] == "classifier_status":
                yield json.dumps({
                    "event": "status",
                    "message": event["message"],
                }) + "\n"
            elif event["type"] == "confusion_matrices":
                yield json.dumps({
                    "event": "confusion_matrices",
                    "confusion_matrices": event["confusion_matrices"],
                }) + "\n"

        # Store active_models for deferred training
        _tile_cache["_active_models"] = active_models
        _tile_cache["_model_params"] = model_params
        _tile_cache["_unet_patches"] = unet_patches

        _cancel_flag = None  # reset cancellation flag
        elapsed = time.time() - t0
        yield json.dumps({
            "event": "done",
            "elapsed_seconds": round(elapsed, 1),
            "field": field_name,
            "year": year,
            "models_available": list(_trained_models.keys()),
        }) + "\n"

    return Response(_padded(stream()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/evaluation/train-models", methods=["POST"])
def train_models():
    """Train final models on full data for download. Deferred from evaluation."""
    cache = _tile_cache
    if cache.get("vectors") is None:
        return jsonify({"error": "No evaluation data. Run evaluation first."}), 400

    vectors = cache["vectors"]
    labels = cache["labels"]
    class_names = cache.get("class_names", [])
    active_models = cache.get("_active_models", [])
    model_params = cache.get("_model_params", {})
    unet_patches = cache.get("_unet_patches", [])
    spatial_3x3 = cache.get("spatial_3x3")
    spatial_5x5 = cache.get("spatial_5x5")

    if not active_models:
        return jsonify({"error": "No classifiers configured."}), 400

    valid_class_names = [class_names[lbl] if lbl < len(class_names) else f"Class {lbl}"
                         for lbl in sorted(np.unique(labels))]

    def stream():
        from tessera_eval.classify import make_classifier

        # Clean up old models
        for old_path in _trained_models.values():
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        _trained_models.clear()

        yield json.dumps({"event": "status", "message": "Training final models for download..."}) + "\n"

        for name in active_models:
            logger.info("Training %s...", name)
            yield json.dumps({"event": "status", "message": f"Training {name}..."}) + "\n"
            try:
                if name == "unet":
                    from tessera_eval.unet import train_unet_on_patches, _HAS_TORCH
                    import torch as _torch
                    if _HAS_TORCH and unet_patches:
                        n_cls = len(np.unique(labels))
                        _unet_progress = []
                        def _unet_cb(epoch, total, loss):
                            _unet_progress.append((epoch, total, loss))
                        model = train_unet_on_patches(
                            unet_patches, n_cls, model_params.get("unet", {}),
                            progress_callback=_unet_cb)
                        for ep, tot, loss in _unet_progress:
                            yield json.dumps({"event": "status", "message": f"U-Net epoch {ep}/{tot} loss={loss:.4f}"}) + "\n"
                        tmp = tempfile.NamedTemporaryFile(suffix=".pt", prefix=f"{name}_model_", delete=False)
                        _torch.save({"model_state": model.state_dict(), "class_names": valid_class_names}, tmp.name)
                        _trained_models[name] = tmp.name
                    else:
                        yield json.dumps({"event": "status", "message": "U-Net skipped — no patches or PyTorch"}) + "\n"
                        continue
                elif name == "spatial_mlp" and spatial_3x3 is not None:
                    from tessera_eval.classify import augment_spatial
                    X_aug, y_aug = augment_spatial(spatial_3x3, labels, window=3, dim=vectors.shape[1])
                    clf = make_classifier(name, model_params.get(name, {}))
                    clf.fit(X_aug, y_aug)
                    tmp = tempfile.NamedTemporaryFile(suffix=".joblib", prefix=f"{name}_model_", delete=False)
                    joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                    _trained_models[name] = tmp.name
                elif name == "spatial_mlp_5x5" and spatial_5x5 is not None:
                    from tessera_eval.classify import augment_spatial
                    X_aug, y_aug = augment_spatial(spatial_5x5, labels, window=5, dim=vectors.shape[1])
                    clf = make_classifier(name, model_params.get(name, {}))
                    clf.fit(X_aug, y_aug)
                    tmp = tempfile.NamedTemporaryFile(suffix=".joblib", prefix=f"{name}_model_", delete=False)
                    joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                    _trained_models[name] = tmp.name
                else:
                    clf = make_classifier(name, model_params.get(name, {}))
                    clf.fit(vectors, labels)
                    tmp = tempfile.NamedTemporaryFile(suffix=".joblib", prefix=f"{name}_model_", delete=False)
                    joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                    _trained_models[name] = tmp.name
                logger.info("Trained model '%s' → %s", name, tmp.name)
                yield json.dumps({"event": "model_ready", "classifier": name}) + "\n"
            except Exception as e:
                logger.warning("Failed to train model '%s': %s", name, e)
                yield json.dumps({"event": "status", "message": f"Failed to train {name}: {e}"}) + "\n"

        yield json.dumps({"event": "done", "models_available": list(_trained_models.keys())}) + "\n"

    return Response(_padded(stream()), mimetype="application/x-ndjson",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/evaluation/download-model/<name>", methods=["GET"])
def download_model(name):
    """Serve a trained model file."""
    path = _trained_models.get(name)
    if not path or not Path(path).exists():
        return jsonify({"error": f"No trained model for '{name}'"}), 404
    ext = ".pt" if name == "unet" else ".joblib"
    return send_file(path, as_attachment=True, download_name=f"{name}_model{ext}")


@app.route("/health", methods=["GET"])
def health():
    """Health check — reports status, hosted server, and loaded data."""
    import socket
    gdf = _get_merged_gdf()
    return jsonify({
        "status": "ok",
        "mode": "compute",
        "compute_host": socket.gethostname(),
        "hosted": _hosted_url,
        "version": _get_version(),
        "shapefiles": len(_uploaded_shapefiles),
        "features": len(gdf) if gdf is not None else 0,
        "models_available": list(_trained_models.keys()),
    })


# ── Reverse proxy for everything else ──

@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
def proxy(path):
    """Forward all non-eval requests to the hosted server."""
    import requests as _requests

    if not _hosted_url:
        return jsonify({"error": "No --hosted URL configured"}), 502

    target = f"{_hosted_url}/{path}"
    if request.query_string:
        target += f"?{request.query_string.decode()}"

    # Forward headers (skip hop-by-hop)
    skip = {"host", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in request.headers if k.lower() not in skip}

    try:
        resp = _requests.request(
            method=request.method,
            url=target,
            headers=headers,
            data=request.get_data(),
            cookies=request.cookies,
            stream=True,
            timeout=300,
        )
    except _requests.ConnectionError:
        return jsonify({"error": f"Cannot reach hosted server at {_hosted_url}"}), 502
    except _requests.Timeout:
        return jsonify({"error": "Hosted server timed out"}), 504

    # Stream response back
    proxy_headers = {}
    for k, v in resp.headers.items():
        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding", "connection"):
            proxy_headers[k] = v

    return Response(
        resp.iter_content(chunk_size=8192),
        status=resp.status_code,
        headers=proxy_headers,
    )


# ── Helpers ──

def _get_version():
    try:
        from tessera_eval import __version__
        return __version__
    except Exception:
        return "unknown"


# ── CLI entry point ──

def main():
    global _hosted_url

    parser = argparse.ArgumentParser(
        description="TEE compute server — run ML evaluation locally, proxy data from hosted server",
    )
    parser.add_argument(
        "--hosted", default="https://tee.cl.cam.ac.uk",
        help="URL of the hosted TEE server for data/UI (default: https://tee.cl.cam.ac.uk)",
    )
    parser.add_argument(
        "--port", type=int, default=8001,
        help="Port to serve on (default: 8001)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Run in Flask debug mode (auto-reload, verbose errors)",
    )
    args = parser.parse_args()

    _hosted_url = args.hosted.rstrip("/")

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("TEE Compute Server")
    logger.info("  Hosted server: %s", _hosted_url)
    logger.info("  Listening on:  http://%s:%d", args.host, args.port)
    logger.info("")
    logger.info("Open http://localhost:%d in your browser", args.port)

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True)
    else:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=4, channel_timeout=7200)


if __name__ == "__main__":
    main()
