#!/bin/bash

# TEE Project Status Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve data directory (same logic as lib/config.py)
if [ -n "$TEE_DATA_DIR" ]; then
    DATA_DIR="$TEE_DATA_DIR"
elif id tee >/dev/null 2>&1; then
    DATA_DIR="/home/tee/data"
else
    DATA_DIR="$HOME/data"
fi

echo "TEE Project Status"
echo "========================"
echo ""

# Git status
echo "Git Repository:"
echo "  Branch: $(cd "$SCRIPT_DIR" && git branch --show-current 2>/dev/null || echo 'N/A')"
echo "  Status: $(cd "$SCRIPT_DIR" && if git diff --quiet && git diff --cached --quiet; then echo 'Clean'; else echo 'Changes pending'; fi 2>/dev/null)"
echo ""

# Data directories
echo "Data ($DATA_DIR):"
if [ -d "$DATA_DIR" ]; then
    echo "  mosaics:       $(du -sh "$DATA_DIR/mosaics" 2>/dev/null | cut -f1 || echo "not found")"
    echo "  embeddings:    $(du -sh "$DATA_DIR/embeddings" 2>/dev/null | cut -f1 || echo "not found")"
    echo "  pyramids:      $(du -sh "$DATA_DIR/pyramids" 2>/dev/null | cut -f1 || echo "not found")"
    echo "  vectors:       $(du -sh "$DATA_DIR/vectors" 2>/dev/null | cut -f1 || echo "not found")"
else
    echo "  Data directory not found: $DATA_DIR"
fi
echo ""

# Disk space
echo "Disk Space:"
df -h "$DATA_DIR" 2>/dev/null | tail -1 | awk '{printf "  Used: %s / %s (%s available)\n", $3, $2, $4}'
echo ""

# Services
echo "Services:"
if pgrep -f "python.*waitress.*tee_project\|python.*manage.py.*runserver" >/dev/null 2>&1; then
    echo "  Web server:  running"
else
    echo "  Web server:  stopped"
fi
echo ""
