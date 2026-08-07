"""Tests for api/views/postcard.py's per-IP rate limiter: _client_ip must
resolve to nginx's own trustworthy observation of the client, not a value
the client can freely choose -- otherwise "per-IP" is unenforceable.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tessera_vq.client import NoCoverageError  # noqa: E402

from api.views import postcard  # noqa: E402

# generate_postcard's error paths return JsonResponse, which needs
# settings.DEFAULT_CHARSET -- unlike the success path (raw HttpResponse),
# so this wasn't needed until these tests started exercising 404/502/504.
# No other Django features (DB, apps) are touched.
import django.conf  # noqa: E402

if not django.conf.settings.configured:
    django.conf.settings.configure(DEFAULT_CHARSET="utf-8")


def _request(**meta):
    return SimpleNamespace(META=meta)


class _FakeRequest:
    """Minimal Django-request stand-in, matching test_postcard_origin_metadata's
    helper: only .method, .body, .META are read by generate_postcard."""

    def __init__(self, lat, lon, ip):
        self.method = "POST"
        self.body = json.dumps({"lat": lat, "lon": lon}).encode()
        self.META = {"REMOTE_ADDR": ip}


def test_client_ip_prefers_x_real_ip():
    """nginx sets X-Real-IP unconditionally (overwrites, not appends), so it's
    the most trustworthy signal when present."""
    req = _request(HTTP_X_REAL_IP="203.0.113.7", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7")
    assert postcard._client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_last_x_forwarded_for_entry():
    """Without X-Real-IP, use the *last* X-Forwarded-For entry -- the one
    nginx itself appended ($proxy_add_x_forwarded_for appends to whatever a
    client already sent) -- not the first, which the client fully controls."""
    req = _request(HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.7")
    assert postcard._client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_remote_addr_with_no_proxy_headers():
    """Local dev (deploy-compute.sh --local, no nginx in front) has neither
    header at all."""
    req = _request(REMOTE_ADDR="127.0.0.1")
    assert postcard._client_ip(req) == "127.0.0.1"


def test_client_ip_defaults_to_unknown_with_nothing_present():
    req = _request()
    assert postcard._client_ip(req) == "unknown"


def test_client_ip_resists_x_forwarded_for_spoofing_across_requests():
    """Regression: the same real client (same nginx-observed IP) sending a
    *different* fake first X-Forwarded-For entry on every request used to
    resolve to a different _client_ip() each time, bypassing the per-IP
    rate limit entirely (each fake value got its own fresh quota). nginx
    appends the real IP last regardless of what the client sends first, so
    reading the last entry collapses all of these back to one identity."""
    real_client_ip = "203.0.113.7"
    resolved = {
        postcard._client_ip(_request(HTTP_X_FORWARDED_FOR=f"{fake}, {real_client_ip}"))
        for fake in ("1.1.1.1", "2.2.2.2", "3.3.3.3")
    }
    assert resolved == {real_client_ip}


def test_check_rate_limit_is_independent_per_ip():
    """Two different IPs must not share a quota -- exhausting one leaves the
    other's untouched."""
    postcard._rate_state.clear()
    for _ in range(postcard.RATE_LIMIT_MAX):
        allowed, _retry_after, _slot = postcard._check_rate_limit("1.1.1.1")
        assert allowed
    blocked, retry_after, slot = postcard._check_rate_limit("1.1.1.1")
    assert blocked is False
    assert retry_after > 0
    assert slot is None

    # A genuinely different IP has its own, untouched quota.
    allowed, _retry_after, _slot = postcard._check_rate_limit("2.2.2.2")
    assert allowed is True


def test_check_rate_limit_blocks_after_max_requests_within_window():
    postcard._rate_state.clear()
    ip = "9.9.9.9"
    for _ in range(postcard.RATE_LIMIT_MAX):
        allowed, _, _slot = postcard._check_rate_limit(ip)
        assert allowed
    allowed, retry_after, slot = postcard._check_rate_limit(ip)
    assert allowed is False
    assert 0 < retry_after <= postcard.RATE_LIMIT_WINDOW_SECONDS
    assert slot is None


