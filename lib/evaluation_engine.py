"""Pure evaluation helpers: vector loading, rasterization, classifiers, learning curve."""

import gzip
import io
import json
import logging
import time
import warnings

import numpy as np
import rasterio.features
from affine import Affine
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import LabelEncoder

from lib.config import VECTORS_DIR

logger = logging.getLogger(__name__)


def dequantize(quantized, dim_min, dim_max):
    """Convert uint8 embeddings to float32 vectors using per-dimension min/max."""
    dim_scale = dim_max - dim_min
    dim_scale[dim_scale == 0] = 1
    return quantized.astype(np.float32) / 255.0 * dim_scale + dim_min


def load_vectors(viewport, year):
    """Load dequantized float32 vectors + pixel coords + metadata."""
    vector_dir = VECTORS_DIR / viewport / str(year)

    emb_path = vector_dir / "all_vectors_uint8.npy.gz"
    quant_path = vector_dir / "quantization.json"
    coords_path = vector_dir / "pixel_coords.npy.gz"
    meta_path = vector_dir / "metadata.json"

    for p in [emb_path, quant_path, coords_path, meta_path]:
        if not p.exists():
            raise FileNotFoundError(f"Missing vector file: {p}")

    with open(quant_path) as f:
        quant = json.load(f)
    dim_min = np.array(quant["dim_min"], dtype=np.float32)
    dim_max = np.array(quant["dim_max"], dtype=np.float32)

    with gzip.open(emb_path, "rb") as f:
        quantized = np.load(io.BytesIO(f.read()))

    vectors = dequantize(quantized, dim_min, dim_max)

    with gzip.open(coords_path, "rb") as f:
        coords = np.load(io.BytesIO(f.read()))

    with open(meta_path) as f:
        metadata = json.load(f)

    return vectors, coords, metadata


def rasterize_shapefile(gdf, field, transform, width, height):
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


def gather_spatial_features(vectors, coords, width, height, radius=1,
                            subset_mask=None):
    """Build (2r+1)^2 neighbourhood features from (N, dim) vectors on a regular grid."""
    dim = vectors.shape[1]
    window = 2 * radius + 1
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
        nr = sub_coords[:, 1] + dr
        nc = sub_coords[:, 0] + dc
        valid = (nr >= 0) & (nr < height) & (nc >= 0) & (nc < width)
        idx = np.where(valid, grid[np.clip(nr, 0, height - 1), np.clip(nc, 0, width - 1)], -1)
        has_neighbour = valid & (idx >= 0)
        spatial[has_neighbour, i * dim:(i + 1) * dim] = vectors[idx[has_neighbour]]

    return spatial


def augment_spatial(X, y, window, dim):
    """4x data augmentation via horizontal/vertical flips of spatial patches."""
    n = len(X)
    patches = X.reshape(n, window, window, dim)
    augmented = [
        X,
        patches[:, :, ::-1, :].copy().reshape(n, -1),
        patches[:, ::-1, :, :].copy().reshape(n, -1),
        patches[:, ::-1, ::-1, :].copy().reshape(n, -1),
    ]
    return np.concatenate(augmented, axis=0), np.tile(y, 4)


def make_classifier(name, params=None):
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


def run_learning_curve(vectors, labels, classifier_names, training_sizes,
                       repeats=5, classifier_params=None, spatial_vectors=None,
                       spatial_vectors_5x5=None, vector_grid=None,
                       labelled_coords=None, finish_classifiers=None):
    """Generator that yields progress events after each training size."""
    if finish_classifiers is None:
        finish_classifiers = set()

    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    n_samples = len(labels)
    n_classes = len(np.unique(labels))

    valid_sizes = training_sizes
    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    for size in valid_sizes:
        active = [n for n in classifier_names if n not in finish_classifiers]
        if not active:
            break

        f1_scores = {name: [] for name in active}
        f1w_scores = {name: [] for name in active}
        is_largest = (size == valid_sizes[-1])

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

            X_train, y_train = vectors[train_idx], labels[train_idx]
            X_test, y_test = vectors[test_idx], labels[test_idx]

            for name in active:
                if name == "unet" and seed >= unet_repeats:
                    continue

                if name == "unet" and vector_grid is not None:
                    try:
                        from api.views.unet_model import train_unet, predict_unet
                        model = train_unet(
                            vector_grid, labelled_coords, labels,
                            train_idx, n_classes,
                            (classifier_params or {}).get("unet", {}))
                        preds = predict_unet(model, vector_grid)
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

                if name == "spatial_mlp" and spatial_vectors is not None:
                    X_tr, X_te = spatial_vectors[train_idx], spatial_vectors[test_idx]
                    X_tr, y_tr_aug = augment_spatial(X_tr, y_train, window=3, dim=vectors.shape[1])
                elif name == "spatial_mlp_5x5" and spatial_vectors_5x5 is not None:
                    X_tr, X_te = spatial_vectors_5x5[train_idx], spatial_vectors_5x5[test_idx]
                    X_tr, y_tr_aug = augment_spatial(X_tr, y_train, window=5, dim=vectors.shape[1])
                else:
                    X_tr, X_te = X_train, X_test
                    y_tr_aug = y_train
                clf = make_classifier(name, (classifier_params or {}).get(name, {}))
                try:
                    clf.fit(X_tr, y_tr_aug)
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

    confusion_matrices = {}
    for name in classifier_names:
        if cm_accum[name].any():
            confusion_matrices[name] = cm_accum[name].tolist()
    if confusion_matrices:
        yield {"type": "confusion_matrices", "confusion_matrices": confusion_matrices}
