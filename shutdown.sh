#!/bin/bash
##
# Shut down TEE web server.
##

echo "Shutting down TEE services..."

STOPPED=false

for pattern in "python.*manage.py.*runserver" "python.*waitress.*tee_project"; do
    if pkill -f "$pattern" 2>/dev/null; then
        echo "  Stopped: $pattern"
        STOPPED=true
    fi
done

if lsof -ti:8001 2>/dev/null | xargs kill -9 2>/dev/null; then
    echo "  Freed port 8001"
    STOPPED=true
fi

if [ "$STOPPED" = true ]; then
    echo "All services stopped."
else
    echo "No running services found."
fi
