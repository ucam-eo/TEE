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
from django.http import FileResponse, JsonResponse
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


def run_evaluation(request):
    """Run learning-curve evaluation with selected classifiers."""
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

    t0 = time.time()

    # 1. Load vectors
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
    # coords is (N, 2) with (x, y) = (col, row)
    pixel_labels = class_raster[coords[:, 1], coords[:, 0]]

    # Filter out unlabelled pixels (value 0 = no data)
    labelled_mask = pixel_labels > 0
    labelled_embeddings = embeddings[labelled_mask]
    labelled_labels = pixel_labels[labelled_mask]

    if len(labelled_labels) == 0:
        return JsonResponse({
            "error": "No pixels overlap with the shapefile. Check that the shapefile covers the viewport area."
        }, status=400)

    # Build class name mapping
    le = LabelEncoder()
    le.fit(gdf[field].dropna().unique())
    # class_raster used 1-based indexing matching le.classes_ order
    class_names = le.classes_.tolist()

    # Count pixels per class
    unique_labels, counts = np.unique(labelled_labels, return_counts=True)
    class_info = []
    for lbl, cnt in zip(unique_labels, counts):
        name = class_names[lbl - 1] if lbl <= len(class_names) else f"Class {lbl}"
        class_info.append({"name": str(name), "pixels": int(cnt)})

    # Filter classes with < 50 pixels
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

    # Re-encode labels to contiguous 0..N-1
    label_encoder_final = LabelEncoder()
    labelled_labels = label_encoder_final.fit_transform(labelled_labels)

    # Map re-encoded labels back to original class names
    valid_class_names = []
    for enc_label in label_encoder_final.classes_:
        name = class_names[enc_label - 1] if enc_label <= len(class_names) else f"Class {enc_label}"
        valid_class_names.append(str(name))

    total_labelled = len(labelled_labels)
    logger.info(f"Evaluation: {total_labelled} labelled pixels, "
                f"{len(valid_classes)} classes, classifiers={classifiers}")

    # 4. Run learning curve
    # Build log-spaced training sizes up to max_train
    all_sizes = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]
    training_sizes = [s for s in all_sizes if s <= max_train]
    if not training_sizes or training_sizes[-1] < max_train:
        training_sizes.append(max_train)
    results = _run_learning_curve(
        labelled_embeddings, labelled_labels, classifiers, training_sizes,
        repeats=5, classifier_params=classifier_params
    )

    # Retrain each classifier on ALL labelled data and cache for download
    _trained_models.clear()
    for name in classifiers:
        try:
            clf = _make_classifier(name, (classifier_params or {}).get(name, {}))
            clf.fit(labelled_embeddings, labelled_labels)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".joblib", prefix=f"{name}_model_", delete=False
            )
            joblib.dump({"model": clf, "class_names": valid_class_names}, tmp.name)
            _trained_models[name] = tmp.name
            logger.info(f"Trained and cached model '{name}' → {tmp.name}")
        except Exception as e:
            logger.warning(f"Failed to retrain {name} on full data: {e}")

    elapsed = time.time() - t0

    return JsonResponse({
        "training_sizes": results["training_sizes"],
        "classifiers": results["classifiers"],
        "confusion_matrices": results["confusion_matrices"],
        "confusion_matrix_labels": valid_class_names,
        "classes": class_info,
        "total_labelled_pixels": total_labelled,
        "elapsed_seconds": round(elapsed, 1),
        "models_available": list(_trained_models.keys()),
    })


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


def _run_learning_curve(embeddings, labels, classifier_names, training_sizes,
                        repeats=5, classifier_params=None):
    """Run learning curve evaluation. Returns dict with mean/std F1 per classifier."""
    # Suppress sklearn warnings about small training sets (expected at low sample sizes)
    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    n_samples = len(labels)
    n_classes = len(np.unique(labels))

    # Use all requested training sizes — the stratified sampler below
    # caps per-class at what's available, so large sizes still work
    valid_sizes = training_sizes

    results = {name: {"mean_f1": [], "std_f1": [], "mean_f1w": [], "std_f1w": []} for name in classifier_names}
    # Accumulate confusion matrices at the largest training size
    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    for size in valid_sizes:
        f1_scores = {name: [] for name in classifier_names}
        f1w_scores = {name: [] for name in classifier_names}
        is_largest = (size == valid_sizes[-1])

        for seed in range(repeats):
            rng = np.random.RandomState(seed)

            # Stratified sample: target per_class pixels from each class,
            # capped at 80% of that class's count to leave test data
            per_class = max(1, size // n_classes)
            train_idx = []
            for cls in range(n_classes):
                cls_indices = np.where(labels == cls)[0]
                n_take = min(per_class, int(0.8 * len(cls_indices)))
                n_take = max(1, n_take)
                chosen = rng.choice(cls_indices, size=n_take, replace=False)
                train_idx.extend(chosen)
            train_idx = np.array(train_idx)

            # Test set = everything not in train
            all_idx = np.arange(n_samples)
            test_idx = np.setdiff1d(all_idx, train_idx)

            if len(test_idx) == 0:
                continue

            X_train, y_train = embeddings[train_idx], labels[train_idx]
            X_test, y_test = embeddings[test_idx], labels[test_idx]

            for name in classifier_names:
                clf = _make_classifier(name, (classifier_params or {}).get(name, {}))
                try:
                    clf.fit(X_train, y_train)
                    y_pred = clf.predict(X_test)
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

        for name in classifier_names:
            scores = f1_scores[name]
            results[name]["mean_f1"].append(round(float(np.mean(scores)), 4) if scores else 0.0)
            results[name]["std_f1"].append(round(float(np.std(scores)), 4) if scores else 0.0)
            scoresw = f1w_scores[name]
            results[name]["mean_f1w"].append(round(float(np.mean(scoresw)), 4) if scoresw else 0.0)
            results[name]["std_f1w"].append(round(float(np.std(scoresw)), 4) if scoresw else 0.0)

    # Convert accumulated CMs to lists for JSON serialization
    confusion_matrices = {name: cm_accum[name].tolist() for name in classifier_names}

    return {"training_sizes": valid_sizes, "classifiers": results, "confusion_matrices": confusion_matrices}


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
    else:
        raise ValueError(f"Unknown classifier: {name}")


def download_model(request, classifier):
    """Serve a trained model joblib file for download."""
    path = _trained_models.get(classifier)
    if not path or not Path(path).exists():
        return JsonResponse(
            {"error": f"No trained model for '{classifier}'. Run evaluation first."},
            status=404,
        )
    return FileResponse(
        open(path, "rb"),
        content_type="application/octet-stream",
        as_attachment=True,
        filename=f"{classifier}_model.joblib",
    )
