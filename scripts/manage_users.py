#!/usr/bin/env python3
"""
User management CLI for TEE authentication.

Usage:
    python3 scripts/manage_users.py add <username>       # prompts for password
    python3 scripts/manage_users.py remove <username>
    python3 scripts/manage_users.py list
    python3 scripts/manage_users.py check <username>     # verify password

In Docker:
    docker exec -it <container> python3 scripts/manage_users.py add admin
"""

import sys
import os
import getpass
from pathlib import Path

# Bootstrap Django so django.contrib.auth.hashers is available
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tee_project.settings.base')
import django; django.setup()
from django.contrib.auth.hashers import make_password, check_password

# Resolve data directory same way as lib/config.py
DATA_DIR = Path(os.environ.get('TEE_DATA_DIR', Path.home() / 'data'))
PASSWD_FILE = DATA_DIR / 'passwd'


def load_users():
    """Load users from passwd file. Returns dict of username -> hash."""
    users = {}
    if not PASSWD_FILE.exists():
        return users
    for line in PASSWD_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if ':' not in line:
            continue
        username, hashed = line.split(':', 1)
        username = username.strip()
        hashed = hashed.strip()
        if username and hashed:
            users[username] = hashed
    return users


def save_users(users):
    """Write users dict back to passwd file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{username}:{hashed}" for username, hashed in sorted(users.items())]
    PASSWD_FILE.write_text('\n'.join(lines) + '\n')
    PASSWD_FILE.chmod(0o600)


def cmd_add(username):
    """Add or update a user."""
    password = getpass.getpass(f"Password for {username}: ")
    confirm = getpass.getpass(f"Confirm password: ")
    if password != confirm:
        print("Passwords do not match.")
        sys.exit(1)
    if len(password) < 4:
        print("Password must be at least 4 characters.")
        sys.exit(1)

    users = load_users()
    hashed = make_password(password)
    action = "Updated" if username in users else "Added"
    users[username] = hashed
    save_users(users)
    print(f"{action} user: {username}")


def cmd_remove(username):
    """Remove a user."""
    users = load_users()
    if username not in users:
        print(f"User not found: {username}")
        sys.exit(1)
    del users[username]
    if users:
        save_users(users)
    else:
        # No users left — remove passwd file to disable auth
        PASSWD_FILE.unlink(missing_ok=True)
    print(f"Removed user: {username}")


def cmd_list():
    """List all users."""
    users = load_users()
    if not users:
        print("No users configured (auth disabled).")
        return
    print(f"{len(users)} user(s):")
    for username in sorted(users):
        print(f"  {username}")


def cmd_check(username):
    """Verify a user's password."""
    users = load_users()
    if username not in users:
        print(f"User not found: {username}")
        sys.exit(1)
    password = getpass.getpass(f"Password for {username}: ")
    if check_password(password, users[username]):
        print("Password correct.")
    else:
        print("Password incorrect.")
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: manage_users.py <add|remove|list|check> [username]")
        sys.exit(1)

    command = sys.argv[1]

    if command == 'list':
        cmd_list()
    elif command in ('add', 'remove', 'check'):
        if len(sys.argv) < 3:
            print(f"Usage: manage_users.py {command} <username>")
            sys.exit(1)
        username = sys.argv[2]
        if command == 'add':
            cmd_add(username)
        elif command == 'remove':
            cmd_remove(username)
        elif command == 'check':
            cmd_check(username)
    else:
        print(f"Unknown command: {command}")
        print("Commands: add, remove, list, check")
        sys.exit(1)


if __name__ == '__main__':
    main()
