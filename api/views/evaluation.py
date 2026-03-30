"""Evaluation proxy — forwards all eval requests to the compute server (tee-compute).

All ML evaluation runs on tee-compute. Django proxies these requests
so the browser only talks to one origin (port 8001).

The compute server URL is configured via the TEE_COMPUTE_URL environment
variable (default: http://localhost:8002 for local dev).
"""

import os
import logging

import requests as _requests
from django.http import StreamingHttpResponse, JsonResponse

logger = logging.getLogger(__name__)

COMPUTE_URL = os.environ.get("TEE_COMPUTE_URL", "http://localhost:8002")


def _proxy_to_compute(request, path):
    """Forward a request to the compute server and stream the response back."""
    target = f"{COMPUTE_URL}/{path}"
    if request.META.get("QUERY_STRING"):
        target += f"?{request.META['QUERY_STRING']}"

    # Forward headers
    headers = {}
    content_type = request.content_type
    if content_type:
        headers["Content-Type"] = content_type

    try:
        resp = _requests.request(
            method=request.method,
            url=target,
            headers=headers,
            data=request.body if request.method != "GET" else None,
            files={k: (f.name, f, f.content_type) for k, f in request.FILES.items()} if request.FILES else None,
            stream=True,
            timeout=600,
        )
    except _requests.ConnectionError:
        return JsonResponse(
            {"error": f"Compute server not available at {COMPUTE_URL}. Is tee-compute running?"},
            status=502,
        )
    except _requests.Timeout:
        return JsonResponse({"error": "Compute server timed out"}, status=504)

    # Stream response back
    proxy_headers = {}
    for k, v in resp.headers.items():
        if k.lower() not in ("content-encoding", "content-length", "transfer-encoding", "connection"):
            proxy_headers[k] = v

    django_resp = StreamingHttpResponse(
        resp.iter_content(chunk_size=8192),
        status=resp.status_code,
        content_type=resp.headers.get("Content-Type", "application/json"),
    )
    for k, v in proxy_headers.items():
        django_resp[k] = v
    return django_resp


def upload_shapefile(request):
    return _proxy_to_compute(request, "api/evaluation/upload-shapefile")


def clear_shapefiles(request):
    return _proxy_to_compute(request, "api/evaluation/clear-shapefiles")


def run_evaluation(request):
    return _proxy_to_compute(request, "api/evaluation/run-large-area")


def finish_classifier(request):
    return _proxy_to_compute(request, "api/evaluation/finish-classifier")


def download_model(request, classifier):
    return _proxy_to_compute(request, f"api/evaluation/download-model/{classifier}")
