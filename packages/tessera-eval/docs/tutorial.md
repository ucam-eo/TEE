# Tutorial: classify a habitat from labelled polygons

This walks through the full workflow: labelled polygons → Tessera embeddings →
cross-validation → learning curve → spatial hold-out → confusion matrix. It
assumes you have some polygons with a class attribute (a habitat survey, a crop
map, land-cover ground truth) as a shapefile or GeoJSON in any CRS.

```bash
pip install "tessera-eval[geotessera,plot]"
```

## 1. Inspect your labels

```python
import geopandas as gpd
from tessera_eval import detect_field_type

gdf = gpd.read_file("habitats.geojson")
print(gdf.columns.tolist())
print(gdf["habitat"].value_counts())
print(detect_field_type(gdf, "habitat"))   # "classification" or "regression"
```

`detect_field_type` treats a numeric column with many unique values as a regression
target; everything else is classification. The rest of this tutorial assumes
classification (`habitat` is categorical).

## 2. Pull embeddings under the polygons

```python
from geotessera import GeoTessera
from tessera_eval import load_embeddings_for_shapefile

gt = GeoTessera()
vectors, labels, class_names, stats = load_embeddings_for_shapefile(
    gdf, field="habitat", year=2024, gt_instance=gt,
    callback=lambda i, n: print(f"tile {i}/{n}", end="\r"),
)
print(f"\n{stats['total_pixels']:,} pixels, {stats['n_classes']} classes: {class_names}")
```

- `vectors` is `float32 (N, 128)` — one dequantized embedding per labelled pixel.
- `labels` is `int (N,)`, 0-indexed; `class_names[k]` is the name of class `k`.
- Loading is **memory-bounded**: one tile is read, labelled pixels kept, tile
  discarded — so this scales to large areas.

## 3. A quick cross-validation

```python
from tessera_eval import run_kfold_cv

for event in run_kfold_cv(vectors, labels, ["nn", "rf", "mlp"], k=5):
    if event["type"] == "aggregate":
        for name, m in event["models"].items():
            print(f"{name:>4}: macro-F1 {m['mean_f1']:.3f} ± {m['std_f1']:.3f} "
                  f"| weighted-F1 {m['mean_f1w']:.3f}")
```

**Read both numbers.** Macro-F1 averages classes equally (rare classes count);
weighted-F1 weights by class frequency. A big gap means rare classes are being
missed — common, and exactly what you want to see.

## 4. How much labelling is enough? — learning curve

`run_learning_curve` trains at increasing fractions of your labels, so you can see
where accuracy plateaus (i.e. whether more labelling would help):

```python
import numpy as np

curve = {}
for event in run_learning_curve(
    vectors, labels,
    classifier_names=["rf"],
    training_pcts=[1, 5, 10, 30, 50, 80],
    repeats=5,
):
    if event["type"] == "progress":
        curve[event["pct"]] = event["classifiers"]["rf"]["mean_f1"]

for pct, f1 in curve.items():
    print(f"{pct:>3}% of labels → macro-F1 {f1:.3f}")
```

Plot `curve` (x = training pixels, y = F1) to find the plateau.

## 5. Honest accuracy — spatial hold-out

A random pixel split **overstates** accuracy: neighbouring pixels are highly
correlated, so test pixels look a lot like training pixels. For a defensible
number, hold out a *spatially separate* region. Split your polygons by a bounding
box, load each side, and pass the held-out side as a fixed test set:

```python
minx, miny, maxx, maxy = gdf.total_bounds
mid = (minx + maxx) / 2
west, east = gdf[gdf.centroid.x < mid], gdf[gdf.centroid.x >= mid]

Xtr, ytr, names, _ = load_embeddings_for_shapefile(west, "habitat", 2024, gt)
Xte, yte, _,     _ = load_embeddings_for_shapefile(east, "habitat", 2024, gt)

for event in run_learning_curve(
    Xtr, ytr, ["rf"], training_pcts=[10, 50, 100],
    test_vectors=Xte, test_labels=yte,     # <- fixed spatial test set
):
    if event["type"] == "progress":
        print(f"{event['pct']:>3}% → spatial macro-F1 "
              f"{event['classifiers']['rf']['mean_f1']:.3f}")
```

Expect lower (more realistic) F1 than the random split. The gap between random and
spatial accuracy is itself informative — it quantifies spatial autocorrelation in
your labels. (For class IDs to line up across the two loads, both must contain the
same set of classes.)

## 6. Confusion matrix

The largest training percentage (or k-fold) yields a confusion matrix — which
classes get confused for which:

```python
cm = None
for event in run_kfold_cv(vectors, labels, ["rf"], k=5):
    if event["type"] == "confusion_matrices":
        cm = np.array(event["confusion_matrices"]["rf"])

# Row-normalize to per-class recall
recall = cm / cm.sum(axis=1, keepdims=True).clip(min=1)
for i, name in enumerate(class_names):
    worst = np.argsort(recall[i])[::-1]
    print(f"{name:>20}: recall {recall[i, i]:.2f} "
          f"(most confused with {class_names[worst[1]]})")
```

## 7. Spatial features (optional)

Land cover is spatially smooth, so a pixel's neighbours carry signal. The
`spatial_mlp` models consume `(2r+1)²·128`-d neighbourhood features. Build them
from a contiguous tile with `gather_spatial_features_2d`, or from sparse
`(coords)` with `gather_spatial_features`, and pass via `spatial_vectors=` to
`run_learning_curve`. See the [API reference](api-reference.md).

## 8. Regression

For a continuous target (e.g. canopy height), use `run_kfold_cv(..., task="regression")`
with regressor names (`rf_reg`, `nn_reg`, …); aggregate events carry
`mean_r2 / mean_rmse / mean_mae`.

---

### Pointers

- **Models:** `rf` is the robust default; `nn` is a fast baseline; `mlp` /
  `spatial_mlp` can win with enough labels; `xgboost` if installed; `unet` for
  dense tile segmentation (needs torch).
- **Reproducibility:** pass `seed=` to `run_kfold_cv`; estimators are already
  seeded.
- **Already have a TEE vector directory** instead of raw GeoTessera tiles? Use
  `load_tee_vectors`, rasterize your shapefile onto its grid with
  `rasterize_shapefile` (using the geotransform from `metadata`), and extract the
  labelled rows — then the evaluation steps above are identical.
