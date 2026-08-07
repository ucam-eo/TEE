"""Tests for api/views/postcard.py's per-IP rate limiter: _client_ip must
resolve to nginx's own trustworthy observation of the client, not a value
the client can freely choose -- otherwise "per-IP" is unenforceable.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.views import postcard  # noqa: E402


def _request(**meta):
    return SimpleNamespace(META=meta)


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
        allowed, _retry_after = postcard._check_rate_limit("1.1.1.1")
        assert allowed
    blocked, retry_after = postcard._check_rate_limit("1.1.1.1")
    assert blocked is False
    assert retry_after > 0

    # A genuinely different IP has its own, untouched quota.
    allowed, _retry_after = postcard._check_rate_limit("2.2.2.2")
    assert allowed is True


def test_check_rate_limit_blocks_after_max_requests_within_window():
    postcard._rate_state.clear()
    ip = "9.9.9.9"
    for _ in range(postcard.RATE_LIMIT_MAX):
        allowed, _ = postcard._check_rate_limit(ip)
        assert allowed
    allowed, retry_after = postcard._check_rate_limit(ip)
    assert allowed is False
    assert 0 < retry_after <= postcard.RATE_LIMIT_WINDOW_SECONDS


def test_check_rate_limit_allows_again_once_window_elapses(monkeypatch):
    """Timestamps older than RATE_LIMIT_WINDOW_SECONDS drop out of the
    window, freeing up quota -- simulated by rewriting stored timestamps to
    the past rather than sleeping in a test."""
    postcard._rate_state.clear()
    ip = "8.8.8.8"
    for _ in range(postcard.RATE_LIMIT_MAX):
        postcard._check_rate_limit(ip)
    allowed, _ = postcard._check_rate_limit(ip)
    assert allowed is False

    # Age out every recorded timestamp past the window.
    now = time.monotonic()
    postcard._rate_state[ip] = [
        t - postcard.RATE_LIMIT_WINDOW_SECONDS - 1 for t in postcard._rate_state[ip]
    ]
    allowed, _ = postcard._check_rate_limit(ip)
    assert allowed is True
