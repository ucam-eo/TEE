"""Integration test: upload cumbria_naddle.zip through Django proxy to tee-compute.

Requires both Django (:8001) and tee-compute (:8002) to be running.
Skip gracefully if either is down.
"""

import json
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
SHAPEFILE = ROOT / "cumbria_naddle.zip"
DJANGO_URL = "http://localhost:8001"
COMPUTE_URL = "http://localhost:8002"


def _server_up(url):
    try:
        r = requests.get(f"{url}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not SHAPEFILE.exists(),
    reason="cumbria_naddle.zip not found in project root",
)


@pytest.fixture(autouse=True)
def _require_servers():
    if not _server_up(DJANGO_URL):
        pytest.skip("Django not running on :8001")
    if not _server_up(COMPUTE_URL):
        pytest.skip("tee-compute not running on :8002")


@pytest.fixture(autouse=True)
def _clear_shapefiles():
    """Clear shapefiles before each test."""
    requests.post(f"{DJANGO_URL}/api/evaluation/clear-shapefiles")
    yield
    requests.post(f"{DJANGO_URL}/api/evaluation/clear-shapefiles")


class TestUploadCumbriaNaddle:
    def test_upload_returns_200(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        assert resp.status_code == 200, f"Upload failed: {resp.text[:200]}"

    def test_upload_returns_fields(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        assert "fields" in data
        assert len(data["fields"]) > 0

    def test_upload_has_habitat_field(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        field_names = [f["name"] for f in data["fields"]]
        assert "Habitat" in field_names or "HabUK" in field_names, (
            f"Expected Habitat or HabUK field, got: {field_names}"
        )

    def test_upload_has_non_null_counts(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        for field in data["fields"]:
            assert "non_null" in field, f"Field {field['name']} missing non_null count"
            assert "total" in field, f"Field {field['name']} missing total count"

    def test_upload_returns_geojson(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        assert "geojson" in data
        assert "features" in data["geojson"]
        assert len(data["geojson"]["features"]) > 0

    def test_upload_returns_641_features(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        n_features = len(data["geojson"]["features"])
        assert n_features == 641, f"Expected 641 features, got {n_features}"

    def test_upload_returns_files_list(self):
        with open(SHAPEFILE, "rb") as f:
            resp = requests.post(
                f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                files={"file": ("cumbria_naddle.zip", f, "application/zip")},
            )
        data = resp.json()
        assert "files" in data
        assert len(data["files"]) == 1
        assert "cumbria_naddle.zip" in data["files"][0]

    def test_no_duplicates_on_reupload(self):
        """Uploading twice via the clear+upload flow should not double features."""
        for _ in range(2):
            requests.post(f"{DJANGO_URL}/api/evaluation/clear-shapefiles")
            with open(SHAPEFILE, "rb") as f:
                resp = requests.post(
                    f"{DJANGO_URL}/api/evaluation/upload-shapefile",
                    files={"file": ("cumbria_naddle.zip", f, "application/zip")},
                )
        data = resp.json()
        n_features = len(data["geojson"]["features"])
        assert n_features == 641, f"Expected 641 (no duplicates), got {n_features}"
