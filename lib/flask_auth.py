"""
Flask auth hook for tile_server.py.

Thin wrapper that reuses the passwd-file logic from api.middleware
but exposes it as a Flask before_request hook + Flask session routes.
"""

import os
import secrets
import logging
from pathlib import Path

from flask import request, session, jsonify, redirect

from api.middleware import (
    auth_enabled,
    _passwd_file,
    _is_public_path,
    _is_write_endpoint,
)

logger = logging.getLogger(__name__)


def _require_auth():
    """before_request hook: enforce authentication when enabled."""
    if not auth_enabled():
        return
    if _is_public_path(request.path):
        return
    if session.get('user'):
        return
    if _is_write_endpoint(request.path):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Authentication required'}), 401
        else:
            return redirect('/login.html')


def init_auth(app, data_dir):
    """Initialize authentication on a Flask app (for tile_server.py)."""
    data_dir = Path(data_dir)

    # Persistent secret key so sessions survive restarts
    secret_key_file = data_dir / '.flask_secret_key'
    if secret_key_file.exists():
        app.secret_key = secret_key_file.read_text().strip()
    else:
        key = secrets.token_hex(32)
        data_dir.mkdir(parents=True, exist_ok=True)
        secret_key_file.write_text(key)
        secret_key_file.chmod(0o600)
        app.secret_key = key

    app.config['SESSION_COOKIE_NAME'] = 'tee_session'
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('TEE_HTTPS', '') in ('1', 'true')

    app.before_request(_require_auth)

    logger.info(f"Auth initialized for Flask app (passwd file: {_passwd_file})")
