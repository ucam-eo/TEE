"""Classifier factory and spatial feature extraction."""

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier


def available_classifiers():
    """Return list of available classifier names."""
    names = ["nn", "rf", "mlp", "spatial_mlp", "spatial_mlp_5x5"]
    try:
        import xgboost  # noqa: F401
        names.append("xgboost")
    except ImportError:
        pass
    return names


def make_classifier(name, params=None):
    """Create a classifier instance by name with optional hyperparameters.

    Args:
        name: Classifier name — one of 'nn', 'rf', 'xgboost', 'mlp',
              'spatial_mlp', 'spatial_mlp_5x5'
        params: Optional dict of hyperparameters

    Returns:
        scikit-learn compatible classifier (fit/predict interface)

    Raises:
        ValueError: If name is unknown
        ImportError: If xgboost is requested but not installed
    """
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
    elif name in ("spatial_mlp", "spatial_mlp_5x5"):
        default_layers = "256,128" if name == "spatial_mlp" else "512,256"
        default_iter = 300 if name == "spatial_mlp" else 400
        layers_str = p.get("hidden_layers", default_layers)
        if isinstance(layers_str, str):
            hidden = tuple(int(x) for x in layers_str.split(","))
        else:
            hidden = tuple(int(x) for x in default_layers.split(","))
        return MLPClassifier(
            hidden_layer_sizes=hidden,
            max_iter=int(p.get("max_iter", default_iter)),
            random_state=42,
        )
    else:
        raise ValueError(f"Unknown classifier: {name}")


def gather_spatial_features(vectors, coords, width, height, radius=1,
                            subset_mask=None):
    """Build (2r+1)^2 neighbourhood features from (N, dim) vectors on a regular grid.

    For each pixel, concatenates its own embedding with those of its
    neighbours in a (2*radius+1) x (2*radius+1) window. Missing neighbours
    are zero-filled.

    Args:
        vectors: float32 array, shape (N, dim)
        coords: int32 array, shape (N, 2) — pixel (x, y) coordinates
        width: Grid width in pixels
        height: Grid height in pixels
        radius: Neighbourhood radius (1 = 3x3, 2 = 5x5)
        subset_mask: Optional bool array, shape (N,) — only compute for
                     these pixels (saves memory)

    Returns:
        float32 array, shape (M, window*window*dim) where M = sum(subset_mask) or N
    """
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
    """4x data augmentation via horizontal/vertical flips of spatial patches.

    Args:
        X: float32 array, shape (N, window*window*dim)
        y: int array, shape (N,)
        window: Spatial window size (e.g., 3 or 5)
        dim: Embedding dimension (e.g., 128)

    Returns:
        Tuple of (X_aug, y_aug) with 4x the samples
    """
    n = len(X)
    patches = X.reshape(n, window, window, dim)
    augmented = [
        X,
        patches[:, :, ::-1, :].copy().reshape(n, -1),
        patches[:, ::-1, :, :].copy().reshape(n, -1),
        patches[:, ::-1, ::-1, :].copy().reshape(n, -1),
    ]
    return np.concatenate(augmented, axis=0), np.tile(y, 4)
