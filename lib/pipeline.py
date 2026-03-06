#!/usr/bin/env python3
"""
Shared pipeline orchestration for viewport data processing.
Single source of truth for: Process viewport → Satellite pyramids
Used by both web_server.py and setup_viewport.py
"""

import json
import subprocess
import logging
import signal
import os
import threading
from pathlib import Path
import time

from lib.progress_tracker import ProgressTracker
from lib.config import PYRAMIDS_DIR, VECTORS_DIR, PROGRESS_DIR

logger = logging.getLogger(__name__)

# Pipeline stage progress allocation (must sum to 100)
STAGE_PROGRESS = {
    'process': (0, 98),     # 0-98%: Download tiles + pyramids + vectors + UMAP
    'satellite': (98, 100), # 98-100%: Satellite pyramids (quick)
}

# Global registry of active pipeline processes (for cancellation)
_active_pipelines = {}  # viewport_name -> {'process': Popen, 'cancelled': bool}


def cancel_pipeline(viewport_name):
    """Cancel a running pipeline by killing its subprocess."""
    if viewport_name in _active_pipelines:
        info = _active_pipelines[viewport_name]
        info['cancelled'] = True
        proc = info.get('process')
        if proc and proc.poll() is None:  # Still running
            try:
                # Kill the process group to catch all children
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                logger.info(f"[PIPELINE] Sent SIGTERM to process group for '{viewport_name}' (PID: {proc.pid})")
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
                    logger.info(f"[PIPELINE] Force-killed process for '{viewport_name}' (PID: {proc.pid})")
            except (ProcessLookupError, PermissionError) as e:
                logger.warning(f"[PIPELINE] Could not kill process: {e}")
                try:
                    proc.kill()
                    proc.wait(timeout=1)
                except:
                    pass
        return True
    return False


def is_pipeline_cancelled(viewport_name):
    """Check if a pipeline has been cancelled."""
    return _active_pipelines.get(viewport_name, {}).get('cancelled', False)


