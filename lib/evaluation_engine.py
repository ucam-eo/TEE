"""Shim: delegates to tessera_eval library.

TEE's evaluation logic now lives in the tessera-eval package.
This file preserves the original import interface so api/views/evaluation.py
doesn't need to change.

Install the library: pip install -e /path/to/tessera-eval
"""

from lib.config import VECTORS_DIR

# Re-export everything TEE uses
from tessera_eval.data import dequantize_uint8 as dequantize
from tessera_eval.rasterize import rasterize_shapefile
from tessera_eval.classify import (
    make_classifier,
    gather_spatial_features,
    augment_spatial,
)
from tessera_eval.evaluate import run_learning_curve, run_kfold_cv, regression_metrics, detect_field_type
from tessera_eval.classify import make_regressor, available_regressors
from tessera_eval.data import load_embeddings_for_shapefile


def load_vectors(viewport, year):
    """Load vectors for a viewport/year (TEE path convention).

    This wraps tessera_eval.data.load_tee_vectors with TEE's VECTORS_DIR.
    """
    from tessera_eval.data import load_tee_vectors
    vector_dir = VECTORS_DIR / viewport / str(year)
    return load_tee_vectors(str(vector_dir))
