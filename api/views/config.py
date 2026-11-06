"""Static file serving, health check, and client config endpoints."""

import mimetypes
import socket
import subprocess

from django.http import JsonResponse, FileResponse, Http404

from lib.config import DATA_DIR, APP_DIR
from api.middleware import auth_enabled

# Compute git version once at startup
try:
    _VERSION = subprocess.check_output(
        ['git', 'describe', '--tags', '--always'],
        cwd=str(APP_DIR), stderr=subprocess.DEVNULL
    ).decode().strip()
except Exception:
    # Docker: .git is excluded, read baked VERSION file instead
    try:
        _VERSION = (APP_DIR / 'VERSION').read_text().strip()
    except Exception:
        _VERSION = 'unknown'

PUBLIC_DIR = APP_DIR / 'public'


def serve_index(request):
    """Serve the landing chooser (anonymous, auth enabled) or the viewport
    selector directly (logged in, or no-auth/single-user mode).

    The chooser (Sign in / Demo mode / Postcard) only applies to first-time,
    unauthenticated visitors -- once you have a session, '/' drops you
    straight into the app as before.
    """
    if auth_enabled() and not request.user.is_authenticated:
        index_file = PUBLIC_DIR / 'landing.html'
    else:
        index_file = PUBLIC_DIR / 'viewport_selector.html'
    if not index_file.exists():
        raise Http404
    return FileResponse(index_file.open('rb'), content_type='text/html')


# Sample ground-truth shapefiles bundled at the repo/image root (not public/).
SAMPLE_DATA_FILES = ('austria.zip', 'gothenburg.zip')


def serve_sample_data(request, name='austria.zip'):
    """Serve a bundled sample ground-truth shapefile for evaluation.

    austria.zip: Austrian INVEKOS crop fields; gothenburg.zip: Gothenburg urban
    land cover. Both live at the repo/image root (not public/) -- see the
    "Try it" callout on the Validation tab and the Validation section of the
    user guide.
    """
    if name not in SAMPLE_DATA_FILES:
        raise Http404
    file_path = APP_DIR / name
    if not file_path.exists():
        raise Http404
    response = FileResponse(file_path.open('rb'), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{name}"'
    return response


def serve_static(request, path):
    """Serve static files from public/ directory."""
    file_path = (PUBLIC_DIR / path).resolve()
    # Prevent path traversal
    if not str(file_path).startswith(str(PUBLIC_DIR.resolve())):
        raise Http404

    if not file_path.exists() or not file_path.is_file():
        raise Http404

    content_type, _ = mimetypes.guess_type(str(file_path))
    if content_type is None:
        content_type = 'application/octet-stream'
    return FileResponse(file_path.open('rb'), content_type=content_type)


def health(request):
    """Health check endpoint for Docker/monitoring."""
    return JsonResponse({
        'status': 'healthy',
        'service': 'TEE',
        'version': _VERSION,
        'host': socket.gethostname(),
    })


def get_config(request):
    """Return client configuration."""
    return JsonResponse({})
