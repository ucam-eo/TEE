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

FLUSH_PAD = 18 * 1024  # pad NDJSON lines to force Waitress flush


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
    _merged_gdf = None  # invalidate cache
    _tile_cache["key"] = None  # invalidate tile cache

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
    _tile_cache["key"] = None
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

        # Check tile cache
        cache_key = (field_name, year)
        if _tile_cache["key"] == cache_key and _tile_cache["vectors"] is not None:
            vectors = _tile_cache["vectors"]
            labels = _tile_cache["labels"]
            class_names = _tile_cache["class_names"]
            stats = _tile_cache["stats"]
            spatial_3x3 = _tile_cache.get("spatial_3x3")
            spatial_5x5 = _tile_cache.get("spatial_5x5")
            logger.info("Cache hit for %s/%s (%d pixels)", field_name, year, len(labels))

            # Recompute spatial features if needed but not cached
            if needs_spatial_3x3 and spatial_3x3 is None:
                _tile_cache["key"] = None  # force reload
            if needs_spatial_5x5 and spatial_5x5 is None:
                _tile_cache["key"] = None

        if _tile_cache["key"] != cache_key:
            # Load embeddings tile by tile
            gt = GeoTessera()

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

                for tile_idx, (yr, tile_lon, tile_lat, tile_emb, tile_crs, tile_transform) in enumerate(
                    gt.fetch_embeddings(tiles)
                ):
                    yield json.dumps({
                        "event": "download_progress", "tile": tile_idx + 1, "total": total_tiles,
                    }) + "\n"

                    h, w, dim = tile_emb.shape
                    tile_bounds = _array_bounds(h, w, tile_transform)
                    gdf_proj = gdf.to_crs(tile_crs) if gdf.crs != tile_crs else gdf
                    tile_gdf = gdf_proj[gdf_proj.intersects(_box(*tile_bounds))]
                    if tile_gdf.empty:
                        continue

                    class_raster = rasterize_shapefile(
                        tile_gdf, field_name, tile_transform, w, h, label_encoder=le)

                    labelled_mask = class_raster > 0
                    if labelled_mask.sum() == 0:
                        continue

                    tiles_with_data += 1
                    all_labels.append(class_raster[labelled_mask] - 1)
                    all_vectors.append(tile_emb[labelled_mask])

                    # Per-tile spatial features
                    if needs_spatial_3x3:
                        sf = gather_spatial_features_2d(tile_emb, radius=1)
                        all_spatial_3x3.append(sf[labelled_mask])
                    if needs_spatial_5x5:
                        sf = gather_spatial_features_2d(tile_emb, radius=2)
                        all_spatial_5x5.append(sf[labelled_mask])

                    # Per-tile U-Net patches
                    if needs_unet:
                        from tessera_eval.unet import extract_labelled_patches
                        patches = extract_labelled_patches(tile_emb, class_raster)
                        all_unet_patches.extend(patches)

                if not all_vectors:
                    yield json.dumps({"event": "error", "message": "No labelled pixels found across any tiles"}) + "\n"
                    return

                vectors = np.concatenate(all_vectors, axis=0).astype(np.float32)
                labels = np.concatenate(all_labels, axis=0).astype(np.int32)
                spatial_3x3 = np.concatenate(all_spatial_3x3, axis=0).astype(np.float32) if all_spatial_3x3 else None
                spatial_5x5 = np.concatenate(all_spatial_5x5, axis=0).astype(np.float32) if all_spatial_5x5 else None

                stats = {
                    "tile_count": total_tiles,
                    "tiles_with_data": tiles_with_data,
                    "total_pixels": len(labels),
                    "n_classes": len(class_names),
                }

                # Cache
                _tile_cache.update({
                    "key": cache_key, "vectors": vectors, "labels": labels,
                    "class_names": class_names, "stats": stats,
                    "spatial_3x3": spatial_3x3, "spatial_5x5": spatial_5x5,
                })

            except ValueError as e:
                yield json.dumps({"event": "error", "message": str(e)}) + "\n"
                return
        else:
            spatial_3x3 = _tile_cache.get("spatial_3x3")
            spatial_5x5 = _tile_cache.get("spatial_5x5")

        total_labelled = len(vectors)

        # Training sizes
        all_sizes = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]
        cap = max_train if max_train else total_labelled
        training_sizes = [s for s in all_sizes if s <= cap]
        if not training_sizes or training_sizes[-1] < cap:
            training_sizes.append(cap)

        # Class info
        unique_labels, counts = np.unique(labels, return_counts=True)
        class_info = []
        for lbl, cnt in zip(unique_labels, counts):
            name = class_names[lbl] if lbl < len(class_names) else f"Class {lbl}"
            class_info.append({"name": str(name), "pixels": int(cnt)})

        # Filter classifiers that the user hasn't installed deps for
        active_names = []
        for name in model_names:
            if name == "unet":
                try:
                    from tessera_eval.unet import _HAS_TORCH
                    if not _HAS_TORCH:
                        logger.warning("Skipping U-Net: PyTorch not installed")
                        continue
                except ImportError:
                    continue
            active_names.append(name)
        model_names = active_names

        yield json.dumps({
            "event": "start",
            "classifiers": model_names,
            "classes": class_info if is_classification else [],
            "total_labelled_pixels": total_labelled,
            "confusion_matrix_labels": class_names if is_classification else [],
            "training_sizes": training_sizes,
            "stats": stats,
        }) + "\n"

        # Run learning curve
        for event in run_learning_curve(
            vectors, labels, model_names, training_sizes,
            repeats=5, classifier_params=model_params,
            spatial_vectors=spatial_3x3, spatial_vectors_5x5=spatial_5x5,
            finish_classifiers=_finish_classifiers,
        ):
            if event["type"] == "progress":
                yield json.dumps({
                    "event": "progress",
                    "size": event["size"],
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

        for name in model_names:
            try:
                if name == "unet" and needs_unet:
                    from tessera_eval.unet import train_unet_on_patches
                    import torch as _torch
                    unet_patches = _tile_cache.get("_unet_patches", all_unet_patches if 'all_unet_patches' in dir() else [])
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
        serve(app, host=args.host, port=args.port, threads=4)


if __name__ == "__main__":
    main()
