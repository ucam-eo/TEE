#!/bin/bash
##
# Shut down all TEE services (web server + tile server).
##

echo "Shutting down TEE services..."

STOPPED=false

for pattern in "python.*manage.py.*runserver" "python.*waitress.*tee_project" \
               "python.*tile_server.py" "gunicorn.*tile_server"; do
    if pkill -f "$pattern" 2>/dev/null; then
        echo "  Stopped: $pattern"
        STOPPED=true
    fi
done

for port in 8001 5125; do
    if lsof -ti:$port 2>/dev/null | xargs kill -9 2>/dev/null; then
        echo "  Freed port $port"
        STOPPED=true
    fi
done

if [ "$STOPPED" = true ]; then
    echo "All services stopped."
else
    echo "No running services found."
fi
