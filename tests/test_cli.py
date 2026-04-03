"""Unit tests for the CLI script config validation and dry-run."""

import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

# Add scripts dir to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from tee_evaluate import validate_config, detect_field_type, run_dry_run


# ── Fixtures ──

@pytest.fixture
def tmp_shapefile(tmp_path):
    """Create a temporary shapefile with classification and regression fields."""
    gdf = gpd.GeoDataFrame({
        "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1), box(0, 1, 1, 2)] * 10,
        "habitat": ["woodland", "grassland", "wetland"] * 10,
        "carbon_tCO2": np.random.RandomState(42).uniform(0, 100, 30),
    }, crs="EPSG:4326")
    shp_path = tmp_path / "test.shp"
    gdf.to_file(shp_path)

    # Create a zip for the shapefile
    import zipfile
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for ext in [".shp", ".dbf", ".shx", ".prj"]:
            fpath = tmp_path / f"test{ext}"
            if fpath.exists():
                zf.write(fpath, f"test{ext}")

    return str(zip_path), gdf


@pytest.fixture
def valid_config(tmp_shapefile):
    shp_path, _ = tmp_shapefile
    return {
        "$schema": "tee_evaluate_config_v1",
        "shapefile": shp_path,
        "fields": [{"name": "habitat", "type": "auto"}],
        "classifiers": {"nn": {"n_neighbors": 5}, "rf": {}},
        "years": [2024],
        "kfold": 5,
        "output_dir": "./eval_output",
        "seed": 42,
    }


# ── TestConfigValidation ──

class TestConfigValidation:
    def test_valid_config_loads(self, valid_config):
        result = validate_config(valid_config)
        assert result is not None

    def test_missing_shapefile_raises(self, valid_config):
        valid_config["shapefile"] = "/nonexistent/path.zip"
        with pytest.raises(ValueError, match="Shapefile not found"):
            validate_config(valid_config)

    def test_missing_fields_raises(self, valid_config):
        valid_config["fields"] = []
        with pytest.raises(ValueError, match="at least one field"):
            validate_config(valid_config)

    def test_invalid_classifier_name_raises(self, valid_config):
        valid_config["classifiers"] = {"invalid_clf": {}}
        with pytest.raises(ValueError, match="Invalid classifier"):
            validate_config(valid_config)

    def test_spatial_classifier_rejected(self, valid_config):
        valid_config["classifiers"] = {"spatial_mlp": {}}
        with pytest.raises(ValueError, match="Invalid classifier"):
            validate_config(valid_config)

    def test_missing_schema_raises(self, valid_config):
        del valid_config["$schema"]
        with pytest.raises(ValueError, match="Missing.*schema"):
            validate_config(valid_config)


# ── TestAutoTypeDetection ──

class TestAutoTypeDetection:
    def test_classification_text_field(self, tmp_shapefile):
        _, gdf = tmp_shapefile
        assert detect_field_type(gdf, "habitat") == "classification"

    def test_regression_numeric_field(self, tmp_shapefile):
        _, gdf = tmp_shapefile
        assert detect_field_type(gdf, "carbon_tCO2") == "regression"

    def test_numeric_few_unique_is_classification(self):
        gdf = gpd.GeoDataFrame({
            "geometry": [box(0, 0, 1, 1)] * 10,
            "code": [1, 2, 3, 1, 2, 3, 1, 2, 3, 1],
        })
        assert detect_field_type(gdf, "code") == "classification"


# ── TestDryRun ──

class TestDryRun:
    @patch("geotessera.GeoTessera")
    def test_dry_run_outputs_stats(self, MockGT, valid_config, tmp_shapefile):
        _, gdf = tmp_shapefile
        mock_gt = MockGT.return_value
        mock_gt.registry.load_blocks_for_region.return_value = [
            (2024, 0.05, 0.05), (2024, 0.15, 0.05),
        ]

        out = StringIO()
        run_dry_run(valid_config, gdf, out=out)

        output = out.getvalue()
        events = [json.loads(line) for line in output.strip().split("\n")]
        assert len(events) >= 1
        assert events[0]["event"] == "dry_run"
        assert events[0]["tile_count"] == 2
        assert events[0]["field"] == "habitat"
        assert events[0]["field_type"] == "classification"

    @patch("geotessera.GeoTessera")
    def test_dry_run_no_download(self, MockGT, valid_config, tmp_shapefile):
        _, gdf = tmp_shapefile
        mock_gt = MockGT.return_value
        mock_gt.registry.load_blocks_for_region.return_value = []

        out = StringIO()
        run_dry_run(valid_config, gdf, out=out)

        # fetch_embeddings should never be called in dry run
        mock_gt.fetch_embeddings.assert_not_called()
