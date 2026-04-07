"""Delegated enrolment: enrollers can create/manage user accounts."""

import json
import logging

from django.contrib.auth.models import User
from django.http import JsonResponse

from api.models import UserProfile

logger = logging.getLogger(__name__)

DEFAULT_QUOTA_MB = 2048


def _is_enroller(user):
    """Check if user can enrol others (superuser or has can_enrol flag)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        return user.profile.can_enrol
    except UserProfile.DoesNotExist:
        return False


def _can_manage(enroller, target_user):
    """Check if enroller can manage the target user."""
    if enroller.is_superuser:
        return True
    try:
        return target_user.profile.created_by == enroller
    except UserProfile.DoesNotExist:
        return False


def create_enrolled_user(request):
    """Create a new user account (enroller or admin only)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _is_enroller(request.user):
        return JsonResponse({'error': 'Enroller or admin privileges required'}, status=403)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    username = body.get('username', '').strip()
    email = body.get('email', '').strip()
    password = body.get('password', '')
    quota_mb = int(body.get('quota_mb', DEFAULT_QUOTA_MB))

    if not username:
        return JsonResponse({'error': 'Username is required'}, status=400)
    if len(username) > 64:
        return JsonResponse({'error': 'Username must be 64 characters or fewer'}, status=400)
    if not username.isalnum() and not all(c.isalnum() or c in '_-' for c in username):
        return JsonResponse({'error': 'Username must be alphanumeric (with _ or -)'}, status=400)
    if not password or len(password) < 6:
        return JsonResponse({'error': 'Password must be at least 6 characters'}, status=400)

    if User.objects.filter(username=username).exists():
        return JsonResponse({'error': f'Username "{username}" already exists'}, status=409)

    # Non-admin enrollers have a cap on how many users they can create
    if not request.user.is_superuser:
        enrolled_count = UserProfile.objects.filter(created_by=request.user).count()
        if enrolled_count >= 50:
            return JsonResponse({'error': 'Enrolment limit reached (50 users)'}, status=403)

    user = User.objects.create_user(username=username, password=password, email=email)
    UserProfile.objects.update_or_create(
        user=user,
        defaults={
            'quota_mb': quota_mb,
            'can_enrol': False,
            'created_by': request.user,
        }
    )

    logger.info(f"[ENROL] {request.user.username} created user '{username}' (quota={quota_mb}MB)")
    return JsonResponse({
        'success': True,
        'username': username,
        'quota_mb': quota_mb,
    })


def list_enrolled_users(request):
    """List enrolled users. Enrollers see their own; admin sees all."""
    if not _is_enroller(request.user):
        return JsonResponse({'error': 'Enroller or admin privileges required'}, status=403)

    if request.user.is_superuser:
        profiles = UserProfile.objects.select_related('user', 'created_by').all()
    else:
        profiles = UserProfile.objects.select_related('user', 'created_by').filter(
            created_by=request.user
        )

    users = []
    for p in profiles:
        users.append({
            'username': p.user.username,
            'email': p.user.email or '',
            'quota_mb': p.quota_mb,
            'can_enrol': p.can_enrol,
            'is_active': p.user.is_active,
            'is_admin': p.user.is_superuser,
            'created_by': p.created_by.username if p.created_by else None,
        })

    return JsonResponse({'success': True, 'users': users})


def disable_enrolled_user(request):
    """Disable/enable a user account (enroller manages own enrollees, admin manages all)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    if not _is_enroller(request.user):
        return JsonResponse({'error': 'Enroller or admin privileges required'}, status=403)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    username = body.get('username', '').strip()
    enable = body.get('enable', False)

    if not username:
        return JsonResponse({'error': 'Username is required'}, status=400)

    try:
        target_user = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({'error': f'User "{username}" not found'}, status=404)

    if target_user.is_superuser:
        return JsonResponse({'error': 'Cannot disable admin accounts'}, status=403)

    if not _can_manage(request.user, target_user):
        return JsonResponse({'error': 'You can only manage users you created'}, status=403)

    target_user.is_active = bool(enable)
    target_user.save()

    action = 'enabled' if enable else 'disabled'
    logger.info(f"[ENROL] {request.user.username} {action} user '{username}'")
    return JsonResponse({'success': True, 'username': username, 'is_active': target_user.is_active})
