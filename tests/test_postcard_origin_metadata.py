"""Tests for api/views/postcard.py's bbox/origin metadata (the fix for postcard
images landing at the wrong spot -- see requirements.txt's tessera-vq pin and
tessera-vq's QuantizedStructure.origin, propagated end-to-end starting v0.5.7).

Doesn't spin up a full Django app (no DB, no INSTALLED_APPS): generate_postcard
only touches django.conf.settings inside get_embeddings_provider() (stubbed out
directly here, so settings never need configuring for these tests), and
django.http.{HttpResponse, JsonResponse} don't need more than that to import or
construct.
"""

import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    """Call generate_postcard with get_embeddings_provider stubbed to return a
    structure whose bbox is postcard.py's own _bbox_from_center(lat, lon) and
    whose origin is the given value -- returns the decoded meta dict."""
    bbox = postcard._bbox_from_center(lat, lon)
    struct_ = _make_structure(bbox, origin)

    class _FakeClient:
        def fetch_quantized_structure(self, bbox, year):  # noqa: ARG002
            return struct_

    monkeypatch.setattr(
        "api.embeddings_provider.get_embeddings_provider", lambda _name: _FakeClient()
    )
    # Rate limiter is in-process global state keyed by IP; give each test its
    # own IP so tests don't interfere with each other's 5-requests/10min cap.
    request = _FakeRequest(lat, lon)
    request.META["REMOTE_ADDR"] = f"test-{lat}-{lon}-{origin}"

    resp = postcard.generate_postcard(request)
    assert resp.status_code == 200
    return _unpack_header(resp.content), bbox


def test_generate_postcard_ships_bbox_and_origin_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """meta.bbox matches the requested bbox; meta.origin round-trips qs.origin."""
    origin = (-0.014, 50.081, 0.00009, -0.00009)
    meta, bbox = _generate(monkeypatch, lat=52.2099, lon=0.1823, origin=origin)
    assert meta["bbox"] == pytest.approx(list(bbox))
    assert meta["origin"] == pytest.approx(list(origin))


def test_generate_postcard_ships_null_origin_for_pre_057_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structure with no origin (pre-0.5.7 bolt-on) ships meta.origin = null,
    so the browser's cropRegionFromOrigin is skipped in favour of the old
    center-crop fallback -- not a crash, not a fabricated offset."""
    meta, bbox = _generate(monkeypatch, lat=52.2099, lon=0.1823, origin=None)
    assert meta["bbox"] == pytest.approx(list(bbox))
    assert meta["origin"] is None


def test_generate_postcard_uses_the_site_wide_embeddings_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """postcard.py no longer builds its own VQ client with a postcard-specific
    tile size -- it uses get_embeddings_provider(None), same as every other
    consumer (viewport creation, evaluation).

    postcard used to override t (see git history: POSTCARD_TILE_PX) to work
    around reconstruct_from_structure truncating the mosaic down to a tile_size
    multiple, which could leave too little room for a correctly-positioned
    crop. tessera-vq >=0.6.0 fixed that at the root (the mosaic is never
    truncated -- see tessera_vq.client.n_tiles_along/tile_pixel_offset), so
    the override was removed: it no longer helps alignment and only made
    per-tile codebook-boundary seams worse (more, smaller independently-fit
    tiles). This just confirms the plain site-wide path is what's called.
    """
    calls = []
    struct_ = _make_structure(postcard._bbox_from_center(52.2099, 0.1823), origin=None)

    class _FakeClient:
        def fetch_quantized_structure(self, bbox, year):  # noqa: ARG002
            return struct_

    def _fake_provider(viewport_name):
        calls.append(viewport_name)
        return _FakeClient()

    monkeypatch.setattr("api.embeddings_provider.get_embeddings_provider", _fake_provider)
    request = _FakeRequest(52.2099, 0.1823)
    request.META["REMOTE_ADDR"] = "test-site-wide-provider"
    resp = postcard.generate_postcard(request)
    assert resp.status_code == 200
    assert calls == [None]  # get_embeddings_provider(None), not a custom-built client
