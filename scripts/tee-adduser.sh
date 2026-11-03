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
#   scripts/tee-adduser.sh [--quota SIZE] someone@example.org [another@example.org ...]
#
#   --quota SIZE   disk quota for the new accounts: 512M, 1G or a number of MB
#                  (at least 100 MB). Without it, the site's default for new
#                  accounts applies. Existing accounts are never changed --
#                  use manage.sh "Set quota" for those.
#
# Environment (optional):
#   TEE_URL         site to talk to (default https://tee.cl.cam.ac.uk)
#   TEE_ADMIN_USER  your TEE login (prompted for if unset)
#   TEE_INITIAL_PASSWORD_FILE  (default ~/.config/tee/initial_password)

set -euo pipefail

TEE_URL="${TEE_URL:-https://tee.cl.cam.ac.uk}"
PASSWORD_FILE="${TEE_INITIAL_PASSWORD_FILE:-$HOME/.config/tee/initial_password}"

usage() { echo "Usage: $0 [--quota SIZE] email [email ...]   (SIZE: 512M, 1G or MB)" >&2; exit 2; }

quota_mb=""
emails=()
while [ $# -gt 0 ]; do
    case "$1" in
        --quota) [ $# -ge 2 ] || usage; quota_raw="$2"; shift 2 ;;
        --quota=*) quota_raw="${1#--quota=}"; shift ;;
        -*) usage ;;
        *) emails+=("$1"); shift ;;
    esac
done
[ ${#emails[@]} -gt 0 ] || usage

if [ -n "${quota_raw:-}" ]; then
    if [[ "$quota_raw" =~ ^([0-9]+)[Gg]$ ]]; then
        quota_mb=$(( BASH_REMATCH[1] * 1024 ))
    elif [[ "$quota_raw" =~ ^([0-9]+)[Mm]?$ ]]; then
        quota_mb=$(( BASH_REMATCH[1] ))
    else
        echo "Invalid quota '$quota_raw' (examples: 512M, 1G, 2048)" >&2; exit 2
    fi
    if [ "$quota_mb" -lt 100 ]; then
        echo "Quota must be at least 100 MB (got $quota_mb MB)" >&2; exit 2
    fi
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
for raw in "${emails[@]}"; do
    email=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')
    if ! [[ "$email" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$ ]]; then
        echo "INVALID  $raw: not an email address" >&2
        status=1
        continue
    fi
    body="{\"username\": \"$email\", \"email\": \"$email\", \"password\": $(printf '%s' "$INITIAL_PASSWORD" | json_str)${quota_mb:+, \"quota_mb\": $quota_mb}}"
    response=$(curl -sS -b "$cookies" -w '\n%{http_code}' -H 'Content-Type: application/json' \
        -d "$body" "$TEE_URL/api/enrol/create-user")
    code=${response##*$'\n'}
    case "$code" in
        200) echo "CREATED  $email ($(printf '%s' "${response%$'\n'*}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("quota_mb", "?"))') MB)" ;;
        409) echo "SKIPPED  $email: $(printf '%s' "${response%$'\n'*}" | json_error)" ;;
        *)   echo "FAILED   $email (HTTP $code): $(printf '%s' "${response%$'\n'*}" | json_error)" >&2
             status=1 ;;
    esac
done
exit $status
