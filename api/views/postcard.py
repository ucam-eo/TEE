"""Postcard: a narrow, unauthenticated "just for fun" demo endpoint.

Pick a location -> fetch one year of quantized VQ embeddings -> ship the raw
codebooks + index maps to the browser, which reconstructs and renders the
image itself (see public/js/vq_reconstruct.js and postcard.html). Nothing is
persisted server-side (no viewport, no files on disk); the whole thing runs
in memory for the duration of the request. Deliberately separate from
viewport creation (which is gated behind login) and rate limited per-IP,
since this is reachable by anonymous users in demo mode.

Rendering (percentile-stretch to RGB, "change colours" band selection, the
postcard message) all happen client-side on the reconstructed data -- once
fetched, recoloring is free: no further requests hit this endpoint, so
there's nothing extra to rate limit there.
"""

import io
import os
import json
import logging
import struct
import threading
import time
from math import cos, radians

import numpy as np
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

KM_PER_DEGREE = 111.32  # matches public/viewport_selector.html's kmToDegrees
# 10km x 6km (postcard aspect ratio) rather than a square. Same 10m/pixel
# native resolution as before, just a wider-than-tall frame. The browser
# crops the (larger, tile-aligned) reconstructed mosaic down to this after
# decoding -- see postcard.html's crop step.
POSTCARD_WIDTH_KM = 10.0
POSTCARD_HEIGHT_KM = 6.0
POSTCARD_YEAR = 2024
# Native resolution is 10m/pixel.
POSTCARD_WIDTH_PX = round(POSTCARD_WIDTH_KM * 1000 / 10)
POSTCARD_HEIGHT_PX = round(POSTCARD_HEIGHT_KM * 1000 / 10)

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


def _bbox_from_center(lat, lon, width_km=POSTCARD_WIDTH_KM, height_km=POSTCARD_HEIGHT_KM):
    """Same center -> bounds math as kmToDegrees() in viewport_selector.html."""
    half_w, half_h = width_km / 2, height_km / 2
    lat_offset = half_h / KM_PER_DEGREE
    lon_offset = half_w / (KM_PER_DEGREE * cos(radians(lat)))
    return (lon - lon_offset, lat - lat_offset, lon + lon_offset, lat + lat_offset)


def _pack_vq_bundle(meta, arrays):
    """[4B big-endian header length][UTF-8 JSON header][raw array bytes, in order].

    Purpose-built instead of reusing np.savez: nothing is persisted to disk
    here (savez wants a file-like target we'd still have to unzip client-side
    with a new library), and this needs zero new client dependencies -- the
    browser reads it back with a DataView (see postcard.html's unpackVqBundle).
    Django's GZipMiddleware already compresses the HTTP response transparently,
    so the arrays are written raw (no per-array gzip, unlike the on-disk
    per-viewport format which is fetched as separate cacheable files).
    """
    header = dict(meta)
    header['arrays'] = []
    payload = io.BytesIO()
    for name, arr in arrays:
        arr = np.ascontiguousarray(arr)
        header['arrays'].append({
            'name': name,
            'dtype': str(arr.dtype),
            'shape': list(arr.shape),
        })
        payload.write(arr.tobytes())
    header_bytes = json.dumps(header).encode('utf-8')
    return struct.pack('>I', len(header_bytes)) + header_bytes + payload.getvalue()


def generate_postcard(request):
    """POST {lat, lon} -> a VQ codebook bundle (see _pack_vq_bundle) for the
    browser to reconstruct and render. Unauthenticated (demo mode); rate
    limited per-IP.
    """
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

    try:
        body = json.loads(request.body)
        lat = float(body['lat'])
        lon = float(body['lon'])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'error': 'lat and lon are required numeric fields'}, status=400)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return JsonResponse({'error': 'lat/lon out of range'}, status=400)

    bbox = _bbox_from_center(lat, lon)

    try:
        from api.embeddings_provider import get_embeddings_provider
        from tessera_vq.client import NoCoverageError
        from process_viewport import _quantise_codebook, _assemble_indices_from_tiles

        client = get_embeddings_provider(None)
        qs = client.fetch_quantized_structure(bbox=bbox, year=POSTCARD_YEAR)

        t = qs.tile_size
        full_h, full_w = int(qs.mosaic_shape[0]), int(qs.mosaic_shape[1])
        out_h, out_w = (full_h // t) * t, (full_w // t) * t
        is_rvq = qs.codebooks2 is not None

        idx1 = _assemble_indices_from_tiles(qs, 'indices1', out_h, out_w)
        cb1_u8, cb1_scales = _quantise_codebook(qs.codebooks1)
        arrays = [
            ('codebooks1_uint8', cb1_u8),
            ('codebooks1_scales', cb1_scales),
            ('indices1', idx1),
        ]
        if is_rvq:
            idx2 = _assemble_indices_from_tiles(qs, 'indices2', out_h, out_w)
            cb2_u8, cb2_scales = _quantise_codebook(qs.codebooks2)
            arrays += [
                ('codebooks2_uint8', cb2_u8),
                ('codebooks2_scales', cb2_scales),
                ('indices2', idx2),
            ]
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

    meta = {
        'kind': 'rvq' if is_rvq else 'vq',
        'tile_size': t,
        'k1': int(qs.k1),
        'k2': int(qs.k2) if is_rvq else None,
        'n_tile_rows': full_h // t,
        'n_tile_cols': full_w // t,
        'embedding_dim': int(qs.codebooks1.shape[-1]),
        'output_shape': [out_h, out_w],
        'crop_width_px': POSTCARD_WIDTH_PX,
        'crop_height_px': POSTCARD_HEIGHT_PX,
    }
    body = _pack_vq_bundle(meta, arrays)
    return HttpResponse(body, content_type='application/octet-stream')
