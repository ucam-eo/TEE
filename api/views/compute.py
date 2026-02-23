"""Compute endpoints: UMAP, PCA, status checks, distance heatmap."""

import json
import time
import logging

import numpy as np
from django.http import JsonResponse

from lib.viewport_utils import validate_viewport_name
from lib.config import PROGRESS_DIR
from api.helpers import FAISS_INDICES_DIR, parse_json_body

logger = logging.getLogger(__name__)


def _load_projection(faiss_dir, coords_filename):
    """Load projection coordinates and compute geo coords.

    Returns (coords, lons, lats) on success, or raises on failure.
    """
    coords = np.load(str(faiss_dir / coords_filename))
    pixel_coords = np.load(str(faiss_dir / 'pixel_coords.npy'))

    with open(faiss_dir / 'metadata.json') as f:
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
        year = data.get('year', 2024)

        faiss_dir = FAISS_INDICES_DIR / viewport_name / str(year)
        if not faiss_dir.exists():
            return JsonResponse({
                'success': False,
                'error': f'FAISS index not found for {viewport_name} ({year})'
            }, status=404)

        coords_file = faiss_dir / coords_filename
        if not coords_file.exists():
            return JsonResponse({
                'success': False,
                'error': f'{label} not yet available for {viewport_name} ({year}).'
            }, status=404)

        try:
            coords, lons, lats = _load_projection(faiss_dir, coords_filename)
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


def _projection_status(request, viewport_name, coords_filename, label):
    """Shared logic for umap-status and pca-status endpoints."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    try:
        year = request.GET.get('year', '2024')
        faiss_dir = FAISS_INDICES_DIR / viewport_name / str(year)
        coords_file = faiss_dir / coords_filename
        operation_id = f"{viewport_name}_pipeline"
        progress_file = PROGRESS_DIR / f"{operation_id}_progress.json"

        if coords_file.exists():
            return JsonResponse({'exists': True, 'computing': False})

        if progress_file.exists():
            with open(progress_file) as f:
                progress = json.load(f)
            if progress.get('status') in ('in_progress', 'processing', 'starting', 'downloading'):
                return JsonResponse({'exists': False, 'computing': True, 'operation_id': operation_id})

        return JsonResponse({
            'exists': False,
            'computing': False,
            'waiting': True,
            'message': f'Waiting for pipeline to compute {label}...'
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


def distance_heatmap(request):
    """Compute pixel-wise Euclidean distance between two years of embeddings (vectorized)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        from scipy.spatial import cKDTree

        data, err = parse_json_body(request)
        if err:
            return err
        viewport_id = data.get('viewport_id')
        year1 = data.get('year1', 2024)
        year2 = data.get('year2', 2024)

        if not viewport_id:
            return JsonResponse({
                'success': False,
                'error': 'viewport_id required'
            }, status=400)

        validate_viewport_name(viewport_id)

        start_time = time.time()
        logger.info(f"[HEATMAP] Computing distance between {year1} and {year2} for {viewport_id}...")

        faiss_dir1 = FAISS_INDICES_DIR / viewport_id / str(year1)
        faiss_dir2 = FAISS_INDICES_DIR / viewport_id / str(year2)

        for faiss_dir in [faiss_dir1, faiss_dir2]:
            if not faiss_dir.exists():
                return JsonResponse({
                    'success': False,
                    'error': f'FAISS index not found: {faiss_dir}'
                }, status=404)

        try:
            all_emb1 = np.load(str(faiss_dir1 / 'all_embeddings.npy'))
            pixel_coords1 = np.load(str(faiss_dir1 / 'pixel_coords.npy'))
            with open(faiss_dir1 / 'metadata.json') as f:
                metadata1 = json.load(f)

            all_emb2 = np.load(str(faiss_dir2 / 'all_embeddings.npy'))
            pixel_coords2 = np.load(str(faiss_dir2 / 'pixel_coords.npy'))
            with open(faiss_dir2 / 'metadata.json') as f:
                metadata2 = json.load(f)

            load_time = time.time()
            logger.info(f"[HEATMAP] Loaded data in {load_time - start_time:.2f}s")

            gt1 = metadata1['geotransform']
            lons1 = gt1['c'] + gt1['a'] * pixel_coords1[:, 0] + gt1['b'] * pixel_coords1[:, 1]
            lats1 = gt1['f'] + gt1['d'] * pixel_coords1[:, 0] + gt1['e'] * pixel_coords1[:, 1]

            gt2 = metadata2['geotransform']
            lons2 = gt2['c'] + gt2['a'] * pixel_coords2[:, 0] + gt2['b'] * pixel_coords2[:, 1]
            lats2 = gt2['f'] + gt2['d'] * pixel_coords2[:, 0] + gt2['e'] * pixel_coords2[:, 1]

        except Exception as e:
            logger.error(f"[HEATMAP] Error loading FAISS data: {e}", exc_info=True)
            return JsonResponse({
                'success': False,
                'error': f'Error loading embeddings: {str(e)}'
            }, status=500)

        coords2 = np.column_stack([lats2, lons2])
        tree2 = cKDTree(coords2)

        coords1 = np.column_stack([lats1, lons1])
        distances_to_nearest, indices2 = tree2.query(coords1, k=1, distance_upper_bound=1e-5)

        matched_mask = np.isfinite(distances_to_nearest)
        matched_idx1 = np.where(matched_mask)[0]
        matched_idx2 = indices2[matched_mask]

        match_time = time.time()
        logger.info(f"[HEATMAP] Matched {len(matched_idx1):,} of {len(lats1):,} pixels in {match_time - load_time:.2f}s")

        if len(matched_idx1) == 0:
            return JsonResponse({
                'success': True,
                'distances': [],
                'stats': {
                    'matched': 0,
                    'unmatched': len(lats1),
                    'total': len(lats1),
                    'min_distance': 0.0,
                    'max_distance': 0.0,
                    'mean_distance': 0.0,
                    'median_distance': 0.0
                }
            })

        emb1_matched = all_emb1[matched_idx1].astype(np.float32)
        emb2_matched = all_emb2[matched_idx2].astype(np.float32)

        distance_values = np.linalg.norm(emb1_matched - emb2_matched, axis=1)

        dist_time = time.time()
        logger.info(f"[HEATMAP] Computed {len(distance_values):,} distances in {dist_time - match_time:.2f}s")

        lats_matched = lats1[matched_idx1]
        lons_matched = lons1[matched_idx1]

        distances = [
            {'lat': float(lat), 'lon': float(lon), 'distance': float(dist)}
            for lat, lon, dist in zip(lats_matched, lons_matched, distance_values)
        ]

        min_dist = float(np.min(distance_values))
        max_dist = float(np.max(distance_values))
        mean_dist = float(np.mean(distance_values))
        median_dist = float(np.median(distance_values))

        total_time = time.time() - start_time
        logger.info(f"[HEATMAP] Complete in {total_time:.2f}s - min: {min_dist:.3f}, max: {max_dist:.3f}, mean: {mean_dist:.3f}")

        return JsonResponse({
            'success': True,
            'distances': distances,
            'stats': {
                'matched': len(matched_idx1),
                'unmatched': len(lats1) - len(matched_idx1),
                'total': len(lats1),
                'min_distance': min_dist,
                'max_distance': max_dist,
                'mean_distance': mean_dist,
                'median_distance': median_dist,
                'compute_time_ms': int(total_time * 1000)
            }
        })

    except Exception as e:
        logger.error(f"[HEATMAP] Unexpected error: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
