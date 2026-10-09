"""DemoModeMiddleware: which API requests need a logged-in user.

Regression for an allow-list that had drifted from the routes it guarded
('/api/evaluation/run' vs the real run-large-area), which left evaluation
runs, training, map creation, cancel and clear-shapefiles open to anonymous
users -- and tee-compute's state is shared by everyone, so an anonymous
cancel/clear would hit a logged-in user's work. Writes are now default-deny.
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tee_project.settings")
import django  # noqa: E402

django.setup()

import pytest  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import RequestFactory  # noqa: E402
from django.urls import URLPattern, URLResolver, get_resolver  # noqa: E402

from api import middleware  # noqa: E402


def _api_paths():
    """Every routed /api/ path, with a dummy value for each URL parameter."""
    def walk(patterns, prefix):
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, route)
            elif isinstance(p, URLPattern):
                yield "/" + route
    paths = []
    for path in walk(get_resolver().url_patterns, ""):
        if path.startswith("/api/"):
            for conv in ("<str:viewport_name>", "<str:viewport>", "<str:year>", "<str:filename>",
                         "<str:operation_id>", "<str:classifier>", "<str:name>",
                         "<str:sanitized_email>"):
                path = path.replace(conv, "x")
            paths.append(path)
    return paths


API_PATHS = _api_paths()


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(middleware, "auth_enabled", lambda: True)
    mw = middleware.DemoModeMiddleware(lambda request: HttpResponse("ok"))
    factory = RequestFactory()

    def _run(method, path, user=None):
        request = getattr(factory, method.lower())(path)
        request.user = user or AnonymousUser()
        return mw(request).status_code
    return _run


def test_routes_were_found():
    assert "/api/evaluation/run-large-area" in API_PATHS
    assert len(API_PATHS) > 30


@pytest.mark.parametrize("path", API_PATHS)
def test_anonymous_post_needs_login_unless_deliberately_public(run, path):
    allowed = (path in middleware.ANONYMOUS_WRITE_ENDPOINTS or path in middleware.PUBLIC_PATHS
               or path == "/api/evaluation/health")  # status probe only
    assert run("POST", path) == (200 if allowed else 401)


@pytest.mark.parametrize("path", [p for p in API_PATHS if p.startswith("/api/evaluation/")])
def test_anonymous_cannot_touch_compute_state(run, path):
    expected = 200 if path == "/api/evaluation/health" else 401
    assert run("GET", path) == expected


def test_demo_mode_reads_and_public_writes_still_work(run):
    assert run("GET", "/api/viewports/list") == 200
    assert run("GET", "/api/vector-data/x/2024/metadata.json") == 200
    assert run("POST", "/api/viewports/switch") == 200
    assert run("POST", "/api/postcard/generate") == 200
    assert run("POST", "/api/auth/login") == 200
    assert run("GET", "/api/downloads/embeddings") == 401


def test_logged_in_user_passes(run):
    class _User:
        is_authenticated = True
    assert run("POST", "/api/evaluation/run-large-area", _User()) == 200
    assert run("POST", "/api/evaluation/cancel", _User()) == 200
