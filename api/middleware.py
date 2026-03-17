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

# Endpoints that require login (write/destructive operations)
WRITE_ENDPOINTS = {
    '/api/viewports/create',
    '/api/viewports/delete',
    '/api/auth/change-password',
    '/api/downloads/embeddings',
    '/api/downloads/process',
    '/api/evaluation/upload-shapefile',
    '/api/evaluation/run',
}


def _is_public_path(path):
    """Check if the request path is public (no auth required)."""
    return path in PUBLIC_PATHS


def _is_write_endpoint(path):
    """Check if the request path is a write/destructive endpoint requiring login."""
    if path in WRITE_ENDPOINTS:
        return True
    # Match /api/viewports/<name>/cancel-processing
    if path.startswith('/api/viewports/') and path.endswith('/cancel-processing'):
        return True
    if path.startswith('/api/viewports/') and path.endswith('/add-years'):
        return True
    if path.startswith('/api/evaluation/download-model/'):
        return True
    return False


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
        if _is_write_endpoint(request.path):
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Authentication required'}, status=401)
            else:
                return HttpResponseRedirect('/login.html')

        return self.get_response(request)
