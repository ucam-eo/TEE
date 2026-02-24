#!/bin/bash
##
# Restart TEE Web Server
#
# Uses Django + waitress.
# Auto-detects: if 'tee' system user exists, runs as tee (server mode).
# Otherwise runs as the current user (local development).
##

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="$SCRIPT_DIR/venv/bin/python3"

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
lsof -ti:8001 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 1

# Set host: localhost for server (behind Apache), all interfaces for local dev
if [ "$MODE" = "server" ]; then
    HOST="127.0.0.1"
else
    HOST="0.0.0.0"
fi

# Start web server
echo "  Web server on $HOST:8001"
if [ "$MODE" = "local" ]; then
    $PYTHON -m waitress --host="$HOST" --port=8001 --threads=16 tee_project.wsgi:application \
        >> "$LOG_DIR/web_server.log" 2>&1 &
else
    $RUN env TEE_MODE=production \
        $PYTHON -m waitress --host="$HOST" --port=8001 --threads=16 tee_project.wsgi:application \
        >> "$LOG_DIR/web_server.log" 2>&1 &
fi
WEB_PID=$!

sleep 2

# Verify
if ps -p $WEB_PID > /dev/null 2>&1; then
    echo "  Web server OK (PID: $WEB_PID)"
else
    echo "  Web server FAILED -- check $LOG_DIR/web_server.log"
    exit 1
fi

echo ""
echo "TEE running at http://$HOST:8001"
echo "Logs: $LOG_DIR/"
