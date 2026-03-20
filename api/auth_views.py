"""Auth views: login, logout, change-password, status."""

import time
import logging

from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse

from api.middleware import auth_enabled
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

    user = authenticate(request, username=username, password=password)
    if user is not None:
        login(request, user)
        request.session.cycle_key()  # prevent session fixation
        return JsonResponse({'success': True, 'user': user.username})

    # Brute-force delay
    time.sleep(0.5)
    return JsonResponse({'success': False, 'error': 'Invalid credentials'}, status=401)


def auth_logout(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    logout(request)
    return JsonResponse({'success': True})


def auth_change_password(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    if not request.user.is_authenticated:
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

    if not request.user.check_password(current_password):
        time.sleep(0.5)
        return JsonResponse({'success': False, 'error': 'Current password is incorrect'}, status=403)

    request.user.set_password(new_password)
    request.user.save()

    # Re-authenticate so the session stays valid after password change
    login(request, request.user)

    logger.info(f"Password changed for user: {request.user.username}")
    return JsonResponse({'success': True})


def auth_status(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    enabled = auth_enabled()
    logged_in = request.user.is_authenticated if enabled else False
    user = request.user.username if logged_in else None
    is_admin = request.user.is_superuser if logged_in else False
    is_enroller = False
    if logged_in and not is_admin:
        try:
            is_enroller = request.user.profile.can_enrol
        except Exception:
            pass
    resp = {
        'auth_enabled': enabled,
        'logged_in': logged_in,
        'user': user,
    }
    if logged_in:
        resp['is_admin'] = is_admin
        resp['is_enroller'] = is_admin or is_enroller
    return JsonResponse(resp)
