"""Compute endpoints: UMAP, PCA, status checks, distance heatmap."""

import json
import time
import logging
import subprocess
from datetime import datetime, timezone

import numpy as np
from django.http import JsonResponse

from lib.viewport_utils import validate_viewport_name
from lib.config import PROGRESS_DIR, MOSAICS_DIR
from lib.config import VECTORS_DIR
from api.helpers import VENV_PYTHON, PROJECT_ROOT, parse_json_body

logger = logging.getLogger(__name__)


def _load_projection(vector_dir, coords_filename):
    """Load projection coordinates and compute geo coords.

    Returns (coords, lons, lats) on success, or raises on failure.
    """
    coords = np.load(str(vector_dir / coords_filename))
    pixel_coords = np.load(str(vector_dir / 'pixel_coords.npy'))

    with open(vector_dir / 'metadata.json') as f:
        metadata = json.load(f)

    gt = metadata['geotransform']
    lons = gt['c'] + gt['a'] * pixel_coords[:, 0] + gt['b'] * pixel_coords[:, 1]
    lats = gt['f'] + gt['d'] * pixel_coords[:, 0] + gt['e'] * pixel_coords[:, 1]

    return coords, lons, lats


def _projection_to_points(coords, lons, lats):
    """Convert projection arrays to list of point dicts."""
    has_z = coords.shape[1] >= 3
    points = []
    for i in range(len(lats)):
        point = {
            'lat': float(lats[i]),
            'lon': float(lons[i]),
            'x': float(coords[i, 0]),
            'y': float(coords[i, 1]),
        }
        if has_z:
            point['z'] = float(coords[i, 2])
        points.append(point)
    return points


def _find_year_with_file(viewport_name, filename, preferred_year=None):
    """Find a year directory containing the given file.

    Checks preferred_year first, then falls back to any available year.
    Returns (year_str, vector_dir) or (None, None).
    """
    vectors_root = VECTORS_DIR / viewport_name
    if not vectors_root.exists():
        return None, None

    # Try preferred year first
    if preferred_year:
        d = vectors_root / str(preferred_year)
        if d.is_dir() and (d / filename).exists():
            return str(preferred_year), d

    # Fall back to any year (newest first)
    for year_dir in sorted(vectors_root.iterdir(), reverse=True):
        if year_dir.is_dir() and (year_dir / filename).exists():
            return year_dir.name, year_dir

    return None, None


def _find_year_with_embeddings(viewport_name, preferred_year=None):
    """Find a year directory containing all_embeddings.npy or .npy.gz."""
    year, d = _find_year_with_file(viewport_name, 'all_embeddings.npy', preferred_year)
    if d:
        return year, d
    return _find_year_with_file(viewport_name, 'all_embeddings.npy.gz', preferred_year)


