"""Tests for api/views/postcard.py's bbox/origin metadata (the fix for postcard
images landing at the wrong spot -- see requirements.txt's tessera-vq pin and
tessera-vq's QuantizedStructure.origin, propagated end-to-end starting v0.5.7)
and its dedicated small VQ tile size (POSTCARD_TILE_PX -- see that constant's
comment in postcard.py for the truncation bug it fixes).

Doesn't spin up a full Django app (no DB, no INSTALLED_APPS): generate_postcard
only touches django.conf.settings for TESSERA_VQ_DEFAULTS/URL/TIMEOUT, which
settings.configure() below supplies directly, and django.http.{HttpResponse,
JsonResponse} don't need more than that to import or construct.
"""

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest
from django.conf import settings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not settings.configured:
    settings.configure(
        TESSERA_VQ_URL="http://test",
        TESSERA_VQ_TIMEOUT_SECONDS=300.0,
        TESSERA_VQ_DEFAULTS={"t": 512, "k": 20, "k2": 256, "m": "euclidean"},
    )

from tessera_vq.client import QuantizedStructure  # noqa: E402

from api.views import postcard  # noqa: E402


class _FakeRequest:
    """Minimal Django-request stand-in: only .method, .body, .META are read."""

    def __init__(self, lat, lon):
        self.method = "POST"
        self.body = json.dumps({"lat": lat, "lon": lon}).encode()
        self.META = {}


def _make_structure(bbox, origin, *, t=16, k1=4, full_h=32, full_w=32):
    """A minimal single-tile QuantizedStructure covering the whole (full_h, full_w)."""
    rng = np.random.default_rng(0)
    dim = 128
    codebooks1 = rng.standard_normal((1, k1, dim)).astype(np.float32)
    indices1 = rng.integers(0, k1, size=(1, t, t), dtype=np.uint8)
    positions = np.array([[0, 0]], dtype=np.int32)
    return QuantizedStructure(
        codebooks1=codebooks1,
        indices1=indices1,
        codebooks2=None,
        indices2=None,
        positions=positions,
        tile_size=t,
        k1=k1,
        k2=None,
        metric="euclidean",
        mosaic_shape=(full_h, full_w),
        bbox=bbox,
        year=2024,
        origin=origin,
    )


def _unpack_header(body_bytes):
    """Mirror postcard.html's unpackVqBundle header decode, Python-side."""
    header_len = struct.unpack(">I", body_bytes[:4])[0]
    return json.loads(body_bytes[4 : 4 + header_len])


def _generate(monkeypatch, *, lat, lon, origin):
    """Call generate_postcard with build_vq_client_from_config stubbed to return
    a structure whose bbox is postcard.py's own _bbox_from_center(lat, lon) and
    whose origin is the given value. Returns (meta, bbox, vq_config) where
    vq_config is whatever dict postcard.py actually passed to
    build_vq_client_from_config -- lets tests assert on the tile size used,
    not just the response."""
    bbox = postcard._bbox_from_center(lat, lon)
    struct_ = _make_structure(bbox, origin)
    captured_config = {}

    class _FakeClient:
        def fetch_quantized_structure(self, bbox, year):  # noqa: ARG002
            return struct_

    def _fake_build(vq_config):
        captured_config.update(vq_config)
        return _FakeClient()

    monkeypatch.setattr("api.embeddings_provider.build_vq_client_from_config", _fake_build)
    # Rate limiter is in-process global state keyed by IP; give each test its
    # own IP so tests don't interfere with each other's 5-requests/10min cap.
    request = _FakeRequest(lat, lon)
    request.META["REMOTE_ADDR"] = f"test-{lat}-{lon}-{origin}"

    resp = postcard.generate_postcard(request)
    assert resp.status_code == 200
    return _unpack_header(resp.content), bbox, captured_config


def test_generate_postcard_ships_bbox_and_origin_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """meta.bbox matches the requested bbox; meta.origin round-trips qs.origin."""
    origin = (-0.014, 50.081, 0.00009, -0.00009)
    meta, bbox, _config = _generate(monkeypatch, lat=52.2099, lon=0.1823, origin=origin)
    assert meta["bbox"] == pytest.approx(list(bbox))
    assert meta["origin"] == pytest.approx(list(origin))


def test_generate_postcard_ships_null_origin_for_pre_057_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structure with no origin (pre-0.5.7 bolt-on) ships meta.origin = null,
    so the browser's cropOffsetFromOrigin is skipped in favour of the old
    center-crop fallback -- not a crash, not a fabricated offset."""
    meta, bbox, _config = _generate(monkeypatch, lat=52.2099, lon=0.1823, origin=None)
    assert meta["bbox"] == pytest.approx(list(bbox))
    assert meta["origin"] is None


def test_generate_postcard_requests_its_own_small_tile_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """postcard.py must request t=POSTCARD_TILE_PX, NOT the site-wide
    TESSERA_VQ_DEFAULTS['t'] (512 in this test's settings, matching production).

    Regression guard for the bug where inheriting t=512 let tile-size
    truncation throw away nearly half of a 6km-tall mosaic (real bboxes
    measured full_h=941-978px, truncating to a single 512px tile), forcing
    postcard.html's crop offset to clamp to 0 and anchoring the image several
    km from the requested location. t must stay small enough that this
    doesn't recur; k/k2/m should still come from the site default.
    """
    _meta, _bbox, config = _generate(monkeypatch, lat=52.2099, lon=0.1823, origin=None)
    assert config["t"] == postcard.POSTCARD_TILE_PX
    assert config["t"] < settings.TESSERA_VQ_DEFAULTS["t"]
    # k/k2/m still come from the site default -- only t is postcard-specific.
    assert config["k"] == settings.TESSERA_VQ_DEFAULTS["k"]
    assert config["k2"] == settings.TESSERA_VQ_DEFAULTS["k2"]
    assert config["m"] == settings.TESSERA_VQ_DEFAULTS["m"]


def test_postcard_tile_size_clears_crop_target_for_measured_worst_case() -> None:
    """POSTCARD_TILE_PX must keep out_h/out_w >= the crop target even for the
    smallest full_h/full_w actually observed against the live bolt-on for a
    real 6km-tall postcard bbox (941px, near Cambridge) -- see
    POSTCARD_TILE_PX's comment for how this was measured. A regression here
    means the truncation bug (see the test above) is back for real requests,
    not just in principle.
    """
    measured_worst_full_h = 941
    measured_worst_full_w = 1909
    t = postcard.POSTCARD_TILE_PX
    out_h = (measured_worst_full_h // t) * t
    out_w = (measured_worst_full_w // t) * t
    assert out_h >= postcard.POSTCARD_HEIGHT_PX
    assert out_w >= postcard.POSTCARD_WIDTH_PX
