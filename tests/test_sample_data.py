"""Bundled sample ground-truth shapefiles (/sample-data/<name>.zip).

austria.zip and gothenburg.zip live at the repo / image root and are served by
api.views.config.serve_sample_data. Only the listed names are served, and the
Docker image has to include them (.dockerignore excludes every other *.zip).
"""

import os
import struct
import zipfile
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tee_project.settings")
import django  # noqa: E402

django.setup()

import pytest  # noqa: E402
from django.http import Http404  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import resolve  # noqa: E402

from api.views import config  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SAMPLES = ["austria.zip", "gothenburg.zip"]


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_is_routed_and_served(name, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", REPO)
    match = resolve(f"/sample-data/{name}")
    assert match.func is config.serve_sample_data
    response = match.func(RequestFactory().get(f"/sample-data/{name}"), *match.args, **match.kwargs)
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert response["Content-Disposition"] == f'attachment; filename="{name}"'
    assert b"".join(response.streaming_content) == (REPO / name).read_bytes()


def test_only_listed_names_are_served(monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", REPO)
    assert resolve("/sample-data/other.zip").func is not config.serve_sample_data
    for name in ("other.zip", "../tee_project/settings.py", "manage.py"):
        with pytest.raises(Http404):
            config.serve_sample_data(RequestFactory().get("/"), name=name)


@pytest.mark.parametrize("name", SAMPLES)
def test_sample_is_in_the_docker_image(name):
    lines = (REPO / ".dockerignore").read_text().splitlines()
    assert f"!{name}" in lines


def _dbf_fields_and_records(dbf):
    """Field names and record count from a dBase III header (the .dbf of a shapefile)."""
    n_records = struct.unpack("<I", dbf[4:8])[0]
    fields, pos = [], 32
    while dbf[pos] != 0x0D:
        fields.append(dbf[pos:pos + 11].split(b"\0")[0].decode())
        pos += 32
    return fields, n_records


def test_gothenburg_zip_is_one_shapefile_layer_with_a_name_field():
    """The Validation tab reads every .shp in the zip and offers its columns as
    class fields; the user guide says to pick "name". Labels -> Import reads
    "name" and "color" as well."""
    with zipfile.ZipFile(REPO / "gothenburg.zip") as zf:
        names = zf.namelist()
        shp = [n for n in names if n.endswith(".shp")]
        assert len(shp) == 1
        stem = shp[0][:-len(".shp")]
        for ext in (".shx", ".dbf", ".prj", ".cpg"):
            assert stem + ext in names
        assert any(n.endswith("README.txt") for n in names)
        fields, n_records = _dbf_fields_and_records(zf.read(stem + ".dbf"))
        prj = zf.read(stem + ".prj").decode()
    assert fields == ["name", "class_id", "color"]
    assert n_records == 7483
    assert "WGS_1984" in prj or "WGS 84" in prj
