"""Label sharing endpoints: submit, list, and download shared labels."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from django.http import FileResponse, JsonResponse

from lib.config import SHARE_DIR
from lib.viewport_utils import validate_viewport_name

logger = logging.getLogger(__name__)


def _sanitize_email(email):
    """Sanitize email for use as directory name: alice@cam.ac.uk → alice_at_cam_ac_uk"""
    s = email.strip().lower()
    s = s.replace('@', '_at_')
    s = s.replace('.', '_')
    # Remove any remaining unsafe characters
    s = re.sub(r'[^a-z0-9_-]', '', s)
    if not s or len(s) > 128:
        return None
    return s


def _validate_no_traversal(name):
    """Reject path traversal attempts."""
    if not name or '..' in name or '/' in name or '\\' in name:
        return False
    return True


def submit_share(request):
    """Accept shared label data (private JSON or public multipart)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        # Parse based on content type
        content_type = request.content_type or ''

        if 'multipart' in content_type:
            # Public mode: metadata JSON + ZIP file
            metadata_str = request.POST.get('metadata')
            if not metadata_str:
                return JsonResponse({'error': 'metadata field required'}, status=400)
            metadata = json.loads(metadata_str)
            zip_file = request.FILES.get('labels')
            if not zip_file:
                return JsonResponse({'error': 'labels file required'}, status=400)
        else:
            # Private mode: JSON body
            try:
                metadata = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'error': 'Invalid JSON'}, status=400)
            zip_file = None

        # Validate required fields
        user = metadata.get('user', {})
        if not user.get('name') or not user.get('email') or not user.get('organization'):
            return JsonResponse({'error': 'user.name, user.email, and user.organization are required'}, status=400)

        fmt = metadata.get('format')
        if fmt not in ('private', 'public'):
            return JsonResponse({'error': 'format must be "private" or "public"'}, status=400)

        viewport = metadata.get('viewport')
        if not viewport:
            return JsonResponse({'error': 'viewport is required'}, status=400)

        if fmt == 'public' and not zip_file:
            return JsonResponse({'error': 'Public shares require a labels ZIP file'}, status=400)

        if fmt == 'private' and 'labels' not in metadata:
            return JsonResponse({'error': 'Private shares require a labels array'}, status=400)

        # Sanitize and validate paths
        sanitized_email = _sanitize_email(user['email'])
        if not sanitized_email:
            return JsonResponse({'error': 'Invalid email address'}, status=400)

        try:
            validate_viewport_name(viewport)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)

        if not _validate_no_traversal(sanitized_email) or not _validate_no_traversal(viewport):
            return JsonResponse({'error': 'Invalid characters in email or viewport'}, status=400)

        # Create directory (overwrites on re-submit)
        share_dir = SHARE_DIR / sanitized_email / viewport
        share_dir.mkdir(parents=True, exist_ok=True)

        # Add timestamp
        metadata['shared_at'] = datetime.now(timezone.utc).isoformat()

        # Write metadata.json
        with open(share_dir / 'metadata.json', 'w') as f:
            json.dump(metadata, f, indent=2)

        # Write labels
        if fmt == 'private':
            with open(share_dir / 'labels.json', 'w') as f:
                json.dump(metadata.get('labels', []), f)
            # Remove any old public ZIP
            (share_dir / 'labels.zip').unlink(missing_ok=True)
            logger.info(f"[SHARE] Private share from {user['email']} for {viewport}: {len(metadata.get('labels', []))} labels")
        else:
            with open(share_dir / 'labels.zip', 'wb') as f:
                for chunk in zip_file.chunks():
                    f.write(chunk)
            # Remove any old private JSON
            (share_dir / 'labels.json').unlink(missing_ok=True)
            logger.info(f"[SHARE] Public share from {user['email']} for {viewport}")

        return JsonResponse({'status': 'ok', 'path': f'{sanitized_email}/{viewport}'})

    except Exception as e:
        logger.error(f"[SHARE] Submit error: {e}")
        return JsonResponse({'error': str(e)}, status=400)


def list_shares(request, viewport_name):
    """List available PUBLIC shares for a viewport."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    shares = []
    if SHARE_DIR.exists():
        for user_dir in sorted(SHARE_DIR.iterdir()):
            if not user_dir.is_dir():
                continue
            meta_path = user_dir / viewport_name / 'metadata.json'
            if not meta_path.exists():
                continue
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
                if meta.get('format') != 'public':
                    continue
                user = meta.get('user', {})
                shares.append({
                    'name': user.get('name', ''),
                    'email': user.get('email', ''),
                    'organization': user.get('organization', ''),
                    'shared_at': meta.get('shared_at', ''),
                    'sanitized_email': user_dir.name,
                })
            except (json.JSONDecodeError, IOError):
                continue

    return JsonResponse({'shares': shares})


def download_share(request, sanitized_email, viewport_name):
    """Download a public share as shapefile ZIP."""
    if not _validate_no_traversal(sanitized_email) or not _validate_no_traversal(viewport_name):
        return JsonResponse({'error': 'Invalid path'}, status=400)

    meta_path = SHARE_DIR / sanitized_email / viewport_name / 'metadata.json'
    if not meta_path.exists():
        return JsonResponse({'error': 'Share not found'}, status=404)

    try:
        with open(meta_path) as f:
            meta = json.load(f)
    except (json.JSONDecodeError, IOError):
        return JsonResponse({'error': 'Corrupt metadata'}, status=500)

    if meta.get('format') != 'public':
        return JsonResponse({'error': 'Share not found'}, status=404)

    zip_path = SHARE_DIR / sanitized_email / viewport_name / 'labels.zip'
    if not zip_path.exists():
        return JsonResponse({'error': 'Labels file missing'}, status=404)

    user = meta.get('user', {})
    filename = f"shared_labels_{user.get('name', 'unknown')}_{viewport_name}.zip"

    return FileResponse(
        open(zip_path, 'rb'),
        content_type='application/zip',
        as_attachment=True,
        filename=filename,
    )