def test_check_rate_limit_allows_again_once_window_elapses(monkeypatch):
    """Timestamps older than RATE_LIMIT_WINDOW_SECONDS drop out of the
    window, freeing up quota -- simulated by rewriting stored timestamps to
    the past rather than sleeping in a test."""
    postcard._rate_state.clear()
    ip = "8.8.8.8"
    for _ in range(postcard.RATE_LIMIT_MAX):
        postcard._check_rate_limit(ip)
    allowed, _, _slot = postcard._check_rate_limit(ip)
    assert allowed is False

    # Age out every recorded timestamp past the window.
    now = time.monotonic()
    postcard._rate_state[ip] = [
        t - postcard.RATE_LIMIT_WINDOW_SECONDS - 1 for t in postcard._rate_state[ip]
    ]
    allowed, _, _slot = postcard._check_rate_limit(ip)
    assert allowed is True


def test_release_rate_limit_frees_a_reserved_slot():
    """A released slot no longer counts toward the quota -- the next request
    from the same IP is allowed even though RATE_LIMIT_MAX were already
    reserved."""
    postcard._rate_state.clear()
    ip = "7.7.7.7"
    slots = []
    for _ in range(postcard.RATE_LIMIT_MAX):
        _allowed, _retry_after, slot = postcard._check_rate_limit(ip)
        slots.append(slot)
    allowed, _retry_after, _slot = postcard._check_rate_limit(ip)
    assert allowed is False  # quota genuinely exhausted

    postcard._release_rate_limit(ip, slots[0])
    allowed, _retry_after, _slot = postcard._check_rate_limit(ip)
    assert allowed is True  # releasing one slot frees exactly one retry


def test_release_rate_limit_is_a_noop_for_none_slot():
    """Rejected requests hand back slot=None (see _check_rate_limit) --
    releasing that must not raise or touch other IPs' state."""
    postcard._rate_state.clear()
    postcard._release_rate_limit("6.6.6.6", None)  # must not raise
    assert postcard._rate_state.get("6.6.6.6", []) == []


def test_release_latest_rate_limit_frees_exactly_one_slot():
    """Regression: a browser that Cancels a still-pending generate request
    used to still have that abandoned attempt (which keeps running
    server-side -- Django/waitress can't interrupt it) count against their
    quota once it finished. Releasing the most recent reservation undoes
    that specific cost without touching earlier, genuinely-used slots."""
    postcard._rate_state.clear()
    ip = "4.4.4.1"
    for _ in range(postcard.RATE_LIMIT_MAX):
        postcard._check_rate_limit(ip)
    assert _quota_used(ip) == postcard.RATE_LIMIT_MAX

    postcard._release_latest_rate_limit(ip)
    assert _quota_used(ip) == postcard.RATE_LIMIT_MAX - 1
    allowed, _retry_after, _slot = postcard._check_rate_limit(ip)
    assert allowed is True  # exactly one retry freed up


def test_release_latest_rate_limit_is_a_noop_with_no_reservations():
    """Cancelling before any request has ever reserved a slot for this IP
    (or after the window has already emptied it) must not raise."""
    postcard._rate_state.clear()
    postcard._release_latest_rate_limit("4.4.4.2")  # must not raise
    assert postcard._rate_state.get("4.4.4.2", []) == []


def test_cancel_postcard_endpoint_releases_a_slot():
    """End-to-end: POST /api/postcard/cancel (cancel_postcard) is what
    postcard.html's cancelGeneration actually calls -- exercise the view
    itself, not just the helper it wraps."""
    postcard._rate_state.clear()
    ip = "4.4.4.3"
    for _ in range(postcard.RATE_LIMIT_MAX):
        postcard._check_rate_limit(ip)
    assert _quota_used(ip) == postcard.RATE_LIMIT_MAX

    resp = postcard.cancel_postcard(SimpleNamespace(method="POST", META={"REMOTE_ADDR": ip}))
    assert resp.status_code == 200
    assert _quota_used(ip) == postcard.RATE_LIMIT_MAX - 1


