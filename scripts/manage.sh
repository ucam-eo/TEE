#!/bin/bash
# TEE container management script.
# Run on the host (outside Docker) to manage users, quotas, and updates.
#
# Setup (one-time):
#   docker cp tee:/app/scripts/manage.sh ~/manage.sh && chmod +x ~/manage.sh
#
# Usage:
#   sudo ./manage.sh

set -euo pipefail

CONTAINER="tee"
IMAGE="sk818/tee:stable"

# ── helpers ──────────────────────────────────────────────────────────────────

die()  { echo "Error: $*" >&2; }

# ── commands ─────────────────────────────────────────────────────────────────

cmd_list() {
    docker exec "$CONTAINER" python3 manage.py tee_listusers
}

cmd_add() {
    read -rp "Username: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
    fi

    read -rp "Admin? (y/N): " is_admin
    admin_flag=""
    if [[ "$is_admin" =~ ^[Yy]$ ]]; then
        admin_flag="--admin"
    fi

    # Read password (no echo)
    read -rsp "Password: " password; echo
    read -rsp "Confirm:  " confirm; echo
    if [ "$password" != "$confirm" ]; then
        die "Passwords do not match"; return 1
    fi
    if [ ${#password} -lt 4 ]; then
        die "Password must be at least 4 characters"; return 1
    fi

    docker exec -e PASSWORD="$password" "$CONTAINER" python3 manage.py tee_adduser "$username" $admin_flag
}

cmd_remove() {
    cmd_list

    read -rp "Username to remove: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
    fi

    docker exec "$CONTAINER" python3 manage.py tee_removeuser "$username"
}

cmd_quota() {
    cmd_list

    read -rp "Username: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
    fi

    read -rp "Quota (e.g. 4G, 512M, or MB): " raw

    # Parse quota
    if [[ "$raw" =~ ^([0-9]+)[Gg]$ ]]; then
        quota_mb=$(( ${BASH_REMATCH[1]} * 1024 ))
    elif [[ "$raw" =~ ^([0-9]+)[Mm]$ ]]; then
        quota_mb=${BASH_REMATCH[1]}
    elif [[ "$raw" =~ ^[0-9]+$ ]]; then
        quota_mb="$raw"
    else
        die "Invalid quota '$raw' (examples: 4G, 512M, 4096)"; return 1
    fi

    if [ "$quota_mb" -eq 0 ]; then
        die "Quota must be greater than zero"; return 1
    fi

    docker exec "$CONTAINER" python3 manage.py tee_setquota "$username" "$quota_mb"
}

cmd_grant_enroller() {
    cmd_list

    read -rp "Username to grant enroller: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
    fi

    docker exec "$CONTAINER" python3 manage.py tee_setenroller "$username"
}

cmd_revoke_enroller() {
    cmd_list

    read -rp "Username to revoke enroller: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
    fi

    docker exec "$CONTAINER" python3 manage.py tee_setenroller "$username" --revoke
}

cmd_update() {
    echo "Pulling $IMAGE..."
    docker pull "$IMAGE"
    echo ""
    echo "Restarting container..."
    docker stop "$CONTAINER" 2>/dev/null || true
    docker rm "$CONTAINER" 2>/dev/null || true
    docker run -d \
        --name "$CONTAINER" \
        --restart unless-stopped \
        -p 8001:8001 \
        -e TEE_HTTPS=1 \
        -v /data:/data \
        -v /data/viewports:/app/viewports \
        "$IMAGE"
    echo ""
    echo "Waiting for health check..."
    sleep 3
    if curl -sf http://localhost:8001/health > /dev/null; then
        echo "Container is healthy."
    else
        echo "Warning: health check failed — check 'docker logs $CONTAINER'"
    fi
}

# ── menu ─────────────────────────────────────────────────────────────────────

while true; do
    echo ""
    echo "TEE Management"
    echo "  1) List users"
    echo "  2) Add user"
    echo "  3) Remove user"
    echo "  4) Set quota"
    echo "  5) Grant enroller"
    echo "  6) Revoke enroller"
    echo "  7) Update container"
    echo "  8) Exit"
    echo ""
    read -rp "Choice: " choice

    case "$choice" in
        1) cmd_list ;;
        2) cmd_add ;;
        3) cmd_remove ;;
        4) cmd_quota ;;
        5) cmd_grant_enroller ;;
        6) cmd_revoke_enroller ;;
        7) cmd_update ;;
        8) exit 0 ;;
        *) echo "Invalid choice" ;;
    esac
done
