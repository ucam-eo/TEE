#!/usr/bin/env python3
"""
Compute PCA 3D projection of embeddings.

Much faster than UMAP (~seconds vs minutes), but may not show
cluster separation as well.

Usage:
    python3 compute_pca.py Eddington 2024
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


def compute_pca(viewport_name, year):
    """Compute PCA for embeddings."""
    # Initialize progress tracker - use script-specific progress file to avoid conflicts with pipeline orchestrator
    progress = ProgressTracker(f"{viewport_name}_pca")
    progress.update("starting", f"Initializing PCA for {viewport_name}/{year}...")

    try:
        from sklearn.decomposition import PCA
    except ImportError:
        logger.error("❌ scikit-learn not installed. Install with: pip install scikit-learn")
        progress.error("scikit-learn not installed")
        return False

    vector_dir = VECTORS_DIR / viewport_name / str(year)
    embeddings_file = vector_dir / "all_embeddings.npy"

    # PCA needs embeddings (created by vector extraction stage)
    if not embeddings_file.exists():
        logger.error(f"❌ Embeddings not available yet: {embeddings_file}")
        progress.error(f"Embeddings not available yet (vector extraction may still be in progress)")
        return False

    pca_file = vector_dir / "pca_coords.npy"
    if pca_file.exists():
        logger.info(f"✓ Already computed: {pca_file}")
        progress.complete(f"PCA already exists for {viewport_name}/{year}")
        return True

    logger.info(f"📊 Computing PCA for {viewport_name}/{year}...")
    progress.update("processing", f"Loading embeddings for {viewport_name}/{year}...", 10, 100)

    try:
        embeddings = np.load(str(embeddings_file))
        num_points = embeddings.shape[0]
        logger.info(f"   Embeddings: {embeddings.shape}")
        progress.update("processing", f"Loaded {num_points:,} embeddings, fitting PCA...", 30, 100)

        logger.info(f"   Fitting PCA (3 components)...")
        pca = PCA(n_components=3)
        pca_coords = pca.fit_transform(embeddings)

        explained_variance = pca.explained_variance_ratio_
        total_variance = sum(explained_variance)
        logger.info(f"   Explained variance: {explained_variance[0]:.1%}, {explained_variance[1]:.1%}, {explained_variance[2]:.1%} (total: {total_variance:.1%})")

        progress.update("processing", f"PCA fitted, saving coordinates...", 80, 100)

        logger.info(f"   Saving PCA coordinates...")
        np.save(str(pca_file), pca_coords)
        size_mb = pca_file.stat().st_size / (1024 * 1024)
        logger.info(f"✓ PCA saved: {pca_file}")
        logger.info(f"   Size: {size_mb:.1f} MB")

        progress.complete(f"PCA complete: {num_points:,} points ({size_mb:.1f} MB, {total_variance:.1%} variance)")
        return True

    except Exception as e:
        logger.error(f"❌ Failed: {e}")
        progress.error(f"PCA failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logger.error("Usage: python3 compute_pca.py <viewport> <year>")
        logger.error("Example: python3 compute_pca.py Eddington 2024")
        sys.exit(1)

    viewport = sys.argv[1]
    year = int(sys.argv[2])

    success = compute_pca(viewport, year)
    sys.exit(0 if success else 1)
