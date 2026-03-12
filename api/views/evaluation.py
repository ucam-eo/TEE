"""Evaluation endpoints: shapefile upload and learning-curve computation."""

import gzip
import io
import json
import logging
import tempfile
import time
import warnings
import zipfile
from pathlib import Path

import geopandas as gpd
import joblib
import numpy as np
import rasterio.features
from affine import Affine
from django.http import FileResponse, JsonResponse, StreamingHttpResponse
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import LabelEncoder

from lib.config import VECTORS_DIR

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
        embeddings, coords, metadata = _load_vectors(viewport, str(year))
    except FileNotFoundError as e:
        return JsonResponse({"error": str(e)}, status=400)

    width = metadata["mosaic_width"]
    height = metadata["mosaic_height"]
    gt = metadata["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])

    class_raster = _rasterize_shapefile(gdf, field, transform, width, height)
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
        embeddings, coords, metadata = _load_vectors(viewport, str(year))
    except FileNotFoundError as e:
        return JsonResponse({"error": str(e)}, status=400)

    width = metadata["mosaic_width"]
    height = metadata["mosaic_height"]
    gt = metadata["geotransform"]
    transform = Affine(gt["a"], gt["b"], gt["c"], gt["d"], gt["e"], gt["f"])

    # 2. Rasterize shapefile
    class_raster = _rasterize_shapefile(gdf, field, transform, width, height)

    # 3. Build class labels per pixel
    pixel_labels = class_raster[coords[:, 1], coords[:, 0]]

    labelled_mask = pixel_labels > 0
    labelled_embeddings = embeddings[labelled_mask]
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
    labelled_embeddings = labelled_embeddings[valid_mask]
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
    subset_mask = np.zeros(len(embeddings), dtype=bool)
    subset_mask[np.where(labelled_mask)[0][valid_mask]] = True

    spatial_embeddings = None
    if "spatial_mlp" in classifiers:
        spatial_embeddings = _gather_spatial_features(
            embeddings, coords, width, height, radius=1, subset_mask=subset_mask)

    spatial_embeddings_5x5 = None
    if "spatial_mlp_5x5" in classifiers:
        spatial_embeddings_5x5 = _gather_spatial_features(
            embeddings, coords, width, height, radius=2, subset_mask=subset_mask)

    embedding_grid = None
    labelled_coords = None
    if "unet" in classifiers:
        from api.views.unet_model import build_embedding_grid, TORCH_MISSING_MSG
        from api.views.unet_model import torch as _unet_torch
        if _unet_torch is None:
            return JsonResponse({"error": TORCH_MISSING_MSG}, status=400)
        embedding_grid = build_embedding_grid(embeddings, coords, width, height)
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
        for event in _run_learning_curve(
            labelled_embeddings, labelled_labels, classifiers, training_sizes,
            repeats=5, classifier_params=classifier_params,
            spatial_embeddings=spatial_embeddings,
            spatial_embeddings_5x5=spatial_embeddings_5x5,
            embedding_grid=embedding_grid,
            labelled_coords=labelled_coords,
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
                                dim = labelled_embeddings.shape[1]
                                n_cls = len(np.unique(labelled_labels))
                                model = train_unet(
                                    embedding_grid, labelled_coords, labelled_labels,
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
                                    X_full = spatial_embeddings
                                elif name == "spatial_mlp_5x5":
                                    X_full = spatial_embeddings_5x5
                                else:
                                    X_full = labelled_embeddings
                                clf = _make_classifier(name, (classifier_params or {}).get(name, {}))
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
                        dim = labelled_embeddings.shape[1]
                        n_cls = len(np.unique(labelled_labels))
                        model = train_unet(
                            embedding_grid, labelled_coords, labelled_labels,
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
                            X_full = spatial_embeddings
                        elif name == "spatial_mlp_5x5":
                            X_full = spatial_embeddings_5x5
                        else:
                            X_full = labelled_embeddings
                        clf = _make_classifier(name, (classifier_params or {}).get(name, {}))
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


def _load_vectors(viewport, year):
    """Load dequantized float32 embeddings + pixel coords + metadata."""
    vector_dir = VECTORS_DIR / viewport / year

    emb_path = vector_dir / "all_embeddings_uint8.npy.gz"
    quant_path = vector_dir / "quantization.json"
    coords_path = vector_dir / "pixel_coords.npy.gz"
    meta_path = vector_dir / "metadata.json"

    for p in [emb_path, quant_path, coords_path, meta_path]:
        if not p.exists():
            raise FileNotFoundError(f"Missing vector file: {p}")

    # Load quantization params
    with open(quant_path) as f:
        quant = json.load(f)
    dim_min = np.array(quant["dim_min"], dtype=np.float32)
    dim_max = np.array(quant["dim_max"], dtype=np.float32)

    # Load uint8 embeddings
    with gzip.open(emb_path, "rb") as f:
        quantized = np.load(io.BytesIO(f.read()))

    # Dequantize to float32
    dim_scale = dim_max - dim_min
    dim_scale[dim_scale == 0] = 1
    embeddings = quantized.astype(np.float32) / 255.0 * dim_scale + dim_min

    # Load coords
    with gzip.open(coords_path, "rb") as f:
        coords = np.load(io.BytesIO(f.read()))

    # Load metadata
    with open(meta_path) as f:
        metadata = json.load(f)

    return embeddings, coords, metadata


def _rasterize_shapefile(gdf, field, transform, width, height):
    """Rasterize shapefile field onto pixel grid. Returns (H, W) int array."""
    le = LabelEncoder()
    gdf = gdf.dropna(subset=[field]).copy()
    gdf["_class_id"] = le.fit_transform(gdf[field]) + 1  # 1-based (0 = nodata)

    shapes = list(zip(gdf.geometry, gdf["_class_id"]))

    class_raster = rasterio.features.rasterize(
        shapes,
        out_shape=(height, width),
        transform=transform,
        fill=0,
        dtype=np.int32,
        all_touched=True,
    )

    return class_raster


def _gather_spatial_features(embeddings, coords, width, height, radius=1,
                             subset_mask=None):
    """Build (2r+1)² neighbourhood features from (N, dim) embeddings on a regular grid.

    If *subset_mask* (bool array, length N) is given, only compute features for
    those pixels, drastically reducing memory for large viewports.
    """
    dim = embeddings.shape[1]  # 128
    window = 2 * radius + 1
    # Build (row, col) → index lookup (uses all pixels for neighbour access)
    grid = np.full((height, width), -1, dtype=np.int32)
    grid[coords[:, 1], coords[:, 0]] = np.arange(len(coords))

    if subset_mask is not None:
        sub_coords = coords[subset_mask]
    else:
        sub_coords = coords

    offsets = [(dr, dc) for dr in range(-radius, radius + 1)
                        for dc in range(-radius, radius + 1)]
    spatial = np.zeros((len(sub_coords), window * window * dim), dtype=np.float32)

    for i, (dr, dc) in enumerate(offsets):
        nr = sub_coords[:, 1] + dr   # neighbour rows
        nc = sub_coords[:, 0] + dc   # neighbour cols
        valid = (nr >= 0) & (nr < height) & (nc >= 0) & (nc < width)
        idx = np.where(valid, grid[np.clip(nr, 0, height - 1), np.clip(nc, 0, width - 1)], -1)
        has_neighbour = valid & (idx >= 0)
        spatial[has_neighbour, i * dim:(i + 1) * dim] = embeddings[idx[has_neighbour]]

    return spatial


def _run_learning_curve(embeddings, labels, classifier_names, training_sizes,
                        repeats=5, classifier_params=None, spatial_embeddings=None,
                        spatial_embeddings_5x5=None, embedding_grid=None,
                        labelled_coords=None):
    """Generator that yields progress events after each training size."""
    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    n_samples = len(labels)
    n_classes = len(np.unique(labels))

    valid_sizes = training_sizes
    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    for size in valid_sizes:
        # Only evaluate classifiers not yet finished
        active = [n for n in classifier_names if n not in _finish_classifiers]
        if not active:
            break

        f1_scores = {name: [] for name in active}
        f1w_scores = {name: [] for name in active}
        is_largest = (size == valid_sizes[-1])

        # U-Net uses 1 repeat (gradient descent is deterministic enough)
        unet_repeats = 1

        for seed in range(repeats):
            rng = np.random.RandomState(seed)

            per_class = max(1, size // n_classes)
            train_idx = []
            for cls in range(n_classes):
                cls_indices = np.where(labels == cls)[0]
                n_take = min(per_class, int(0.8 * len(cls_indices)))
                n_take = max(1, n_take)
                chosen = rng.choice(cls_indices, size=n_take, replace=False)
                train_idx.extend(chosen)
            train_idx = np.array(train_idx)

            all_idx = np.arange(n_samples)
            test_idx = np.setdiff1d(all_idx, train_idx)

            if len(test_idx) == 0:
                continue

            X_train, y_train = embeddings[train_idx], labels[train_idx]
            X_test, y_test = embeddings[test_idx], labels[test_idx]

            for name in active:
                # U-Net only runs 1 repeat
                if name == "unet" and seed >= unet_repeats:
                    continue

                if name == "unet" and embedding_grid is not None:
                    try:
                        from api.views.unet_model import train_unet, predict_unet
                        model = train_unet(
                            embedding_grid, labelled_coords, labels,
                            train_idx, n_classes,
                            (classifier_params or {}).get("unet", {}))
                        preds = predict_unet(model, embedding_grid)
                        y_pred = preds[labelled_coords[test_idx, 1],
                                       labelled_coords[test_idx, 0]]
                        f1 = f1_score(y_test, y_pred, average="macro", zero_division=0)
                        f1w = f1_score(y_test, y_pred, average="weighted", zero_division=0)
                        f1_scores[name].append(f1)
                        f1w_scores[name].append(f1w)
                        if is_largest:
                            cm = confusion_matrix(y_test, y_pred, labels=np.arange(n_classes))
                            cm_accum[name] += cm
                    except Exception as e:
                        logger.warning(f"U-Net failed at size {size}: {e}")
                        f1_scores[name].append(0.0)
                        f1w_scores[name].append(0.0)
                    continue

                if name == "spatial_mlp" and spatial_embeddings is not None:
                    X_tr, X_te = spatial_embeddings[train_idx], spatial_embeddings[test_idx]
                elif name == "spatial_mlp_5x5" and spatial_embeddings_5x5 is not None:
                    X_tr, X_te = spatial_embeddings_5x5[train_idx], spatial_embeddings_5x5[test_idx]
                else:
                    X_tr, X_te = X_train, X_test
                clf = _make_classifier(name, (classifier_params or {}).get(name, {}))
                try:
                    clf.fit(X_tr, y_train)
                    y_pred = clf.predict(X_te)
                    f1 = f1_score(y_test, y_pred, average="macro", zero_division=0)
                    f1w = f1_score(y_test, y_pred, average="weighted", zero_division=0)
                    f1_scores[name].append(f1)
                    f1w_scores[name].append(f1w)
                    if is_largest:
                        cm = confusion_matrix(y_test, y_pred, labels=np.arange(n_classes))
                        cm_accum[name] += cm
                except Exception as e:
                    logger.warning(f"Classifier {name} failed at size {size}: {e}")
                    f1_scores[name].append(0.0)
                    f1w_scores[name].append(0.0)

        size_results = {}
        for name in active:
            scores = f1_scores[name]
            scoresw = f1w_scores[name]
            size_results[name] = {
                "mean_f1": round(float(np.mean(scores)), 4) if scores else 0.0,
                "std_f1": round(float(np.std(scores)), 4) if scores else 0.0,
                "mean_f1w": round(float(np.mean(scoresw)), 4) if scoresw else 0.0,
                "std_f1w": round(float(np.std(scoresw)), 4) if scoresw else 0.0,
            }

        yield {"type": "progress", "size": size, "classifiers": size_results}

    # Confusion matrices from the largest size (only for classifiers that ran it)
    confusion_matrices = {}
    for name in classifier_names:
        if cm_accum[name].any():
            confusion_matrices[name] = cm_accum[name].tolist()
    if confusion_matrices:
        yield {"type": "confusion_matrices", "confusion_matrices": confusion_matrices}


def _make_classifier(name, params=None):
    """Create a classifier instance by name with optional hyperparameters."""
    p = params or {}
    if name == "nn":
        return KNeighborsClassifier(
            n_neighbors=int(p.get("n_neighbors", 5)),
            weights=p.get("weights", "uniform"),
            metric="euclidean",
        )
    elif name == "rf":
        max_depth = p.get("max_depth")
        if max_depth is not None:
            max_depth = int(max_depth)
        return RandomForestClassifier(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=max_depth,
            n_jobs=-1, random_state=42,
        )
    elif name == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=int(p.get("max_depth", 6)),
            learning_rate=float(p.get("learning_rate", 0.3)),
            n_jobs=-1, random_state=42,
            use_label_encoder=False, eval_metric="mlogloss", verbosity=0,
        )
    elif name == "mlp":
        layers_str = p.get("hidden_layers", "64,32")
        if isinstance(layers_str, str):
            hidden = tuple(int(x) for x in layers_str.split(","))
        else:
            hidden = (64, 32)
        return MLPClassifier(
            hidden_layer_sizes=hidden,
            max_iter=int(p.get("max_iter", 200)),
            random_state=42,
        )
    elif name == "spatial_mlp":
        layers_str = p.get("hidden_layers", "256,128")
        if isinstance(layers_str, str):
            hidden = tuple(int(x) for x in layers_str.split(","))
        else:
            hidden = (256, 128)
        return MLPClassifier(
            hidden_layer_sizes=hidden,
            max_iter=int(p.get("max_iter", 300)),
            random_state=42,
        )
    elif name == "spatial_mlp_5x5":
        layers_str = p.get("hidden_layers", "512,256")
        if isinstance(layers_str, str):
            hidden = tuple(int(x) for x in layers_str.split(","))
        else:
            hidden = (512, 256)
        return MLPClassifier(
            hidden_layer_sizes=hidden,
            max_iter=int(p.get("max_iter", 400)),
            random_state=42,
        )
    elif name == "unet":
        return None  # handled specially in the learning-curve loop
    else:
        raise ValueError(f"Unknown classifier: {name}")


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
