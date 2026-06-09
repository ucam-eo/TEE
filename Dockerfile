# TEE (Tessera Embeddings Explorer) Docker Image
#
# Build: docker build -t tee .
# Run:   docker run -p 8001:8001 -v ~/tee_data:/data tee
#
# Environment variables:
#   TEE_DATA_DIR - Data directory (default: /data)
#   TEE_APP_DIR  - Application directory (default: /app)

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.0

# Static OCI label; the authoritative runtime version is the GIT_VERSION
# build arg baked into /app/VERSION below (git describe, e.g. v1.2.1-...).
LABEL org.opencontainers.image.title="TEE" \
      org.opencontainers.image.source="https://github.com/ucam-eo/TEE"

WORKDIR /app

# Install system dependencies. git is required because tessera-vq is pinned
# as a git+https requirement in requirements.txt and pip shells out to git
# to clone it.
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# numpy first, on its own, with --ignore-installed: the gdal base image's
# apt python3-numpy lacks pip RECORD files (Debian-managed), so pip can't
# uninstall it. When something else in requirements.txt later resolves to a
# newer numpy, pip aborts mid-install. Doing numpy as its own pip call with
# --ignore-installed sidesteps that — the apt numpy stays in
# /usr/lib/python3/dist-packages where it can't conflict, and the pip-
# installed numpy lands in /usr/local/lib/python3.12/dist-packages where it
# takes import priority. Without this preamble we'd be back to either
# global --ignore-installed (wasteful) or build failure.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages --ignore-installed numpy

# Install Python packages. The gdal base image doesn't ship most of the
# geo-Python wheels (rasterio, scipy, sklearn, xgboost, umap-learn, zarr,
# dask, …), so pip downloads them on every cache-busted rebuild. The
# BuildKit cache mount keeps the wheel cache on the builder between builds
# — subsequent rebuilds (different requirements.txt content) re-download
# only the diff, not the whole stack. Requires BuildKit, which scripts/
# build-image.sh uses via `docker buildx`.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install --break-system-packages -r requirements.txt

# Copy application code
COPY . .

# tessera-eval is installed from its own repo via requirements.txt (git pin),
# so there is no local package install step here any more.

# Bake git version (passed as build arg since .git is excluded)
ARG GIT_VERSION=unknown
RUN echo "$GIT_VERSION" > /app/VERSION

# Create data directory
RUN mkdir -p /data

# Set environment variables
ENV TEE_DATA_DIR=/data
ENV TEE_APP_DIR=/app

# Expose port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Run migrations, collect admin static files, migrate passwd, then start server
ENV TEE_MODE=production
CMD python3 manage.py migrate --noinput && \
    python3 manage.py collectstatic --noinput && \
    python3 manage.py migrate_passwd --auto && \
    exec python3 -m waitress --host=0.0.0.0 --port=8001 --threads=16 --channel-timeout=7200 tee_project.wsgi:application
