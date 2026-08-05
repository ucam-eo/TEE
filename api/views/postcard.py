"""Postcard: a narrow, unauthenticated "just for fun" demo endpoint.

Pick a location -> fetch one year of embeddings -> render the first 3
embedding dims as a percentile-stretched JPEG. Nothing is persisted (no
viewport, no files on disk);
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


def _embeddings_to_rgb(mosaic):
    """(H, W, 128) float32 -> (H, W, 3) uint8 using the first 3 embedding
    dimensions directly, 2nd-98th percentile stretched per channel -- the
    same technique the app's own satellite pyramid tiles use (see
    percentile_normalize() / write_pyramid_levels() in process_viewport.py).
    PCA was tried here first but the reduced components read as garish/
    arbitrary color; the raw first 3 dims give calmer, more natural-looking
    imagery that matches what you see in the viewer.
    """
    rgb = np.zeros((mosaic.shape[0], mosaic.shape[1], 3), dtype=np.uint8)
    for c in range(3):
        band = mosaic[:, :, c]
        valid = band[~np.isnan(band)]
        if len(valid) == 0:
            continue
        p2, p98 = np.percentile(valid, [2, 98])
        span = p98 - p2
        if span == 0:
            continue
        clipped = np.clip(np.nan_to_num(band, nan=p2), p2, p98)
        rgb[:, :, c] = ((clipped - p2) / span * 255).astype(np.uint8)
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
    except NoCoverageError as e:
        logger.info('postcard: no coverage for lat=%s lon=%s: %s', lat, lon, e)
        return JsonResponse(
            {'error': 'No Tessera coverage for this location — try somewhere else.'},
            status=404,
        )
    except TimeoutError as e:
        # A cold origin fetch for a busy/complex area can exceed the bolt-on
        # timeout even though coverage genuinely exists (e.g. central London).
        # Distinct from NoCoverageError -- don't call this "no coverage",
        # and a retry is often fast since the origin fetch may have warmed
        # the bolt-on's cache regardless of the client giving up.
        logger.warning('postcard: timed out for lat=%s lon=%s: %s', lat, lon, e)
        return JsonResponse(
            {'error': 'Request timed out fetching embeddings — this can happen for busy areas '
                      'on the first fetch. Try again, it\'s often faster the second time.'},
            status=504,
        )
    except Exception as e:
        # Anything else (bolt-on 500ing, network errors, etc.) -- log the real
        # exception server-side, surface a generic but honest message.
        logger.warning('postcard: generation failed for lat=%s lon=%s: %s', lat, lon, e)
        return JsonResponse(
            {'error': 'Generation failed — please try again.'},
            status=502,
        )

    rgb = _embeddings_to_rgb(mosaic)

    from PIL import Image as PILImage
    buf = io.BytesIO()
    PILImage.fromarray(rgb, mode='RGB').save(buf, format='JPEG', quality=92)
    buf.seek(0)

    response = HttpResponse(buf.read(), content_type='image/jpeg')
    response['Content-Disposition'] = 'attachment; filename="tee-postcard.jpg"'
    return response