class PipelineRunner:
    """Execute complete viewport data processing pipeline."""

    def __init__(self, project_root, venv_python=None):
        """
        Args:
            project_root: Path to project root
            venv_python: Path to venv Python (defaults to current Python)
        """
        self.project_root = Path(project_root)
        self.venv_python = venv_python or Path(__import__('sys').executable)
        self.progress = None  # Unified pipeline progress tracker
        self.viewport_name = None  # Set when running pipeline
        self._last_percent = 0  # Track last reported percent for monotonicity
        self._active_stage = None  # (stage_name, viewport_name) during subprocess runs

    def update_progress(self, stage: str, stage_percent: int, message: str,
                        current_file: str = "", current_value: int = 0, total_value: int = 0):
        """Update unified pipeline progress (monotonically increasing).

        Args:
            stage: Stage name ('process', 'satellite')
            stage_percent: Progress within this stage (0-100)
            message: Status message
            current_file: File currently being processed
            current_value: Byte-level progress (e.g. bytes downloaded)
            total_value: Byte-level total (e.g. total bytes to download)
        """
        if not self.progress:
            return

        start, end = STAGE_PROGRESS.get(stage, (0, 100))
        # Map stage_percent (0-100) to the stage's allocated range
        overall_percent = start + int((end - start) * stage_percent / 100)
        # Enforce monotonicity — never report a lower percent than before
        overall_percent = max(overall_percent, self._last_percent)
        self._last_percent = overall_percent
        self.progress.update("processing", message, current_value=current_value,
                             total_value=total_value, current_file=current_file,
                             percent=overall_percent)

    def _poll_substage_progress(self):
        """Read the active sub-operation's progress file and forward ALL fields to pipeline."""
        if not self._active_stage:
            return
        stage_name, viewport_name = self._active_stage
        sub_file = PROGRESS_DIR / f"{viewport_name}_{stage_name}_progress.json"
        if not sub_file.exists():
            return
        try:
            with open(sub_file) as f:
                sub_data = json.load(f)
            if sub_data.get('status') in ('complete', 'error'):
                return
            sub_percent = sub_data.get('percent', 0)
            sub_message = sub_data.get('message', '')
            if sub_percent > 0 or sub_message:
                self.update_progress(stage_name, sub_percent, sub_message,
                                     current_file=sub_data.get('current_file', ''),
                                     current_value=sub_data.get('current_value', 0),
                                     total_value=sub_data.get('total_value', 0))
        except (ValueError, IOError, OSError):
            pass

    def _stream_pipe(self, pipe, label, lines_out):
        """Read lines from a pipe, log them, and collect into lines_out."""
        try:
            for line in pipe:
                line = line.rstrip('\n')
                logger.info(f"[PIPELINE]   {label}: {line}")
                lines_out.append(line)
        except ValueError:
            pass  # Pipe closed

    def run_script(self, script_name, *args, timeout=1800):
        """Run a Python script and return result. Supports cancellation.

        Streams stdout/stderr to the log in real-time so output is not lost
        if the process is killed (e.g. OOM SIGKILL).
        """
        cmd = [str(self.venv_python), str(self.project_root / script_name)] + list(args)
        cmd_str = ' '.join(str(c) for c in cmd)
        logger.info(f"[PIPELINE] Running: {cmd_str}")
        logger.info(f"[PIPELINE]   cwd: {self.project_root}")

        # Use Popen to allow cancellation
        try:
            # Start process in new process group for clean killing
            proc = subprocess.Popen(
                cmd,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True  # Create new process group
            )
            logger.info(f"[PIPELINE]   PID: {proc.pid}")

            # Stream stdout/stderr to log in real-time via reader threads.
            stdout_lines = []
            stderr_lines = []
            stdout_thread = threading.Thread(
                target=self._stream_pipe, args=(proc.stdout, 'stdout', stdout_lines), daemon=True)
            stderr_thread = threading.Thread(
                target=self._stream_pipe, args=(proc.stderr, 'stderr', stderr_lines), daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            # Register the process for potential cancellation
            if self.viewport_name:
                if self.viewport_name not in _active_pipelines:
                    _active_pipelines[self.viewport_name] = {'cancelled': False}
                _active_pipelines[self.viewport_name]['process'] = proc

            # Use event-based wait instead of polling to avoid CPU spinning
            done_event = threading.Event()

            def wait_for_proc():
                proc.wait()
                done_event.set()

            waiter = threading.Thread(target=wait_for_proc, daemon=True)
            waiter.start()

            start_time = time.time()
            while not done_event.wait(timeout=1.0):
                # Forward sub-operation progress to pipeline
                self._poll_substage_progress()

                # Check if cancelled
                if self.viewport_name and is_pipeline_cancelled(self.viewport_name):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except:
                        proc.kill()
                    proc.wait()
                    stdout_thread.join(timeout=2)
                    stderr_thread.join(timeout=2)
                    logger.info(f"[PIPELINE]   Cancelled after {time.time() - start_time:.1f}s")
                    return subprocess.CompletedProcess(
                        cmd, -1, '\n'.join(stdout_lines), 'Cancelled by user')

                # Check timeout
                if time.time() - start_time > timeout:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    except:
                        proc.kill()
                    proc.wait()
                    stdout_thread.join(timeout=2)
                    stderr_thread.join(timeout=2)
                    logger.error(f"[PIPELINE]   Timed out after {timeout}s")
                    raise subprocess.TimeoutExpired(cmd, timeout)

            # Process finished — wait for reader threads to drain remaining output
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

            elapsed = time.time() - start_time
            stdout = '\n'.join(stdout_lines)
            stderr = '\n'.join(stderr_lines)
            logger.info(f"[PIPELINE]   Exit code: {proc.returncode} (after {elapsed:.1f}s)")
            if proc.returncode != 0 and not stderr_lines:
                if proc.returncode < 0:
                    sig = -proc.returncode
                    logger.warning(f"[PIPELINE]   Process killed by signal {sig} (SIGKILL=9 often means OOM)")
                else:
                    logger.warning(f"[PIPELINE]   stderr: (empty despite non-zero exit code)")
            return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

        except subprocess.TimeoutExpired:
            raise
        except Exception as e:
            logger.error(f"[PIPELINE] Error running {script_name}: {e}", exc_info=True)
            return subprocess.CompletedProcess(cmd, -1, '', str(e))

    def wait_for_file(self, file_path, min_size_bytes=1024, max_retries=30, retry_interval=1.0):
        """Wait for file to exist and reach minimum size."""
        file_path = Path(file_path)
        for attempt in range(max_retries):
            if file_path.exists():
                try:
                    file_size = file_path.stat().st_size
                    if file_size >= min_size_bytes:
                        logger.info(f"[WAIT] File ready: {file_path.name} ({file_size / (1024*1024):.1f} MB)")
                        return True
                except OSError:
                    pass

            if attempt < max_retries - 1:
                time.sleep(retry_interval)

        return False

    def stage_1_process_viewport(self, viewport_name, years_str):
        """Stage 1: Process viewport — download tiles, create pyramids + vectors + UMAP."""
        logger.info(f"[PIPELINE] STAGE 1/2: Processing viewport '{viewport_name}' (years: {years_str})...")
        logger.info(f"[PIPELINE]   Python: {self.venv_python}")

        if years_str:
            logger.info(f"[PIPELINE]   $ python process_viewport.py --years {years_str}")
            result = self.run_script('process_viewport.py', '--years', years_str)
        else:
            logger.info(f"[PIPELINE]   $ python process_viewport.py")
            result = self.run_script('process_viewport.py')

        if result.returncode != 0:
            stderr_text = result.stderr.strip() if result.stderr else '(no stderr output)'
            stdout_tail = '\n'.join(result.stdout.strip().splitlines()[-10:]) if result.stdout else '(no stdout output)'
            if result.returncode < 0:
                kill_hint = f" [killed by signal {-result.returncode}, SIGKILL=9 usually means out of memory]"
            else:
                kill_hint = ""
            error_msg = (
                f"Stage 1 failed - Process viewport (exit code {result.returncode}{kill_hint}):\n"
                f"  stderr: {stderr_text[:1000]}\n"
                f"  last stdout: {stdout_tail[:500]}"
            )
            logger.error(f"[PIPELINE] {error_msg}")
            return False, error_msg

        # Verify pyramids + vectors exist for at least one year
        pyramids_dir = PYRAMIDS_DIR / viewport_name
        vectors_dir = VECTORS_DIR / viewport_name

        has_pyramids = False
        has_vectors = False
        if pyramids_dir.exists():
            for year_dir in pyramids_dir.iterdir():
                if year_dir.is_dir() and year_dir.name != 'satellite':
                    if (year_dir / 'level_0.tif').exists():
                        has_pyramids = True
                        break
        if vectors_dir.exists():
            for year_dir in vectors_dir.iterdir():
                if year_dir.is_dir() and (year_dir / 'all_embeddings_uint8.npy.gz').exists():
                    has_vectors = True
                    break

        if not has_pyramids and not has_vectors:
            logger.warning(f"[PIPELINE] Stage 1: No pyramids or vectors found — no data available")
            return True, None  # Not an error — region may lack coverage

        logger.info(f"[PIPELINE] Stage 1 complete: pyramids={has_pyramids}, vectors={has_vectors}")
        return True, None

    def stage_2_satellite_pyramids(self, viewport_name):
        """Stage 2: Create satellite pyramids (if satellite file exists)."""
        logger.info(f"[PIPELINE] STAGE 2/2: Creating satellite pyramids for '{viewport_name}'...")
        logger.info(f"[PIPELINE]   $ python create_pyramids.py")

        result = self.run_script('create_pyramids.py')

        if result.returncode != 0:
            # Satellite pyramids are not critical — warn but don't fail
            logger.warning(f"[PIPELINE] Stage 2 warning - Satellite pyramid creation failed")
            logger.warning(f"[PIPELINE]   Error: {result.stderr[:200] if result.stderr else '(no stderr)'}")
            return True, None  # Don't fail pipeline

        logger.info(f"[PIPELINE] Stage 2 complete: Satellite pyramids created")
        return True, None

    def run_full_pipeline(self, viewport_name, years_str=None, cancel_check=None, **kwargs):
        """
        Run complete 2-stage pipeline:
        1. Process viewport: download + pyramids + vectors (per year)
        2. Satellite pyramids

        Args:
            viewport_name: Name of viewport
            years_str: Comma-separated years (e.g., "2023,2024") or None for all available
            cancel_check: Optional callable that returns True if pipeline should be cancelled
            **kwargs: Accepted for backward compatibility

        Returns:
            (success: bool, error_message: str or None)
        """
        logger.info(f"\n{'=' * 70}")
        logger.info(f"PIPELINE START: {viewport_name}")
        logger.info(f"{'=' * 70}")
        logger.info(f"   Years: {years_str or 'all available'}")
        logger.info(f"{'=' * 70}\n")

        # Register this pipeline for cancellation support
        self.viewport_name = viewport_name
        _active_pipelines[viewport_name] = {'cancelled': False, 'process': None}

        # Initialize unified pipeline progress tracker
        self.progress = ProgressTracker(f"{viewport_name}_pipeline")
        self.progress.update("processing", "Starting pipeline...", 0, 100)

        # Helper to check cancellation (uses both callback and global registry)
        def check_cancelled():
            if is_pipeline_cancelled(viewport_name):
                logger.info(f"[PIPELINE] Cancelled by user (registry): {viewport_name}")
                self.progress.error("Cancelled by user")
                return True
            if cancel_check and cancel_check():
                logger.info(f"[PIPELINE] Cancelled by user (callback): {viewport_name}")
                self.progress.error("Cancelled by user")
                return True
            return False

        # Stage 1: Process viewport (download + pyramids + vectors + UMAP)
        self.update_progress('process', 0, "Processing viewport...")
        self._active_stage = ('process', viewport_name)
        success, error = self.stage_1_process_viewport(viewport_name, years_str or "")
        self._active_stage = None
        if not success:
            self.progress.error(f"Processing failed: {error}")
            return False, error
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('process', 100, "Viewport processed")

        # Stage 2: Satellite pyramids
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('satellite', 0, "Creating satellite pyramids...")
        self._active_stage = ('satellite', viewport_name)
        success, error = self.stage_2_satellite_pyramids(viewport_name)
        self._active_stage = None
        self.update_progress('satellite', 100, "Satellite pyramids complete")

        if check_cancelled():
            return False, "Cancelled by user"

        logger.info(f"\n{'=' * 70}")
        logger.info(f"PIPELINE COMPLETE: {viewport_name}")
        logger.info(f"{'=' * 70}\n")

        self.progress.complete(f"Pipeline complete for {viewport_name}")

        # Clean up per-stage progress files (subprocess temp files)
        for stage in ('process', 'satellite'):
            stage_file = PROGRESS_DIR / f"{viewport_name}_{stage}_progress.json"
            try:
                if stage_file.exists():
                    stage_file.unlink()
            except OSError:
                pass

        return True, None
