# michael deploy — tessera-vq bolt-on + nginx front

Reproducible record of the host config that is applied **directly on michael** (it is
not otherwise in git). The bolt-on (`tessera-vq` package) runs as a systemd service and
is fronted by nginx for public, per-IP rate-limited access; TEE talks to it directly.

## Topology

```
internet ─▶ nginx :8000  (per-IP rate limit) ─▶ 127.0.0.1:8010  bolt-on (tessera-vq)
TEE container ──────────(direct, unthrottled)─▶ 127.0.0.1:8010  bolt-on
internet ─▶ nginx :80/:443 ─▶ :8001  TEE web app
```

- `TESSERA_VQ_URL=http://127.0.0.1:8010` is set on the TEE container (see
  `../scripts/manage.sh`) so the trusted consumer bypasses the rate limit.
- The bolt-on binds `127.0.0.1:8010` (`TESSERA_VQ_BIND`), so it is **not** directly
  reachable from outside — only via nginx.

## Files

| file | install location on michael |
|---|---|
| `tessera-vq.service` | `/etc/systemd/system/tessera-vq.service` |
| `tessera-vq.service.d/cache.conf` | `/etc/systemd/system/tessera-vq.service.d/cache.conf` (drop-in: cache + bind) |
| `nginx-tessera-vq.conf` | `/etc/nginx/conf.d/tessera-vq.conf` |

## Apply

```bash
# bolt-on service (code lives in ~/tessera-vq, a git checkout of sk818/tessera-vq)
sudo cp deploy/tessera-vq.service /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/tessera-vq.service.d
sudo cp deploy/tessera-vq.service.d/cache.conf /etc/systemd/system/tessera-vq.service.d/
sudo systemctl daemon-reload && sudo systemctl enable --now tessera-vq.service
curl -s localhost:8010/health        # {"ok":true}

# nginx front (keep the existing TEE site in sites-enabled/default intact)
sudo cp deploy/nginx-tessera-vq.conf /etc/nginx/conf.d/tessera-vq.conf
sudo nginx -t && sudo systemctl reload nginx
curl -s localhost:8000/health        # {"ok":true} via nginx
```

To upgrade the bolt-on: `cd ~/tessera-vq && git fetch --tags && git checkout vX.Y.Z &&
~/.local/bin/uv sync --extra server && sudo systemctl restart tessera-vq.service`
(**`--extra server`** is required — a plain `uv sync` prunes Flask/geotessera).

## Tunables (env in the drop-in / nginx)

- `TESSERA_VQ_CACHE_MAX_GB` (default 500) — durable cache cap; `TESSERA_VQ_CACHE_DIR` to relocate.
- `TESSERA_VQ_MAX_CONCURRENCY` — in-app compute cap (defaults from cpu_count); add to the drop-in to override.
- nginx `rate` / `burst` / `limit_conn` in `nginx-tessera-vq.conf` — per-IP limits.
