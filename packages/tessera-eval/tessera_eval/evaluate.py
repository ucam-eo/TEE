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


def run_learning_curve(vectors, labels, classifier_names, training_sizes,
                       repeats=5, classifier_params=None, spatial_vectors=None,
                       spatial_vectors_5x5=None, finish_classifiers=None,
                       **kwargs):
    """Generator that yields progress events after each training size.

    Runs stratified cross-validation at each training size, computing F1 scores
    (macro and weighted) with multiple random repeats. Yields confusion matrices
    at the largest training size.

    Args:
        vectors: float32 array, shape (N, dim) — labelled pixel embeddings
        labels: int array, shape (N,) — class labels (0-indexed)
        classifier_names: list of classifier names (e.g., ['nn', 'rf', 'mlp'])
        training_sizes: list of ints — training set sizes to evaluate
        repeats: Number of random repeats per size (default 5)
        classifier_params: Optional dict of {classifier_name: {param: value}}
        spatial_vectors: Optional float32 array for spatial_mlp (3x3 features)
        spatial_vectors_5x5: Optional float32 array for spatial_mlp_5x5 (5x5 features)
        finish_classifiers: Optional set of classifier names to skip
        **kwargs: Extra arguments (e.g. vector_grid, labelled_coords) accepted
            for compatibility with the web view but not used here.

    Yields:
        dict events:
        - {"type": "progress", "size": int, "classifiers": {name: {mean_f1, std_f1, mean_f1w, std_f1w}}}
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
    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    for size in training_sizes:
        active = [n for n in classifier_names if n not in finish_classifiers]
        f1_scores = {name: [] for name in active}
        f1w_scores = {name: [] for name in active}
        is_largest = (size == training_sizes[-1])

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
                    logger.warning("Classifier %s failed at size %d seed %d: %s", name, size, seed, exc)
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
