"""Learning curve and k-fold cross-validation evaluation with streaming results."""

import logging
import warnings

import numpy as np

logger = logging.getLogger(__name__)
from sklearn.metrics import (
    confusion_matrix, f1_score, mean_absolute_error, mean_squared_error, r2_score,
)
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn.preprocessing import LabelEncoder

from tessera_eval.classify import make_classifier, make_regressor, augment_spatial


def run_learning_curve(vectors, labels, classifier_names, training_pcts,
                       repeats=5, classifier_params=None, spatial_vectors=None,
                       spatial_vectors_5x5=None, finish_classifiers=None,
                       unet_patches=None, **kwargs):
    """Generator that yields progress events after each training percentage.

    Runs stratified sampling at each training percentage, computing F1 scores
    (macro and weighted) with multiple random repeats. Yields confusion matrices
    at the largest percentage. Supports U-Net via patch-based train/test splits.

    Args:
        vectors: float32 array, shape (N, dim) — labelled pixel embeddings
        labels: int array, shape (N,) — class labels (0-indexed)
        classifier_names: list of classifier names (e.g., ['nn', 'rf', 'unet'])
        training_pcts: list of floats — training percentages (e.g., [1, 5, 10, 30, 50, 80])
        repeats: Number of random repeats per size (default 5)
        classifier_params: Optional dict of {classifier_name: {param: value}}
        spatial_vectors: Optional float32 array for spatial_mlp (3x3 features)
        spatial_vectors_5x5: Optional float32 array for spatial_mlp_5x5 (5x5 features)
        finish_classifiers: Optional set of classifier names to skip
        unet_patches: Optional list of (emb_patch, label_patch) tuples for U-Net
        **kwargs: Extra arguments accepted for compatibility.

    Yields:
        dict events:
        - {"type": "progress", "pct": float, "classifiers": {name: {mean_f1, std_f1, mean_f1w, std_f1w}}}
        - {"type": "confusion_matrices", "confusion_matrices": {name: [[int]]}}
    """
    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    if finish_classifiers is None:
        finish_classifiers = set()

    n_samples = len(labels)
    n_classes = len(np.unique(labels))

    # Separate pixel-based and U-Net classifiers
    pixel_classifiers = [n for n in classifier_names if n != 'unet']
    has_unet = 'unet' in classifier_names and unet_patches and len(unet_patches) > 0

    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    # Pre-compute per-class indices once (avoid N*n_classes scans in the loop)
    precomputed_cls_indices = [np.where(labels == cls)[0] for cls in range(n_classes)]

    MAX_TEST = 200_000  # subsample test set for speed (negligible accuracy loss)

    import time as _time
    logger.info("Learning curve: %d pixels, %d classes, %d classifiers, pcts=%s",
                n_samples, n_classes, len(classifier_names), training_pcts)

    for pct_idx, pct in enumerate(training_pcts):
        pct_t0 = _time.time()
        active = [n for n in classifier_names if n not in finish_classifiers]
        active_pixel = [n for n in active if n != 'unet']
        f1_scores = {name: [] for name in active}
        f1w_scores = {name: [] for name in active}
        is_largest = (pct == training_pcts[-1])

        # Number of pixels for this percentage
        size = max(1, int(n_samples * pct / 100.0))

        # Adaptive repeats: fewer at high percentages where variance is low
        if pct >= 50:
            n_repeats = max(1, repeats - 3)
        elif pct >= 20:
            n_repeats = max(2, repeats - 2)
        else:
            n_repeats = repeats

        for seed in range(n_repeats):
            rng = np.random.RandomState(seed)

            # Stratified pixel sampling (using pre-computed indices)
            per_class = max(1, size // n_classes)
            train_idx = []
            for cls in range(n_classes):
                cls_indices = precomputed_cls_indices[cls]
                if len(cls_indices) == 0:
                    continue
                n_take = min(per_class, int(0.8 * len(cls_indices)))
                n_take = max(1, n_take)
                chosen = rng.choice(cls_indices, size=n_take, replace=False)
                train_idx.extend(chosen)
            train_idx = np.array(train_idx)

            # Boolean mask for test set (O(N) instead of O(N log N) setdiff1d)
            test_mask = np.ones(n_samples, dtype=bool)
            test_mask[train_idx] = False
            test_idx = np.where(test_mask)[0]

            if len(test_idx) == 0:
                continue

            # Subsample test set if too large (saves huge time on KNN/predict)
            if len(test_idx) > MAX_TEST:
                test_idx = rng.choice(test_idx, size=MAX_TEST, replace=False)

            X_train, y_train = vectors[train_idx], labels[train_idx]
            X_test, y_test = vectors[test_idx], labels[test_idx]

            # Pixel-based classifiers
            for clf_idx, name in enumerate(active_pixel):
                if seed == 0:
                    yield {"type": "classifier_status",
                           "message": f"Pct {pct}%: training {name} (repeat {seed+1}/{n_repeats})..."}
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
                except Exception as exc:
                    logger.warning("Classifier %s failed at pct %.1f seed %d: %s", name, pct, seed, exc)
                    f1_scores[name].append(0.0)
                    f1w_scores[name].append(0.0)

            # U-Net: patch-based train/test split
            # Only run 1 repeat for U-Net (training is expensive, variance is dominated by SGD noise)
            if has_unet and 'unet' in active and seed == 0:
                yield {"type": "classifier_status",
                       "message": f"Pct {pct}%: training U-Net..."}
                try:
                    from tessera_eval.unet import train_unet_on_patches, predict_unet_tile, _HAS_TORCH
                    if _HAS_TORCH:
                        n_patches = len(unet_patches)
                        n_train = max(1, int(n_patches * pct / 100.0))
                        n_train = min(n_train, n_patches - 1)  # keep at least 1 for test
                        n_train = min(n_train, 20)  # cap training patches for speed
                        patch_idx = rng.permutation(n_patches)
                        train_patches = [unet_patches[i] for i in patch_idx[:n_train]]
                        test_patches = [unet_patches[i] for i in patch_idx[n_train:n_train + 10]]  # cap test too

                        if train_patches and test_patches:
                            # Use fewer epochs for learning curve (full epochs only for final model)
                            unet_params = dict((classifier_params or {}).get('unet', {}))
                            unet_params.setdefault('epochs', 15)
                            model = train_unet_on_patches(
                                train_patches, n_classes, unet_params)

                            # Evaluate on test patches
                            all_true, all_pred = [], []
                            for emb_patch, lbl_patch in test_patches:
                                pred = predict_unet_tile(model, emb_patch,
                                                         patch_size=emb_patch.shape[0])
                                mask = lbl_patch > 0
                                if mask.any():
                                    all_true.append(lbl_patch[mask] - 1)  # 1-based → 0-based
                                    all_pred.append(pred[mask] - 1)

                            if all_true:
                                y_true = np.concatenate(all_true)
                                y_pred = np.concatenate(all_pred)
                                f1 = f1_score(y_true, y_pred, average="macro", zero_division=0)
                                f1w = f1_score(y_true, y_pred, average="weighted", zero_division=0)
                                f1_scores['unet'].append(float(f1))
                                f1w_scores['unet'].append(float(f1w))
                                if is_largest:
                                    cm = confusion_matrix(y_true, y_pred, labels=np.arange(n_classes))
                                    cm_accum['unet'] += cm
                            else:
                                f1_scores['unet'].append(0.0)
                                f1w_scores['unet'].append(0.0)
                        else:
                            f1_scores['unet'].append(0.0)
                            f1w_scores['unet'].append(0.0)
                except Exception as exc:
                    logger.warning("U-Net failed at pct %.1f seed %d: %s", pct, seed, exc)
                    f1_scores.setdefault('unet', []).append(0.0)
                    f1w_scores.setdefault('unet', []).append(0.0)

        pct_results = {}
        for name in active:
            scores = f1_scores[name]
            scoresw = f1w_scores[name]
            pct_results[name] = {
                "mean_f1": round(float(np.mean(scores)), 4) if scores else 0.0,
                "std_f1": round(float(np.std(scores)), 4) if scores else 0.0,
                "mean_f1w": round(float(np.mean(scoresw)), 4) if scoresw else 0.0,
                "std_f1w": round(float(np.std(scoresw)), 4) if scoresw else 0.0,
            }

        pct_elapsed = _time.time() - pct_t0
        f1_summary = ", ".join(f"{n}={pct_results[n]['mean_f1']:.3f}" for n in active if n in pct_results)
        logger.info("Pct %d/%d (%.0f%%) done in %.1fs — %s",
                     pct_idx + 1, len(training_pcts), pct, pct_elapsed, f1_summary)
        yield {"type": "progress", "pct": pct, "classifiers": pct_results}

    confusion_matrices = {}
    for name in classifier_names:
        if cm_accum[name].any():
            confusion_matrices[name] = cm_accum[name].tolist()
    if confusion_matrices:
        yield {"type": "confusion_matrices", "confusion_matrices": confusion_matrices}


def evaluate(vectors, labels, classifiers=None, training_sizes=None,
             max_train=10000, repeats=5, classifier_params=None,
             spatial_vectors=None, spatial_vectors_5x5=None):
    """Run evaluation and collect all results (non-streaming).

    Convenience wrapper around run_learning_curve that collects all events
    and returns a Results object.

    Args:
        vectors: float32 array, shape (N, dim)
        labels: int array, shape (N,)
        classifiers: list of classifier names (default: ['nn', 'rf', 'mlp'])
        training_sizes: list of ints (default: log-spaced up to max_train)
        max_train: Maximum training size (default 10000)
        repeats: Number of random repeats (default 5)
        classifier_params: Optional hyperparameter overrides
        spatial_vectors: Optional 3x3 spatial features
        spatial_vectors_5x5: Optional 5x5 spatial features

    Returns:
        Results object with .summary(), .confusion_matrices, .training_sizes, etc.
    """
    if classifiers is None:
        classifiers = ["nn", "rf", "mlp"]

    if training_sizes is None:
        all_sizes = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000]
        training_sizes = [s for s in all_sizes if s <= max_train]
        if not training_sizes or training_sizes[-1] < max_train:
            training_sizes.append(max_train)

    progress_events = []
    confusion_matrices = {}

    for event in run_learning_curve(
        vectors, labels, classifiers, training_sizes, repeats,
        classifier_params, spatial_vectors, spatial_vectors_5x5
    ):
        if event["type"] == "progress":
            progress_events.append(event)
        elif event["type"] == "confusion_matrices":
            confusion_matrices = event["confusion_matrices"]

    return Results(classifiers, training_sizes, progress_events, confusion_matrices)


