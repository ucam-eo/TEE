"""FAISS data serving endpoint."""

import logging

from django.http import JsonResponse, FileResponse

from lib.viewport_utils import validate_viewport_name
from api.helpers import FAISS_INDICES_DIR

logger = logging.getLogger(__name__)

ALLOWED_FILES = {'all_embeddings.npy', 'pixel_coords.npy', 'metadata.json'}


def serve_faiss_data(request, viewport, year, filename):
    """Serve FAISS data files (embeddings, coords, metadata) for client-side search."""
    if filename not in ALLOWED_FILES:
        return JsonResponse({'error': 'File not allowed'}, status=403)

    try:
        validate_viewport_name(viewport)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    faiss_dir = FAISS_INDICES_DIR / viewport / str(year)
    file_path = faiss_dir / filename

    if not file_path.exists():
        return JsonResponse({'error': 'File not found'}, status=404)

    content_type = 'application/json' if filename.endswith('.json') else 'application/octet-stream'
    file_size = file_path.stat().st_size

    response = FileResponse(file_path.open('rb'), content_type=content_type)
    response['Content-Length'] = file_size
    return response
