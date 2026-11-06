"""GeoTIFF export of a year's embedding map (/tiles/<vp>/<year>/export.tif)."""

import json
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tee_project.settings")
import django  # noqa: E402

django.setup()

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import rasterio  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from PIL import Image  # noqa: E402

from api.views import tiles  # noqa: E402

GT = {"a": 0.0001, "b": 0.0, "c": 0.3, "d": 0.0, "e": -0.0001, "f": 52.6}


@pytest.fixture
def pyramids(tmp_path, monkeypatch):
    monkeypatch.setattr(tiles, "PYRAMIDS_BASE_DIR", tmp_path)
    d = tmp_path / "demo" / "2024"
    d.mkdir(parents=True)
    rgb = np.zeros((20, 30, 3), dtype=np.uint8)
    rgb[..., 0] = np.arange(30)
    Image.fromarray(rgb, mode="RGB").save(d / "level_0.png")
    (d / "pyramid_meta.json").write_text(json.dumps({
        "crs": "EPSG:4326",
        "levels": [{"file": "level_0.png", "width": 30, "height": 20, "transform": GT}],
    }))
    return tmp_path


def _get(viewport, year):
    return tiles.export_geotiff(RequestFactory().get("/"), viewport, year)


def test_export_is_georeferenced_full_resolution(pyramids, tmp_path):
    resp = _get("demo", "2024")
    assert resp.status_code == 200
    assert 'filename="demo_2024_embeddings.tif"' in resp["Content-Disposition"]
    out = tmp_path / "out.tif"
    out.write_bytes(resp.content)
    with rasterio.open(out) as ds:
        assert (ds.width, ds.height, ds.count) == (30, 20, 3)
        assert ds.crs.to_epsg() == 4326
        assert ds.transform.c == pytest.approx(0.3) and ds.transform.f == pytest.approx(52.6)
        assert ds.read(1)[0, 29] == 29


def test_missing_year_is_404(pyramids):
    assert _get("demo", "2019").status_code == 404


def test_bad_inputs_rejected(pyramids):
    assert _get("demo", "notayear").status_code == 400
    assert _get("../etc", "2024").status_code == 400
