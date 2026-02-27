"""
Centralized configuration for TEE (Tessera Embeddings Explorer).

All paths are configurable via environment variables for Docker support.
Defaults to ~/data for the data directory and the project root for the app.
"""

import os
from pathlib import Path

# Base data directory - configurable via TEE_DATA_DIR env var
DATA_DIR = Path(os.environ.get('TEE_DATA_DIR', Path.home() / 'data'))

# Subdirectories
MOSAICS_DIR = DATA_DIR / 'mosaics'
PYRAMIDS_DIR = DATA_DIR / 'pyramids'
VECTORS_DIR = DATA_DIR / 'vectors'
EMBEDDINGS_DIR = DATA_DIR / 'embeddings'
PROGRESS_DIR = DATA_DIR / 'progress'

# Application directory - defaults to project root (parent of lib/)
APP_DIR = Path(os.environ.get('TEE_APP_DIR', Path(__file__).resolve().parent.parent))
VIEWPORTS_DIR = APP_DIR / 'viewports'


def ensure_dirs():
    """Create all required directories if they don't exist."""
    for d in [DATA_DIR, MOSAICS_DIR, PYRAMIDS_DIR, VECTORS_DIR, EMBEDDINGS_DIR, PROGRESS_DIR, VIEWPORTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
