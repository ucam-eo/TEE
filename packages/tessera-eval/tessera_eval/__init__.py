"""tessera-eval: Evaluate habitat classifiers on Tessera satellite embeddings."""

# Must be set before numpy/scipy import to avoid OpenBLAS crash on >128-core machines
import os as _os
if 'OPENBLAS_NUM_THREADS' not in _os.environ:
    _os.environ['OPENBLAS_NUM_THREADS'] = '64'

__version__ = "0.2.0"

from tessera_eval.data import load_tee_vectors, dequantize_uint8, dequantize_int8, load_embeddings_for_shapefile
from tessera_eval.rasterize import rasterize_shapefile
from tessera_eval.classify import (
    make_classifier, available_classifiers, gather_spatial_features,
    gather_spatial_features_2d, make_regressor, available_regressors,
)
from tessera_eval.evaluate import run_learning_curve, evaluate, run_kfold_cv, regression_metrics, detect_field_type
