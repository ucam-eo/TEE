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
PASSWD_FILE="/data/passwd"
DEFAULT_QUOTA_MB=2048

# ── helpers ──────────────────────────────────────────────────────────────────

die()  { echo "Error: $*" >&2; }

# Generate bcrypt hash using the TEE Docker image (no host dependencies)
bcrypt_hash() {
    local password="$1"
    docker run --rm -e PASSWORD="$password" "$IMAGE" python3 -c \
        "import bcrypt,os; print(bcrypt.hashpw(os.environ['PASSWORD'].encode(), bcrypt.gensalt()).decode())"
}

user_exists() {
    [ -f "$PASSWD_FILE" ] && grep -q "^${1}:" "$PASSWD_FILE"
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_list() {
    if [ ! -f "$PASSWD_FILE" ]; then
        echo "No users configured (auth disabled)."
        return
    fi
    echo ""
    printf "  %-20s %s\n" "USER" "QUOTA"
    printf "  %-20s %s\n" "----" "-----"
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == \#* ]] && continue
        [[ "$line" != *:* ]] && continue
        user=$(echo "$line" | cut -d: -f1)
        quota=$(echo "$line" | cut -d: -f3)
        if [ "$user" = "admin" ]; then
            printf "  %-20s %s\n" "$user" "unlimited"
        elif [ -n "$quota" ]; then
            printf "  %-20s %s\n" "$user" "$((quota / 1024))G (${quota} MB)"
        else
            printf "  %-20s %s\n" "$user" "default (${DEFAULT_QUOTA_MB} MB)"
        fi
    done < "$PASSWD_FILE"
    echo ""
}

cmd_add() {
    read -rp "Username: " username
    if [ -z "$username" ]; then
        die "Username cannot be empty"; return 1
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

    echo "Generating hash..."
    hash=$(bcrypt_hash "$password")

    if user_exists "$username"; then
        # Preserve existing quota
        quota=$(grep "^${username}:" "$PASSWD_FILE" | cut -d: -f3)
        tmpfile=$(mktemp /data/.passwd.XXXXXX)
        while IFS= read -r line || [ -n "$line" ]; do
            if [[ "$line" == "${username}:"* ]]; then
                if [ -n "$quota" ]; then
                    echo "${username}:${hash}:${quota}"
                else
                    echo "${username}:${hash}"
                fi
            else
                echo "$line"
            fi
        done < "$PASSWD_FILE" > "$tmpfile"
        mv "$tmpfile" "$PASSWD_FILE"
        chmod 644 "$PASSWD_FILE"
        echo "Updated user: $username"
    else
        echo "${username}:${hash}" >> "$PASSWD_FILE"
        chmod 644 "$PASSWD_FILE"
        echo "Added user: $username"
    fi
}

cmd_remove() {
    if [ ! -f "$PASSWD_FILE" ]; then
        die "No users configured"; return 1
    fi
    cmd_list

    read -rp "Username to remove: " username
    if ! user_exists "$username"; then
        die "User '$username' not found"; return 1
    fi

    tmpfile=$(mktemp /data/.passwd.XXXXXX)
    grep -v "^${username}:" "$PASSWD_FILE" > "$tmpfile" || true
    if [ -s "$tmpfile" ]; then
        mv "$tmpfile" "$PASSWD_FILE"
        chmod 644 "$PASSWD_FILE"
    else
        rm "$tmpfile" "$PASSWD_FILE"
        echo "(No users left — auth disabled)"
    fi
    echo "Removed user: $username"
}

cmd_quota() {
    if [ ! -f "$PASSWD_FILE" ]; then
        die "No users configured"; return 1
    fi
    cmd_list

    read -rp "Username: " username
    if ! user_exists "$username"; then
        die "User '$username' not found"; return 1
    fi
    if [ "$username" = "admin" ]; then
        die "Admin always has unlimited quota"; return 1
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

    tmpfile=$(mktemp /data/.passwd.XXXXXX)
    while IFS= read -r line || [ -n "$line" ]; do
        if [[ "$line" == "${username}:"* ]]; then
            hash=$(echo "$line" | cut -d: -f2)
            echo "${username}:${hash}:${quota_mb}"
        else
            echo "$line"
        fi
    done < "$PASSWD_FILE" > "$tmpfile"
    mv "$tmpfile" "$PASSWD_FILE"
    chmod 644 "$PASSWD_FILE"
    echo "Set quota for '$username' to ${quota_mb} MB ($((quota_mb / 1024))G)"
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
    echo "  5) Update container"
    echo "  6) Exit"
    echo ""
    read -rp "Choice: " choice

    case "$choice" in
        1) cmd_list ;;
        2) cmd_add ;;
        3) cmd_remove ;;
        4) cmd_quota ;;
        5) cmd_update ;;
        6) exit 0 ;;
        *) echo "Invalid choice" ;;
    esac
done
