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
import numpy as np
from flask import Flask, Response, jsonify, request, send_file

logger = logging.getLogger(__name__)

app = Flask(__name__)

# ── State (single-user, same as Django views) ──

_uploaded_shapefile = {"path": None, "gdf": None}
_trained_models = {}
_hosted_url = None


# ── Local evaluation endpoints ──

@app.route("/api/evaluation/upload-shapefile", methods=["POST"])
def upload_shapefile():
    """Accept a .zip containing .shp/.dbf/.shx/.prj, return field info."""
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

    non_geom_cols = [c for c in gdf.columns if c != "geometry"]
    if not non_geom_cols:
        return jsonify({"error": "Shapefile has no attribute fields"}), 400

    # Reproject to EPSG:4326
    if gdf.crs is None:
        logger.warning("Shapefile has no CRS — assuming EPSG:4326")
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    _uploaded_shapefile["path"] = str(shp_files[0])
    _uploaded_shapefile["gdf"] = gdf

    # Build field info
    fields = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        unique_count = gdf[col].nunique()
        samples = gdf[col].dropna().head(10).tolist()
        samples = [s if isinstance(s, (str, int, float)) else str(s) for s in samples]
        fields.append({"name": col, "unique_count": int(unique_count), "samples": samples})

    # Build GeoJSON for map overlay
    MAX_OVERLAY = 10_000
    if len(gdf) > MAX_OVERLAY:
        geojson = json.loads(gdf.iloc[:MAX_OVERLAY].to_json())
        geojson["truncated"] = len(gdf)
    else:
        geojson = json.loads(gdf.to_json())

    return jsonify({"fields": fields, "geojson": geojson})


@app.route("/api/evaluation/run-large-area", methods=["POST"])
def run_large_area():
    """Run large-area evaluation: GeoTessera tile loading + learning curve."""
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

    gdf = _uploaded_shapefile.get("gdf")
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

    def stream():
        from geotessera import GeoTessera
        from rasterio.transform import array_bounds as _array_bounds
        from shapely.geometry import box as _box
        from sklearn.preprocessing import LabelEncoder
        from tessera_eval.rasterize import rasterize_shapefile
        from tessera_eval.evaluate import run_learning_curve

        t0 = time.time()

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

            if not all_vectors:
                yield json.dumps({"event": "error", "message": "No labelled pixels found across any tiles"}) + "\n"
                return

            vectors = np.concatenate(all_vectors, axis=0).astype(np.float32)
            labels = np.concatenate(all_labels, axis=0).astype(np.int32)

            stats = {
                "tile_count": total_tiles,
                "tiles_with_data": tiles_with_data,
                "total_pixels": len(labels),
                "n_classes": len(class_names),
            }

        except ValueError as e:
            yield json.dumps({"event": "error", "message": str(e)}) + "\n"
            return

        total_labelled = len(labels)

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

        yield json.dumps({
            "event": "start",
            "classifiers": model_names,
            "classes": class_info if is_classification else [],
            "total_labelled_pixels": total_labelled,
            "confusion_matrix_labels": class_names if is_classification else [],
            "training_sizes": training_sizes,
            "stats": stats,
        }) + "\n"

        for event in run_learning_curve(
            vectors, labels, model_names, training_sizes,
            repeats=5, classifier_params=model_params,
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

        elapsed = time.time() - t0
        yield json.dumps({
            "event": "done",
            "elapsed_seconds": round(elapsed, 1),
            "field": field_name,
            "year": year,
        }) + "\n"

    return Response(stream(), mimetype="application/x-ndjson",
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
    """Health check — also reports what hosted server we proxy to."""
    return jsonify({
        "status": "ok",
        "mode": "compute",
        "hosted": _hosted_url,
        "version": _get_version(),
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
