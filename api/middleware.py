"""
DemoModeMiddleware - ports backend/auth.py's _require_auth() hook to Django.

Uses DATA_DIR/passwd file with hashed passwords and mtime caching.
If no passwd file exists: open access (backwards compatible).
If passwd file exists + user not in session: block writes (401 JSON), allow reads.
"""

import logging

from django.http import JsonResponse, HttpResponseRedirect

from lib.config import DATA_DIR
from api.helpers import DEFAULT_QUOTA_MB

logger = logging.getLogger(__name__)

# Module state (same pattern as backend/auth.py)
_passwd_file = DATA_DIR / 'passwd'
_passwd_mtime = 0
_passwd_users = {}  # username -> {'hash': str, 'quota_mb': int}


def _load_passwd():
    """Reload passwd file if it has changed (mtime check)."""
    global _passwd_mtime, _passwd_users

    if not _passwd_file.exists():
        _passwd_users = {}
        _passwd_mtime = 0
        return

    try:
        mtime = _passwd_file.stat().st_mtime
    except OSError:
        _passwd_users = {}
        _passwd_mtime = 0
        return

    if mtime == _passwd_mtime:
        return  # no change

    users = {}
    try:
        for line in _passwd_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' not in line:
                continue
            parts = line.split(':')
            username = parts[0].strip()
            hashed = parts[1].strip()
            quota_mb = DEFAULT_QUOTA_MB
            if len(parts) > 2 and parts[2].strip():
                try:
                    quota_mb = int(parts[2].strip())
                except ValueError:
                    pass
            if username and hashed:
                users[username] = {'hash': hashed, 'quota_mb': quota_mb}
    except OSError as e:
        logger.error(f"Error reading passwd file: {e}")
        return

    _passwd_users = users
    _passwd_mtime = mtime
    logger.info(f"Loaded {len(users)} user(s) from passwd file")


def auth_enabled():
    """Return True if passwd file exists and has at least one user."""
    _load_passwd()
    return len(_passwd_users) > 0


def check_credentials(username, password):
    """Verify username/password against the passwd file (htpasswd bcrypt format)."""
    import bcrypt as _bcrypt
    _load_passwd()
    entry = _passwd_users.get(username)
    if entry is None:
        return False
    try:
        normalized = entry['hash'].replace('$2y$', '$2b$', 1)
        return _bcrypt.checkpw(password.encode(), normalized.encode())
    except Exception:
        return False


def get_user_quota(username):
    """Return disk quota in MB for *username*. Admin is unlimited."""
    _load_passwd()
    if username == 'admin':
        return float('inf')
    entry = _passwd_users.get(username)
    if entry is None:
        return DEFAULT_QUOTA_MB
    return entry['quota_mb']


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
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not auth_enabled():
            return self.get_response(request)  # no passwd file -> open access

        if _is_public_path(request.path):
            return self.get_response(request)  # public endpoint

        if request.session.get('user'):
            return self.get_response(request)  # logged in

        # Not authenticated - block write endpoints, allow reads (demo mode)
        if _is_write_endpoint(request.path):
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Authentication required'}, status=401)
            else:
                return HttpResponseRedirect('/login.html')

        return self.get_response(request)
