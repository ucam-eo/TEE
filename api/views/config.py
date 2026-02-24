"""Static file serving, health check, and client config endpoints."""

import mimetypes

from django.http import JsonResponse, FileResponse, Http404

from lib.config import DATA_DIR, APP_DIR

PUBLIC_DIR = APP_DIR / 'public'


def serve_index(request):
    """Serve the viewport selector HTML."""
    index_file = PUBLIC_DIR / 'viewport_selector.html'
    if not index_file.exists():
        raise Http404
    return FileResponse(index_file.open('rb'), content_type='text/html')


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
        'data_dir': str(DATA_DIR)
    })


def get_config(request):
    """Return client configuration."""
    return JsonResponse({})
