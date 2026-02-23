"""Auth views: login, logout, change-password, status."""

import time
import logging

import bcrypt
from django.http import JsonResponse

from api.middleware import auth_enabled, check_credentials, _passwd_file
from api.helpers import parse_json_body

logger = logging.getLogger(__name__)


def auth_login(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    data, err = parse_json_body(request)
    if err:
        return err

    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return JsonResponse({'success': False, 'error': 'Username and password required'}, status=400)

    if check_credentials(username, password):
        request.session['user'] = username
        return JsonResponse({'success': True, 'user': username})

    # Brute-force delay
    time.sleep(0.5)
    return JsonResponse({'success': False, 'error': 'Invalid credentials'}, status=401)


def auth_logout(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    request.session.pop('user', None)
    return JsonResponse({'success': True})


def auth_change_password(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    user = request.session.get('user')
    if not user:
        return JsonResponse({'success': False, 'error': 'Not logged in'}, status=401)

    data, err = parse_json_body(request)
    if err:
        return err

    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not current_password or not new_password:
        return JsonResponse({'success': False, 'error': 'Current and new password required'}, status=400)

    if len(new_password) < 6:
        return JsonResponse({'success': False, 'error': 'New password must be at least 6 characters'}, status=400)

    if not check_credentials(user, current_password):
        time.sleep(0.5)
        return JsonResponse({'success': False, 'error': 'Current password is incorrect'}, status=403)

    # Hash new password and update passwd file in-place
    new_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    try:
        lines = _passwd_file.read_text().splitlines()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and ':' in stripped:
                uname = stripped.split(':', 1)[0].strip()
                if uname == user:
                    new_lines.append(f'{user}:{new_hash}')
                    continue
            new_lines.append(line)
        _passwd_file.write_text('\n'.join(new_lines) + '\n')
    except OSError as e:
        logger.error(f"Error updating passwd file: {e}")
        return JsonResponse({'success': False, 'error': 'Failed to update password'}, status=500)

    # Clear mtime cache so the change is picked up immediately
    import api.middleware
    api.middleware._passwd_mtime = 0

    logger.info(f"Password changed for user: {user}")
    return JsonResponse({'success': True})


def auth_status(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    enabled = auth_enabled()
    user = request.session.get('user') if enabled else None
    return JsonResponse({
        'auth_enabled': enabled,
        'logged_in': user is not None,
        'user': user,
    })
