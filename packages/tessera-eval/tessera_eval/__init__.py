"""tessera-eval: Evaluate habitat classifiers on Tessera satellite embeddings."""

# Must be set before numpy/scipy import to avoid OpenBLAS crash on >128-core machines.
# Use 1 thread per BLAS call — joblib handles higher-level parallelism in sklearn.
import os as _os
for _var in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
    if _var not in _os.environ:
        _os.environ[_var] = '1'

__version__ = "1.0.0"

from tessera_eval.data import load_tee_vectors, dequantize_uint8, dequantize_int8, load_embeddings_for_shapefile
from tessera_eval.rasterize import rasterize_shapefile
from tessera_eval.classify import (
    make_classifier, available_classifiers, gather_spatial_features,
    gather_spatial_features_2d, make_regressor, available_regressors,
)
from tessera_eval.evaluate import run_learning_curve, evaluate, run_kfold_cv, regression_metrics, detect_field_type
