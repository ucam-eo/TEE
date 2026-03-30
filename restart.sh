#!/bin/bash
##
# Restart TEE Services
#
# Local mode:  Django on :8002 (data) + tee-compute on :8001 (eval + proxy)
# Server mode: Django on :8001 (behind Apache, no eval — eval via user's tee-compute)
#
# Auto-detects: if 'tee' system user exists, runs as tee (server mode).
# Otherwise runs as the current user (local development).
##

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/venv/bin/python3"
TEE_COMPUTE="$SCRIPT_DIR/venv/bin/tee-compute"

# Auto-detect run mode: server (tee user) vs local (current user)
if id tee >/dev/null 2>&1; then
    RUN="sudo -u tee"
    MODE="server"
    echo "TEE server mode (running as tee user)"
else
    RUN=""
    MODE="local"
    echo "TEE local mode (running as $(whoami))"
fi

# Logs: /var/log/tee on server, ./logs locally
if [ "$MODE" = "server" ]; then
    LOG_DIR="/var/log/tee"
else
    LOG_DIR="$SCRIPT_DIR/logs"
fi
mkdir -p "$LOG_DIR"

echo "Shutting down existing services..."

# Kill any existing TEE processes
pkill -f "python.*waitress.*tee_project" 2>/dev/null || true
pkill -f "tee-compute" 2>/dev/null || true
lsof -ti:8001 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:8002 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

if [ "$MODE" = "server" ]; then
    # Server mode: Django on :8001 (behind Apache), no tee-compute
    # Users run their own tee-compute pointing --hosted at this server
    HOST="127.0.0.1"
    echo "  Django on $HOST:8001"
    $RUN env TEE_MODE=production \
        $PYTHON -m waitress --host="$HOST" --port=8001 --threads=16 tee_project.wsgi:application \
        >> "$LOG_DIR/web_server.log" 2>&1 &
    WEB_PID=$!

    sleep 2
    if ps -p $WEB_PID > /dev/null 2>&1; then
        echo "  Django OK (PID: $WEB_PID)"
    else
        echo "  Django FAILED -- check $LOG_DIR/web_server.log"
        exit 1
    fi

    echo ""
    echo "TEE running at http://$HOST:8001"
else
    # Local mode: Django on :8001, tee-compute on :8002
    # Django forwards /api/evaluation/* to tee-compute
    HOST="0.0.0.0"
    COMPUTE_PORT=8002

    echo "  Django on $HOST:8001"
    $PYTHON -m waitress --host="$HOST" --port=8001 --threads=16 tee_project.wsgi:application \
        >> "$LOG_DIR/web_server.log" 2>&1 &
    DJANGO_PID=$!

    sleep 2
    if ps -p $DJANGO_PID > /dev/null 2>&1; then
        echo "  Django OK (PID: $DJANGO_PID)"
    else
        echo "  Django FAILED -- check $LOG_DIR/web_server.log"
        exit 1
    fi

    echo "  tee-compute on $HOST:$COMPUTE_PORT (eval)"
    $TEE_COMPUTE --hosted "http://localhost:8001" --host "$HOST" --port $COMPUTE_PORT \
        >> "$LOG_DIR/compute_server.log" 2>&1 &
    COMPUTE_PID=$!

    sleep 2
    if ps -p $COMPUTE_PID > /dev/null 2>&1; then
        echo "  tee-compute OK (PID: $COMPUTE_PID)"
    else
        echo "  tee-compute FAILED -- check $LOG_DIR/compute_server.log"
        exit 1
    fi

    echo ""
    echo "TEE running at http://localhost:8001"
fi

echo "Logs: $LOG_DIR/"
