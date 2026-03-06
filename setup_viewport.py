#!/usr/bin/env python3
"""
Complete viewport data processing workflow using shared pipeline.
Download embeddings → Create RGB → Create pyramids → Extract Vectors → Compute UMAP

Usage:
    python3 setup_viewport.py --years 2022,2023,2024 --umap-year 2024
    python3 setup_viewport.py --years 2024                        (uses 2024 for UMAP)
"""

import sys
from pathlib import Path
import logging

# Add parent directory to path for lib imports
sys.path.insert(0, str(Path(__file__).parent))

from lib.pipeline import PipelineRunner
from lib.viewport_utils import get_active_viewport

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='Complete viewport setup: download embeddings → RGB → pyramids → vectors'
    )
    parser.add_argument(
        '--years',
        type=str,
        required=True,
        help='Comma-separated years (e.g., 2022,2023,2024)'
    )

    args = parser.parse_args()

    # Parse years
    years = [y.strip() for y in args.years.split(',')]
    logger.info(f"\n🎯 Viewport Setup Workflow")
    logger.info(f"   Years to download: {', '.join(years)}")

    # Get active viewport
    try:
        viewport = get_active_viewport()
        viewport_name = viewport['viewport_id']
        logger.info(f"   Viewport: {viewport_name}")
    except Exception as e:
        logger.error(f"❌ Failed to read active viewport: {e}")
        return 1

    # Run pipeline
    project_root = Path(__file__).parent
    runner = PipelineRunner(project_root)

    success, error = runner.run_full_pipeline(
        viewport_name=viewport_name,
        years_str=args.years,
    )

    if not success:
        logger.error(f"\n❌ Pipeline failed: {error}\n")
        return 1

    # Summary
    logger.info(f"\n📊 Results:")
    logger.info(f"   Viewport: {viewport_name}")
    logger.info(f"   Years downloaded: {args.years}")
    logger.info(f"   Pyramids: Created for web viewing")
    logger.info(f"   Vectors: Extracted for each year")
    logger.info(f"\n🚀 Next steps:")
    logger.info(f"   1. Run: bash restart.sh")
    logger.info(f"   2. Open: http://localhost:8001\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())

