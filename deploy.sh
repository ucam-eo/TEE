#!/bin/bash
##
# TEE Server Deployment Setup
#
# Run once on a new VM to set up the tee user, data directories,
# and auto-start. Not needed for local development.
#
# Usage: sudo bash deploy.sh
##

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: run with sudo"
    exit 1
fi

echo "=== TEE Server Deployment ==="
echo "  App dir: $SCRIPT_DIR"
echo ""

# --- 1. Create tee user ---
if id tee >/dev/null 2>&1; then
    echo "[1/7] User 'tee' already exists"
else
    echo "[1/7] Creating user 'tee'..."
    useradd -r -m -d /home/tee -s /sbin/nologin tee
fi

# Ensure home directory exists (may be missing if user was created without -m)
mkdir -p /home/tee
chown tee:tee /home/tee

# --- 2. Create data and cache directories ---
echo "[2/7] Setting up directories..."
sudo -u tee mkdir -p /home/tee/data /home/tee/.cache

# --- 3. Fix app directory ownership ---
echo "[3/7] Fixing app directory ownership..."
chown -R tee:tee "$SCRIPT_DIR/viewports"
mkdir -p /var/log/tee
chown tee:tee /var/log/tee

# Ensure Django session dir and secret key are owned by tee
DATA_DIR="/home/tee/data"
if [ -d "$DATA_DIR/.django_sessions" ]; then
    chown -R tee:tee "$DATA_DIR/.django_sessions"
fi
if [ -f "$DATA_DIR/.django_secret_key" ]; then
    chown tee:tee "$DATA_DIR/.django_secret_key"
    chmod 600 "$DATA_DIR/.django_secret_key"
fi

# --- 4. Remove old systemd services ---
echo "[4/7] Cleaning up old systemd services..."
for svc in tessera-web tessera-tiles; do
    if systemctl list-unit-files 2>/dev/null | grep -q "$svc"; then
        echo "  Removing $svc..."
        systemctl stop "$svc" 2>/dev/null || true
        systemctl disable "$svc" 2>/dev/null || true
        rm -f "/etc/systemd/system/$svc.service"
    fi
done
systemctl daemon-reload 2>/dev/null || true

# --- 5. Set up Python venv ---
echo "[5/7] Setting up Python venv..."
if [ ! -d "$SCRIPT_DIR/venv" ]; then
    python3 -m venv "$SCRIPT_DIR/venv"
fi
"$SCRIPT_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

# Validate Django configuration
echo "  Running Django system check..."
sudo -u tee env TEE_MODE=production "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/manage.py" check --deploy 2>&1 | sed 's/^/  /'

# --- 6. Auto-start on reboot ---
echo "[6/7] Setting up auto-start..."
CRON_LINE="@reboot cd $SCRIPT_DIR && bash restart.sh >> /var/log/tee/startup.log 2>&1"
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -qF "restart.sh"; then
    echo "  @reboot entry already exists"
else
    echo "$EXISTING
$CRON_LINE" | crontab -
    echo "  Added @reboot crontab entry"
fi

# --- 7. Check for old data to consolidate ---
echo "[7/7] Checking for old data..."
for old_dir in /root/blore_data /var/tessera_data /home/tessera/blore_data; do
    if [ -d "$old_dir" ]; then
        echo ""
        echo "  Found old data in $old_dir"
        echo "  To consolidate, run:"
        echo "    sudo rsync -a $old_dir/ /home/tee/data/"
        echo "    sudo chown -R tee:tee /home/tee/data"
        echo "    sudo rm -rf $old_dir"
    fi
done

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Consolidate old data (see above if any found)"
echo "  2. Start services: sudo bash restart.sh"
echo "  3. Verify: curl http://localhost:8001/health"
echo "  4. Add users: sudo -u tee $SCRIPT_DIR/venv/bin/python3 $SCRIPT_DIR/scripts/manage_users.py add admin"
