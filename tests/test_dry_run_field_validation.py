"""Test dry-run with nonexistent field name (Fix 7)."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from tee_evaluate import run_dry_run


@pytest.fixture
def gdf():
    return gpd.GeoDataFrame({
        "geometry": [box(0, 0, 1, 1)] * 5,
        "habitat": ["woodland"] * 5,
    }, crs="EPSG:4326")


@pytest.fixture
def config_with_bad_field(tmp_path, gdf):
    shp_path = tmp_path / "test.shp"
    gdf.to_file(shp_path)
    return {
        "$schema": "tee_evaluate_config_v1",
        "shapefile": str(shp_path),
        "fields": [{"name": "nonexistent_field", "type": "auto"}],
        "classifiers": {"nn": {}, "rf": {}},
        "years": [2024],
        "kfold": 5,
    }


@patch("geotessera.GeoTessera")
def test_dry_run_bad_field_emits_error(MockGT, config_with_bad_field, gdf):
    mock_gt = MockGT.return_value
    mock_gt.registry.load_blocks_for_region.return_value = [(2024, 0.5, 0.5)]

    out = StringIO()
    run_dry_run(config_with_bad_field, gdf, out=out)

    events = [json.loads(line) for line in out.getvalue().strip().split("\n")]
    assert len(events) == 1
    assert events[0]["event"] == "error"
    assert "nonexistent_field" in events[0]["message"]
