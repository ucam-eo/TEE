#!/usr/bin/env python3
"""
Shared pipeline orchestration for viewport data processing.
Single source of truth for: Download → RGB → Pyramids → FAISS → UMAP
Used by both web_server.py and setup_viewport.py
"""

import subprocess
import logging
import signal
import os
import threading
from pathlib import Path
import time

from lib.progress_tracker import ProgressTracker
from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, FAISS_DIR, PROGRESS_DIR

logger = logging.getLogger(__name__)

# Pipeline stage progress allocation (must sum to 100)
STAGE_PROGRESS = {
    'download': (0, 50),    # 0-50%: Downloading embeddings (slowest)
    'rgb': (50, 60),        # 50-60%: Creating RGB
    'pyramids': (60, 75),   # 60-75%: Creating pyramids
    'faiss': (75, 85),      # 75-85%: Creating FAISS index
    'pca': (85, 95),        # 85-95%: Computing PCA for all years
    'umap': (95, 100),      # 95-100%: Computing UMAP (optional)
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

    def update_progress(self, stage: str, stage_percent: int, message: str):
        """Update unified pipeline progress (monotonically increasing).

        Args:
            stage: Stage name ('download', 'rgb', 'pyramids', 'faiss', 'umap')
            stage_percent: Progress within this stage (0-100)
            message: Status message
        """
        if not self.progress:
            return

        start, end = STAGE_PROGRESS.get(stage, (0, 100))
        # Map stage_percent (0-100) to the stage's allocated range
        overall_percent = start + int((end - start) * stage_percent / 100)
        # Enforce monotonicity — never report a lower percent than before
        overall_percent = max(overall_percent, self._last_percent)
        self._last_percent = overall_percent
        self.progress.update("processing", message, overall_percent, 100)

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
            # This ensures output is captured even if the process is killed.
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

    def stage_1_download_embeddings(self, viewport_name, years_str):
        """Stage 1: Download embeddings from GeoTessera."""
        logger.info(f"[PIPELINE] STAGE 1/5: Downloading embeddings for '{viewport_name}' (years: {years_str})...")
        logger.info(f"[PIPELINE]   Python: {self.venv_python}")
        logger.info(f"[PIPELINE]   $ python download_embeddings.py --years {years_str}")

        if years_str:
            result = self.run_script('download_embeddings.py', '--years', years_str)
        else:
            result = self.run_script('download_embeddings.py')

        if result.returncode != 0:
            stderr_text = result.stderr.strip() if result.stderr else '(no stderr output)'
            stdout_tail = '\n'.join(result.stdout.strip().splitlines()[-10:]) if result.stdout else '(no stdout output)'
            if result.returncode < 0:
                kill_hint = f" [killed by signal {-result.returncode}, SIGKILL=9 usually means out of memory]"
            else:
                kill_hint = ""
            error_msg = (
                f"Stage 1 failed - Embeddings download (exit code {result.returncode}{kill_hint}):\n"
                f"  stderr: {stderr_text[:1000]}\n"
                f"  last stdout: {stdout_tail[:500]}"
            )
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        # Verify embeddings exist
        mosaics_dir = MOSAICS_DIR
        embedding_files = list(mosaics_dir.glob(f"{viewport_name}_embeddings_*.tif"))
        if not embedding_files:
            logger.warning(f"[PIPELINE] ⚠️  Stage 1: No embeddings files found — no data available for requested years")
            return True, None  # Not an error — region may lack coverage for those years

        embeddings_file = embedding_files[0]
        if not self.wait_for_file(embeddings_file, min_size_bytes=1024*1024):
            error_msg = "Stage 1 verification failed - Embeddings file incomplete/missing"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        logger.info(f"[PIPELINE] ✓ Stage 1 complete: {len(embedding_files)} year(s)")
        return True, None

    def stage_2_create_rgb(self, viewport_name):
        """Stage 2: Create RGB visualization from embeddings."""
        logger.info(f"[PIPELINE] STAGE 2/5: Creating RGB visualization for '{viewport_name}'...")
        logger.info(f"[PIPELINE]   $ python create_rgb_embeddings.py")

        result = self.run_script('create_rgb_embeddings.py')

        if result.returncode != 0:
            error_msg = f"Stage 2 failed - RGB creation:\n{result.stderr[:500]}"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        # Verify RGB files exist
        mosaics_dir = MOSAICS_DIR
        rgb_dir = mosaics_dir / "rgb"
        rgb_files = list(rgb_dir.glob(f"{viewport_name}_*_rgb.tif")) if rgb_dir.exists() else []

        if not rgb_files:
            logger.warning(f"[PIPELINE] ⚠️  Stage 2: No RGB files found — no data available for requested years")
            return True, None

        rgb_file = rgb_files[0]
        if not self.wait_for_file(rgb_file, min_size_bytes=512*1024):
            error_msg = "Stage 2 verification failed - RGB file incomplete/missing"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        logger.info(f"[PIPELINE] ✓ Stage 2 complete: RGB visualization created")
        return True, None

    def stage_3_create_pyramids(self, viewport_name):
        """Stage 3: Create pyramid tiles for web viewing."""
        logger.info(f"[PIPELINE] STAGE 3/5: Creating pyramid tiles for '{viewport_name}'...")
        logger.info(f"[PIPELINE]   $ python create_pyramids.py")

        result = self.run_script('create_pyramids.py')

        if result.returncode != 0:
            error_msg = f"Stage 3 failed - Pyramid creation:\n{result.stderr[:500]}"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        # Verify pyramids exist
        pyramids_dir = PYRAMIDS_DIR
        viewport_pyramids_dir = pyramids_dir / viewport_name

        if not viewport_pyramids_dir.exists():
            logger.warning(f"[PIPELINE] ⚠️  Stage 3: No pyramid directory — no data available for requested years")
            return True, None

        # Find year directory with pyramids
        pyramid_year_dir = None
        for year_dir in viewport_pyramids_dir.glob("*"):
            if year_dir.is_dir() and year_dir.name not in ['satellite', 'rgb']:
                level_0_file = year_dir / "level_0.tif"
                if level_0_file.exists():
                    pyramid_year_dir = year_dir
                    break

        if not pyramid_year_dir:
            logger.warning(f"[PIPELINE] ⚠️  Stage 3: No pyramid levels found — no data available for requested years")
            return True, None

        level_0_file = pyramid_year_dir / "level_0.tif"
        if not self.wait_for_file(level_0_file, min_size_bytes=512*1024):
            error_msg = "Stage 3 verification failed - Pyramid level_0 incomplete/missing"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        pyramid_levels = list(pyramid_year_dir.glob("level_*.tif"))
        if len(pyramid_levels) < 3:
            error_msg = f"Stage 3 verification failed - Only {len(pyramid_levels)} levels created (expected >= 3)"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        logger.info(f"[PIPELINE] ✓ Stage 3 complete: {len(pyramid_levels)} pyramid levels created")
        return True, None

    def stage_4_create_faiss(self, viewport_name):
        """Stage 4: Create FAISS similarity search indices."""
        logger.info(f"[PIPELINE] STAGE 4/5: Creating FAISS index for '{viewport_name}'...")
        logger.info(f"[PIPELINE]   $ python create_faiss_index.py")

        result = self.run_script('create_faiss_index.py')

        if result.returncode != 0:
            error_msg = f"Stage 4 failed - FAISS creation:\n{result.stderr[:500]}"
            logger.error(f"[PIPELINE] ✗ {error_msg}")
            return False, error_msg

        # Verify FAISS index exists (year-specific)
        faiss_dir = FAISS_DIR
        faiss_viewport_dir = faiss_dir / viewport_name

        if not faiss_viewport_dir.exists():
            logger.warning(f"[PIPELINE] ⚠️  Stage 4: No FAISS directory — no data available for requested years")
            return True, None

        faiss_found = False
        faiss_year_dir = None
        for year_dir in faiss_viewport_dir.glob("*"):
            if year_dir.is_dir():
                index_file = year_dir / "embeddings.index"
                if index_file.exists():
                    faiss_found = True
                    faiss_year_dir = year_dir
                    break

        if not faiss_found:
            logger.warning(f"[PIPELINE] ⚠️  Stage 4: No FAISS index found — no data available for requested years")
            return True, None

        # Verify supporting files
        required_files = ["embeddings.index", "all_embeddings.npy", "pixel_coords.npy", "metadata.json"]
        missing_files = [f for f in required_files if not (faiss_year_dir / f).exists()]
        if missing_files:
            logger.warning(f"[PIPELINE] Stage 4 warning - Missing files: {missing_files}")

        logger.info(f"[PIPELINE] ✓ Stage 4 complete: FAISS index created")

        # Cleanup: Delete GeoTIFF mosaics now that FAISS has the embeddings
        self.cleanup_mosaics(viewport_name)

        return True, None

    def cleanup_mosaics(self, viewport_name):
        """Delete GeoTIFF mosaic files after FAISS creation to save disk space.

        After FAISS index is created, the following are no longer needed:
        - {viewport}_embeddings_{year}.tif (~100-200MB each)
        - {viewport}_rgb_{year}.tif (~50MB each)

        The FAISS directory contains all necessary data:
        - all_embeddings.npy (128D vectors)
        - pixel_coords.npy (x,y coordinates)
        - metadata.json (geotransform for lat/lon conversion)
        """
        try:
            deleted_files = []
            total_saved_mb = 0

            # Delete embedding mosaics
            for mosaic_file in MOSAICS_DIR.glob(f"{viewport_name}_embeddings_*.tif"):
                size_mb = mosaic_file.stat().st_size / (1024 * 1024)
                mosaic_file.unlink()
                deleted_files.append(mosaic_file.name)
                total_saved_mb += size_mb
                logger.info(f"[PIPELINE] Deleted mosaic: {mosaic_file.name} ({size_mb:.1f} MB)")

            # Delete RGB mosaics (pattern: {viewport}_{year}_rgb.tif)
            rgb_dir = MOSAICS_DIR / "rgb"
            if rgb_dir.exists():
                for rgb_file in rgb_dir.glob(f"{viewport_name}_*_rgb.tif"):
                    size_mb = rgb_file.stat().st_size / (1024 * 1024)
                    rgb_file.unlink()
                    deleted_files.append(rgb_file.name)
                    total_saved_mb += size_mb
                    logger.info(f"[PIPELINE] Deleted RGB mosaic: {rgb_file.name} ({size_mb:.1f} MB)")

            if deleted_files:
                logger.info(f"[PIPELINE] ✓ Cleanup complete: Deleted {len(deleted_files)} files, saved {total_saved_mb:.1f} MB")
            else:
                logger.info(f"[PIPELINE] No mosaic files to clean up for '{viewport_name}'")

        except Exception as e:
            # Non-critical - log but don't fail pipeline
            logger.warning(f"[PIPELINE] Cleanup warning: {e}")

    def stage_4b_compute_pca(self, viewport_name):
        """Stage 4b: Compute PCA for ALL years (for Panel 4 visualization)."""
        logger.info(f"[PIPELINE] STAGE 4b: Computing PCA for '{viewport_name}' (all years)...")

        faiss_dir = FAISS_DIR / viewport_name
        if not faiss_dir.exists():
            logger.warning(f"[PIPELINE] ⚠️  PCA skipped - no FAISS directory for {viewport_name}")
            return True, None

        # Find all years with FAISS indices
        years_processed = 0
        years_failed = 0

        for year_dir in sorted(faiss_dir.iterdir()):
            if year_dir.is_dir() and (year_dir / "embeddings.index").exists():
                year = year_dir.name
                pca_file = year_dir / "pca_coords.npy"

                # Skip if PCA already exists
                if pca_file.exists():
                    logger.info(f"[PIPELINE]   ✓ PCA already exists for {year}")
                    years_processed += 1
                    continue

                logger.info(f"[PIPELINE]   Computing PCA for {year}...")
                result = self.run_script('compute_pca.py', viewport_name, year, timeout=120)

                if result.returncode != 0:
                    logger.warning(f"[PIPELINE]   ⚠️  PCA failed for {year}: {result.stderr[:100]}")
                    years_failed += 1
                else:
                    logger.info(f"[PIPELINE]   ✓ PCA computed for {year}")
                    years_processed += 1

        if years_processed > 0:
            logger.info(f"[PIPELINE] ✓ Stage 4b complete: PCA computed for {years_processed} years")
        else:
            logger.warning(f"[PIPELINE] ⚠️  Stage 4b: No PCA files created")

        return True, None  # PCA is non-critical, don't fail pipeline

    def stage_5_compute_umap(self, viewport_name, umap_year):
        """Stage 5: Compute UMAP 2D projection (optional)."""
        logger.info(f"[PIPELINE] STAGE 5/5: Computing UMAP for '{viewport_name}' (year: {umap_year})...")
        logger.info(f"[PIPELINE]   $ python compute_umap.py {viewport_name} {umap_year}")

        result = self.run_script('compute_umap.py', viewport_name, umap_year)

        if result.returncode != 0:
            # UMAP is optional - warn but don't fail
            logger.warning(f"[PIPELINE] ⚠️  Stage 5 warning - UMAP computation failed (may need: pip install umap-learn)")
            logger.warning(f"[PIPELINE]   Error: {result.stderr[:200]}")
            return True, None  # Don't fail pipeline

        # Verify UMAP coordinates file exists
        faiss_dir = FAISS_DIR
        umap_file = faiss_dir / viewport_name / str(umap_year) / "umap_coords.npy"

        if not self.wait_for_file(umap_file, min_size_bytes=100):
            logger.warning(f"[PIPELINE] ⚠️  Stage 5 warning - UMAP file not found")
            return True, None  # Don't fail pipeline

        logger.info(f"[PIPELINE] ✓ Stage 5 complete: UMAP computed")
        return True, None

    def run_full_pipeline(self, viewport_name, years_str=None, compute_umap=True, umap_year=None, cancel_check=None):
        """
        Run complete pipeline in PARALLEL PER YEAR:
        - Download multiple years in parallel (one script call with all years)
        - As each year completes download, process RGB → Pyramids → FAISS per-year
        - Compute UMAP from first completed year

        Args:
            viewport_name: Name of viewport
            years_str: Comma-separated years (e.g., "2023,2024") or None for all available
            compute_umap: Whether to compute UMAP (default: True)
            umap_year: Which year to compute UMAP for (default: first to complete)
            cancel_check: Optional callable that returns True if pipeline should be cancelled

        Returns:
            (success: bool, error_message: str or None)

        PIPELINE STAGES (Per-Year Parallel):
        1. Download embeddings (all years in one call - downloads in parallel internally)
        2. Create RGB (all years in one call - processes in parallel)
        3. Create pyramids (all years in one call - processes in parallel)
        4. Create FAISS (all years in one call - processes in parallel)
        5. Compute UMAP (from first year to complete, if enabled)

        KEY GUARANTEES:
        - Viewer can switch as soon as ANY year has pyramids (Stage 3)
        - Labeling available as soon as ANY year has FAISS (Stage 4)
        - UMAP available once computed (Stage 5)
        """
        logger.info(f"\n{'=' * 70}")
        logger.info(f"🚀 PARALLEL PIPELINE START: {viewport_name}")
        logger.info(f"{'=' * 70}")
        logger.info(f"   Years: {years_str or 'all available'}")
        logger.info(f"   Compute UMAP: {compute_umap}")
        logger.info(f"{'=' * 70}\n")

        # Register this pipeline for cancellation support
        self.viewport_name = viewport_name
        _active_pipelines[viewport_name] = {'cancelled': False, 'process': None}

        # Initialize unified pipeline progress tracker
        self.progress = ProgressTracker(f"{viewport_name}_pipeline")
        self.progress.update("processing", "Starting pipeline...", 0, 100)

        # Helper to check cancellation (uses both callback and global registry)
        def check_cancelled():
            # Check global cancellation registry
            if is_pipeline_cancelled(viewport_name):
                logger.info(f"[PIPELINE] ❌ Cancelled by user (registry): {viewport_name}")
                self.progress.error("Cancelled by user")
                return True
            # Check callback
            if cancel_check and cancel_check():
                logger.info(f"[PIPELINE] ❌ Cancelled by user (callback): {viewport_name}")
                self.progress.error("Cancelled by user")
                return True
            return False

        # Stage 1: Download embeddings (all years in parallel)
        # This single call downloads all requested years in parallel
        self.update_progress('download', 0, "Downloading embeddings...")
        success, error = self.stage_1_download_embeddings(viewport_name, years_str or "")
        if not success:
            self.progress.error(f"Download failed: {error}")
            return False, error
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('download', 100, "Embeddings downloaded")

        # Stage 2: Create RGB (all years in parallel)
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('rgb', 0, "Creating RGB visualizations...")
        success, error = self.stage_2_create_rgb(viewport_name)
        if not success:
            self.progress.error(f"RGB creation failed: {error}")
            return False, error
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('rgb', 100, "RGB created")

        # Stage 3: Create pyramids (all years in parallel) - CRITICAL for viewer
        # ✓ After this stage, viewer CAN SWITCH (pyramids available for at least one year)
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('pyramids', 0, "Creating tile pyramids...")
        success, error = self.stage_3_create_pyramids(viewport_name)
        if not success:
            self.progress.error(f"Pyramid creation failed: {error}")
            return False, error
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('pyramids', 100, "Pyramids created")

        # Stage 4: Create FAISS (all years in parallel)
        # ✓ After this stage, labeling controls BECOME AVAILABLE
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('faiss', 0, "Building FAISS index...")
        success, error = self.stage_4_create_faiss(viewport_name)
        if not success:
            self.progress.error(f"FAISS creation failed: {error}")
            return False, error
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('faiss', 100, "FAISS index ready")

        # Stage 4b: Compute PCA for all years (for Panel 4 visualization)
        # ✓ After this stage, Panel 4 PCA scatter plot BECOMES AVAILABLE
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('pca', 0, "Computing PCA for visualization...")
        success, error = self.stage_4b_compute_pca(viewport_name)
        if check_cancelled():
            return False, "Cancelled by user"
        self.update_progress('pca', 100, "PCA ready")

        # Stage 5: Compute UMAP (optional)
        # ✓ After this stage, UMAP visualization BECOMES AVAILABLE
        if check_cancelled():
            return False, "Cancelled by user"
        if compute_umap:
            effective_umap_year = umap_year
            if not effective_umap_year and years_str:
                effective_umap_year = years_str.split(',')[0].strip()
            if not effective_umap_year:
                # No year specified — pick the first year that has FAISS data
                faiss_vp_dir = FAISS_DIR / viewport_name
                if faiss_vp_dir.exists():
                    year_dirs = sorted(d.name for d in faiss_vp_dir.iterdir()
                                       if d.is_dir() and (d / 'all_embeddings.npy').exists())
                    if year_dirs:
                        effective_umap_year = year_dirs[0]
                        logger.info(f"[PIPELINE] Auto-selected year {effective_umap_year} for UMAP")
            if effective_umap_year:
                self.update_progress('umap', 0, "Computing UMAP projection...")
                success, error = self.stage_5_compute_umap(viewport_name, effective_umap_year)
                self.update_progress('umap', 100, "UMAP complete")
            else:
                logger.warning(f"[PIPELINE] ⚠️  Stage 5 skipped - no FAISS data found for UMAP")
        else:
            logger.info(f"[PIPELINE] Stage 5 skipped (compute_umap=False)")

        if check_cancelled():
            return False, "Cancelled by user"

        logger.info(f"\n{'=' * 70}")
        logger.info(f"✅ PARALLEL PIPELINE COMPLETE: {viewport_name}")
        logger.info(f"{'=' * 70}\n")

        self.progress.complete(f"Pipeline complete for {viewport_name}")

        # Clean up per-stage progress files (subprocess temp files)
        for stage in ('download', 'rgb', 'pyramids', 'faiss', 'pca', 'umap'):
            stage_file = PROGRESS_DIR / f"{viewport_name}_{stage}_progress.json"
            try:
                if stage_file.exists():
                    stage_file.unlink()
            except OSError:
                pass

        return True, None
