#!/usr/bin/env python3
"""
One-time migration: convert GeoTIFF pyramid levels to PNG + pyramid_meta.json.

Run on michael:
    python3 /app/scripts/migrate_tif_to_png.py

Or with a custom pyramids directory:
    python3 scripts/migrate_tif_to_png.py /data/pyramids

For each year directory that has .tif files but no .png files, this script:
1. Opens each level_N.tif with rasterio
2. Saves it as level_N.png with PIL
3. Writes pyramid_meta.json with geotransform from the GeoTIFF
4. Removes the .tif files after successful conversion

Dry-run mode (no changes):
    python3 scripts/migrate_tif_to_png.py --dry-run
"""

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

NUM_ZOOM_LEVELS = 6


def convert_year_dir(year_dir, dry_run=False):
    """Convert all level_*.tif in a year directory to PNG + meta."""
    tif_files = sorted(year_dir.glob("level_*.tif"))
    png_files = sorted(year_dir.glob("level_*.png"))
    meta_path = year_dir / "pyramid_meta.json"

    if not tif_files:
        return False  # nothing to convert

    if png_files and meta_path.exists():
        print(f"  SKIP {year_dir} — already has PNG pyramids")
        return False

    print(f"  CONVERT {year_dir} ({len(tif_files)} tif files)")

    if dry_run:
        return True

    import rasterio

    def _transform_dict(t):
        return {"a": t.a, "b": t.b, "c": t.c, "d": t.d, "e": t.e, "f": t.f}

    meta = {"crs": None, "levels": []}

    for tif_path in tif_files:
        level_idx = int(tif_path.stem.split("_")[1])
        png_path = year_dir / f"level_{level_idx}.png"

        with rasterio.open(tif_path) as src:
            data = src.read()  # (bands, H, W)
            transform = src.transform
            crs = str(src.crs)

            if data.shape[0] == 1:
                rgb = np.stack([data[0], data[0], data[0]], axis=0)
            else:
                rgb = data[:3]

            img = Image.fromarray(np.transpose(rgb, (1, 2, 0)).astype(np.uint8), mode="RGB")
            img.save(png_path, format="PNG")

            if meta["crs"] is None:
                meta["crs"] = crs

            # Ensure levels list is large enough
            while len(meta["levels"]) <= level_idx:
                meta["levels"].append(None)

            meta["levels"][level_idx] = {
                "file": f"level_{level_idx}.png",
                "width": src.width,
                "height": src.height,
                "transform": _transform_dict(transform),
            }

            size_kb = png_path.stat().st_size / 1024
            print(f"    level_{level_idx}: {src.width}x{src.height} → PNG ({size_kb:.1f} KB)")

    # Remove None gaps (shouldn't happen but be safe)
    meta["levels"] = [l for l in meta["levels"] if l is not None]

    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    # Remove .tif files after successful conversion
    for tif_path in tif_files:
        tif_path.unlink()
        print(f"    deleted {tif_path.name}")

    return True


def main():
    dry_run = "--dry-run" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        pyramids_dir = Path(args[0])
    else:
        # Default: try /data/pyramids (Docker) or local viewports
        for candidate in [Path("/data/pyramids"), Path("viewports")]:
            if candidate.exists():
                pyramids_dir = candidate
                break
        else:
            print("Usage: python3 migrate_tif_to_png.py [/path/to/pyramids] [--dry-run]")
            sys.exit(1)

    if dry_run:
        print(f"DRY RUN — scanning {pyramids_dir}")
    else:
        print(f"Migrating TIF→PNG in {pyramids_dir}")

    converted = 0
    for viewport_dir in sorted(pyramids_dir.iterdir()):
        if not viewport_dir.is_dir():
            continue
        for year_dir in sorted(viewport_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            if convert_year_dir(year_dir, dry_run=dry_run):
                converted += 1

    if dry_run:
        print(f"\nDry run complete: {converted} directories would be converted")
    else:
        print(f"\nDone: {converted} directories converted")


if __name__ == "__main__":
    main()
