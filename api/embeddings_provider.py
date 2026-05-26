"""Per-viewport embeddings provider.

Returns either :class:`tessera_vq.client.VQTessera` (fast path) or
:class:`geotessera.GeoTessera` based on the viewport's saved config in
``{viewport}_config.json``. Both clients expose the same
``(mosaic, transform, crs)`` signature for ``fetch_mosaic_for_region`` and
``fetch_embedding``, so call sites are identical downstream.

The fast path is opted in per-viewport at creation time (UI checkbox); the
``(t, k, k2, m)`` values used for that viewport are persisted in the same
config file and reused for *every* embedding fetch (process_viewport,
tessera_eval evaluation, Create Map) so embeddings stay consistent across
the pipeline.

The provider is cheap to call repeatedly: ``VQTessera`` opens no socket
until a fetch method is invoked.
"""

import logging
from typing import Optional

from django.conf import settings
from geotessera import GeoTessera

from api.helpers import get_viewport_vq_config

logger = logging.getLogger(__name__)


def get_embeddings_provider(viewport_name: Optional[str] = None,
                            *,
                            embeddings_dir: Optional[str] = None):
    """Return a GeoTessera-compatible client for ``viewport_name``.

    If the viewport has ``fast_path=True``, returns a ``VQTessera`` pointed at
    ``settings.TESSERA_VQ_URL`` with the viewport's saved ``(t, k, k2, m)``.
    Otherwise returns a plain ``GeoTessera`` (with the tile cache dir if
    provided). Anonymous / legacy viewports get the standard ``GeoTessera``
    path.

    ``VQTessera`` does not take an ``embeddings_dir`` — it talks over HTTP to
    the bolt-on — so the ``embeddings_dir`` argument is only forwarded when
    returning a ``GeoTessera``.
    """
    vq = get_viewport_vq_config(viewport_name) if viewport_name else None
    if vq is None:
        if embeddings_dir is not None:
            return GeoTessera(embeddings_dir=embeddings_dir)
        return GeoTessera()

    # Fast path — import lazily so installs without `tessera-vq` still work
    # for plain-GeoTessera viewports (e.g. dev environments).
    from tessera_vq.client import VQTessera

    logger.info(
        "Using VQTessera fast path for viewport=%s (t=%d k=%d k2=%s m=%s url=%s)",
        viewport_name, vq['t'], vq['k'], vq['k2'], vq['m'], settings.TESSERA_VQ_URL,
    )
    return VQTessera(
        url=settings.TESSERA_VQ_URL,
        t=vq['t'], k=vq['k'], m=vq['m'], k2=vq['k2'],
    )


def build_vq_client_from_config(vq: dict):
    """Construct a ``VQTessera`` directly from an explicit ``(t,k,k2,m)`` dict.

    Used by the eval server / CLI paths that receive the VQ config in their
    request payload rather than reading it from a viewport file. ``vq`` is the
    same shape returned by :func:`api.helpers.get_viewport_vq_config`.
    """
    from tessera_vq.client import VQTessera
    return VQTessera(
        url=settings.TESSERA_VQ_URL,
        t=int(vq['t']),
        k=int(vq['k']),
        m=str(vq.get('m', 'euclidean')).lower(),
        k2=(int(vq['k2']) if vq.get('k2') is not None else None),
    )
