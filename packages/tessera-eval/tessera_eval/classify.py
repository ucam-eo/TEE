"""Classifier and regressor factory, plus spatial feature extraction."""

import re

import numpy as np
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.neighbors import KNeighborsClassifier, KNeighborsRegressor
from sklearn.neural_network import MLPClassifier, MLPRegressor


def _strip_variant_suffix(name):
    """Strip hyperparameter variant suffix (e.g., 'mlp_v2' -> 'mlp').

    Variant names are created by the server when a classifier has multiple
    parameter sets. The base name is used for classifier lookup.
    """
    return re.sub(r'_v\d+$', '', name)


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
              'spatial_mlp', 'spatial_mlp_5x5'.
              May include a variant suffix (e.g., 'mlp_v2') which is
              stripped before lookup.
        params: Optional dict of hyperparameters

    Returns:
        scikit-learn compatible classifier (fit/predict interface)

    Raises:
        ValueError: If name is unknown
        ImportError: If xgboost is requested but not installed
    """
    base_name = _strip_variant_suffix(name)
    p = params or {}
    if base_name == "nn":
        return KNeighborsClassifier(
            n_neighbors=int(p.get("n_neighbors", 5)),
            weights=p.get("weights", "uniform"),
            metric="euclidean",
        )
    elif base_name == "rf":
        max_depth = p.get("max_depth")
        if max_depth is not None:
            max_depth = int(max_depth)
        return RandomForestClassifier(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=max_depth,
            n_jobs=-1, random_state=42,
        )
    elif base_name == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=int(p.get("max_depth", 6)),
            learning_rate=float(p.get("learning_rate", 0.3)),
            n_jobs=-1, random_state=42,
            use_label_encoder=False, eval_metric="mlogloss", verbosity=0,
        )
    elif base_name == "mlp":
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
    elif base_name in ("spatial_mlp", "spatial_mlp_5x5"):
        default_layers = "256,128" if base_name == "spatial_mlp" else "512,256"
        default_iter = 300 if base_name == "spatial_mlp" else 400
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


def gather_spatial_features_2d(tile_emb, radius=1, mask=None):
    """Extract spatial neighbourhood features from a contiguous 2D tile.

    For each pixel, concatenates the embeddings of all pixels in a
    (2*radius+1) x (2*radius+1) window centered on it. Edge pixels
    are zero-padded.

    Args:
        tile_emb: float32 array, shape (H, W, dim) — contiguous tile embeddings
        radius: neighbourhood radius (1 for 3x3, 2 for 5x5)
        mask: optional bool array, shape (H, W) — if provided, only extract
            features for True pixels (much faster for sparse labels)

    Returns:
        If mask is None: float32 array, shape (H, W, window*window*dim)
        If mask is provided: float32 array, shape (N, window*window*dim)
            where N = mask.sum()
    """
    H, W, dim = tile_emb.shape
    window = 2 * radius + 1
    padded = np.pad(tile_emb, ((radius, radius), (radius, radius), (0, 0)))
    windows = np.lib.stride_tricks.sliding_window_view(padded, (window, window, dim))
    # windows shape: (H, W, 1, window, window, dim)
    if mask is not None:
        # Only materialize features for masked pixels (avoids H*W*window²*dim allocation)
        # windows is a view — indexing with mask only copies selected pixels
        masked = windows[mask]  # shape: (N, 1, window, window, dim)
        return masked.reshape(masked.shape[0], window * window * dim).astype(np.float32)
    return windows.reshape(H, W, window * window * dim).astype(np.float32)


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


def available_regressors():
    """Return list of available regressor names."""
    names = ["nn_reg", "rf_reg", "mlp_reg"]
    try:
        import xgboost  # noqa: F401
        names.append("xgboost_reg")
    except ImportError:
        pass
    return names


def make_regressor(name, params=None):
    """Create a regressor instance by name with optional hyperparameters.

    Args:
        name: Regressor name — one of 'nn_reg', 'rf_reg', 'mlp_reg', 'xgboost_reg'.
              May include a variant suffix (e.g., 'rf_reg_v2') which is
              stripped before lookup.
        params: Optional dict of hyperparameters

    Returns:
        scikit-learn compatible regressor (fit/predict interface)

    Raises:
        ValueError: If name is unknown
        ImportError: If xgboost_reg is requested but not installed
    """
    base_name = _strip_variant_suffix(name)
    p = params or {}
    if base_name == "nn_reg":
        return KNeighborsRegressor(
            n_neighbors=int(p.get("n_neighbors", 5)),
            weights=p.get("weights", "uniform"),
            metric="euclidean",
        )
    elif base_name == "rf_reg":
        max_depth = p.get("max_depth")
        if max_depth is not None:
            max_depth = int(max_depth)
        return RandomForestRegressor(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=max_depth,
            n_jobs=-1, random_state=42,
        )
    elif base_name == "xgboost_reg":
        from xgboost import XGBRegressor
        return XGBRegressor(
            n_estimators=int(p.get("n_estimators", 100)),
            max_depth=int(p.get("max_depth", 6)),
            learning_rate=float(p.get("learning_rate", 0.3)),
            n_jobs=-1, random_state=42, verbosity=0,
        )
    elif base_name == "mlp_reg":
        layers_str = p.get("hidden_layers", "64,32")
        if isinstance(layers_str, str):
            hidden = tuple(int(x) for x in layers_str.split(","))
        else:
            hidden = (64, 32)
        return MLPRegressor(
            hidden_layer_sizes=hidden,
            max_iter=int(p.get("max_iter", 200)),
            random_state=42,
        )
    else:
        raise ValueError(f"Unknown regressor: {name}")
