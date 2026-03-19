"""Vector data serving endpoint."""

import logging

from django.http import JsonResponse, FileResponse

from lib.viewport_utils import validate_viewport_name
from lib.config import VECTORS_DIR

logger = logging.getLogger(__name__)

ALLOWED_FILES = {
    'pixel_coords.npy', 'pixel_coords.npy.gz', 'metadata.json',
    'all_embeddings_uint8.npy.gz', 'quantization.json',
}


def serve_vector_data(request, viewport, year, filename):
    """Serve vector data files (embeddings, coords, metadata) for client-side search."""
    if filename not in ALLOWED_FILES:
        return JsonResponse({'error': 'File not allowed'}, status=403)

    try:
        validate_viewport_name(viewport)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    vector_dir = VECTORS_DIR / viewport / str(year)

    # For pixel_coords.npy, prefer the pre-compressed .gz if it exists
    if filename == 'pixel_coords.npy':
        gz_path = vector_dir / 'pixel_coords.npy.gz'
        if gz_path.exists():
            file_size = gz_path.stat().st_size
            response = FileResponse(gz_path.open('rb'), content_type='application/gzip')
            response['Content-Length'] = file_size
            return response

    file_path = vector_dir / filename

    if not file_path.exists():
        return JsonResponse({'error': 'File not found'}, status=404)

    content_type = 'application/json' if filename.endswith('.json') else 'application/octet-stream'
    if filename.endswith('.gz'):
        content_type = 'application/gzip'
    file_size = file_path.stat().st_size

    response = FileResponse(file_path.open('rb'), content_type=content_type)
    response['Content-Length'] = file_size
    return response
