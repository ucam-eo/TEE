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

FLUSH_PAD = 18 * 1024  # pad NDJSON lines to force Waitress flush


def _get_cache_dir():
    """Return the cache directory, creating it if needed."""
    global _tile_disk_cache_dir
    if _tile_disk_cache_dir is None:
        _tile_disk_cache_dir = Path.home() / ".cache" / "tessera-eval"
    _tile_disk_cache_dir.mkdir(parents=True, exist_ok=True)
    return _tile_disk_cache_dir


def _result_cache_path(field, year, gdf_hash):
    """Return the disk path for cached evaluation results (vectors + labels)."""
    return _get_cache_dir() / f"result_{field}_{year}_{gdf_hash}.npz"


def _gdf_hash(gdf):
    """Quick hash of a GeoDataFrame for cache keying."""
    import hashlib
    h = hashlib.md5()
    h.update(str(len(gdf)).encode())
    h.update(str(sorted(gdf.columns.tolist())).encode())
    bounds = gdf.total_bounds
    h.update(f"{bounds[0]:.4f},{bounds[1]:.4f},{bounds[2]:.4f},{bounds[3]:.4f}".encode())
    return h.hexdigest()[:12]


def _load_cached_result(field, year, gdf):
    """Load cached evaluation result. Returns (vectors, labels, class_names, stats) or None."""
    path = _result_cache_path(field, year, _gdf_hash(gdf))
    if path.exists():
        try:
            data = np.load(path, allow_pickle=True)
            return (data["vectors"], data["labels"],
                    data["class_names"].tolist(), dict(data["stats"].item()))
        except Exception:
            path.unlink(missing_ok=True)
    return None


def _save_cached_result(field, year, gdf, vectors, labels, class_names, stats):
    """Save evaluation result to disk cache."""
    try:
        path = _result_cache_path(field, year, _gdf_hash(gdf))
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
        fields.append({
            "name": col, "unique_count": int(unique_count),
            "non_null": non_null, "total": total, "samples": samples,
        })

    # Build GeoJSON for map overlay
    MAX_OVERLAY = 10_000
    if len(merged) > MAX_OVERLAY:
        geojson = json.loads(merged.iloc[:MAX_OVERLAY].to_json())
        geojson["truncated"] = len(merged)
    else:
        geojson = json.loads(merged.to_json())

    return jsonify({
        "fields": fields, "geojson": geojson,
        "files": [f for f, _ in _uploaded_shapefiles],
    })


