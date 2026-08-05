"""Postcard: a narrow, unauthenticated "just for fun" demo endpoint.

Pick a location -> fetch one year of embeddings -> PCA the 128 dims down to
3 -> render as a JPEG. Nothing is persisted (no viewport, no files on disk);
the whole thing runs in memory for the duration of the request. Deliberately
separate from viewport creation (which is gated behind login) and rate
limited per-IP, since this is reachable by anonymous users in demo mode.
"""

import io
import os
import logging
import threading
import time
from math import cos, radians

import numpy as np
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

KM_PER_DEGREE = 111.32  # matches public/viewport_selector.html's kmToDegrees
POSTCARD_SIZE_KM = 5.0
POSTCARD_YEAR = 2024

RATE_LIMIT_MAX = int(os.environ.get("POSTCARD_RATE_LIMIT_MAX", "5"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("POSTCARD_RATE_LIMIT_WINDOW_SECONDS", "600"))

# In-memory per-IP rate limiter. Fine for a single waitress process with many
# threads (the actual deploy model here); would need a shared store (Redis
# etc.) if this ever ran behind multiple worker processes.
_rate_lock = threading.Lock()
_rate_state = {}  # ip -> list[request timestamps within the current window]


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


def _check_rate_limit(ip):
    """Return (allowed, retry_after_seconds)."""
    now = time.monotonic()
    with _rate_lock:
        timestamps = [t for t in _rate_state.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SECONDS]
        if len(timestamps) >= RATE_LIMIT_MAX:
            retry_after = int(RATE_LIMIT_WINDOW_SECONDS - (now - timestamps[0]))
            return False, max(retry_after, 1)
        timestamps.append(now)
        _rate_state[ip] = timestamps
        return True, 0


def _bbox_from_center(lat, lon, size_km=POSTCARD_SIZE_KM):
    """Same center -> bounds math as kmToDegrees() in viewport_selector.html."""
    half = size_km / 2
    lat_offset = half / KM_PER_DEGREE
    lon_offset = half / (KM_PER_DEGREE * cos(radians(lat)))
    return (lon - lon_offset, lat - lat_offset, lon + lon_offset, lat + lat_offset)


def _pca_to_rgb(mosaic):
    """(H, W, 128) float32 -> (H, W, 3) uint8 via PCA + per-channel percentile stretch."""
    from sklearn.decomposition import PCA

    h, w, dim = mosaic.shape
    flat = np.nan_to_num(mosaic, nan=0.0).reshape(-1, dim).astype(np.float32)

    pca = PCA(n_components=3)
    components = pca.fit_transform(flat).reshape(h, w, 3)

    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    for c in range(3):
        band = components[:, :, c]
        p2, p98 = np.percentile(band, [2, 98])
        span = p98 - p2
        if span == 0:
            continue
        rgb[:, :, c] = np.clip((band - p2) / span * 255, 0, 255).astype(np.uint8)
    return rgb


def generate_postcard(request):
    """POST {lat, lon} -> JPEG. Unauthenticated (demo mode); rate limited per-IP."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    ip = _client_ip(request)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        response = JsonResponse(
            {'error': f'Rate limit exceeded — try again in {retry_after}s.'},
            status=429,
        )
        response['Retry-After'] = str(retry_after)
        return response

    import json as _json
    try:
        body = _json.loads(request.body)
        lat = float(body['lat'])
        lon = float(body['lon'])
    except (KeyError, ValueError, TypeError, _json.JSONDecodeError):
        return JsonResponse({'error': 'lat and lon are required numeric fields'}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({'error': 'lat/lon out of range'}, status=400)

    bbox = _bbox_from_center(lat, lon)

    try:
        from api.embeddings_provider import get_embeddings_provider
        from tessera_vq.client import reconstruct_from_structure, NoCoverageError

        client = get_embeddings_provider(None)
        qs = client.fetch_quantized_structure(bbox=bbox, year=POSTCARD_YEAR)
        mosaic, _transform, _crs = reconstruct_from_structure(qs)
    except Exception as e:
        # NoCoverageError covers the clean "zero tiles" case; anything else
        # (e.g. the bolt-on 500ing on open ocean/poles instead of returning an
        # empty structure) reads the same to an anonymous visitor -- log the
        # real exception server-side, keep the surfaced message friendly.
        is_coverage = isinstance(e, NoCoverageError)
        logger.log(logging.INFO if is_coverage else logging.WARNING,
                    'postcard generation failed for lat=%s lon=%s: %s', lat, lon, e)
        return JsonResponse(
            {'error': 'No Tessera coverage for this location — try somewhere else.'},
            status=404,
        )

    rgb = _pca_to_rgb(mosaic)

    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.fromarray(rgb, mode='RGB').save(buf, format='JPEG', quality=92)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type='image/jpeg')
    response['Content-Disposition'] = 'attachment; filename="tee-postcard.jpg"'
    return response
