# Deployment Plan: TEE on a VM Behind Apache

Deploy TEE on a public-facing VM with Apache as the HTTPS reverse proxy.

> See also [docs/architecture.md §7](docs/architecture.md#7-deployment) for the
> Docker-based production flow and the local-development modes. This document
> covers the bare-metal VM-behind-Apache setup specifically.

## Overview

TEE is a **single Django application** served by the Waitress WSGI server on
port 8001. There is no separate tile server — map tiles are served by Django
itself at the root paths `/tiles/...` and `/bounds/...` (they bypass the
middleware stack via `TileShortcircuitMiddleware` for performance). All ML
evaluation runs on a user-operated `tee-compute` service, **not** on the
hosted server.

## Architecture

```
Internet (Port 443)
       |
  [ Apache ]   HTTPS, HTTP/2, caching, compression, security headers
       |
  everything → Django (Waitress) :8001  (localhost only, run as tee user)
                 ├── /                static UI (public/)
                 ├── /api/*           backend API
                 ├── /tiles/*         tile server (built into Django)
                 └── /bounds/*        layer bounds
```

The hosted server runs Django only. Users who need ML evaluation run their
own `tee-compute` locally and point it at the hosted server with
`tee-compute --hosted https://tee.yourdomain.com`.

| Component | RAM | Notes |
|-----------|-----|-------|
| OS + Apache | ~350 MB | Ubuntu + reverse proxy |
| Django (Waitress, 16 threads) | ~150 MB | Single process, serves UI + API + tiles |
| Pipeline subprocess | ~300 MB | Peak during UMAP; stages run sequentially |
| **Total peak** | **~800 MB** | Safe for 2 GB+ VM |

## Two ways to run on the VM

### Option A — Docker (recommended)

This is the same path documented in the README and `docs/architecture.md`.

```bash
docker pull sk818/tee:stable
docker run -d --name tee --restart unless-stopped \
    -p 8001:8001 -v /data:/data -v /data/viewports:/app/viewports \
    sk818/tee:stable
curl http://localhost:8001/health
```

`docker-compose.yml` runs the same image via Waitress
(`waitress --host=0.0.0.0 --port=8001 --threads=16 tee_project.wsgi:application`)
with a `/health` healthcheck.

**`scripts/manage.sh` is the operational tool for the running container** —
this is how the deployed image on `tee.cl.cam.ac.uk` is updated. Copy it out
of the container once:

```bash
docker cp tee:/app/scripts/manage.sh ~/manage.sh && chmod +x ~/manage.sh
```

Then `sudo ./manage.sh` gives an interactive menu:

| Option | Action |
|--------|--------|
| 1–6 | User management (list / add / remove / set quota / grant–revoke enroller) — wraps the `manage.py tee_*` commands via `docker exec` |
| **7) Update container** | **`docker pull sk818/tee:stable`, then stop + remove + re-`docker run` the container with the production env/volumes, then `/health` check** — the standard image-upgrade path on the server |
| 8 | Exit |

### Option B — Bare metal (git checkout + venv)

Use the repo's own scripts:

```bash
cd /opt
sudo git clone https://github.com/ucam-eo/TEE.git tee
cd /opt/tee
sudo bash deploy.sh        # creates 'tee' user, venv, dirs, @reboot crontab
sudo bash restart.sh       # starts Django via Waitress
curl http://localhost:8001/health
```

`deploy.sh` creates the `tee` system user, the Python venv, data
directories, a Django `check --deploy` validation, and an `@reboot`
crontab entry that runs `restart.sh`. `restart.sh` auto-detects mode: if the
`tee` user exists it runs server mode (Django on `127.0.0.1:8001`, no
tee-compute); otherwise it runs local-dev mode (Django on `:8001` +
tee-compute on `:8002`). Stop with `sudo bash shutdown.sh`, check with
`bash status.sh`.

## Directory Layout (bare-metal)

```
/opt/tee/                   # App (git clone), owned by tee
  manage.py
  tee_project/              # Django project (settings, wsgi, celery)
  api/  lib/  public/
  deploy.sh restart.sh shutdown.sh status.sh
  venv/                     # Python virtualenv (created by deploy.sh)

/home/tee/data/             # Data dir (TEE_DATA_DIR), owned by tee
  mosaics/ pyramids/ vectors/ embeddings/ progress/ share/
  .django_sessions/ .django_secret_key

/var/log/tee/               # Server logs (server mode)
```

Under Docker the data dir is the mounted volume (e.g. `/data`), set via
`TEE_DATA_DIR`; viewport definition files live in `/app/viewports`.

## User Management

There is **no `passwd` file and no `scripts/manage_users.py`**. Users are
Django users managed by management commands (run inside the container with
`docker exec tee python3 manage.py …`, or on bare metal with
`sudo -u tee /opt/tee/venv/bin/python3 manage.py …`):

