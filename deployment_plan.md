# Deployment Plan: TEE on a VM Behind Apache

## Overview

Deploy TEE (web server + tile server) behind Apache reverse proxy on a public-facing VM. Uses plain Flask (no gunicorn) and a dedicated `tee` system user.

## Architecture

```
Internet (Port 443)
       |
  [ Apache ]   HTTPS, caching, compression, security headers
       |
  /api/* + static → Flask:8001 (web server)
  /tiles/*        → Flask:5125 (tile server)
  (both on localhost only, run as tee user)
```

| Component | RAM | Notes |
|-----------|-----|-------|
| OS + Apache | ~350 MB | Ubuntu + reverse proxy |
| Flask web server | ~120 MB | Single process, threaded |
| Flask tile server | ~80 MB | Single process, stateless |
| Pipeline subprocess | ~300 MB | Peak during UMAP; stages run sequentially    |
| **Total peak** | **~850 MB** | Safe for 2GB+ VM |

## Directory Layout

```
/opt/tee/                   # App (git clone), owned by tee
  backend/web_server.py
  tile_server.py
  deploy.sh                 # First-time setup (run once)
  restart.sh                # Start/restart services
  shutdown.sh               # Stop services
  logs/                     # Server logs (local dev only)

/home/tee/data/             # Data (auto-created), owned by tee
  mosaics/
  pyramids/
  vectors/
  embeddings/
  progress/
  passwd                    # Auth credentials
```

---

## First-Time Setup

### 1. Clone the repo

```bash
cd /opt
sudo git clone https://github.com/sk818/TEE.git tee
cd /opt/tee
```

### 2. Run deploy.sh

```bash
sudo bash deploy.sh
```

This creates the `tee` user, data directories, Python venv, and `@reboot` crontab entry. See the script for details.

### 3. Consolidate old data (if migrating)

```bash
sudo rsync -a /root/blore_data/ /home/tee/data/
sudo chown -R tee:tee /home/tee/data
```

### 4. Create admin user

```bash
sudo -u tee /opt/tee/venv/bin/python3 /opt/tee/scripts/manage_users.py add admin
```

### 5. Start services

```bash
sudo bash restart.sh
curl http://localhost:8001/health   # verify
```

---

## Day-to-Day Operations

**Update code:**
```bash
cd /opt/tee
sudo git pull
sudo bash restart.sh
```

**Stop services:**
```bash
sudo bash shutdown.sh
```

**Check status:**
```bash
bash status.sh
```

**View logs:**
```bash
tail -f /var/log/tee/web_server.log
tail -f /var/log/tee/tile_server.log
```

**Manage users:**
```bash
sudo -u tee /opt/tee/venv/bin/python3 /opt/tee/scripts/manage_users.py add alice
sudo -u tee /opt/tee/venv/bin/python3 /opt/tee/scripts/manage_users.py list
sudo -u tee /opt/tee/venv/bin/python3 /opt/tee/scripts/manage_users.py remove alice
```

---

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

    # Health checks - exempt from auth
    <Location /health>
        Require all granted
    </Location>

    # Proxy to Flask servers (localhost only)
    ProxyPreserveHost On
    ProxyRequests Off
    ProxyTimeout 300

    ProxyPass /tiles http://127.0.0.1:5125/tiles
    ProxyPassReverse /tiles http://127.0.0.1:5125/tiles
    ProxyPass /bounds http://127.0.0.1:5125/bounds
    ProxyPassReverse /bounds http://127.0.0.1:5125/bounds
    ProxyPass / http://127.0.0.1:8001/
    ProxyPassReverse / http://127.0.0.1:8001/

    # Security headers
    Header always set X-Content-Type-Options "nosniff"
    Header always set X-Frame-Options "DENY"
    Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains"
    ServerSignature Off

    # Compression
    <IfModule mod_deflate.c>
        AddOutputFilterByType DEFLATE application/json text/html text/css application/javascript
        SetEnvIfNoCase Request_URI "\.png$" no-gzip
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

---

## Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8001/tcp    # Block direct Flask access
sudo ufw deny 5125/tcp
sudo ufw enable
```

---

## Why Not Gunicorn?

Flask's built-in threaded server is sufficient for TEE because:

- **1-2 concurrent users** — this is a research tool, not a high-traffic service
- **CPU-heavy work runs as subprocesses** — pipeline stages (download, vectors, UMAP) are already memory-isolated via `subprocess.Popen`
- **Apache handles the hard parts** — TLS, HTTP/2, caching, compression
- **Memory matters** — gunicorn with 4 workers uses ~800MB idle; Flask uses ~120MB
- **No worker recycling** — gunicorn's `max_requests` can kill workers mid-pipeline

If concurrency becomes a problem (>5 users), add gunicorn back with a single line change in `restart.sh`.

---

## Design Principles

- **Fewer moving parts** — no systemd services, no multi-worker processes
- **One data directory** — `/home/tee/data`, owned by `tee`, no env var needed
- **Auto-detect environment** — `restart.sh` works on both server (tee user) and laptop (your user)
- **Memory efficient** — total peak ~850MB on a 3.8GB VM
