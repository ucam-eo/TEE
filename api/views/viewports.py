"""Viewport CRUD endpoints."""

import json
import logging
import time as _time

from django.http import JsonResponse

from lib.viewport_utils import (
    get_active_viewport,
    get_active_viewport_name,
    list_viewports as lib_list_viewports,
    read_viewport_file,
    validate_viewport_name,
)
from lib.viewport_writer import set_active_viewport, clear_active_viewport, create_viewport_from_bounds
from lib.pipeline import cancel_pipeline
from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, VIEWPORTS_DIR, PROGRESS_DIR
from lib.config import VECTORS_DIR, pyramid_exists
from lib.viewport_ops import check_readiness, delete_viewport_data, compute_data_size
from api.helpers import (
    MIN_YEAR,
    MAX_YEAR,
    check_viewport_mosaics_exist,
    check_viewport_pyramids_exist,
    get_viewport_data_size,
    get_user_total_data_size,
    estimate_viewport_size,
    cleanup_viewport_embeddings,
    check_viewport_owner,
    parse_json_body,
)
from api.middleware import get_user_quota
from api.tasks import tasks, tasks_lock, trigger_data_download_and_processing

logger = logging.getLogger(__name__)


def _get_pyramid_years(viewport_name):
    """Return sorted list of years with pyramid data for a viewport."""
    years = []
    viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
    if viewport_pyramids_dir.exists():
        for year in range(MIN_YEAR, MAX_YEAR + 1):
            if pyramid_exists(viewport_pyramids_dir / str(year)):
                years.append(year)
    return sorted(years, reverse=True)


