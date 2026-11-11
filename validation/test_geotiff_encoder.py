"""The browser GeoTIFF encoder (public/js/geotiff_export.js), read back with GDAL.

Covers the Labelling classification export (palette image via labels.js
classificationRaster) and the two Change Detection kinds (RGBA, float32).
"""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

rasterio = pytest.importorskip("rasterio")
ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@pytest.fixture(scope="module")
def tifs(tmp_path_factory):
    out = tmp_path_factory.mktemp("geotiff")
    res = subprocess.run(
        ["node", str(ROOT / "validation" / "geotiff_encode_fixture.mjs"), str(out)],
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    return out, json.loads(res.stdout)


def test_classification_palette(tifs):
    out, info = tifs
    assert info == {"classified": 8, "width": 4, "height": 3}
    with rasterio.open(out / "classification.tif") as ds:
        assert ds.count == 1 and ds.dtypes[0] == "uint8"
        assert ds.crs.to_epsg() == 4326
        assert ds.nodata == 0
        # origin = viewport origin + pixel offset (10, 20), same as the overlay
        t = ds.transform
        assert t.a == pytest.approx(0.0001) and t.e == pytest.approx(-0.0001)
        assert t.c == pytest.approx(16.7 + 10 * 0.0001)
        assert t.f == pytest.approx(48.2 - 20 * 0.0001)
        assert ds.colorinterp[0].name == "palette"
        cmap = ds.colormap(1)
        assert cmap[1][:3] == (255, 0, 0)
        assert cmap[2][:3] == (0, 160, 255)
        assert ds.tags()["class_2"] == "B #00a0ff"
        assert ds.read(1).tolist() == [[1, 1, 1, 1], [0, 0, 0, 0], [2, 2, 2, 2]]


def test_rgba(tifs):
    out, _ = tifs
    with rasterio.open(out / "rgba.tif") as ds:
        assert ds.count == 4 and ds.crs.to_epsg() == 4326
        a = ds.read()
        assert a[0, 0, 1] == 4 and a[1, 0, 0] == 1 and a[3].min() == 255


def test_float(tifs):
    out, _ = tifs
    with rasterio.open(out / "float.tif") as ds:
        assert ds.dtypes[0] == "float32" and math.isnan(ds.nodata)
        v = ds.read(1)
        assert v[0, 0] == 0.5 and math.isnan(v[0, 1]) and v[1, 2] == 4
