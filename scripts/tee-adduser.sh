#!/bin/bash
# Create TEE accounts from your laptop, one per email address.
#
# Talks to TEE's web API over HTTPS, logged in as you (an admin or enroller)
# -- the same enrolment endpoint the web UI uses, so no ssh, sudo or docker.
# The email address becomes both the login and the account's email, and
# every new account starts with the initial password (your autoresponder
# tells people what it is and to change it). The password is read from a
# private file on your machine and is deliberately NOT in this repository:
#
#   mkdir -p ~/.config/tee && echo 'the-password' > ~/.config/tee/initial_password
#   chmod 600 ~/.config/tee/initial_password
#
# An address that already has an account is skipped, never overwritten.
#
# Usage:
#   scripts/tee-adduser.sh someone@example.org [another@example.org ...]
#
# Environment (optional):
#   TEE_URL         site to talk to (default https://tee.cl.cam.ac.uk)
#   TEE_ADMIN_USER  your TEE login (prompted for if unset)
#   TEE_INITIAL_PASSWORD_FILE  (default ~/.config/tee/initial_password)

set -euo pipefail

TEE_URL="${TEE_URL:-https://tee.cl.cam.ac.uk}"
PASSWORD_FILE="${TEE_INITIAL_PASSWORD_FILE:-$HOME/.config/tee/initial_password}"

if [ $# -eq 0 ]; then
    echo "Usage: $0 email [email ...]" >&2
    exit 2
fi

if [ ! -r "$PASSWORD_FILE" ]; then
    echo "No initial password file at $PASSWORD_FILE -- see the top of $0." >&2
    exit 2
fi
INITIAL_PASSWORD=$(head -n 1 "$PASSWORD_FILE")
if [ ${#INITIAL_PASSWORD} -lt 6 ]; then
    echo "The initial password in $PASSWORD_FILE must be at least 6 characters." >&2
    exit 2
fi

admin_user="${TEE_ADMIN_USER:-}"
if [ -z "$admin_user" ]; then
    read -rp "Your TEE login: " admin_user
fi
read -rsp "Password for $admin_user: " admin_password; echo

cookies=$(mktemp)
trap 'rm -f "$cookies"' EXIT

# JSON string from stdin (handles any character in the password).
json_str() { python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'; }
# The "error" field of a JSON response, or the raw body if it isn't JSON.
json_error() { python3 -c 'import json,sys
body = sys.stdin.read()
try: print(json.loads(body).get("error") or body)
except ValueError: print(body.strip()[:200])'; }

login_body="{\"username\": $(printf '%s' "$admin_user" | json_str), \"password\": $(printf '%s' "$admin_password" | json_str)}"
response=$(curl -sS -c "$cookies" -w '\n%{http_code}' -H 'Content-Type: application/json' \
    -d "$login_body" "$TEE_URL/api/auth/login")
code=${response##*$'\n'}
if [ "$code" != "200" ]; then
    echo "Login to $TEE_URL failed (HTTP $code): $(printf '%s' "${response%$'\n'*}" | json_error)" >&2
    exit 1
fi

status=0
for raw in "$@"; do
    email=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
    if ! [[ "$email" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$ ]]; then
        echo "INVALID  $raw: not an email address" >&2
        status=1
        continue
    fi
    body="{\"username\": \"$email\", \"email\": \"$email\", \"password\": $(printf '%s' "$INITIAL_PASSWORD" | json_str)}"
    response=$(curl -sS -b "$cookies" -w '\n%{http_code}' -H 'Content-Type: application/json' \
        -d "$body" "$TEE_URL/api/enrol/create-user")
    code=${response##*$'\n'}
    case "$code" in
        200) echo "CREATED  $email" ;;
        409) echo "SKIPPED  $email: $(printf '%s' "${response%$'\n'*}" | json_error)" ;;
        *)   echo "FAILED   $email (HTTP $code): $(printf '%s' "${response%$'\n'*}" | json_error)" >&2
             status=1 ;;
    esac
done
exit $status
