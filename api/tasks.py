"""Background task tracking and pipeline trigger (same pattern as web_server.py)."""

import logging
import subprocess
import threading

from lib.pipeline import PipelineRunner
from lib.progress_tracker import ProgressTracker
from api.helpers import VENV_PYTHON, PROJECT_ROOT

logger = logging.getLogger(__name__)

# Module-level task state (same as Flask version)
tasks = {}
tasks_lock = threading.Lock()


def trigger_data_download_and_processing(viewport_name, years=None):
    """Download embeddings and run full preprocessing pipeline using shared PipelineRunner."""
    operation_id = f"{viewport_name}_full_pipeline"

    def download_and_process():
        try:
            runner = PipelineRunner(PROJECT_ROOT, VENV_PYTHON)
            years_str = ','.join(str(y) for y in years) if years else None

            def is_cancelled():
                with tasks_lock:
                    task = tasks.get(operation_id, {})
                    return task.get('status') == 'cancelled'

            success, error = runner.run_full_pipeline(
                viewport_name=viewport_name,
                years_str=years_str,
                cancel_check=is_cancelled
            )

            if success:
                logger.info(f"[PIPELINE] SUCCESS: All stages complete for viewport '{viewport_name}'")
                with tasks_lock:
                    tasks[operation_id] = {'status': 'success', 'current_stage': 'complete', 'error': None}
            else:
                with tasks_lock:
                    tasks[operation_id] = {'status': 'failed', 'current_stage': 'pipeline_error', 'error': error}

        except subprocess.TimeoutExpired:
            error_msg = "Timeout during preprocessing"
            logger.error(f"[PIPELINE] {error_msg} for '{viewport_name}'")
            with tasks_lock:
                tasks[operation_id] = {'status': 'failed', 'current_stage': 'timeout', 'error': error_msg}
        except Exception as e:
            error_msg = f"Error during preprocessing: {str(e)}"
            logger.error(f"[PIPELINE] {error_msg} for '{viewport_name}'", exc_info=True)
            with tasks_lock:
                tasks[operation_id] = {'status': 'failed', 'current_stage': 'exception', 'error': error_msg}

    # Mark as starting BEFORE spawning thread so concurrent is-ready requests see it
    with tasks_lock:
        tasks[operation_id] = {'status': 'starting', 'current_stage': 'initialization', 'error': None}

    # Write a fresh pipeline progress file immediately so the frontend doesn't
    # read a stale file from a previous run (which would show wrong elapsed time)
    progress = ProgressTracker(f"{viewport_name}_pipeline")
    progress.update("starting", "Starting pipeline...", 0, 100)

    thread = threading.Thread(target=download_and_process, daemon=True)
    thread.start()
