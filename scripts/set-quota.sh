#!/bin/bash
# Set per-user disk quota in the passwd file.
# Usage: sudo ./set-quota.sh <username> <quota>
# Examples: sudo ./set-quota.sh user2 4G
#           sudo ./set-quota.sh user2 512M
#           sudo ./set-quota.sh user2 4096   (bare number = MB)
#
# The passwd file format is:  username:bcrypt_hash[:quota_mb]
# If the quota field is absent the app defaults to 2 GB.

set -euo pipefail

PASSWD_FILE="/data/passwd"

if [ $# -ne 2 ]; then
    echo "Usage: $0 <username> <quota>"
    echo "  quota: 4G, 512M, or bare number in MB"
    exit 1
fi

USERNAME="$1"
RAW="$2"

# Parse quota: accept 4G / 512M / bare MB
if [[ "$RAW" =~ ^([0-9]+)[Gg]$ ]]; then
    QUOTA_MB=$(( ${BASH_REMATCH[1]} * 1024 ))
elif [[ "$RAW" =~ ^([0-9]+)[Mm]$ ]]; then
    QUOTA_MB=${BASH_REMATCH[1]}
elif [[ "$RAW" =~ ^[0-9]+$ ]]; then
    QUOTA_MB="$RAW"
else
    echo "Error: invalid quota '$RAW' (examples: 4G, 512M, 4096)"
    exit 1
fi

if [ "$QUOTA_MB" -eq 0 ]; then
    echo "Error: quota must be greater than zero"
    exit 1
fi

if [ ! -f "$PASSWD_FILE" ]; then
    echo "Error: $PASSWD_FILE not found"
    exit 1
fi

# Check user exists
if ! grep -q "^${USERNAME}:" "$PASSWD_FILE"; then
    echo "Error: user '$USERNAME' not found in $PASSWD_FILE"
    exit 1
fi

# Build updated file (temp file on same filesystem for atomic mv)
TMPFILE=$(mktemp /data/.passwd.XXXXXX)
while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" == "${USERNAME}:"* ]]; then
        HASH=$(echo "$line" | cut -d: -f2)
        echo "${USERNAME}:${HASH}:${QUOTA_MB}"
    else
        echo "$line"
    fi
done < "$PASSWD_FILE" > "$TMPFILE"

mv "$TMPFILE" "$PASSWD_FILE"
chmod 644 "$PASSWD_FILE"

echo "Set quota for '$USERNAME' to ${QUOTA_MB} MB ($((QUOTA_MB / 1024))G)"
