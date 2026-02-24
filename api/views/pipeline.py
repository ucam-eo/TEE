"""Pipeline / download endpoints - mechanical translation from Flask."""

import re
import json
import glob as glob_module
import logging
import uuid
import threading
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from django.http import JsonResponse

from lib.viewport_utils import (
    get_active_viewport,
    validate_viewport_name,
    read_viewport_file,
    get_active_viewport_name,
)
from lib.viewport_writer import clear_active_viewport
from lib.pipeline import cancel_pipeline
from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, PROGRESS_DIR, VIEWPORTS_DIR
from api.helpers import (
    FAISS_INDICES_DIR,
    run_script,
    cleanup_viewport_embeddings,
)
from api.tasks import tasks, tasks_lock

logger = logging.getLogger(__name__)


def _run_download_process(task_id):
    """Background task to run downloads and processing in parallel."""
    import rasterio

    def update_progress(progress, stage):
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['progress'] = progress
                tasks[task_id]['stage'] = stage

    try:
        update_progress(5, "Checking for existing pyramid data...")

        viewport = get_active_viewport()
        viewport_name = viewport['viewport_id']
        bounds = viewport['bounds']
        BOUNDS_TOLERANCE = 0.0001

        pyramids_dir = PYRAMIDS_DIR / '2024'
        pyramid_metadata = pyramids_dir / 'pyramid_metadata.json'

        if pyramid_metadata.exists():
            try:
                with open(pyramid_metadata) as f:
                    metadata = json.load(f)
                    cached_bounds = metadata.get('bounds', {})

                    if (abs(cached_bounds.get('minLon', 0) - bounds['minLon']) < BOUNDS_TOLERANCE and
                        abs(cached_bounds.get('minLat', 0) - bounds['minLat']) < BOUNDS_TOLERANCE and
                        abs(cached_bounds.get('maxLon', 0) - bounds['maxLon']) < BOUNDS_TOLERANCE and
                        abs(cached_bounds.get('maxLat', 0) - bounds['maxLat']) < BOUNDS_TOLERANCE):

                        logger.info(f"Pyramid data already exists for viewport - skipping downloads")
                        update_progress(100, "Pyramid data already cached!")

                        with tasks_lock:
                            if task_id in tasks:
                                tasks[task_id]['completed'] = True
                        return
            except Exception as e:
                logger.warning(f"Could not read pyramid metadata: {e}")

        update_progress(8, "Checking for existing mosaic files...")

        embeddings_mosaic = MOSAICS_DIR / f'{viewport_name}_embeddings_2024.tif'

        skip_downloads = False
        if embeddings_mosaic.exists():
            try:
                with rasterio.open(embeddings_mosaic) as src:
                    cached_bounds = src.bounds

                    viewport_contained = (
                        cached_bounds.left <= bounds['minLon'] + BOUNDS_TOLERANCE and
                        cached_bounds.bottom <= bounds['minLat'] + BOUNDS_TOLERANCE and
                        cached_bounds.right >= bounds['maxLon'] - BOUNDS_TOLERANCE and
                        cached_bounds.top >= bounds['maxLat'] - BOUNDS_TOLERANCE
                    )

                    if viewport_contained:
                        logger.info(f"Embeddings mosaic already exists and contains viewport - skipping downloads")
                        skip_downloads = True
                        update_progress(45, "Embeddings mosaic found - skipping downloads, creating pyramids...")
            except Exception as e:
                logger.warning(f"Could not check mosaic bounds: {e}")

        if not skip_downloads:
            update_progress(5, "Downloading TESSERA embeddings...")

            executor = ThreadPoolExecutor(max_workers=1)

            def download_embeddings():
                try:
                    update_progress(10, "Downloading embeddings_2024.tif (TESSERA)...")
                    result = run_script('download_embeddings.py', timeout=600)
                    if result.returncode == 0:
                        update_progress(30, "Embeddings downloaded")
                    return result.returncode == 0
                except Exception as e:
                    logger.error(f"Embeddings download error: {e}")
                    update_progress(30, "Embeddings download failed")
                    return False

            update_progress(10, "Starting embeddings download...")
            embeddings_future = executor.submit(download_embeddings)
            embeddings_ok = embeddings_future.result()

            if not embeddings_ok:
                raise Exception("Embeddings download failed")
            update_progress(50, "Downloads complete. Creating pyramids...")

        update_progress(55, "Creating pyramids and FAISS index in parallel...")

        executor = ThreadPoolExecutor(max_workers=2)

        def create_pyramids():
            try:
                update_progress(60, "Creating pyramid tiles...")
                result = run_script('create_pyramids.py', timeout=1200)
                if result.returncode != 0:
                    logger.warning(f"Pyramid creation returned non-zero: {result.stderr}")
                return result.returncode == 0
            except Exception as e:
                logger.error(f"Pyramid creation error: {e}")
                return False

        def create_faiss_index():
            try:
                faiss_dir = FAISS_INDICES_DIR / viewport_name
                metadata_file = faiss_dir / 'metadata.json'

                if faiss_dir.exists() and metadata_file.exists():
                    try:
                        with open(metadata_file) as f:
                            metadata = json.load(f)
                            cached_bounds = metadata.get('viewport_bounds', [])

                            bounds_list = [bounds['minLon'], bounds['minLat'],
                                         bounds['maxLon'], bounds['maxLat']]
                            BOUNDS_TOLERANCE_LOCAL = 0.0001

                            if (len(cached_bounds) == 4 and
                                abs(cached_bounds[0] - bounds_list[0]) < BOUNDS_TOLERANCE_LOCAL and
                                abs(cached_bounds[1] - bounds_list[1]) < BOUNDS_TOLERANCE_LOCAL and
                                abs(cached_bounds[2] - bounds_list[2]) < BOUNDS_TOLERANCE_LOCAL and
                                abs(cached_bounds[3] - bounds_list[3]) < BOUNDS_TOLERANCE_LOCAL):

                                logger.info(f"FAISS index already exists for viewport - skipping creation")
                                return True
                    except Exception as e:
                        logger.warning(f"Could not validate FAISS metadata: {e}")

                update_progress(65, "Creating FAISS index for similarity search...")
                result = run_script('create_faiss_index.py', timeout=600)
                if result.returncode == 0:
                    update_progress(75, "FAISS index created")
                else:
                    logger.warning(f"FAISS index creation returned non-zero: {result.stderr}")
                    update_progress(75, "FAISS index creation skipped")
                return True
            except Exception as e:
                logger.error(f"FAISS index creation error: {e}")
                logger.warning("Continuing without FAISS index (non-blocking)")
                return True

        pyramids_future = executor.submit(create_pyramids)
        faiss_future = executor.submit(create_faiss_index)

        pyramids_ok = pyramids_future.result()
        faiss_ok = faiss_future.result()

        if not pyramids_ok:
            logger.warning("Pyramid creation may have failed")

        update_progress(90, "Finalizing...")
        update_progress(100, "Complete! All data ready")

        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['completed'] = True

        logger.info(f"Download process {task_id} completed successfully")

    except Exception as e:
        logger.error(f"Download process {task_id} error: {e}")
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['error'] = str(e)
                tasks[task_id]['completed'] = True