class Results:
    """Container for evaluation results."""

    def __init__(self, classifiers, training_sizes, progress_events, confusion_matrices):
        self.classifiers = classifiers
        self.training_sizes = training_sizes
        self.progress = progress_events
        self.confusion_matrices = confusion_matrices

    def summary(self):
        """Return a formatted summary string."""
        lines = [f"{'Size':>8}  " + "  ".join(f"{n:>12}" for n in self.classifiers)]
        lines.append("-" * len(lines[0]))
        for event in self.progress:
            cols = [f"{event['size']:>8}"]
            for name in self.classifiers:
                s = event["classifiers"].get(name, {})
                cols.append(f"  {s.get('mean_f1', 0):.3f} ± {s.get('std_f1', 0):.3f}")
            lines.append("".join(cols))
        return "\n".join(lines)

    def to_dict(self):
        """Serialize to a JSON-safe dict."""
        return {
            "classifiers": self.classifiers,
            "training_sizes": self.training_sizes,
            "progress": self.progress,
            "confusion_matrices": self.confusion_matrices,
        }


def regression_metrics(y_true, y_pred):
    """Compute regression metrics.

    Args:
        y_true: array of true values
        y_pred: array of predicted values

    Returns:
        dict with r2, rmse, mae
    """
    return {
        "r2": round(float(r2_score(y_true, y_pred)), 4),
        "rmse": round(float(np.sqrt(mean_squared_error(y_true, y_pred))), 4),
        "mae": round(float(mean_absolute_error(y_true, y_pred)), 4),
    }


