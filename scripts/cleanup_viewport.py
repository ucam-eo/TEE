#!/usr/bin/env python3
"""
Clean up all traces of a viewport by name.

Usage:
    python3 scripts/cleanup_viewport.py Eddington
    python3 scripts/cleanup_viewport.py Eddington --data-dir /data

Removes: viewport file, config, pyramids, vectors, progress files,
mosaics, embeddings cache tiles, active viewport symlink (if pointing
to this viewport).
"""

import argparse
import shutil
import sys
from pathlib import Path

def cleanup(name, data_dir, viewports_dir):
    removed = []

    # Viewport files
    for f in [viewports_dir / f"{name}.txt", viewports_dir / f"{name}_config.json"]:
        if f.exists():
            f.unlink()
            removed.append(str(f))

    # Active viewport symlink
    active = viewports_dir / "viewport.txt"
    if active.is_symlink():
        target = active.resolve().name if active.exists() else str(active.readlink())
        if name in target:
            active.unlink()
            removed.append(f"{active} (symlink)")

    active_file = viewports_dir / ".active"
    if active_file.exists():
        content = active_file.read_text().strip()
        if content == name:
            active_file.unlink()
            removed.append(str(active_file))

    # Data directories
    for subdir in ["pyramids", "vectors"]:
        d = data_dir / subdir / name
        if d.exists():
            shutil.rmtree(d)
            removed.append(str(d))

    # Progress files
    progress_dir = data_dir / "progress"
    if progress_dir.exists():
        for f in progress_dir.glob(f"{name}_*"):
            f.unlink()
            removed.append(str(f))

    # Mosaics
    mosaics_dir = data_dir / "mosaics"
    if mosaics_dir.exists():
        for f in mosaics_dir.glob(f"{name}_*"):
            f.unlink()
            removed.append(str(f))
        rgb_dir = mosaics_dir / "rgb"
        if rgb_dir.exists():
            for f in rgb_dir.glob(f"{name}_*"):
                f.unlink()
                removed.append(str(f))

    # Share directory
    share_dir = data_dir / "share"
    if share_dir.exists():
        for user_dir in share_dir.iterdir():
            vp_share = user_dir / name
            if vp_share.exists():
                shutil.rmtree(vp_share)
                removed.append(str(vp_share))

    return removed


def main():
    parser = argparse.ArgumentParser(description="Clean up all traces of a viewport")
    parser.add_argument("name", help="Viewport name to clean up")
    parser.add_argument("--data-dir", default=str(Path.home() / "data"),
                        help="Data directory (default: ~/data)")
    parser.add_argument("--viewports-dir", default=None,
                        help="Viewports directory (default: <script-dir>/../viewports)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be removed")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    viewports_dir = Path(args.viewports_dir) if args.viewports_dir else Path(__file__).resolve().parent.parent / "viewports"

    if args.dry_run:
        print(f"DRY RUN — would clean up viewport '{args.name}':")
    else:
        print(f"Cleaning up viewport '{args.name}':")

    if not args.dry_run:
        removed = cleanup(args.name, data_dir, viewports_dir)
    else:
        # Simulate
        removed = []
        for f in [viewports_dir / f"{args.name}.txt", viewports_dir / f"{args.name}_config.json"]:
            if f.exists(): removed.append(str(f))
        for subdir in ["pyramids", "vectors"]:
            d = data_dir / subdir / args.name
            if d.exists(): removed.append(str(d))
        progress_dir = data_dir / "progress"
        if progress_dir.exists():
            for f in progress_dir.glob(f"{args.name}_*"):
                removed.append(str(f))

    if removed:
        for r in removed:
            print(f"  {'would remove' if args.dry_run else 'removed'}: {r}")
        print(f"\n{'Would remove' if args.dry_run else 'Removed'} {len(removed)} items")
    else:
        print(f"  No traces found for '{args.name}'")


if __name__ == "__main__":
    main()
