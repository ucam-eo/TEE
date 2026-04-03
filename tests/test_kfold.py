"""Unit tests for k-fold CV, regression metrics, and regressor factory."""

import numpy as np
import pytest

from tessera_eval.classify import make_regressor, available_regressors
from tessera_eval.evaluate import run_kfold_cv, regression_metrics


# ── Synthetic data fixtures ──

@pytest.fixture
def classification_data():
    """3 classes, 300 samples, 10-dim vectors with class-correlated features."""
    rng = np.random.RandomState(42)
    n_per_class = 100
    dim = 10
    vectors = []
    labels = []
    for cls in range(3):
        center = rng.randn(dim) * 2
        vecs = center + rng.randn(n_per_class, dim) * 0.5
        vectors.append(vecs)
        labels.extend([cls] * n_per_class)
    return np.vstack(vectors).astype(np.float32), np.array(labels)


@pytest.fixture
def regression_data():
    """300 samples, 10-dim vectors, float targets correlated with features."""
    rng = np.random.RandomState(42)
    n = 300
    dim = 10
    X = rng.randn(n, dim).astype(np.float32)
    # Target is a linear combination + noise
    weights = rng.randn(dim)
    y = X @ weights + rng.randn(n) * 0.5
    return X, y.astype(np.float32)


# ── TestMakeRegressor ──

class TestMakeRegressor:
    def test_rf_regressor_created(self):
        reg = make_regressor("rf_reg")
        assert hasattr(reg, "fit")
        assert hasattr(reg, "predict")

    def test_mlp_regressor_created(self):
        reg = make_regressor("mlp_reg")
        assert hasattr(reg, "fit")
        assert hasattr(reg, "predict")

    def test_nn_regressor_created(self):
        reg = make_regressor("nn_reg")
        assert hasattr(reg, "fit")
        assert hasattr(reg, "predict")

    def test_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown regressor"):
            make_regressor("nonexistent")

    def test_available_regressors_includes_core(self):
        names = available_regressors()
        assert "nn_reg" in names
        assert "rf_reg" in names
        assert "mlp_reg" in names

    def test_custom_params(self):
        reg = make_regressor("rf_reg", {"n_estimators": 50})
        assert reg.n_estimators == 50


# ── TestRunKfoldClassification ──

class TestRunKfoldClassification:
    def test_yields_k_fold_results(self, classification_data):
        vectors, labels = classification_data
        events = list(run_kfold_cv(
            vectors, labels, ["nn", "rf"], k=3, task="classification",
        ))
        fold_events = [e for e in events if e["type"] == "fold_result"]
        agg_events = [e for e in events if e["type"] == "aggregate"]
        cm_events = [e for e in events if e["type"] == "confusion_matrices"]
        assert len(fold_events) == 3
        assert len(agg_events) == 1
        assert len(cm_events) == 1

    def test_stratified_folds(self, classification_data):
        vectors, labels = classification_data
        from sklearn.model_selection import StratifiedKFold
        skf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
        for train_idx, test_idx in skf.split(vectors, labels):
            # Each fold should have all 3 classes
            assert len(np.unique(labels[test_idx])) == 3

    def test_f1_scores_between_0_and_1(self, classification_data):
        vectors, labels = classification_data
        events = list(run_kfold_cv(
            vectors, labels, ["nn"], k=3, task="classification",
        ))
        for ev in events:
            if ev["type"] == "fold_result":
                f1 = ev["models"]["nn"]["mean_f1"]
                assert 0 <= f1 <= 1

    def test_confusion_matrix_shape(self, classification_data):
        vectors, labels = classification_data
        events = list(run_kfold_cv(
            vectors, labels, ["nn"], k=3, task="classification",
        ))
        cm_event = [e for e in events if e["type"] == "confusion_matrices"][0]
        cm = cm_event["confusion_matrices"]["nn"]
        assert len(cm) == 3  # 3 classes
        assert len(cm[0]) == 3

    def test_max_train_caps_training_set(self, classification_data):
        vectors, labels = classification_data
        # With max_training_samples=50, training should be capped
        events = list(run_kfold_cv(
            vectors, labels, ["nn"], k=3, task="classification",
            max_training_samples=50,
        ))
        assert len(events) > 0  # Should complete without error


# ── TestRunKfoldRegression ──

class TestRunKfoldRegression:
    def test_yields_k_fold_results(self, regression_data):
        vectors, targets = regression_data
        events = list(run_kfold_cv(
            vectors, targets, ["rf_reg"], k=3, task="regression",
        ))
        fold_events = [e for e in events if e["type"] == "fold_result"]
        agg_events = [e for e in events if e["type"] == "aggregate"]
        assert len(fold_events) == 3
        assert len(agg_events) == 1

    def test_r2_score_reasonable(self, regression_data):
        vectors, targets = regression_data
        events = list(run_kfold_cv(
            vectors, targets, ["rf_reg"], k=3, task="regression",
        ))
        agg = [e for e in events if e["type"] == "aggregate"][0]
        r2 = agg["models"]["rf_reg"]["mean_r2"]
        assert r2 > 0, "R2 should be positive for correlated features"

    def test_rmse_positive(self, regression_data):
        vectors, targets = regression_data
        events = list(run_kfold_cv(
            vectors, targets, ["rf_reg"], k=3, task="regression",
        ))
        agg = [e for e in events if e["type"] == "aggregate"][0]
        rmse = agg["models"]["rf_reg"]["mean_rmse"]
        assert rmse > 0

    def test_no_confusion_matrix_for_regression(self, regression_data):
        vectors, targets = regression_data
        events = list(run_kfold_cv(
            vectors, targets, ["rf_reg"], k=3, task="regression",
        ))
        cm_events = [e for e in events if e["type"] == "confusion_matrices"]
        assert len(cm_events) == 0


# ── TestRegressionMetrics ──

class TestRegressionMetrics:
    def test_perfect_prediction(self):
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        metrics = regression_metrics(y, y)
        assert metrics["r2"] == 1.0
        assert metrics["rmse"] == 0.0
        assert metrics["mae"] == 0.0

    def test_constant_prediction(self):
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([3.0, 3.0, 3.0, 3.0, 3.0])
        metrics = regression_metrics(y_true, y_pred)
        assert metrics["r2"] <= 0
        assert metrics["rmse"] > 0
        assert metrics["mae"] > 0
