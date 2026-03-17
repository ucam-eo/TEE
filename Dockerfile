# TEE (Tessera Embeddings Explorer) Docker Image
#
# Build: docker build -t tee .
# Run:   docker run -p 8001:8001 -v ~/tee_data:/data tee
#
# Environment variables:
#   TEE_DATA_DIR - Data directory (default: /data)
#   TEE_APP_DIR  - Application directory (default: /app)

FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.0

LABEL version="3.1"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3-pip \
    python3-venv \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python packages (ignore system numpy to avoid conflicts)
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed numpy -r requirements.txt

# Copy application code
COPY . .

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
    exec python3 -m waitress --host=0.0.0.0 --port=8001 tee_project.wsgi:application
