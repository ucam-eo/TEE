"""
DemoModeMiddleware - enforces authentication using Django's built-in auth.

If no User objects exist: open access (backwards compatible).
If users exist + user not authenticated: block writes (401 JSON), allow reads.
"""

import logging

from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseRedirect

from api.helpers import DEFAULT_QUOTA_MB

logger = logging.getLogger(__name__)


def auth_enabled():
    """Return True if at least one Django User exists."""
    return User.objects.exists()


def get_user_quota(user):
    """Return disk quota in MB for a Django User. Superusers are unlimited."""
    if user.is_superuser:
        return float('inf')
    try:
        return user.profile.quota_mb
    except Exception:
        return DEFAULT_QUOTA_MB


# Paths that never require authentication
PUBLIC_PATHS = {
    '/health',
    '/api/auth/login',
    '/api/auth/logout',
    '/api/auth/status',
    '/login.html',
}

# Login is required for every state-changing request to the API (any method
# other than GET/HEAD/OPTIONS) -- default-deny, so a new or renamed endpoint is
# protected without anyone remembering to list it here. (An allow-list of
# protected paths used to live here; it had drifted, e.g. '/api/evaluation/run'
# while the real route is run-large-area, leaving evaluation runs, training,
# map creation, cancel and clear-shapefiles open to anonymous users.)
#
# These are the only API writes demo mode allows without logging in:
ANONYMOUS_WRITE_ENDPOINTS = {
    '/api/viewports/switch',              # browse another viewport (per-session)
    '/api/viewports/embedding-coverage',  # read-only query, sent as POST for its body
    '/api/postcard/generate',             # public demo, rate-limited per IP
}

# Reads that still require login:
LOGIN_REQUIRED_READS = {
    '/api/downloads/embeddings',
    '/api/downloads/process',
}

_SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}


def _is_public_path(path):
    """Check if the request path is public (no auth required)."""
    return path in PUBLIC_PATHS


def _is_write_endpoint(path, method='POST'):
    """Does this request need a logged-in user?"""
    if path.startswith('/api/evaluation/'):
        # tee-compute keeps one global state shared by everyone (uploaded
        # ground truth, trained models, generated maps, the cancel flag), so
        # even its reads are only for logged-in users; health is the probe
        # the panel uses to show whether a compute server is attached.
        return path != '/api/evaluation/health'
    if method not in _SAFE_METHODS:
        return path.startswith('/api/') and path not in ANONYMOUS_WRITE_ENDPOINTS
    return path in LOGIN_REQUIRED_READS


class TileShortcircuitMiddleware:
    """Skip all other middleware for tile/bounds requests.

    Tiles are anonymous read-only — no need for session, CORS, security,
    or DemoMode processing.  Must be first in MIDDLEWARE.
    Resolves the URL and calls the view directly, bypassing the chain.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path.startswith('/tiles/') or path.startswith('/bounds/'):
            from django.urls import resolve
            match = resolve(path)
            return match.func(request, *match.args, **match.kwargs)
        return self.get_response(request)


class DemoModeMiddleware:
    """Enforce authentication when enabled.

    Strategy: allow unauthenticated read access (demo mode),
    but require login for write/destructive operations.
    Django admin paths are skipped (admin has its own auth).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Django admin handles its own auth
        if request.path.startswith('/admin/'):
            return self.get_response(request)

        if not auth_enabled():
            return self.get_response(request)  # no users -> open access

        if _is_public_path(request.path):
            return self.get_response(request)  # public endpoint

        if request.user.is_authenticated:
            return self.get_response(request)  # logged in

        # Not authenticated - block write endpoints, allow reads (demo mode)
        if _is_write_endpoint(request.path, request.method):
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Authentication required'}, status=401)
            else:
                return HttpResponseRedirect('/login.html')

        return self.get_response(request)
