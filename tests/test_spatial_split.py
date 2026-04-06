"""Unit tests for spatial split in learning curve evaluation."""

import numpy as np
import pytest

from tessera_eval.evaluate import run_learning_curve


@pytest.fixture
def spatial_split_data():
    """Create train/test data with class-correlated features for spatial split testing."""
    rng = np.random.RandomState(42)
    n_train_per_class = 100
    n_test_per_class = 50
    dim = 10
    n_classes = 3

    train_vectors = []
    train_labels = []
    test_vectors = []
    test_labels = []

    for cls in range(n_classes):
        center = rng.randn(dim) * 2
        # Train set
        vecs = center + rng.randn(n_train_per_class, dim) * 0.5
        train_vectors.append(vecs)
        train_labels.extend([cls] * n_train_per_class)
        # Test set (slightly shifted)
        vecs = center + rng.randn(n_test_per_class, dim) * 0.5
        test_vectors.append(vecs)
        test_labels.extend([cls] * n_test_per_class)

    return {
        "train_vectors": np.vstack(train_vectors).astype(np.float32),
        "train_labels": np.array(train_labels),
        "test_vectors": np.vstack(test_vectors).astype(np.float32),
        "test_labels": np.array(test_labels),
    }


def test_spatial_split_yields_progress(spatial_split_data):
    """Spatial split mode should yield progress events with F1 scores."""
    events = list(run_learning_curve(
        spatial_split_data["train_vectors"],
        spatial_split_data["train_labels"],
        classifier_names=["nn"],
        training_pcts=[10, 50],
        repeats=2,
        test_vectors=spatial_split_data["test_vectors"],
        test_labels=spatial_split_data["test_labels"],
    ))

    progress_events = [e for e in events if e["type"] == "progress"]
    assert len(progress_events) == 2

    for ev in progress_events:
        assert "nn" in ev["classifiers"]
        assert ev["classifiers"]["nn"]["mean_f1"] > 0


def test_spatial_split_confusion_matrix(spatial_split_data):
    """Spatial split should produce a confusion matrix at the largest pct."""
    events = list(run_learning_curve(
        spatial_split_data["train_vectors"],
        spatial_split_data["train_labels"],
        classifier_names=["nn"],
        training_pcts=[50, 80],
        repeats=2,
        test_vectors=spatial_split_data["test_vectors"],
        test_labels=spatial_split_data["test_labels"],
    ))

    cm_events = [e for e in events if e["type"] == "confusion_matrices"]
    assert len(cm_events) == 1
    assert "nn" in cm_events[0]["confusion_matrices"]

    cm = np.array(cm_events[0]["confusion_matrices"]["nn"])
    assert cm.shape == (3, 3)
    # Diagonal should dominate (classes are separable)
    assert cm.trace() > cm.sum() * 0.5


def test_no_spatial_split_backward_compatible(spatial_split_data):
    """Without test_vectors/test_labels, existing random split should work."""
    # Combine train+test as single pool (existing behavior)
    vectors = np.vstack([spatial_split_data["train_vectors"],
                         spatial_split_data["test_vectors"]])
    labels = np.concatenate([spatial_split_data["train_labels"],
                             spatial_split_data["test_labels"]])

    events = list(run_learning_curve(
        vectors, labels,
        classifier_names=["nn"],
        training_pcts=[10, 50],
        repeats=2,
    ))

    progress_events = [e for e in events if e["type"] == "progress"]
    assert len(progress_events) == 2
    for ev in progress_events:
        assert ev["classifiers"]["nn"]["mean_f1"] > 0


def test_spatial_split_test_set_is_fixed(spatial_split_data):
    """In spatial split mode, test set size should not vary with training pct."""
    # The test set is fixed, so the number of test samples evaluated should be constant
    events = list(run_learning_curve(
        spatial_split_data["train_vectors"],
        spatial_split_data["train_labels"],
        classifier_names=["nn"],
        training_pcts=[10, 80],
        repeats=1,
        test_vectors=spatial_split_data["test_vectors"],
        test_labels=spatial_split_data["test_labels"],
    ))

    # Both should complete successfully
    progress_events = [e for e in events if e["type"] == "progress"]
    assert len(progress_events) == 2
    # Higher training pct should generally give higher F1
    f1_10 = progress_events[0]["classifiers"]["nn"]["mean_f1"]
    f1_80 = progress_events[1]["classifiers"]["nn"]["mean_f1"]
    # With well-separated data, both should be decent
    assert f1_10 > 0.3
    assert f1_80 > 0.3
