"""process_year's message after the NPY-fetch retry loop is exhausted.

Confirmed live: a viewport ("gaza", 2025) failed all 3 fetch attempts with
HTTPError 500s, and that raw exception text ("HTTPError: HTTP Error 500:
INTERNAL SERVER ERROR") became the *user-facing* failure message. Every
attempt in this loop is a network fetch, so exhausting all of them always
means the same thing regardless of the exact exception -- report that
plainly instead of leaking urllib/HTTPError internals.
"""

from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest

import process_viewport


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # The retry loop sleeps 5s between attempts -- don't actually wait.
    monkeypatch.setattr(process_viewport._time, "sleep", lambda s: None)


class _FailingTessera:
    """No fetch_quantized_structure attr -- QS fast path is skipped, so
    process_year goes straight to the NPY retry loop under test."""

    def __init__(self, exc_factory):
        self.registry = SimpleNamespace(load_blocks_for_region=lambda *a, **k: [])
        self._exc_factory = exc_factory
        self.calls = 0

    def fetch_mosaic_for_region(self, bbox=None, year=None, target_crs=None, auto_download=None):
        self.calls += 1
        raise self._exc_factory()


def _run(tmp_path, tessera, year=2025):
    pyramids_dir = tmp_path / "pyramids"
    vectors_dir = tmp_path / "vectors"
    pyramids_dir.mkdir()
    vectors_dir.mkdir()
    return process_viewport.process_year(
        tessera, "gaza", (34.2, 31.3, 34.5, 31.6), year, pyramids_dir, vectors_dir
    )


def test_exhausted_http_500_retries_get_a_friendly_message(tmp_path):
    tessera = _FailingTessera(
        lambda: urllib.error.HTTPError("http://x.test", 500, "Internal Server Error", None, None)
    )

    year, ok, msg = _run(tmp_path, tessera)

    assert (year, ok) == (2025, False)
    assert msg == "No embeddings could be found for 2025 at this location."
    assert "HTTPError" not in msg and "500" not in msg
    assert tessera.calls == 3  # all retries were actually used


def test_all_retries_are_used_before_giving_up(tmp_path):
    tessera = _FailingTessera(lambda: ConnectionError("connection reset"))
    _run(tmp_path, tessera)
    assert tessera.calls == 3


def test_no_embedding_tiles_message_still_reads_cleanly(tmp_path):
    # The pre-existing "No embedding tiles found" wording stays intact --
    # this test just confirms the new fallback didn't regress it.
    tessera = _FailingTessera(lambda: RuntimeError("No embedding tiles found for this region"))
    year, ok, msg = _run(tmp_path, tessera)
    assert ok is False
    assert "2025" in msg and "location" in msg
