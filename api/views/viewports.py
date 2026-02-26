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
from api.helpers import (
    FAISS_INDICES_DIR,
    MIN_YEAR,
    MAX_YEAR,
    check_viewport_mosaics_exist,
    check_viewport_pyramids_exist,
    get_viewport_data_size,
    get_user_total_data_size,
    estimate_viewport_size,
    cleanup_viewport_embeddings,
    parse_json_body,
    USER_QUOTA_MB,
)
from api.tasks import tasks, tasks_lock, trigger_data_download_and_processing

logger = logging.getLogger(__name__)


def _get_pyramid_years(viewport_name):
    """Return sorted list of years with pyramid data for a viewport."""
    years = []
    viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
    if viewport_pyramids_dir.exists():
        for year in range(MIN_YEAR, MAX_YEAR + 1):
            if (viewport_pyramids_dir / str(year) / "level_0.tif").exists():
                years.append(year)
    return sorted(years, reverse=True)


def list_viewports(request):
    """List all available viewports."""
    try:
        viewports = lib_list_viewports()
        active_name = get_active_viewport_name()

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
        current_user = request.session.get('user')
        filtered = [
            vp for vp in viewport_data
            if not vp.get('private') or current_user == 'admin' or current_user == vp.get('created_by')
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
    """Get current active viewport."""
    try:
        viewport = get_active_viewport()
        active_name = get_active_viewport_name()
        viewport['name'] = active_name

        return JsonResponse({
            'success': True,
            'viewport': viewport
        })
    except Exception as e:
        logger.error(f"Error getting current viewport: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


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

        set_active_viewport(viewport_name)

        viewport = read_viewport_file(viewport_name)
        viewport['name'] = viewport_name

        response_data = {
            'success': True,
            'message': f'Switched to viewport: {viewport_name}',
            'viewport': viewport,
            'data_ready': True,
            'pyramids_ready': True,
            'faiss_ready': False
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

        faiss_dir = FAISS_INDICES_DIR / viewport_name
        faiss_index_file = faiss_dir / 'all_embeddings.npy'

        if faiss_index_file.exists():
            response_data['faiss_ready'] = True
            logger.info(f"[MONITOR] FAISS ready for '{viewport_name}'")
        else:
            response_data['faiss_ready'] = False
            if pipeline_status:
                response_data['message'] += '\nWaiting for FAISS index (created during pipeline processing)...'

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

        years = data.get('years')
        logger.info(f"[NEW VIEWPORT] API received years: {years} (type: {type(years).__name__})")

        # Per-user disk quota check
        user = request.session.get('user')
        if user and user != 'admin':
            num_years = len(years) if years else 1
            estimated_mb = estimate_viewport_size(bounds, num_years)
            current_mb = get_user_total_data_size(user)
            if current_mb + estimated_mb > USER_QUOTA_MB:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Disk quota exceeded. '
                        f'Your existing viewports use {current_mb:.0f} MB, '
                        f'this viewport would add ~{estimated_mb:.0f} MB, '
                        f'but your limit is {USER_QUOTA_MB} MB ({USER_QUOTA_MB / 1024:.0f} GB). '
                        f'Delete some viewports to free up space.'
                    )
                }, status=403)

        create_viewport_from_bounds(name, bounds, description)

        viewport = read_viewport_file(name)
        viewport['name'] = name

        private_flag = bool(data.get('private', False))
        config = {'years': years, 'created_by': request.session.get('user'), 'private': private_flag}
        config_file = VIEWPORTS_DIR / f"{name}_config.json"
        with open(config_file, 'w') as f:
            json.dump(config, f)
        logger.info(f"[NEW VIEWPORT] Saved config: {config_file}")

        logger.info(f"[NEW VIEWPORT] Triggering data download for new viewport '{name}' with years={years}...")
        trigger_data_download_and_processing(name, years=years)

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

        viewports_dir = VIEWPORTS_DIR
        viewport_file = viewports_dir / f'{viewport_name}.txt'

        if not viewport_file.exists():
            return JsonResponse({'success': False, 'error': 'Viewport not found'}, status=404)

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

        # Delete FAISS indices directory
        if FAISS_INDICES_DIR.exists():
            try:
                faiss_viewport_dir = FAISS_INDICES_DIR / viewport_name
                if faiss_viewport_dir.exists():
                    shutil.rmtree(faiss_viewport_dir)
                    deleted_items.append(f"FAISS/UMAP directory: {viewport_name}/")
            except Exception as e:
                logger.warning(f"Error deleting FAISS index directory for {viewport_name}: {e}")

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
            config = {'years': [], 'created_by': request.session.get('user')}

        existing_years = config.get('years') or []
        merged_years = sorted(set(existing_years) | set(new_years))

        # Disk quota check
        user = request.session.get('user')
        if user and user != 'admin':
            bounds = viewport['bounds']
            bounds_tuple = (bounds['minLon'], bounds['minLat'], bounds['maxLon'], bounds['maxLat'])
            estimated_mb = estimate_viewport_size(bounds_tuple, len(new_years))
            current_mb = get_user_total_data_size(user)
            if current_mb + estimated_mb > USER_QUOTA_MB:
                return JsonResponse({
                    'success': False,
                    'error': (
                        f'Disk quota exceeded. '
                        f'Your existing viewports use {current_mb:.0f} MB, '
                        f'adding {len(new_years)} year(s) would add ~{estimated_mb:.0f} MB, '
                        f'but your limit is {USER_QUOTA_MB} MB ({USER_QUOTA_MB / 1024:.0f} GB). '
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
        # Check FAISS
        has_faiss = False
        faiss_dir = FAISS_INDICES_DIR / viewport_name
        if faiss_dir.exists():
            for year_dir in faiss_dir.glob("*"):
                if year_dir.is_dir() and (year_dir / "embeddings.index").exists():
                    has_faiss = True
                    break

        embedding_files = list(MOSAICS_DIR.glob(f"{viewport_name}_embeddings_*.tif"))
        has_mosaics = len(embedding_files) > 0
        has_embeddings = has_faiss or has_mosaics

        pyramid_dir = PYRAMIDS_DIR / viewport_name
        has_pyramids = False
        years_available = []
        if pyramid_dir.exists():
            for year_dir in pyramid_dir.glob("*"):
                if year_dir.is_dir() and year_dir.name not in ['satellite', 'rgb']:
                    if (year_dir / "level_0.tif").exists():
                        has_pyramids = True
                        years_available.append(year_dir.name)

        has_pca = False
        if faiss_dir.exists():
            for year_dir in faiss_dir.glob("*"):
                if year_dir.is_dir() and (year_dir / 'pca_coords.npy').exists():
                    has_pca = True
                    break

        has_umap = False
        if faiss_dir.exists():
            for year_dir in faiss_dir.glob("*"):
                if year_dir.is_dir() and (year_dir / 'umap_coords.npy').exists():
                    has_umap = True
                    break

        is_ready_flag = has_pyramids

        # Read config to find requested years
        years_requested = []
        config_file = VIEWPORTS_DIR / f"{viewport_name}_config.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                    years_requested = [str(y) for y in config.get('years', [])]
            except Exception:
                pass
        # Only show years the user actually requested (if config exists)
        if years_requested:
            years_available = [y for y in years_available if y in years_requested]
            has_pyramids = len(years_available) > 0
            is_ready_flag = has_pyramids
        years_processing = sorted(set(years_requested) - set(years_available))

        if is_ready_flag:
            available_str = ', '.join(sorted(years_available))
            if years_processing:
                processing_str = ', '.join(years_processing)
                message = f"Ready ({available_str}) — processing: {processing_str}"
            else:
                message = f"Ready to view ({available_str})"
        else:
            operation_id = f"{viewport_name}_full_pipeline"
            pipeline_running = False
            pipeline_failed = False
            with tasks_lock:
                if operation_id in tasks:
                    status = tasks[operation_id].get('status')
                    pipeline_running = status in ('starting', 'in_progress')
                    pipeline_failed = status == 'failed'

            if not pipeline_running and not pipeline_failed:
                logger.info(f"[is-ready] Pipeline not running for '{viewport_name}' but data incomplete - re-triggering pipeline")
                saved_years = [int(y) for y in years_requested] if years_requested else None
                trigger_data_download_and_processing(viewport_name, years=saved_years)
                message = "Restarting pipeline..."
            elif not has_embeddings:
                message = "Downloading embeddings..."
            else:
                message = "Creating pyramids..."

        return JsonResponse({
            'ready': is_ready_flag,
            'message': message,
            'has_embeddings': has_embeddings,
            'has_pyramids': has_pyramids,
            'has_faiss': has_faiss,
            'has_pca': has_pca,
            'has_umap': has_umap,
            'years_available': sorted(years_available),
            'years_processing': years_processing,
        })

    except Exception as e:
        logger.error(f"Error checking viewport readiness: {e}")
        return JsonResponse({'ready': False, 'message': f'Error: {str(e)}'}, status=400)
