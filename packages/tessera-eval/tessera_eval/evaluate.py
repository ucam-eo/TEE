"""Learning curve evaluation with streaming results."""

import warnings

import numpy as np
from sklearn.metrics import confusion_matrix, f1_score
from sklearn.preprocessing import LabelEncoder

from tessera_eval.classify import make_classifier, augment_spatial


def run_learning_curve(vectors, labels, classifier_names, training_sizes,
                       repeats=5, classifier_params=None, spatial_vectors=None,
                       spatial_vectors_5x5=None):
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

    Yields:
        dict events:
        - {"type": "progress", "size": int, "classifiers": {name: {mean_f1, std_f1, mean_f1w, std_f1w}}}
        - {"type": "confusion_matrices", "confusion_matrices": {name: [[int]]}}
    """
    warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
    warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    n_samples = len(labels)
    n_classes = len(np.unique(labels))
    cm_accum = {name: np.zeros((n_classes, n_classes), dtype=np.int64) for name in classifier_names}

    for size in training_sizes:
        f1_scores = {name: [] for name in classifier_names}
        f1w_scores = {name: [] for name in classifier_names}
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

            for name in classifier_names:
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
                except Exception:
                    f1_scores[name].append(0.0)
                    f1w_scores[name].append(0.0)

        size_results = {}
        for name in classifier_names:
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