def _compute_projection(request, viewport_name, coords_filename, label):
    """Shared logic for compute-umap and compute-pca endpoints."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        data, err = parse_json_body(request)
        if err:
            return err
        preferred_year = data.get('year')

        # Find a year that has the coords file, or fall back to one with embeddings
        year, vector_dir = _find_year_with_file(viewport_name, coords_filename, preferred_year)
        if not vector_dir:
            year, vector_dir = _find_year_with_embeddings(viewport_name, preferred_year)
        if not vector_dir:
            return JsonResponse({
                'success': False,
                'error': f'Vector data not found for {viewport_name}'
            }, status=404)

        coords_file = vector_dir / coords_filename
        if not coords_file.exists():
            # Auto-trigger computation if embeddings exist
            if (vector_dir / 'all_embeddings.npy').exists() or (vector_dir / 'all_embeddings.npy.gz').exists():
                sub_label = label.lower()
                script = 'compute_pca.py' if sub_label == 'pca' else 'compute_umap.py'
                operation_id = _trigger_computation(viewport_name, year, script)
                return JsonResponse({
                    'success': False,
                    'error': f'{label} is being computed for {viewport_name} ({year}). Please retry shortly.',
                    'computing': True,
                    'operation_id': operation_id
                }, status=202)
            return JsonResponse({
                'success': False,
                'error': f'{label} not yet available for {viewport_name} ({year}).'
            }, status=404)

        try:
            coords, lons, lats = _load_projection(vector_dir, coords_filename)
        except Exception as e:
            logger.error(f"[{label}] Error loading data: {e}")
            return JsonResponse({
                'success': False,
                'error': f'Error loading {label} data: {str(e)}'
            }, status=500)

        points = _projection_to_points(coords, lons, lats)

        logger.info(f"[{label}] Loaded pre-computed {label} for {len(points):,} points")
        return JsonResponse({
            'success': True,
            'points': points,
            'num_points': len(points)
        })

    except Exception as e:
        logger.error(f"[{label}] Unexpected error: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def compute_umap(request, viewport_name):
    """Load pre-computed UMAP coordinates for visualization."""
    return _compute_projection(request, viewport_name, 'umap_coords.npy', 'UMAP')


def compute_pca(request, viewport_name):
    """Load pre-computed PCA coordinates for visualization."""
    return _compute_projection(request, viewport_name, 'pca_coords.npy', 'PCA')


def _is_progress_fresh(progress_file, max_age_seconds=60):
    """Check if a progress file exists, is non-terminal, and was updated recently."""
    if not progress_file.exists():
        return False
    try:
        with open(progress_file) as f:
            progress = json.load(f)
        if progress.get('status') in ('complete', 'error'):
            return False
        last_update = progress.get('last_update', '')
        if not last_update:
            return False
        updated_at = datetime.fromisoformat(last_update)
        return (datetime.now(timezone.utc) - updated_at).total_seconds() < max_age_seconds
    except (ValueError, TypeError, json.JSONDecodeError, IOError):
        return False


def _trigger_computation(viewport_name, year, script_name):
    """Trigger a background computation (PCA or UMAP) if not already running."""
    sub_label = script_name.replace('compute_', '').replace('.py', '')
    operation_id = f"{viewport_name}_{sub_label}"
    progress_file = PROGRESS_DIR / f"{operation_id}_progress.json"

    # Don't re-trigger if already running
    if _is_progress_fresh(progress_file):
        return operation_id

    logger.info(f"[{sub_label.upper()}] Triggering {script_name} for {viewport_name}/{year}")
    subprocess.Popen(
        [str(VENV_PYTHON), str(PROJECT_ROOT / script_name), viewport_name, str(year)],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )
    return operation_id


def _projection_status(request, viewport_name, coords_filename, label):
    """Shared logic for umap-status and pca-status endpoints."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    try:
        preferred_year = request.GET.get('year')

        # Check if coords file already exists for any year
        year, vector_dir = _find_year_with_file(viewport_name, coords_filename, preferred_year)
        if vector_dir:
            return JsonResponse({'exists': True, 'computing': False})

        sub_label = label.lower()  # 'pca' or 'umap'
        sub_operation_id = f"{viewport_name}_{sub_label}"
        sub_progress_file = PROGRESS_DIR / f"{sub_operation_id}_progress.json"

        # If computation is already running (fresh progress file), report it
        if _is_progress_fresh(sub_progress_file):
            return JsonResponse({'exists': False, 'computing': True, 'operation_id': sub_operation_id})

        # If embeddings exist for any year, trigger computation immediately
        year, vector_dir = _find_year_with_embeddings(viewport_name, preferred_year)
        if vector_dir:
            script = 'compute_pca.py' if sub_label == 'pca' else 'compute_umap.py'
            operation_id = _trigger_computation(viewport_name, year, script)
            return JsonResponse({'exists': False, 'computing': True, 'operation_id': operation_id})

        # Embeddings don't exist yet — waiting for vectors stage
        return JsonResponse({
            'exists': False,
            'computing': False,
            'waiting': True,
            'message': f'Waiting for embeddings (vector extraction)...'
        })

    except Exception as e:
        logger.error(f"[{label}] Status error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


def umap_status(request, viewport_name):
    """Check if UMAP exists."""
    return _projection_status(request, viewport_name, 'umap_coords.npy', 'UMAP')


def pca_status(request, viewport_name):
    """Check if PCA exists."""
    return _projection_status(request, viewport_name, 'pca_coords.npy', 'PCA')