def download_embeddings(request):
    """Download embeddings for the current viewport."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        viewport = get_active_viewport()
        logger.info(f"Downloading embeddings for viewport: {viewport['viewport_id']}")

        result = run_script('download_embeddings.py', timeout=600)

        if result.returncode == 0:
            logger.info("Embeddings download completed successfully")
            return JsonResponse({
                'success': True,
                'message': 'Embeddings downloaded successfully',
                'viewport': viewport['viewport_id']
            })
        else:
            error_msg = result.stderr or result.stdout
            logger.error(f"Embeddings download failed: {error_msg}")
            return JsonResponse({
                'success': False,
                'error': f'Download failed: {error_msg}'
            }, status=400)

    except subprocess.TimeoutExpired:
        logger.error("Embeddings download timeout")
        return JsonResponse({'success': False, 'error': 'Download timeout'}, status=408)
    except Exception as e:
        logger.error(f"Error downloading embeddings: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def downloads_process(request):
    """Start parallel downloads and processing."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        task_id = str(uuid.uuid4())

        with tasks_lock:
            tasks[task_id] = {
                'progress': 0,
                'stage': 'Initializing...',
                'completed': False,
                'error': None
            }

        thread = threading.Thread(target=_run_download_process, args=(task_id,))
        thread.daemon = True
        thread.start()

        return JsonResponse({
            'success': True,
            'task_id': task_id,
            'message': 'Download process started'
        })

    except Exception as e:
        logger.error(f"Error starting download process: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def downloads_progress(request, task_id):
    """Get progress of a download task."""
    try:
        with tasks_lock:
            if task_id not in tasks:
                return JsonResponse({'success': False, 'error': 'Task not found'}, status=404)
            task = tasks[task_id]

        response = {
            'success': True,
            'progress': task['progress'],
            'stage': task['stage'],
            'completed': task['completed'],
            'error': task['error']
        }

        try:
            viewport = get_active_viewport()
            viewport_name = viewport['viewport_id']

            progress_file = PROGRESS_DIR / f"{viewport_name}_pipeline_progress.json"
            if progress_file.exists():
                try:
                    with open(progress_file, 'r') as f:
                        op_progress = json.load(f)
                        if op_progress.get('message'):
                            response['detailed_message'] = op_progress['message']
                        if op_progress.get('current_file'):
                            response['current_file'] = op_progress['current_file']
                        if op_progress.get('current_value'):
                            response['current_value'] = op_progress['current_value']
                        if op_progress.get('total_value'):
                            response['total_value'] = op_progress['total_value']
                except (json.JSONDecodeError, IOError):
                    pass
        except Exception:
            pass

        return JsonResponse(response)

    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def operations_progress(request, operation_id):
    """Get progress of an operation from progress JSON file."""
    try:
        if not re.match(r'^[A-Za-z0-9_-]+$', operation_id):
            return JsonResponse({'success': False, 'error': 'Invalid operation_id'}, status=400)

        progress_file = PROGRESS_DIR / f"{operation_id}_progress.json"

        if not progress_file.exists():
            return JsonResponse({
                'success': False,
                'status': 'not_started',
                'message': 'Operation not started yet'
            })

        with open(progress_file, 'r') as f:
            progress_data = json.load(f)

        # For pipeline operations, merge detail from the active sub-operation
        if operation_id.endswith('_pipeline'):
            viewport_name = operation_id.rsplit('_pipeline', 1)[0]
            for sub_op in ('download', 'pyramids', 'faiss', 'umap', 'pca', 'rgb'):
                sub_file = PROGRESS_DIR / f"{viewport_name}_{sub_op}_progress.json"
                if sub_file.exists():
                    try:
                        with open(sub_file, 'r') as f:
                            sub_data = json.load(f)
                        if sub_data.get('status') not in ('complete', 'error'):
                            for key in ('current_file', 'current_value', 'total_value', 'percent'):
                                if sub_data.get(key):
                                    progress_data[key] = sub_data[key]
                            if sub_data.get('message'):
                                progress_data['message'] = sub_data['message']
                            break
                    except (json.JSONDecodeError, IOError):
                        continue

        return JsonResponse({
            'success': True,
            **progress_data
        })

    except Exception as e:
        logger.error(f"Error getting operation progress: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def pipeline_status(request, viewport_name):
    """Get status of viewport pipeline processing."""
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        operation_id = f"{viewport_name}_full_pipeline"
        with tasks_lock:
            if operation_id in tasks:
                status_info = tasks[operation_id]
                return JsonResponse({
                    'success': True,
                    'operation_id': operation_id,
                    **status_info
                })
            else:
                return JsonResponse({
                    'success': False,
                    'status': 'no_pipeline',
                    'message': 'No pipeline operation found for this viewport'
                })

    except Exception as e:
        logger.error(f"Error getting pipeline status: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def cancel_processing(request, viewport_name):
    """Cancel viewport processing pipeline and clean up all generated files."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        validate_viewport_name(viewport_name)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    try:
        import shutil

        operation_id = f"{viewport_name}_full_pipeline"
        deleted_items = []
        task_was_active = False

        if cancel_pipeline(viewport_name):
            logger.info(f"[CANCEL] Killed running pipeline subprocess for '{viewport_name}'")
            deleted_items.append("subprocess killed")

        with tasks_lock:
            if operation_id in tasks:
                current_status = tasks[operation_id].get('status')
                if current_status in ('starting', 'in_progress'):
                    tasks[operation_id] = {
                        'status': 'cancelled',
                        'current_stage': 'cancelled',
                        'error': 'Cancelled by user'
                    }
                    task_was_active = True
                    logger.info(f"[PIPELINE] Cancelled processing for viewport '{viewport_name}'")

        # Clean up progress files
        progress_patterns = [
            f"{viewport_name}_progress.json",
            f"{viewport_name}_*_progress.json"
        ]
        for pattern in progress_patterns:
            for f in glob_module.glob(str(PROGRESS_DIR / pattern)):
                try:
                    Path(f).unlink()
                    deleted_items.append(f"progress: {Path(f).name}")
                except Exception:
                    pass

        # Delete mosaic files
        if MOSAICS_DIR.exists():
            for mosaic_file in MOSAICS_DIR.glob(f'{viewport_name}_*.tif'):
                try:
                    mosaic_file.unlink()
                    deleted_items.append(f"mosaic: {mosaic_file.name}")
                except Exception:
                    pass

            years_file = MOSAICS_DIR / f'{viewport_name}_years.json'
            if years_file.exists():
                try:
                    years_file.unlink()
                    deleted_items.append(f"years: {years_file.name}")
                except Exception:
                    pass

            rgb_dir = MOSAICS_DIR / 'rgb'
            if rgb_dir.exists():
                for rgb_file in rgb_dir.glob(f'{viewport_name}_*.tif'):
                    try:
                        rgb_file.unlink()
                        deleted_items.append(f"RGB: {rgb_file.name}")
                    except Exception:
                        pass

        # Delete pyramids directory
        if PYRAMIDS_DIR.exists():
            viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
            if viewport_pyramids_dir.exists():
                try:
                    shutil.rmtree(viewport_pyramids_dir)
                    deleted_items.append(f"pyramids: {viewport_name}/")
                except Exception:
                    pass

        # Delete FAISS directory
        if FAISS_INDICES_DIR.exists():
            faiss_viewport_dir = FAISS_INDICES_DIR / viewport_name
            if faiss_viewport_dir.exists():
                try:
                    shutil.rmtree(faiss_viewport_dir)
                    deleted_items.append(f"FAISS: {viewport_name}/")
                except Exception:
                    pass

        # Clean up embeddings tile cache
        try:
            viewport = read_viewport_file(viewport_name)
            emb_deleted = cleanup_viewport_embeddings(viewport_name, viewport['bounds'])
            deleted_items.extend(emb_deleted)
        except FileNotFoundError:
            logger.warning(f"[CANCEL] Viewport file already gone, skipping embeddings cleanup")
        except Exception as e:
            logger.warning(f"[CANCEL] Embeddings cleanup failed: {e}")

        # Delete viewport config and definition files
        viewports_dir = VIEWPORTS_DIR
        for pattern in [f'{viewport_name}.txt', f'{viewport_name}_config.json']:
            filepath = viewports_dir / pattern
            if filepath.exists():
                try:
                    filepath.unlink()
                    deleted_items.append(f"config: {pattern}")
                except Exception:
                    pass

        # If this was the active viewport, clear the active state
        try:
            active_name = get_active_viewport_name()
            if active_name == viewport_name:
                clear_active_viewport()
                deleted_items.append("active viewport state")
                logger.info(f"[CANCEL] Cleared active viewport state for '{viewport_name}'")
        except Exception:
            pass

        logger.info(f"[CANCEL] Cleaned up {len(deleted_items)} items for '{viewport_name}'")

        if task_was_active:
            message = f'Processing cancelled for {viewport_name}'
        elif deleted_items:
            message = f'No active task, but cleaned up {len(deleted_items)} leftover files for {viewport_name}'
        else:
            message = f'No active processing or files found for {viewport_name}'

        return JsonResponse({
            'success': True,
            'message': message,
            'deleted_items': deleted_items,
            'task_was_active': task_was_active
        })

    except Exception as e:
        logger.error(f"Error cancelling processing: {e}")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