@app.route("/api/evaluation/clear-shapefiles", methods=["POST"])
def clear_shapefiles():
    """Clear all uploaded shapefiles."""
    global _merged_gdf
    _uploaded_shapefiles.clear()
    _merged_gdf = None
    return jsonify({"ok": True})


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
        from geotessera import GeoTessera
        from rasterio.transform import array_bounds as _array_bounds
        from shapely.geometry import box as _box
        from sklearn.preprocessing import LabelEncoder
        from tessera_eval.rasterize import rasterize_shapefile
        from tessera_eval.evaluate import run_learning_curve
        from tessera_eval.classify import make_classifier, gather_spatial_features_2d

        _finish_classifiers.clear()

        # Clean up old models
        for old_path in _trained_models.values():
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        _trained_models.clear()

        t0 = time.time()

        # Check in-memory cache first, then disk cache
        cache_key = (field_name, year)
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
            cached_result = _load_cached_result(field_name, year, gdf)
            if cached_result and not needs_spatial_3x3 and not needs_spatial_5x5 and not needs_unet:
                vectors, labels, class_names, stats = cached_result
                logger.info("Disk result cache hit for %s/%s (%d pixels)", field_name, year, len(labels))

        if vectors is not None:
            yield json.dumps({
                "event": "download_progress", "tile": stats.get("tile_count", 0),
                "total": stats.get("tile_count", 0), "cached": True,
            }) + "\n"

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
            if _geotessera_instance is None:
                _geotessera_instance = GeoTessera()
            gt = _geotessera_instance

            try:
                bounds = gdf.total_bounds
                bbox = (bounds[0], bounds[1], bounds[2], bounds[3])
                tiles = gt.registry.load_blocks_for_region(bbox, year)
                total_tiles = len(tiles)
                if total_tiles == 0:
                    yield json.dumps({"event": "error", "message": f"No GeoTessera tiles for bbox {bbox}, year {year}"}) + "\n"
                    return

                le = LabelEncoder()
                le.fit(gdf[field_name].dropna().unique())
                class_names = le.classes_.tolist()

                all_vectors = []
                all_labels = []
                all_spatial_3x3 = [] if needs_spatial_3x3 else None
                all_spatial_5x5 = [] if needs_spatial_5x5 else None
                all_unet_patches = [] if needs_unet else None
                tiles_with_data = 0

                # Pre-reproject GDF to first tile's CRS (avoid per-tile reprojection)
                _gdf_proj_cache = {}  # crs → reprojected gdf with spatial index

                # Stream tiles from GeoTessera one at a time.
                # No raw tile disk caching — the result cache (vectors+labels) is 100x smaller.
                tile_idx = 0
                tile_iter = gt.fetch_embeddings(tiles)
                while True:
                    try:
                        yr, tile_lon, tile_lat, tile_emb, tile_crs, tile_transform = next(tile_iter)
                    except StopIteration:
                        break
                    except Exception as tile_err:
                        tile_idx += 1
                        logger.warning("Tile %d/%d failed: %s — skipping", tile_idx, total_tiles, tile_err)
                        yield json.dumps({
                            "event": "download_progress", "tile": tile_idx, "total": total_tiles,
                            "error": str(tile_err),
                        }) + "\n"
                        continue

                    tile_idx += 1
                    import psutil
                    rss_gb = psutil.Process().memory_info().rss / (1024**3)
                    logger.info("Tile %d/%d — RSS=%.1fGB, emb=%s",
                                tile_idx, total_tiles, rss_gb, tile_emb.shape)

                    yield json.dumps({
                        "event": "download_progress", "tile": tile_idx, "total": total_tiles,
                    }) + "\n"

                    h, w, dim = tile_emb.shape
                    tile_bounds = _array_bounds(h, w, tile_transform)

                    # Reproject GDF once per CRS (cached), use spatial index
                    crs_key = str(tile_crs)
                    if crs_key not in _gdf_proj_cache:
                        gdf_proj = gdf.to_crs(tile_crs) if str(gdf.crs) != crs_key else gdf
                        if not hasattr(gdf_proj, 'sindex'):
                            gdf_proj = gdf_proj.copy()  # ensure sindex is built
                        _gdf_proj_cache[crs_key] = gdf_proj
                    gdf_proj = _gdf_proj_cache[crs_key]

                    # Use spatial index for fast bbox filtering
                    tile_box = _box(*tile_bounds)
                    if hasattr(gdf_proj, 'sindex') and gdf_proj.sindex is not None:
                        candidates_idx = list(gdf_proj.sindex.intersection(tile_bounds))
                        if not candidates_idx:
                            continue
                        tile_gdf = gdf_proj.iloc[candidates_idx]
                        tile_gdf = tile_gdf[tile_gdf.intersects(tile_box)]
                    else:
                        tile_gdf = gdf_proj[gdf_proj.intersects(tile_box)]
                    if tile_gdf.empty:
                        continue

                    class_raster = rasterize_shapefile(
                        tile_gdf, field_name, tile_transform, w, h, label_encoder=le)

                    labelled_mask = class_raster > 0
                    if labelled_mask.sum() == 0:
                        continue

                    tiles_with_data += 1

                    # Subsample labelled pixels if we've already accumulated enough.
                    # Beyond 1M pixels, more data doesn't improve accuracy meaningfully
                    # but explodes memory (1M × 128 × 4 = 512MB) and runtime.
                    MAX_TOTAL_PIXELS = 1_000_000
                    current_total = sum(a.shape[0] for a in all_vectors) if all_vectors else 0
                    tile_labels = class_raster[labelled_mask] - 1
                    tile_vectors = tile_emb[labelled_mask]
                    remaining = MAX_TOTAL_PIXELS - current_total
                    if remaining <= 0:
                        logger.info("Pixel cap reached (%d) — skipping remaining tiles", MAX_TOTAL_PIXELS)
                        # Free tile and break
                        del tile_emb, class_raster, labelled_mask, tile_labels, tile_vectors
                        import gc; gc.collect()
                        break
                    if len(tile_labels) > remaining:
                        # Subsample this tile to fit under the cap
                        rng = np.random.RandomState(42)
                        idx = rng.choice(len(tile_labels), size=remaining, replace=False)
                        tile_labels = tile_labels[idx]
                        tile_vectors = tile_vectors[idx]
                        labelled_mask_sub = np.zeros_like(labelled_mask)
                        # For spatial features, we need the original mask positions
                        # Just use the subsampled vectors directly
                        logger.info("Subsampled tile from %d to %d pixels (cap=%d)",
                                    labelled_mask.sum(), remaining, MAX_TOTAL_PIXELS)

                    all_labels.append(tile_labels)
                    all_vectors.append(tile_vectors)

                    # Per-tile spatial features (only for labelled pixels — avoids full-tile allocation)
                    # Note: spatial features need the 2D grid, so we use the original mask
                    n_tile_pixels = len(tile_labels)
                    if needs_spatial_3x3:
                        sf = gather_spatial_features_2d(tile_emb, radius=1, mask=labelled_mask)
                        if len(sf) > n_tile_pixels:
                            sf = sf[:n_tile_pixels]  # align with subsampled pixels
                        all_spatial_3x3.append(sf)
                    if needs_spatial_5x5:
                        sf = gather_spatial_features_2d(tile_emb, radius=2, mask=labelled_mask)
                        if len(sf) > n_tile_pixels:
                            sf = sf[:n_tile_pixels]
                        all_spatial_5x5.append(sf)

                    # Per-tile U-Net patches
                    if needs_unet:
                        from tessera_eval.unet import extract_labelled_patches
                        patches = extract_labelled_patches(tile_emb, class_raster)
                        all_unet_patches.extend(patches)

                    # Free tile memory before loading next
                    del tile_emb, class_raster, labelled_mask
                    import gc; gc.collect()
                    logger.info("Tile %d/%d processed — %d labelled pixels accumulated",
                                tile_idx, total_tiles, sum(a.shape[0] for a in all_vectors))

                logger.info("Tile loop complete: %d/%d tiles processed, %d with data",
                            tile_idx, total_tiles, tiles_with_data)

                if not all_vectors:
                    yield json.dumps({"event": "error", "message": "No labelled pixels found across any tiles"}) + "\n"
                    return

                # Estimate memory before concatenating
                import psutil
                n_pixels = sum(a.shape[0] for a in all_vectors)
                dim = all_vectors[0].shape[1] if all_vectors else 128
                mem_vectors = n_pixels * dim * 4  # float32
                mem_spatial_3x3 = n_pixels * 9 * dim * 4 if all_spatial_3x3 else 0
                mem_spatial_5x5 = n_pixels * 25 * dim * 4 if all_spatial_5x5 else 0
                mem_total_gb = (mem_vectors + mem_spatial_3x3 + mem_spatial_5x5) / (1024**3)
                avail_gb = psutil.virtual_memory().available / (1024**3)

                logger.info("Memory estimate: %.1fGB needed (vectors=%.1fGB, spatial_3x3=%.1fGB, spatial_5x5=%.1fGB), %.1fGB available",
                            mem_total_gb, mem_vectors/(1024**3), mem_spatial_3x3/(1024**3), mem_spatial_5x5/(1024**3), avail_gb)

                if mem_total_gb > avail_gb * 0.8:
                    msg = (f"Not enough memory: evaluation needs ~{mem_total_gb:.1f}GB but only "
                           f"{avail_gb:.1f}GB available. ")
                    if mem_spatial_3x3 or mem_spatial_5x5:
                        msg += "Try disabling Spatial MLP classifiers, or reduce the area."
                    else:
                        msg += "Try reducing the area or max training samples."
                    yield json.dumps({"event": "error", "message": msg}) + "\n"
                    return

                vectors = np.concatenate(all_vectors, axis=0).astype(np.float32)
                del all_vectors  # free intermediate lists
                labels = np.concatenate(all_labels, axis=0).astype(np.int32)
                del all_labels
                spatial_3x3 = np.concatenate(all_spatial_3x3, axis=0).astype(np.float32) if all_spatial_3x3 else None
                del all_spatial_3x3
                spatial_5x5 = np.concatenate(all_spatial_5x5, axis=0).astype(np.float32) if all_spatial_5x5 else None
                del all_spatial_5x5

                stats = {
                    "tile_count": total_tiles,
                    "tiles_with_data": tiles_with_data,
                    "total_pixels": len(labels),
                    "n_classes": len(class_names),
                }

                unet_patches = all_unet_patches if all_unet_patches else []

                # Cache in memory (for immediate reuse with different classifiers)
                _tile_cache.update({
                    "key": cache_key, "vectors": vectors, "labels": labels,
                    "class_names": class_names, "stats": stats,
                    "spatial_3x3": spatial_3x3, "spatial_5x5": spatial_5x5,
                    "unet_patches": unet_patches,
                })

                # Cache to disk (for reuse across restarts — vectors + labels only, ~500MB)
                _save_cached_result(field_name, year, gdf, vectors, labels, class_names, stats)

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
                        continue
                except ImportError:
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
            if event["type"] == "progress":
                yield json.dumps({
                    "event": "progress",
                    "pct": event["pct"],
                    "classifiers": event["classifiers"],
                }) + "\n"
            elif event["type"] == "confusion_matrices":
                yield json.dumps({
                    "event": "confusion_matrices",
                    "confusion_matrices": event["confusion_matrices"],
                }) + "\n"

        # Retrain on full data for model download
        valid_class_names = [class_names[lbl] if lbl < len(class_names) else f"Class {lbl}"
                             for lbl in sorted(np.unique(labels))]

        for name in active_models:
            try:
                if name == "unet" and needs_unet:
                    from tessera_eval.unet import train_unet_on_patches
                    import torch as _torch
                    if unet_patches:
                        n_cls = len(np.unique(labels))
                        model = train_unet_on_patches(unet_patches, n_cls, model_params.get("unet", {}))
                        tmp = tempfile.NamedTemporaryFile(suffix=".pt", prefix=f"{name}_model_", delete=False)
                        _torch.save({"model_state": model.state_dict(), "class_names": valid_class_names}, tmp.name)
                        _trained_models[name] = tmp.name
                elif name == "spatial_mlp" and spatial_3x3 is not None:
                    from tessera_eval.classify import augment_spatial
                    X_full = spatial_3x3
                    X_aug, y_aug = augment_spatial(X_full, labels, window=3, dim=vectors.shape[1])
                    clf = make_classifier(name, model_params.get(name, {}))
                    clf.fit(X_aug, y_aug)
                    tmp = tempfile.NamedTemporaryFile(suffix=".joblib", prefix=f"{name}_model_", delete=False)
                    joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                    _trained_models[name] = tmp.name
                elif name == "spatial_mlp_5x5" and spatial_5x5 is not None:
                    from tessera_eval.classify import augment_spatial
                    X_full = spatial_5x5
                    X_aug, y_aug = augment_spatial(X_full, labels, window=5, dim=vectors.shape[1])
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
    gdf = _get_merged_gdf()
    return jsonify({
        "status": "ok",
        "mode": "compute",
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