def test_cancel_postcard_rejects_non_post():
    resp = postcard.cancel_postcard(SimpleNamespace(method="GET", META={}))
    assert resp.status_code == 405


def _fake_client_raising(exc):
    class _FakeClient:
        def fetch_quantized_structure(self, bbox, year):  # noqa: ARG002
            raise exc

    return _FakeClient()


def _quota_used(ip):
    return len(postcard._rate_state.get(ip, []))


def test_generate_postcard_timeout_does_not_consume_quota(monkeypatch):
    """End-to-end: a TimeoutError from the embeddings provider must not cost
    the caller a rate-limit slot -- the 504 response explicitly tells them
    to retry, so a retry can't be what gets them blocked."""
    postcard._rate_state.clear()
    ip = "5.5.5.1"
    monkeypatch.setattr(
        "api.embeddings_provider.get_embeddings_provider",
        lambda _name: _fake_client_raising(TimeoutError("cold fetch")),
    )
    resp = postcard.generate_postcard(_FakeRequest(52.2099, 0.1823, ip))
    assert resp.status_code == 504
    assert _quota_used(ip) == 0


def test_generate_postcard_generic_failure_does_not_consume_quota(monkeypatch):
    """Same reasoning as the TimeoutError case, for any other upstream
    failure (bolt-on 500, network error, ...) -- the 502 response also
    invites a retry."""
    postcard._rate_state.clear()
    ip = "5.5.5.2"
    monkeypatch.setattr(
        "api.embeddings_provider.get_embeddings_provider",
        lambda _name: _fake_client_raising(RuntimeError("bolt-on exploded")),
    )
    resp = postcard.generate_postcard(_FakeRequest(52.2099, 0.1823, ip))
    assert resp.status_code == 502
    assert _quota_used(ip) == 0


def test_generate_postcard_no_coverage_still_consumes_quota(monkeypatch):
    """NoCoverageError is left counted -- it's cheap and the message doesn't
    invite a same-spot retry, so leaving it counted stops free-form
    location probing without punishing genuine retries."""
    postcard._rate_state.clear()
    ip = "5.5.5.3"
    monkeypatch.setattr(
        "api.embeddings_provider.get_embeddings_provider",
        lambda _name: _fake_client_raising(NoCoverageError("no coverage")),
    )
    resp = postcard.generate_postcard(_FakeRequest(52.2099, 0.1823, ip))
    assert resp.status_code == 404
    assert _quota_used(ip) == 1


def test_generate_postcard_success_consumes_quota(monkeypatch):
    """A genuine successful generation still counts -- only failures that
    explicitly invite a retry are released."""
    from tessera_vq.client import QuantizedStructure

    postcard._rate_state.clear()
    ip = "5.5.5.4"
    bbox = postcard._bbox_from_center(52.2099, 0.1823)
    rng = np.random.default_rng(0)
    struct_ = QuantizedStructure(
        codebooks1=rng.standard_normal((1, 4, 128)).astype(np.float32),
        indices1=rng.integers(0, 4, size=(1, 16, 16), dtype=np.uint8),
        codebooks2=None,
        indices2=None,
        positions=np.array([[0, 0]], dtype=np.int32),
        tile_size=16,
        k1=4,
        k2=None,
        metric="euclidean",
        mosaic_shape=(16, 16),
        bbox=bbox,
        year=2024,
        origin=None,
    )

    class _FakeClient:
        def fetch_quantized_structure(self, bbox, year):  # noqa: ARG002
            return struct_

    monkeypatch.setattr(
        "api.embeddings_provider.get_embeddings_provider", lambda _name: _FakeClient()
    )
    resp = postcard.generate_postcard(_FakeRequest(52.2099, 0.1823, ip))
    assert resp.status_code == 200
    assert _quota_used(ip) == 1
