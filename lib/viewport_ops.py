"""Pure viewport operations: readiness checks, data size, data deletion."""

import json
import logging
import shutil
from pathlib import Path

from lib.config import MOSAICS_DIR, PYRAMIDS_DIR, VECTORS_DIR, VIEWPORTS_DIR, pyramid_exists

logger = logging.getLogger(__name__)


def check_readiness(viewport_name, years_requested=None):
    """Check whether a viewport is ready to view.

    Returns a dict with readiness flags and year lists, without any HTTP wrapping.
    """
    requested_set = set(str(y) for y in years_requested) if years_requested else None

    def _year_matches(year_dir_name):
        return requested_set is None or year_dir_name in requested_set

    # Vectors
    has_vectors = False
    vectors_dir = VECTORS_DIR / viewport_name
    if vectors_dir.exists():
        for year_dir in vectors_dir.glob("*"):
            if (year_dir.is_dir() and _year_matches(year_dir.name)
                    and (year_dir / "metadata.json").exists()
                    and ((year_dir / "all_embeddings.npy").exists()
                         or (year_dir / "all_embeddings_uint8.npy.gz").exists())):
                has_vectors = True
                break

    embedding_files = list(MOSAICS_DIR.glob(f"{viewport_name}_embeddings_*.tif"))
    has_mosaics = len(embedding_files) > 0
    has_embeddings = has_vectors or has_mosaics

    # Pyramids
    pyramid_dir = PYRAMIDS_DIR / viewport_name
    has_pyramids = False
    years_available = []
    if pyramid_dir.exists():
        for year_dir in pyramid_dir.glob("*"):
            if year_dir.is_dir() and year_dir.name not in ['satellite', 'rgb']:
                if _year_matches(year_dir.name) and pyramid_exists(year_dir):
                    has_pyramids = True
                    years_available.append(year_dir.name)

    has_umap = has_vectors

    return {
        'has_embeddings': has_embeddings,
        'has_pyramids': has_pyramids,
        'has_vectors': has_vectors,
        'has_umap': has_umap,
        'years_available': sorted(years_available),
    }


def delete_viewport_data(viewport_name, bounds=None):
    """Delete all data files associated with a viewport. Returns list of deleted items."""
    from api.helpers import cleanup_viewport_embeddings

    deleted_items = []

    # Mosaics
    if MOSAICS_DIR.exists():
        for mosaic_file in MOSAICS_DIR.glob('*.tif'):
            if mosaic_file.stem.startswith(viewport_name + '_'):
                mosaic_file.unlink()
                deleted_items.append(f"mosaic: {mosaic_file.name}")

        years_file = MOSAICS_DIR / f'{viewport_name}_years.json'
        if years_file.exists():
            years_file.unlink()
            deleted_items.append(f"years metadata: {years_file.name}")

        rgb_dir = MOSAICS_DIR / 'rgb'
        if rgb_dir.exists():
            for rgb_file in rgb_dir.glob(f'{viewport_name}_*.tif'):
                rgb_file.unlink()
                deleted_items.append(f"RGB mosaic: {rgb_file.name}")

    # Pyramids
    if PYRAMIDS_DIR.exists():
        viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
        if viewport_pyramids_dir.exists():
            shutil.rmtree(viewport_pyramids_dir)
            deleted_items.append(f"pyramids directory: {viewport_name}/")

    # Vectors
    if VECTORS_DIR.exists():
        vectors_viewport_dir = VECTORS_DIR / viewport_name
        if vectors_viewport_dir.exists():
            shutil.rmtree(vectors_viewport_dir)
            deleted_items.append(f"vectors directory: {viewport_name}/")

    # Embeddings tile cache
    if bounds:
        try:
            emb_deleted = cleanup_viewport_embeddings(viewport_name, bounds)
            deleted_items.extend(emb_deleted)
        except Exception as e:
            logger.warning(f"Error cleaning up embeddings for {viewport_name}: {e}")

    # Labels JSON
    labels_file = VIEWPORTS_DIR / f'{viewport_name}_labels.json'
    if labels_file.exists():
        labels_file.unlink()
        deleted_items.append(f"labels JSON: {labels_file.name}")

    # Config JSON
    config_file = VIEWPORTS_DIR / f'{viewport_name}_config.json'
    if config_file.exists():
        config_file.unlink()
        deleted_items.append(f"config: {config_file.name}")

    # Progress files
    from lib.config import PROGRESS_DIR
    for progress_file in PROGRESS_DIR.glob(f'{viewport_name}_*_progress.json'):
        progress_file.unlink()
        deleted_items.append(f"progress file: {progress_file.name}")

    # Viewport file
    viewport_file = VIEWPORTS_DIR / f'{viewport_name}.txt'
    if viewport_file.exists():
        viewport_file.unlink()
        deleted_items.append(f"viewport: {viewport_name}.txt")

    return deleted_items


def compute_data_size(viewport_name):
    """Calculate total data size for a viewport in MB."""
    total_size = 0

    if MOSAICS_DIR.exists():
        for mosaic_file in MOSAICS_DIR.glob(f'{viewport_name}_*.tif'):
            if mosaic_file.is_file():
                total_size += mosaic_file.stat().st_size
        rgb_dir = MOSAICS_DIR / 'rgb'
        if rgb_dir.exists():
            for rgb_file in rgb_dir.glob(f'{viewport_name}_*.tif'):
                if rgb_file.is_file():
                    total_size += rgb_file.stat().st_size

    vector_dir = VECTORS_DIR / viewport_name
    if vector_dir.exists():
        for item in vector_dir.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size

    viewport_pyramids_dir = PYRAMIDS_DIR / viewport_name
    if viewport_pyramids_dir.exists():
        for item in viewport_pyramids_dir.rglob('*'):
            if item.is_file():
                total_size += item.stat().st_size

    return round(total_size / (1024 * 1024), 1)
