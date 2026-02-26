"""Pipeline endpoints — progress reporting and cancellation."""

import re
import json
import glob as glob_module
import logging
from pathlib import Path

from django.http import JsonResponse

from lib.viewport_utils import (
    validate_viewport_name,
    read_viewport_file,
    get_active_viewport_name,
)
from lib.viewport_writer import clear_active_viewport
from lib.pipeline import cancel_pipeline
from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, PROGRESS_DIR, VIEWPORTS_DIR
from api.helpers import (
    FAISS_INDICES_DIR,
    cleanup_viewport_embeddings,
)
from api.tasks import tasks, tasks_lock

logger = logging.getLogger(__name__)


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
        # Keep the pipeline's overall percent (computed from stage ranges) but
        # pull in granular detail (current_file, message, bytes) from the sub-op.
        if operation_id.endswith('_pipeline'):
            viewport_name = operation_id.rsplit('_pipeline', 1)[0]
            for sub_op in ('download', 'pyramids', 'faiss', 'umap', 'pca', 'rgb'):
                sub_file = PROGRESS_DIR / f"{viewport_name}_{sub_op}_progress.json"
                if sub_file.exists():
                    try:
                        with open(sub_file, 'r') as f:
                            sub_data = json.load(f)
                        if sub_data.get('status') not in ('complete', 'error'):
                            # Merge detail fields but NOT percent — pipeline
                            # percent is the authoritative overall progress
                            for key in ('current_file', 'current_value', 'total_value'):
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
