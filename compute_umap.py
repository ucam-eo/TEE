#!/usr/bin/env python3
"""
Compute UMAP 3D projection of embeddings.

Usage:
    python3 compute_umap.py Eddington 2024
"""

import sys
import numpy as np
from pathlib import Path
import logging

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.progress_tracker import ProgressTracker
from lib.config import DATA_DIR, VECTORS_DIR

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def compute_umap(viewport_name, year):
    """Compute UMAP for embeddings."""
    # Initialize progress tracker - use script-specific progress file to avoid conflicts with pipeline orchestrator
    progress = ProgressTracker(f"{viewport_name}_umap")
    progress.update("starting", f"Initializing UMAP for {viewport_name}/{year}...")

    try:
        import umap
    except ImportError:
        logger.error("❌ UMAP not installed. Install with: pip install umap-learn")
        progress.error("UMAP not installed")
        return False

    vector_dir = VECTORS_DIR / viewport_name / str(year)

    if not vector_dir.exists():
        logger.error(f"❌ Vector data not found: {vector_dir}")
        progress.error(f"Vector data not found: {vector_dir}")
        return False

    embeddings_file = vector_dir / "all_embeddings.npy"
    if not embeddings_file.exists():
        logger.error(f"❌ Embeddings not found: {embeddings_file}")
        progress.error(f"Embeddings not found: {embeddings_file}")
        return False

    umap_file = vector_dir / "umap_coords.npy"
    if umap_file.exists():
        logger.info(f"✓ Already computed: {umap_file}")
        progress.complete(f"UMAP already exists for {viewport_name}/{year}")
        return True

    logger.info(f"📊 Computing UMAP for {viewport_name}/{year}...")
    progress.update("processing", f"Loading embeddings for {viewport_name}/{year}...", 10, 100)

    try:
        embeddings = np.load(str(embeddings_file))
        num_points = embeddings.shape[0]
        logger.info(f"   Embeddings: {embeddings.shape}")
        progress.update("processing", f"Loaded {num_points:,} embeddings, fitting UMAP...", 20, 100)

        logger.info(f"   Fitting UMAP (this may take a few minutes)...")
        reducer = umap.UMAP(
            n_neighbors=15,
            min_dist=0.1,
            n_components=3,
            n_jobs=-1,
            verbose=False
        )
        umap_coords = reducer.fit_transform(embeddings)
        progress.update("processing", f"UMAP fitted, saving coordinates...", 90, 100)

        logger.info(f"   Saving UMAP...")
        np.save(str(umap_file), umap_coords)
        size_mb = umap_file.stat().st_size / (1024 * 1024)
        logger.info(f"✓ UMAP saved: {umap_file}")
        logger.info(f"   Size: {size_mb:.1f} MB")

        progress.complete(f"UMAP complete: {num_points:,} points ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        logger.error(f"❌ Failed: {e}")
        progress.error(f"UMAP failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logger.error("Usage: python3 compute_umap.py <viewport> <year>")
        logger.error("Example: python3 compute_umap.py Eddington 2024")
        sys.exit(1)

    viewport = sys.argv[1]
    year = int(sys.argv[2])

    success = compute_umap(viewport, year)
    sys.exit(0 if success else 1)
