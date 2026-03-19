"""Evaluation endpoints: shapefile upload and learning-curve computation."""

import json
import logging
import tempfile
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
from affine import Affine
from django.http import FileResponse, JsonResponse, StreamingHttpResponse
from sklearn.preprocessing import LabelEncoder
from lib.evaluation_engine import (
    load_vectors,
    rasterize_shapefile,
    gather_spatial_features,
    augment_spatial,
    make_classifier,
    run_learning_curve,
)

logger = logging.getLogger(__name__)

# Module-level cache for uploaded shapefile path (per-process; fine for single-user)
_uploaded_shapefile = {"path": None, "gdf": None}

# Cache for trained model files: classifier name → temp file path
_trained_models = {}

# Classifiers the user has marked "done" mid-stream (cleared at stream start)
_finish_classifiers = set()


def upload_shapefile(request):
    """Accept a .zip containing .shp/.dbf/.shx/.prj, return field info."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    if not uploaded.name.endswith(".zip"):
        return JsonResponse({"error": "File must be a .zip"}, status=400)

    # Extract to temp dir
    tmp_dir = tempfile.mkdtemp(prefix="tee_eval_")
    zip_path = Path(tmp_dir) / uploaded.name
    with open(zip_path, "wb") as f:
        for chunk in uploaded.chunks():
            f.write(chunk)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)
    except zipfile.BadZipFile:
        return JsonResponse({"error": "Invalid zip file"}, status=400)

    # Find the .shp file
    shp_files = list(Path(tmp_dir).rglob("*.shp"))
    if not shp_files:
        return JsonResponse({"error": "No .shp file found in zip"}, status=400)

    shp_path = shp_files[0]
    try:
        gdf = gpd.read_file(shp_path)
    except Exception as e:
        return JsonResponse({"error": f"Failed to read shapefile: {e}"}, status=400)

    # Reproject to EPSG:4326
    if gdf.crs and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Cache for run step
    _uploaded_shapefile["path"] = str(shp_path)
    _uploaded_shapefile["gdf"] = gdf

    # Build field info
    fields = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        unique_count = gdf[col].nunique()
        samples = gdf[col].dropna().head(10).tolist()
        # Convert numpy types to native Python for JSON
        samples = [s if isinstance(s, (str, int, float)) else str(s) for s in samples]
        fields.append({
            "name": col,
            "unique_count": int(unique_count),
            "samples": samples,
        })

    # Build GeoJSON for map overlay (cap at 10k features to avoid browser crash)
    MAX_OVERLAY = 10_000
    if len(gdf) > MAX_OVERLAY:
        geojson = json.loads(gdf.iloc[:MAX_OVERLAY].to_json())
        geojson["truncated"] = len(gdf)
    else:
        geojson = json.loads(gdf.to_json())

    return JsonResponse({"fields": fields, "geojson": geojson})


def class_pixel_counts(request):
    """Return pixel counts per class for the uploaded shapefile (no ML, just rasterize)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    viewport = body.get("viewport")
    year = body.get("year")
    field = body.get("field")

    if not all([viewport, year, field]):
        return JsonResponse({"error": "viewport, year, and field are required"}, status=400)

    gdf = _uploaded_shapefile.get("gdf")
    if gdf is None:
        return JsonResponse({"error": "No shapefile uploaded."}, status=400)

    if field not in gdf.columns:
        return JsonResponse({"error": f"Field '{field}' not found"}, status=400)

    try:
        vectors, coords, metadata = load_vectors(viewport, str(year))
    except FileNotFoundError as e:
        return JsonResponse({"error": str(e)}, status=400)

    width = metadata["mosaic_width"]
    height = metadata["mosaic_height"]
    gt = metadata["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])

    class_raster = rasterize_shapefile(gdf, field, transform, width, height)
    pixel_labels = class_raster[coords[:, 1], coords[:, 0]]

    le = LabelEncoder()
    le.fit(gdf[field].dropna().unique())
    class_names = le.classes_.tolist()

    unique_labels, counts = np.unique(pixel_labels[pixel_labels > 0], return_counts=True)
    classes = []
    for lbl, cnt in zip(unique_labels, counts):
        name = class_names[lbl - 1] if lbl <= len(class_names) else f"Class {lbl}"
        classes.append({"name": str(name), "pixels": int(cnt)})

    return JsonResponse({"classes": classes})


def finish_classifier(request):
    """Mark a classifier as finished so the stream stops evaluating it early."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    name = body.get("classifier")
    if not name:
        return JsonResponse({"error": "classifier is required"}, status=400)
    _finish_classifiers.add(name)
    logger.info(f"Classifier '{name}' marked for early finish")
    return JsonResponse({"ok": True})


def run_evaluation(request):
    """Run learning-curve evaluation, streaming NDJSON events."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    viewport = body.get("viewport")
    year = body.get("year")
    field = body.get("field")
    classifiers = body.get("classifiers", ["nn", "rf"])
    classifier_params = body.get("params", {})
    max_train = int(body.get("max_train", 10000))

    if not all([viewport, year, field]):
        return JsonResponse({"error": "viewport, year, and field are required"}, status=400)

    gdf = _uploaded_shapefile.get("gdf")
    if gdf is None:
        return JsonResponse({"error": "No shapefile uploaded. Upload first."}, status=400)

    if field not in gdf.columns:
        return JsonResponse({"error": f"Field '{field}' not found in shapefile"}, status=400)

    # 1. Load vectors (do this before streaming so errors return clean JSON)
    try:
        vectors, coords, metadata = load_vectors(viewport, str(year))
    except FileNotFoundError as e:
        return JsonResponse({"error": str(e)}, status=400)

    width = metadata["mosaic_width"]
    height = metadata["mosaic_height"]
    gt = metadata["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])

    # 2. Rasterize shapefile
    class_raster = rasterize_shapefile(gdf, field, transform, width, height)

    # 3. Build class labels per pixel
    pixel_labels = class_raster[coords[:, 1], coords[:, 0]]

    labelled_mask = pixel_labels > 0
    labelled_vectors = vectors[labelled_mask]
    labelled_labels = pixel_labels[labelled_mask]

    if len(labelled_labels) == 0:
        return JsonResponse({
            "error": "No pixels overlap with the shapefile. Check that the shapefile covers the viewport area."
        }, status=400)

    le = LabelEncoder()
    le.fit(gdf[field].dropna().unique())
    class_names = le.classes_.tolist()

    unique_labels, counts = np.unique(labelled_labels, return_counts=True)
    class_info = []
    for lbl, cnt in zip(unique_labels, counts):
        name = class_names[lbl - 1] if lbl <= len(class_names) else f"Class {lbl}"
        class_info.append({"name": str(name), "pixels": int(cnt)})

    min_pixels = 50
    valid_classes = set(lbl for lbl, cnt in zip(unique_labels, counts) if cnt >= min_pixels)
    if len(valid_classes) < 2:
        return JsonResponse({
            "error": f"Need at least 2 classes with >= {min_pixels} pixels each. "
                     f"Found {len(valid_classes)}."
        }, status=400)

    valid_mask = np.isin(labelled_labels, list(valid_classes))
    labelled_vectors = labelled_vectors[valid_mask]
    labelled_labels = labelled_labels[valid_mask]

    label_encoder_final = LabelEncoder()
    labelled_labels = label_encoder_final.fit_transform(labelled_labels)

    valid_class_names = []
    for enc_label in label_encoder_final.classes_:
        name = class_names[enc_label - 1] if enc_label <= len(class_names) else f"Class {enc_label}"
        valid_class_names.append(str(name))

    total_labelled = len(labelled_labels)
    logger.info(f"Evaluation: {total_labelled} labelled pixels, "
                f"{len(valid_classes)} classes, classifiers={classifiers}")

    # Combined mask: labelled AND valid class, on the full pixel array
    subset_mask = np.zeros(len(vectors), dtype=bool)
    subset_mask[np.where(labelled_mask)[0][valid_mask]] = True

    spatial_vectors = None
    if "spatial_mlp" in classifiers:
        spatial_vectors = gather_spatial_features(
            vectors, coords, width, height, radius=1, subset_mask=subset_mask)

    spatial_vectors_5x5 = None
    if "spatial_mlp_5x5" in classifiers:
        spatial_vectors_5x5 = gather_spatial_features(
            vectors, coords, width, height, radius=2, subset_mask=subset_mask)

    vector_grid = None
    labelled_coords = None
    if "unet" in classifiers:
        from api.views.unet_model import build_vector_grid, _HAS_TORCH, TORCH_MISSING_MSG
        if not _HAS_TORCH:
            return JsonResponse({"error": TORCH_MISSING_MSG}, status=400)
        vector_grid = build_vector_grid(vectors, coords, width, height)
        labelled_coords = coords[np.where(labelled_mask)[0][valid_mask]]

    all_sizes = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]
    training_sizes = [s for s in all_sizes if s <= max_train]
    if not training_sizes or training_sizes[-1] < max_train:
        training_sizes.append(max_train)

    def stream():
        _finish_classifiers.clear()

        # Clean up old models
        for old_path in _trained_models.values():
            try:
                Path(old_path).unlink(missing_ok=True)
            except OSError:
                pass
        _trained_models.clear()

        t0 = time.time()

        # start event
        yield json.dumps({
            "event": "start",
            "classifiers": classifiers,
            "classes": class_info,
            "total_labelled_pixels": total_labelled,
            "confusion_matrix_labels": valid_class_names,
            "training_sizes": training_sizes,
        }) + "\n"

        # Run learning curve as generator
        active_classifiers = list(classifiers)
        for event in run_learning_curve(
            labelled_vectors, labelled_labels, classifiers, training_sizes,
            repeats=5, classifier_params=classifier_params,
            spatial_vectors=spatial_vectors,
            spatial_vectors_5x5=spatial_vectors_5x5,
            vector_grid=vector_grid,
            labelled_coords=labelled_coords,
            finish_classifiers=_finish_classifiers,
        ):
            if event["type"] == "progress":
                yield json.dumps({
                    "event": "progress",
                    "size": event["size"],
                    "classifiers": event["classifiers"],
                }) + "\n"

                # Check for newly finished classifiers and retrain them
                for name in list(active_classifiers):
                    if name in _finish_classifiers and name not in _trained_models:
                        try:
                            if name == "unet":
                                import torch as _torch
                                from api.views.unet_model import train_unet
                                dim = labelled_vectors.shape[1]
                                n_cls = len(np.unique(labelled_labels))
                                model = train_unet(
                                    vector_grid, labelled_coords, labelled_labels,
                                    np.arange(len(labelled_labels)), n_cls,
                                    (classifier_params or {}).get("unet", {}))
                                tmp = tempfile.NamedTemporaryFile(
                                    suffix=".pt", prefix=f"{name}_model_", delete=False)
                                _torch.save({
                                    "model_state": model.state_dict(),
                                    "class_names": valid_class_names,
                                    "in_channels": dim, "n_classes": n_cls,
                                    "depth": int((classifier_params or {}).get("unet", {}).get("depth", 3)),
                                    "base_filters": int((classifier_params or {}).get("unet", {}).get("base_filters", 64)),
                                }, tmp.name)
                                _trained_models[name] = tmp.name
                            else:
                                if name == "spatial_mlp":
                                    X_full = spatial_vectors
                                elif name == "spatial_mlp_5x5":
                                    X_full = spatial_vectors_5x5
                                else:
                                    X_full = labelled_vectors
                                clf = make_classifier(name, (classifier_params or {}).get(name, {}))
                                clf.fit(X_full, labelled_labels)
                                tmp = tempfile.NamedTemporaryFile(
                                    suffix=".joblib", prefix=f"{name}_model_", delete=False
                                )
                                joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                                _trained_models[name] = tmp.name
                            logger.info(f"Early-finish: trained '{name}' → {tmp.name}")
                            yield json.dumps({
                                "event": "model_ready",
                                "classifier": name,
                            }) + "\n"
                        except Exception as e:
                            logger.warning(f"Early-finish retrain failed for {name}: {e}")
                        active_classifiers.remove(name)

            elif event["type"] == "confusion_matrices":
                yield json.dumps({
                    "event": "confusion_matrices",
                    "confusion_matrices": event["confusion_matrices"],
                }) + "\n"

        # Retrain classifiers that weren't finished early
        for name in active_classifiers:
            if name not in _trained_models:
                try:
                    if name == "unet":
                        import torch as _torch
                        from api.views.unet_model import train_unet
                        dim = labelled_vectors.shape[1]
                        n_cls = len(np.unique(labelled_labels))
                        model = train_unet(
                            vector_grid, labelled_coords, labelled_labels,
                            np.arange(len(labelled_labels)), n_cls,
                            (classifier_params or {}).get("unet", {}))
                        tmp = tempfile.NamedTemporaryFile(
                            suffix=".pt", prefix=f"{name}_model_", delete=False)
                        _torch.save({
                            "model_state": model.state_dict(),
                            "class_names": valid_class_names,
                            "in_channels": dim, "n_classes": n_cls,
                            "depth": int((classifier_params or {}).get("unet", {}).get("depth", 3)),
                            "base_filters": int((classifier_params or {}).get("unet", {}).get("base_filters", 64)),
                        }, tmp.name)
                        _trained_models[name] = tmp.name
                    else:
                        if name == "spatial_mlp":
                            X_full = spatial_vectors
                        elif name == "spatial_mlp_5x5":
                            X_full = spatial_vectors_5x5
                        else:
                            X_full = labelled_vectors
                        clf = make_classifier(name, (classifier_params or {}).get(name, {}))
                        clf.fit(X_full, labelled_labels)
                        tmp = tempfile.NamedTemporaryFile(
                            suffix=".joblib", prefix=f"{name}_model_", delete=False
                        )
                        joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
                        _trained_models[name] = tmp.name
                    logger.info(f"Trained and cached model '{name}' → {tmp.name}")
                    yield json.dumps({
                        "event": "model_ready",
                        "classifier": name,
                    }) + "\n"
                except Exception as e:
                    logger.warning(f"Failed to retrain {name} on full data: {e}")

        elapsed = time.time() - t0
        yield json.dumps({
            "event": "done",
            "elapsed_seconds": round(elapsed, 1),
            "models_available": list(_trained_models.keys()),
        }) + "\n"

    resp = StreamingHttpResponse(stream(), content_type="application/x-ndjson")
    resp["Cache-Control"] = "no-cache"
    # Prevent GZipMiddleware from buffering the stream
    resp["Content-Encoding"] = "identity"
    return resp


def download_model(request, classifier):
    """Serve a trained model file for download (.pt for U-Net, .joblib otherwise)."""
    path = _trained_models.get(classifier)
    if not path or not Path(path).exists():
        return JsonResponse(
            {"error": f"No trained model for '{classifier}'. Run evaluation first."},
            status=404,
        )
    ext = ".pt" if classifier == "unet" else ".joblib"
    return FileResponse(
        open(path, "rb"),
        content_type="application/octet-stream",
        as_attachment=True,
        filename=f"{classifier}_model{ext}",
    )
