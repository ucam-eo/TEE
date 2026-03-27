#!/usr/bin/env python3
"""Standalone CLI for large-area evaluation.

Evaluates classifiers/regressors on ground-truth shapefiles that cover
areas larger than a single viewport, using GeoTessera tile-by-tile loading.

Usage:
    python scripts/tee_evaluate.py --config eval_config.json [--dry-run] [--stdout]

No Django dependency. Uses tessera_eval + geotessera.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import geopandas as gpd


def emit(event, file=sys.stdout):
    """Write a single NDJSON event to the given file."""
    print(json.dumps(event, default=str), file=file, flush=True)


def validate_config(config):
    """Validate config dict, raising ValueError on problems."""
    if "$schema" not in config:
        raise ValueError("Missing '$schema' field in config")

    shapefile = config.get("shapefile")
    if not shapefile:
        raise ValueError("Missing 'shapefile' path in config")
    if not Path(shapefile).exists():
        raise ValueError(f"Shapefile not found: {shapefile}")

    fields = config.get("fields")
    if not fields or not isinstance(fields, list) or len(fields) == 0:
        raise ValueError("Config must have at least one field in 'fields'")

    for f in fields:
        if "name" not in f:
            raise ValueError(f"Field entry missing 'name': {f}")

    # Validate classifier/regressor names
    from tessera_eval.classify import available_classifiers, available_regressors
    valid_clf = set(available_classifiers())
    valid_reg = set(available_regressors())
    # Remove spatial classifiers — not supported for large-area mode
    valid_clf -= {"spatial_mlp", "spatial_mlp_5x5"}

    for name in config.get("classifiers", {}):
        if name not in valid_clf:
            raise ValueError(
                f"Invalid classifier '{name}'. "
                f"Available (pixel-level only): {sorted(valid_clf)}"
            )

    for name in config.get("regressors", {}):
        if name not in valid_reg:
            raise ValueError(
                f"Invalid regressor '{name}'. Available: {sorted(valid_reg)}"
            )

    return config


from tessera_eval.evaluate import detect_field_type


def load_shapefile(shapefile_path):
    """Load and prepare a shapefile."""
    gdf = gpd.read_file(shapefile_path)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def run_dry_run(config, gdf, out=sys.stdout):
    """Print stats without downloading or evaluating."""
    from geotessera import GeoTessera

    bounds = gdf.total_bounds
    bbox = (bounds[0], bounds[1], bounds[2], bounds[3])

    gt = GeoTessera()

    for year in config.get("years", [2024]):
        tiles = gt.registry.load_blocks_for_region(bbox, year)

        for field_spec in config["fields"]:
            field_name = field_spec["name"]

            if field_name not in gdf.columns:
                emit({"event": "error", "message": f"Field '{field_name}' not found in shapefile"}, file=out)
                continue

            field_type = field_spec.get("type", "auto")
            if field_type == "auto":
                field_type = detect_field_type(gdf, field_name)

            col = gdf[field_name].dropna()
            unique_count = col.nunique()

            if field_type == "classification":
                models = list(config.get("classifiers", {"nn": {}, "rf": {}}).keys())
            else:
                models = list(config.get("regressors", {"rf_reg": {}}).keys())

            emit({
                "event": "dry_run",
                "year": year,
                "field": field_name,
                "field_type": field_type,
                "unique_values": unique_count,
                "features": len(gdf),
                "tile_count": len(tiles),
                "models": models,
                "kfold": config.get("kfold", 5),
                "bbox": list(bbox),
            }, file=out)


def run_evaluation(config, gdf, out=sys.stdout):
    """Run full evaluation for each year and field."""
    from geotessera import GeoTessera
    from tessera_eval.data import load_embeddings_for_shapefile
    from tessera_eval.evaluate import run_kfold_cv

    gt = GeoTessera()
    seed = config.get("seed", 42)
    k = config.get("kfold", 5)
    max_train = config.get("max_training_samples")
    output_dir = Path(config.get("output_dir", "./eval_output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    for year in config.get("years", [2024]):
        for field_spec in config["fields"]:
            field_name = field_spec["name"]
            field_type = field_spec.get("type", "auto")
            if field_type == "auto":
                field_type = detect_field_type(gdf, field_name)

            if field_name not in gdf.columns:
                emit({"event": "error", "message": f"Field '{field_name}' not in shapefile"}, file=out)
                continue

            is_classification = (field_type == "classification")

            if is_classification:
                model_names = list(config.get("classifiers", {"nn": {}, "rf": {}}).keys())
                model_params = config.get("classifiers", {})
                task = "classification"
            else:
                model_names = list(config.get("regressors", {"rf_reg": {}}).keys())
                model_params = config.get("regressors", {})
                task = "regression"

            emit({
                "event": "field_start",
                "field": field_name,
                "type": field_type,
                "year": year,
                "models": model_names,
                "kfold": k,
            }, file=out)

            # Load embeddings tile by tile
            def progress_cb(current, total):
                emit({"event": "download_progress", "tile": current, "total": total}, file=out)

            t0 = time.time()
            try:
                vectors, labels, class_names, stats = load_embeddings_for_shapefile(
                    gdf, field_name, year, gt, callback=progress_cb,
                )
            except ValueError as e:
                emit({"event": "error", "message": str(e)}, file=out)
                continue

            emit({
                "event": "start",
                "classifiers": model_names,
                "total_labelled_pixels": stats["total_pixels"],
                "classes": [{"name": n} for n in class_names] if is_classification else [],
                "confusion_matrix_labels": class_names if is_classification else [],
                "stats": stats,
            }, file=out)

            # Run k-fold CV
            for event in run_kfold_cv(
                vectors, labels, model_names, k=k, task=task,
                model_params=model_params, max_training_samples=max_train, seed=seed,
            ):
                emit({"event": event["type"], **{k: v for k, v in event.items() if k != "type"}}, file=out)

            elapsed = time.time() - t0

            # Write results file
            result_file = output_dir / f"{field_name}_{year}_{task}.json"
            result_data = {
                "field": field_name,
                "year": year,
                "task": task,
                "models": model_names,
                "kfold": k,
                "stats": stats,
                "elapsed_seconds": round(elapsed, 1),
            }
            result_file.write_text(json.dumps(result_data, indent=2))

            emit({
                "event": "done",
                "field": field_name,
                "year": year,
                "elapsed_seconds": round(elapsed, 1),
                "result_file": str(result_file),
            }, file=out)


def main():
    parser = argparse.ArgumentParser(
        description="Large-area evaluation using GeoTessera embeddings",
    )
    parser.add_argument("--config", required=True, help="Path to JSON config file")
    parser.add_argument("--dry-run", action="store_true", help="Print stats, don't evaluate")
    parser.add_argument("--stdout", action="store_true", help="Write NDJSON to stdout (default)")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(json.dumps({"event": "error", "message": f"Config file not found: {config_path}"}))
        sys.exit(1)

    config = json.loads(config_path.read_text())

    try:
        validate_config(config)
    except ValueError as e:
        print(json.dumps({"event": "error", "message": str(e)}))
        sys.exit(1)

    gdf = load_shapefile(config["shapefile"])

    if args.dry_run:
        run_dry_run(config, gdf)
    else:
        run_evaluation(config, gdf)


if __name__ == "__main__":
    main()