```bash
manage.py tee_adduser <username> [--admin] [--email E] [--quota MB]
manage.py tee_listusers
manage.py tee_removeuser <username>
manage.py tee_setquota <username> <quota_mb>
manage.py tee_setenroller <username> [--revoke]
```

Auth is optional: it activates automatically once at least one Django user
exists. With no users, the site is read-only demo mode.

## Apache Configuration

### Enable modules

```bash
sudo a2enmod proxy proxy_http headers rewrite ssl deflate expires http2
sudo systemctl restart apache2
```

### HTTP → HTTPS redirect (`/etc/apache2/sites-available/tee.conf`)

```apache
<VirtualHost *:80>
    ServerName tee.yourdomain.com
    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^(.*)$ https://%{HTTP_HOST}%{REQUEST_URI} [L,R=301]
</VirtualHost>
```

### HTTPS virtual host (`/etc/apache2/sites-available/tee-ssl.conf`)

A single backend handles everything (UI, API, tiles) on `127.0.0.1:8001`.

```apache
<VirtualHost *:443>
    ServerName tee.yourdomain.com

    # SSL
    SSLEngine on
    SSLCertificateFile /path/to/certificate.crt
    SSLCertificateKeyFile /path/to/private.key
    SSLProtocol all -SSLv3 -TLSv1 -TLSv1.1

    # HTTP/2
    Protocols h2 http/1.1

    # Health check - exempt from any auth
    <Location /health>
        Require all granted
    </Location>

    # Proxy everything to Django (localhost only).
    # Long timeout: evaluation streams NDJSON for up to 2 hours.
    ProxyPreserveHost On
    ProxyRequests Off
    ProxyTimeout 7200

    ProxyPass / http://127.0.0.1:8001/
    ProxyPassReverse / http://127.0.0.1:8001/

    # Security headers
    Header always set X-Content-Type-Options "nosniff"
    Header always set X-Frame-Options "DENY"
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"
    ServerSignature Off

    # Compression — never gzip PNG tiles, and never buffer NDJSON streams
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE application/json text/html text/css application/javascript
        SetEnvIfNoCase Request_URI "\.png$" no-gzip
        SetEnvIf Request_URI "^/api/evaluation/" no-gzip
    </IfModule>

    # Caching
    <IfModule mod_expires.c>
        ExpiresActive On
        <LocationMatch "^/tiles/.*\.png$">
            Header set Cache-Control "public, max-age=31536000, immutable"
        </LocationMatch>
        <LocationMatch "\.(html|js|css)$">
            Header set Cache-Control "public, max-age=3600"
        </LocationMatch>
        <Location /api>
            Header set Cache-Control "no-cache, no-store, must-revalidate"
        </Location>
    </IfModule>
</VirtualHost>
```

### Enable and verify

```bash
sudo a2ensite tee.conf tee-ssl.conf
sudo apache2ctl configtest
sudo systemctl reload apache2
```

## Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8001/tcp    # Block direct access to Django
sudo ufw enable
```

## Day-to-Day Operations

| Action | Docker | Bare metal |
|--------|--------|------------|
| Update | `sudo ./manage.sh` (pull + restart + health-check) | `cd /opt/tee && sudo git pull && sudo bash restart.sh` |
| Stop | `docker stop tee` | `sudo bash shutdown.sh` |
| Status | `docker ps` / `curl localhost:8001/health` | `bash status.sh` |
| Logs | `docker logs -f tee` | `tail -f /var/log/tee/web_server.log` |
| Users | `docker exec tee python3 manage.py tee_listusers` | `sudo -u tee venv/bin/python3 manage.py tee_listusers` |

## Why Not Gunicorn?

Waitress's threaded WSGI server is sufficient for TEE because:

- **1–2 concurrent users** — this is a research tool, not a high-traffic service.
- **CPU-heavy work runs as subprocesses** — pipeline stages (download,
  vectors, UMAP) are memory-isolated via `subprocess.Popen`; ML runs on a
  separate `tee-compute` process entirely.
- **Apache handles the hard parts** — TLS, HTTP/2, caching, compression.
- **Memory matters** — a single Waitress process uses ~150 MB; multi-worker
  setups multiply that with no concurrency benefit here.
- **No worker recycling** — nothing kills the process mid-pipeline.

If concurrency ever becomes a problem (>5 users), raise `--threads` in
`restart.sh` / `docker-compose.yml` before reaching for a multi-process server.

## Design Principles

- **Fewer moving parts** — one Django process; no separate tile server, no
  multi-worker pool.
- **One data directory** — `TEE_DATA_DIR` (e.g. `/home/tee/data` or a Docker
  volume), owned by the `tee` user.
- **Auto-detect environment** — `restart.sh` works as the `tee` user on a
  server and as your own user for local development.
- **Memory efficient** — total peak ~800 MB, comfortable on a 2 GB+ VM.
