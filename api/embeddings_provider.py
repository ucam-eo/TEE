"""Per-viewport embeddings provider.

Returns a :class:`tessera_vq.client.VQTessera` client -- the standard
embeddings client for every viewport, having replaced plain
:class:`geotessera.GeoTessera` as the origin fetch path (not just an opt-in
accelerator).

A viewport's saved config in ``{viewport}_config.json`` still supplies custom
``(t, k, k2, m)`` if tuned at creation time; otherwise the study-recommended
RVQ defaults (``settings.TESSERA_VQ_DEFAULTS``) apply. Either way the same
values are reused for *every* embedding fetch (process_viewport, tessera_eval
evaluation, Create Map) so embeddings stay consistent across the pipeline.

The provider is cheap to call repeatedly: ``VQTessera`` opens no socket
until a fetch method is invoked.
"""

import logging
from typing import Optional

from django.conf import settings

from api.helpers import get_viewport_vq_config

logger = logging.getLogger(__name__)


def get_embeddings_provider(viewport_name: Optional[str] = None):
    """Return a ``VQTessera`` client for ``viewport_name``.

    Uses the viewport's saved ``(t, k, k2, m)`` if present; anonymous /
    legacy viewports (no saved config) get the standard RVQ defaults.
    """
    vq = get_viewport_vq_config(viewport_name) if viewport_name else None
    if vq is None:
        vq = dict(settings.TESSERA_VQ_DEFAULTS)

    from tessera_vq.client import VQTessera

    logger.info(
        "Using VQTessera for viewport=%s (t=%d k=%d k2=%s m=%s url=%s)",
        viewport_name, vq['t'], vq['k'], vq['k2'], vq['m'], settings.TESSERA_VQ_URL,
    )
    return VQTessera(
        server_url=settings.TESSERA_VQ_URL,
        t=vq['t'], k=vq['k'], m=vq['m'],
        timeout=settings.TESSERA_VQ_TIMEOUT_SECONDS,
        k2=vq['k2'],
    )


def build_vq_client_from_config(vq: dict):
    """Construct a ``VQTessera`` directly from an explicit ``(t,k,k2,m)`` dict.

    Used by the eval server / CLI paths that receive the VQ config in their
    request payload rather than reading it from a viewport file. ``vq`` is the
    same shape returned by :func:`api.helpers.get_viewport_vq_config`.
    """
    from tessera_vq.client import VQTessera
    return VQTessera(
        server_url=settings.TESSERA_VQ_URL,
        t=int(vq['t']),
        k=int(vq['k']),
        m=str(vq.get('m', 'euclidean')).lower(),
        timeout=settings.TESSERA_VQ_TIMEOUT_SECONDS,
        k2=(int(vq['k2']) if vq.get('k2') is not None else None),
    )