def run_kfold_cv(vectors, labels, model_names, k=5, task="classification",
                 model_params=None, max_training_samples=None, seed=42):
    """Generator that yields per-fold and aggregate results for k-fold CV.

    Supports both classification and regression tasks. For classification,
    uses StratifiedKFold and computes F1 scores + confusion matrices.
    For regression, uses KFold and computes R², RMSE, MAE.

    Args:
        vectors: float32 array, shape (N, dim)
        labels: array, shape (N,) — class labels (int) or regression targets (float)
        model_names: list of model names (classifier or regressor names)
        k: Number of folds (default 5)
        task: "classification" or "regression"
        model_params: Optional dict of {model_name: {param: value}}
        max_training_samples: Optional cap on training set size per fold
        seed: Random seed for reproducibility

    Yields:
        dict events:
        - {"type": "fold_result", "fold": int, "models": {name: metrics_dict}}
        - {"type": "aggregate", "models": {name: aggregate_metrics_dict}}
        - {"type": "confusion_matrices", "confusion_matrices": {name: [[int]]}}
          (classification only)
    """
    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    n_samples = len(labels)
    is_classification = (task == "classification")

    if is_classification:
        splitter = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
        n_classes = len(np.unique(labels))
        cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64)
                    for name in model_names}
    else:
        splitter = KFold(n_splits=k, shuffle=True, random_state=seed)

    # Collect per-fold metrics for aggregation
    all_fold_metrics = {name: [] for name in model_names}

    for fold_idx, (train_idx, test_idx) in enumerate(splitter.split(vectors, labels)):
        if max_training_samples and len(train_idx) > max_training_samples:
            rng = np.random.RandomState(seed + fold_idx)
            train_idx = rng.choice(train_idx, size=max_training_samples, replace=False)

        X_train, y_train = vectors[train_idx], labels[train_idx]
        X_test, y_test = vectors[test_idx], labels[test_idx]

        fold_results = {}
        for name in model_names:
            try:
                if is_classification:
                    model = make_classifier(name, (model_params or {}).get(name, {}))
                else:
                    model = make_regressor(name, (model_params or {}).get(name, {}))

                model.fit(X_train, y_train)
                y_pred = model.predict(X_test)

                if is_classification:
                    metrics = {
                        "mean_f1": round(float(f1_score(
                            y_test, y_pred, average="macro", zero_division=0)), 4),
                        "mean_f1w": round(float(f1_score(
                            y_test, y_pred, average="weighted", zero_division=0)), 4),
                    }
                    cm = confusion_matrix(y_test, y_pred, labels=np.arange(n_classes))
                    cm_accum[name] += cm
                else:
                    metrics = regression_metrics(y_test, y_pred)
            except Exception as exc:
                logger.warning("Model %s failed on fold %d: %s", name, fold_idx + 1, exc)
                if is_classification:
                    metrics = {"mean_f1": 0.0, "mean_f1w": 0.0}
                else:
                    metrics = {"r2": 0.0, "rmse": 0.0, "mae": 0.0}

            fold_results[name] = metrics
            all_fold_metrics[name].append(metrics)

        yield {"type": "fold_result", "fold": fold_idx + 1, "models": fold_results}

    # Aggregate across folds
    aggregate = {}
    for name in model_names:
        folds = all_fold_metrics[name]
        if is_classification:
            f1s = [f["mean_f1"] for f in folds]
            f1ws = [f["mean_f1w"] for f in folds]
            aggregate[name] = {
                "mean_f1": round(float(np.mean(f1s)), 4),
                "std_f1": round(float(np.std(f1s)), 4),
                "mean_f1w": round(float(np.mean(f1ws)), 4),
                "std_f1w": round(float(np.std(f1ws)), 4),
            }
        else:
            r2s = [f["r2"] for f in folds]
            rmses = [f["rmse"] for f in folds]
            maes = [f["mae"] for f in folds]
            aggregate[name] = {
                "mean_r2": round(float(np.mean(r2s)), 4),
                "std_r2": round(float(np.std(r2s)), 4),
                "mean_rmse": round(float(np.mean(rmses)), 4),
                "std_rmse": round(float(np.std(rmses)), 4),
                "mean_mae": round(float(np.mean(maes)), 4),
                "std_mae": round(float(np.std(maes)), 4),
            }

    yield {"type": "aggregate", "models": aggregate}

    # Confusion matrices for classification
    if is_classification:
        confusion_matrices = {}
        for name in model_names:
            if cm_accum[name].any():
                confusion_matrices[name] = cm_accum[name].tolist()
        if confusion_matrices:
            yield {"type": "confusion_matrices", "confusion_matrices": confusion_matrices}


def detect_field_type(gdf, field_name, threshold=20):
    """Detect whether a field is classification or regression.

    Classification: non-numeric, or numeric with <= threshold unique values.
    Regression: numeric with > threshold unique values.
    """
    col = gdf[field_name].dropna()
    if col.dtype.kind in ("f", "i", "u"):  # float, int, unsigned int
        if col.nunique() > threshold:
            return "regression"
    return "classification"