def list_viewports(request):
    """List all available viewports."""
    try:
        viewports = lib_list_viewports()
        # Per-session active viewport, fallback to global
        active_name = request.session.get('active_viewport') or get_active_viewport_name()

        viewport_data = []
        for viewport_name in viewports:
            try:
                viewport = read_viewport_file(viewport_name)
                viewport['name'] = viewport_name
                viewport['is_active'] = (viewport_name == active_name)
                viewport['data_size_mb'] = get_viewport_data_size(viewport_name, active_name)
                all_pyramid_years = _get_pyramid_years(viewport_name)
                config_file = VIEWPORTS_DIR / f"{viewport_name}_config.json"
                if config_file.exists():
                    with open(config_file) as cf:
                        cfg = json.load(cf)
                    years_configured = sorted(cfg.get('years') or [], reverse=True)
                    viewport['years_configured'] = years_configured
                    viewport['private'] = cfg.get('private', False)
                    viewport['created_by'] = cfg.get('created_by')
                    # Only show pyramid years the user actually requested
                    configured_set = {int(y) for y in years_configured}
                    viewport['years_available'] = [y for y in all_pyramid_years if y in configured_set]
                else:
                    viewport['years_configured'] = []
                    viewport['private'] = False
                    viewport['created_by'] = None
                    viewport['years_available'] = all_pyramid_years
                viewport_data.append(viewport)
            except Exception as e:
                logger.warning(f"Error reading viewport {viewport_name}: {e}")

        # Filter out private viewports for non-owners
        current_user = request.user.username if request.user.is_authenticated else None
        filtered = [
            vp for vp in viewport_data
            if not vp.get('private') or request.user.is_superuser or current_user == vp.get('created_by')
        ]

        return JsonResponse({
            'success': True,
            'viewports': filtered,
            'active': active_name
        })
    except Exception as e:
        logger.error(f"Error listing viewports: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def current_viewport(request):
    """Get current active viewport (per-session, falls back to global)."""
    try:
        # Per-session active viewport (concurrent-safe)
        session_vp = request.session.get('active_viewport')
        if session_vp:
            try:
                viewport = read_viewport_file(session_vp)
                viewport['name'] = session_vp
                return JsonResponse({'success': True, 'viewport': viewport})
            except FileNotFoundError:
                # Session points to deleted viewport — clear and fall through
                del request.session['active_viewport']

        # Fallback: global active viewport (single-user / legacy)
        viewport = get_active_viewport()
        active_name = get_active_viewport_name()
        viewport['name'] = active_name

        return JsonResponse({
            'success': True,
            'viewport': viewport
        })
    except FileNotFoundError:
        return JsonResponse({'success': False, 'error': 'No active viewport'})
    except Exception as e:
        logger.error(f"Error getting current viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def viewport_info(request, viewport_name):
    """Get viewport info by name (no global state change — concurrent-safe)."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        viewport = read_viewport_file(viewport_name)
        viewport['name'] = viewport_name
        return JsonResponse({'success': True, 'viewport': viewport})
    except FileNotFoundError:
        return JsonResponse({'success': False, 'error': f'Viewport {viewport_name} not found'}, status=404)


def switch_viewport(request):
    """Switch to a different viewport and report processing status."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        data, err = parse_json_body(request)
        if err:
            return err
        viewport_name = data.get('name')

        if not viewport_name:
            return JsonResponse({'success': False, 'error': 'Viewport name required'}, status=400)

        try:
            validate_viewport_name(viewport_name)
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

        viewport = read_viewport_file(viewport_name)  # raises FileNotFoundError if missing
        viewport['name'] = viewport_name

        # Store per-session (concurrent-safe) + global (legacy fallback)
        request.session['active_viewport'] = viewport_name
        set_active_viewport(viewport_name)

        response_data = {
            'success': True,
            'message': f'Switched to viewport: {viewport_name}',
            'viewport': viewport,
            'data_ready': True,
            'pyramids_ready': True,
            'vectors_ready': False
        }

        operation_id = f"{viewport_name}_full_pipeline"
        pipeline_status = None
        current_stage = None
        with tasks_lock:
            if operation_id in tasks:
                pipeline_status = tasks[operation_id].get('status')
                current_stage = tasks[operation_id].get('current_stage')
                logger.info(f"[MONITOR] Pipeline for '{viewport_name}': status={pipeline_status}, stage={current_stage}")

        if not check_viewport_mosaics_exist(viewport_name):
            response_data['data_ready'] = False
            if pipeline_status:
                response_data['message'] += f'\nPipeline processing (current stage: {current_stage}). This may take 15-30 minutes...'
            else:
                response_data['message'] += '\nData not available. No processing was initiated at viewport creation.'

        if not check_viewport_pyramids_exist(viewport_name):
            response_data['pyramids_ready'] = False
            if not pipeline_status:
                response_data['message'] += '\nPyramids not ready. Waiting for data to complete...'

        vector_dir = VECTORS_DIR / viewport_name
        if vector_dir.exists() and any(
            (year_dir / 'all_embeddings_uint8.npy.gz').exists()
            for year_dir in vector_dir.iterdir() if year_dir.is_dir()
        ):
            response_data['vectors_ready'] = True
            logger.info(f"[MONITOR] Vectors ready for '{viewport_name}'")
        else:
            response_data['vectors_ready'] = False
            if pipeline_status:
                response_data['message'] += '\nWaiting for vectors (created during pipeline processing)...'

        return JsonResponse(response_data)
    except FileNotFoundError:
        return JsonResponse({'success': False, 'error': 'Viewport not found'}, status=404)
    except Exception as e:
        logger.error(f"Error switching viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def create_viewport(request):
    """Create a new viewport from bounds."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        data, err = parse_json_body(request)
        if err:
            return err

        bounds_str = data.get('bounds')
        name = data.get('name')
        description = data.get('description', '')

        if not bounds_str:
            return JsonResponse({'success': False, 'error': 'Bounds required'}, status=400)

        try:
            parts = bounds_str.split(',')
            if len(parts) != 4:
                raise ValueError("Bounds must have 4 values")
            bounds = tuple(float(p.strip()) for p in parts)
        except ValueError as e:
            return JsonResponse({'success': False, 'error': f'Invalid bounds format: {e}'}, status=400)

        # Validate geographic bounds
        min_lon, min_lat, max_lon, max_lat = bounds
        if not (-180 <= min_lon < max_lon <= 180):
            return JsonResponse({'success': False, 'error': 'Invalid longitude range'}, status=400)
        if not (-90 <= min_lat < max_lat <= 90):
            return JsonResponse({'success': False, 'error': 'Invalid latitude range'}, status=400)

        if not name:
            name = f"viewport_{int(_time.time())}"

        try:
            validate_viewport_name(name)
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

        # Check for duplicate name — if it exists in the viewport list, reject.
        # If stale files remain from a prior deletion, clean them up.
        viewport_path = VIEWPORTS_DIR / f"{name}.txt"
        config_path = VIEWPORTS_DIR / f"{name}_config.json"
        if viewport_path.exists() and config_path.exists():
            return JsonResponse({'success': False, 'error': f'Viewport "{name}" already exists. Delete it first.'}, status=409)

        # Clean up leftover state only if stale files actually exist
        has_stale = (viewport_path.exists() or
                     (PYRAMIDS_DIR / name).exists() or
                     (VECTORS_DIR / name).exists() or
                     any(PROGRESS_DIR.glob(f'{name}_*')))
        if has_stale:
            import shutil
            from api.tasks import tasks, tasks_lock
            logger.info(f"[NEW VIEWPORT] Cleaning up stale data for '{name}'")
            viewport_path.unlink(missing_ok=True)
            config_path.unlink(missing_ok=True)
            cancel_pipeline(name)
            operation_id = f"{name}_full_pipeline"
            with tasks_lock:
                tasks.pop(operation_id, None)
            for leftover_dir in [PYRAMIDS_DIR / name, VECTORS_DIR / name]:
                if leftover_dir.exists():
                    shutil.rmtree(leftover_dir, ignore_errors=True)
            for leftover in PROGRESS_DIR.glob(f'{name}_*'):
                leftover.unlink(missing_ok=True)
            if MOSAICS_DIR.exists():
                for f in MOSAICS_DIR.glob(f'{name}_*'):
                    f.unlink(missing_ok=True)

        # Validate years
        years = data.get('years')
        if years:
            valid_range = range(2017, 2026)
            invalid = [y for y in years if y not in valid_range]
            if invalid:
                return JsonResponse({'success': False, 'error': f'Years out of range (2017-2025): {invalid}'}, status=400)

            # Note: GeoTessera availability is NOT checked here (would block for 28s
            # downloading the registry). The pipeline reports unavailable years as errors.

        logger.info(f"[NEW VIEWPORT] API received years: {years} (type: {type(years).__name__})")

        # Per-user disk quota check
        user = request.user.username if request.user.is_authenticated else None
        if user and not request.user.is_superuser:
            quota_mb = get_user_quota(request.user)
            num_years = len(years) if years else 1
            estimated_mb = estimate_viewport_size(bounds, num_years)
            current_mb = get_user_total_data_size(user)
            if current_mb + estimated_mb > quota_mb:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Disk quota exceeded. '
                        f'Your existing viewports use {current_mb:.0f} MB, '
                        f'this viewport would add ~{estimated_mb:.0f} MB, '
                        f'but your limit is {quota_mb:.0f} MB ({quota_mb / 1024:.0f} GB). '
                        f'Delete some viewports to free up space.'
                    )
                }, status=403)

        import time as _time
        t0 = _time.monotonic()
        create_viewport_from_bounds(name, bounds, description)
        logger.info(f"[NEW VIEWPORT] create_viewport_from_bounds: {(_time.monotonic()-t0)*1000:.0f}ms")

        viewport = read_viewport_file(name)
        viewport['name'] = name

        private_flag = bool(data.get('private', False))
        config = {'years': years, 'created_by': user, 'private': private_flag}
        config_file = VIEWPORTS_DIR / f"{name}_config.json"
        with open(config_file, 'w') as f:
            json.dump(config, f)

        # Set as active viewport for this session
        request.session['active_viewport'] = name

        logger.info(f"[NEW VIEWPORT] Triggering pipeline for '{name}' years={years}")
        trigger_data_download_and_processing(name, years=years)
        logger.info(f"[NEW VIEWPORT] Response ready: {(_time.monotonic()-t0)*1000:.0f}ms total")

        return JsonResponse({
            'success': True,
            'message': f'Created viewport: {name}. Downloading data and creating pyramids in background (this may take 15-30 minutes)...',
            'viewport': viewport,
            'data_preparing': True
        })
    except FileExistsError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=409)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error(f"Error creating viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def delete_viewport(request):
    """Delete a viewport and all associated data."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        import shutil

        data, err = parse_json_body(request)
        if err:
            return err
        viewport_name = data.get('name')

        if not viewport_name:
            return JsonResponse({'success': False, 'error': 'Viewport name required'}, status=400)

        try:
            validate_viewport_name(viewport_name)
        except ValueError as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

        allowed, deny_response = check_viewport_owner(request, viewport_name)
        if not allowed:
            return deny_response

        viewports_dir = VIEWPORTS_DIR
        viewport_file = viewports_dir / f'{viewport_name}.txt'

        # Don't require .txt to exist — clean up whatever data remains
        if not viewport_file.exists():
            logger.warning(f"Viewport file {viewport_file} not found, proceeding with data cleanup")

        active_viewport = get_active_viewport_name()
        if active_viewport == viewport_name:
            clear_active_viewport()
            logger.info(f"Cleared active viewport state before deleting '{viewport_name}'")

        try:
            viewport = read_viewport_file(viewport_name)
            bounds = viewport['bounds']
        except Exception as e:
            logger.warning(f"Could not read viewport bounds, skipping data cleanup: {e}")
            bounds = None

        deleted_items = []

        # Delete associated mosaic files
        if MOSAICS_DIR.exists():
            for mosaic_file in MOSAICS_DIR.glob('*.tif'):
                if mosaic_file.stem.startswith(viewport_name + '_'):
                    mosaic_file.unlink()
                    deleted_items.append(f"mosaic: {mosaic_file.name}")
                    logger.info(f"Deleted mosaic: {mosaic_file.name}")

            years_file = MOSAICS_DIR / f'{viewport_name}_years.json'
            if years_file.exists():
                years_file.unlink()
                deleted_items.append(f"years metadata: {years_file.name}")

            rgb_dir = MOSAICS_DIR / 'rgb'
            if rgb_dir.exists():
                for rgb_file in rgb_dir.glob(f'{viewport_name}_*.tif'):
                    rgb_file.unlink()
                    deleted_items.append(f"RGB mosaic: {rgb_file.name}")

        # Delete viewport-specific pyramid directory
        if PYRAMIDS_DIR.exists():
            try:
                viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
                if viewport_pyramids_dir.exists():
                    shutil.rmtree(viewport_pyramids_dir)
                    deleted_items.append(f"pyramids directory: {viewport_name}/")
            except Exception as e:
                logger.warning(f"Error deleting pyramids directory for {viewport_name}: {e}")

        # Delete vectors directory
        if VECTORS_DIR.exists():
            try:
                vectors_viewport_dir = VECTORS_DIR / viewport_name
                if vectors_viewport_dir.exists():
                    shutil.rmtree(vectors_viewport_dir)
                    deleted_items.append(f"vectors directory: {viewport_name}/")
            except Exception as e:
                logger.warning(f"Error deleting vectors directory for {viewport_name}: {e}")

        # Clean up embeddings tile cache
        if bounds:
            try:
                emb_deleted = cleanup_viewport_embeddings(viewport_name, bounds)
                deleted_items.extend(emb_deleted)
            except Exception as e:
                logger.warning(f"Error cleaning up embeddings for {viewport_name}: {e}")

        # Delete legacy labels JSON file
        labels_file = viewports_dir / f'{viewport_name}_labels.json'
        if labels_file.exists():
            try:
                labels_file.unlink()
                deleted_items.append(f"labels JSON: {labels_file.name}")
            except Exception as e:
                logger.warning(f"Error deleting labels file for {viewport_name}: {e}")

        # Delete viewport config JSON file
        config_file = viewports_dir / f'{viewport_name}_config.json'
        if config_file.exists():
            try:
                config_file.unlink()
                deleted_items.append(f"config: {config_file.name}")
            except Exception as e:
                logger.warning(f"Error deleting config file for {viewport_name}: {e}")

        # Delete progress tracking files
        for progress_file in PROGRESS_DIR.glob(f'{viewport_name}_*_progress.json'):
            try:
                progress_file.unlink()
                deleted_items.append(f"progress file: {progress_file.name}")
            except Exception as e:
                logger.warning(f"Error deleting progress file {progress_file.name}: {e}")

        # Delete the viewport file
        if viewport_file.exists():
            viewport_file.unlink()
            deleted_items.append(f"viewport: {viewport_name}.txt")
        logger.info(f"Deleted viewport: {viewport_name}")

        return JsonResponse({
            'success': True,
            'message': f'Deleted viewport and {len(deleted_items)-1} data files',
            'deleted': deleted_items
        })

    except Exception as e:
        logger.error(f"Error deleting viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def add_years(request, viewport_name):
    """Add years to an existing viewport and re-run the pipeline."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        allowed, deny_response = check_viewport_owner(request, viewport_name)
        if not allowed:
            return deny_response

        viewport = read_viewport_file(viewport_name)

        data, err = parse_json_body(request)
        if err:
            return err
        new_years = data.get('years')
        if not new_years or not isinstance(new_years, list):
            return JsonResponse({'success': False, 'error': 'years must be a non-empty list of integers'}, status=400)

        for y in new_years:
            if not isinstance(y, int) or y < MIN_YEAR or y > MAX_YEAR:
                return JsonResponse({'success': False, 'error': f'Invalid year: {y}. Must be {MIN_YEAR}-{MAX_YEAR}.'}, status=400)

        # Note: GeoTessera availability is NOT checked here (would block for 28s).
        # The pipeline reports unavailable years as errors.

        # Check no pipeline is already running
        operation_id = f"{viewport_name}_full_pipeline"
        with tasks_lock:
            if operation_id in tasks:
                status = tasks[operation_id].get('status')
                if status in ('starting', 'in_progress'):
                    tasks[operation_id] = {
                        'status': 'cancelled',
                        'current_stage': 'cancelled',
                        'error': 'Superseded by add-years request'
                    }
                    logger.info(f"[ADD YEARS] Cancelled existing pipeline for '{viewport_name}' to add new years")
        cancel_pipeline(viewport_name)

        config_file = VIEWPORTS_DIR / f"{viewport_name}_config.json"
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
        else:
            config = {'years': [], 'created_by': request.user.username if request.user.is_authenticated else None}

        existing_years = config.get('years') or []
        merged_years = sorted(set(existing_years) | set(new_years))

        # Disk quota check
        user = request.user.username if request.user.is_authenticated else None
        if user and not request.user.is_superuser:
            quota_mb = get_user_quota(request.user)
            bounds = viewport['bounds']
            bounds_tuple = (bounds['minLon'], bounds['minLat'], bounds['maxLon'], bounds['maxLat'])
            estimated_mb = estimate_viewport_size(bounds_tuple, len(new_years))
            current_mb = get_user_total_data_size(user)
            if current_mb + estimated_mb > quota_mb:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Disk quota exceeded. '
                        f'Your existing viewports use {current_mb:.0f} MB, '
                        f'adding {len(new_years)} year(s) would add ~{estimated_mb:.0f} MB, '
                        f'but your limit is {quota_mb:.0f} MB ({quota_mb / 1024:.0f} GB). '
                        f'Delete some viewports to free up space.'
                    )
                }, status=403)

        config['years'] = merged_years
        with open(config_file, 'w') as f:
            json.dump(config, f)
        logger.info(f"[ADD YEARS] Updated config for '{viewport_name}': years={merged_years}")

        logger.info(f"[ADD YEARS] Triggering pipeline for '{viewport_name}' with years={merged_years}...")
        trigger_data_download_and_processing(viewport_name, years=merged_years)

        return JsonResponse({
            'success': True,
            'message': f'Adding years {new_years} to viewport {viewport_name}. Processing in background...',
            'years': merged_years
        })
    except FileNotFoundError:
        return JsonResponse({'success': False, 'error': f'Viewport {viewport_name} not found'}, status=404)
    except Exception as e:
        logger.error(f"Error adding years to viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def available_years(request, viewport_name):
    """Get list of years with available data for a viewport."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        return JsonResponse({
            'success': True,
            'years': _get_pyramid_years(viewport_name)
        })
    except Exception as e:
        logger.error(f"Error getting available years: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def is_ready(request, viewport_name):
    """Simple synchronous check: is this viewport ready to view?"""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'ready': False, 'message': str(e)}, status=400)
    try:
        # Read config to find requested years (needed for filtering below)
        years_requested = []
        config_file = VIEWPORTS_DIR / f"{viewport_name}_config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                    years_requested = [str(y) for y in config.get('years', [])]
            except Exception:
                pass
        requested_set = set(years_requested) if years_requested else None

        def _year_matches(year_dir_name):
            """True if this year was requested (or no config exists)."""
            return requested_set is None or year_dir_name in requested_set

        # Check vectors — only for requested years
        has_vectors = False
        vectors_dir = VECTORS_DIR / viewport_name
        if vectors_dir.exists():
            for year_dir in vectors_dir.glob("*"):
                if year_dir.is_dir() and _year_matches(year_dir.name) and (year_dir / "metadata.json").exists() and (year_dir / "all_embeddings_uint8.npy.gz").exists():
                    has_vectors = True
                    break

        embedding_files = list(MOSAICS_DIR.glob(f"{viewport_name}_embeddings_*.tif"))
        has_mosaics = len(embedding_files) > 0
        has_embeddings = has_vectors or has_mosaics

        # Check pyramids — only for requested years
        pyramid_dir = PYRAMIDS_DIR / viewport_name
        has_pyramids = False
        years_available = []
        if pyramid_dir.exists():
            for year_dir in pyramid_dir.glob("*"):
                if year_dir.is_dir() and year_dir.name not in ['satellite', 'rgb']:
                    if _year_matches(year_dir.name) and pyramid_exists(year_dir):
                        has_pyramids = True
                        years_available.append(year_dir.name)

        # UMAP is computed client-side from vectors
        has_umap = has_vectors

        is_ready_flag = has_pyramids
        missing_years = sorted(set(years_requested) - set(years_available))

        # Read downloaded years from mosaic metadata
        downloaded_years = set()
        years_meta = MOSAICS_DIR / f"{viewport_name}_years.json"
        if years_meta.exists():
            try:
                with open(years_meta) as f:
                    downloaded_years = {str(y) for y in json.load(f).get('available_years', [])}
            except Exception:
                pass

        # Check if pipeline is running
        operation_id = f"{viewport_name}_full_pipeline"
        pipeline_running = False
        pipeline_failed = False
        with tasks_lock:
            if operation_id in tasks:
                task_status = tasks[operation_id].get('status')
                pipeline_running = task_status in ('starting', 'in_progress')
                pipeline_failed = task_status == 'failed'

        # Split missing years into processing vs unavailable
        if pipeline_running:
            years_processing = missing_years
            years_unavailable = []
        else:
            years_unavailable = [y for y in missing_years if y not in downloaded_years]
            years_processing = [y for y in missing_years if y in downloaded_years]

        if is_ready_flag:
            available_str = ', '.join(sorted(years_available))
            if years_unavailable and years_processing:
                message = f"Ready ({available_str}) — unavailable: {', '.join(years_unavailable)}; processing: {', '.join(years_processing)}"
            elif years_unavailable:
                message = f"Ready ({available_str}) — unavailable: {', '.join(years_unavailable)}"
            elif years_processing:
                message = f"Ready ({available_str}) — processing: {', '.join(years_processing)}"
            else:
                message = f"Ready to view ({available_str})"
        else:
            if not pipeline_running and not pipeline_failed and request.user.is_authenticated:
                logger.info(f"[is-ready] Pipeline not running for '{viewport_name}' but data incomplete - re-triggering pipeline")
                saved_years = [int(y) for y in years_requested] if years_requested else None
                trigger_data_download_and_processing(viewport_name, years=saved_years)
                message = "Restarting pipeline..."
            elif not has_embeddings:
                message = "Downloading GeoTIFF mosaics..."
            else:
                message = "Creating pyramids..."

        return JsonResponse({
            'ready': is_ready_flag,
            'message': message,
            'has_embeddings': has_embeddings,
            'has_pyramids': has_pyramids,
            'has_vectors': has_vectors,
            'has_umap': has_umap,
            'years_available': sorted(years_available),
            'years_processing': years_processing,
            'years_unavailable': years_unavailable,
        })

    except Exception as e:
        logger.error(f"Error checking viewport readiness: {e}")
        return JsonResponse({'ready': False, 'message': f'Error: {str(e)}'}, status=400)
