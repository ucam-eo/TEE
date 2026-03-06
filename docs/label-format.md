# TEE Label Serialisation Format

**Status:** Informational
**Version:** 1.0 (2026-03-06)

## 1. Overview

TEE exports labels in two formats: a native **JSON** format (lossless
round-trip with import) and a **GeoJSON** format (for use in GIS tools).
Both are UTF-8 encoded text files.

## 2. Coordinate Reference System

All geographic coordinates are **EPSG:4326** (WGS 84) with axes ordered
**longitude, latitude** — the GeoJSON convention per RFC 7946 §3.1.1.

| Field          | Convention          | Example            |
|----------------|---------------------|--------------------|
| `source_pixel` | `{lat, lon}`        | `{lat: 51.52, lon: -0.13}` |
| GeoJSON coords | `[lon, lat]`        | `[-0.13, 51.52]`   |

The `source_pixel` object uses `lat`/`lon` keys (note: not GeoJSON axis
order). GeoJSON geometry coordinates follow RFC 7946 `[longitude, latitude]`.

### 2.1 Geotransform

Pixel ↔ geographic conversions use a GDAL-style affine geotransform
stored in the viewport metadata:

```
lon = c + px * a       (a = pixel width,  c = origin longitude)
lat = f + py * e       (e = pixel height, f = origin latitude)
```

Fields `b` and `d` (rotation terms) are always zero for TEE viewports.
The transform is EPSG:4326-native — no intermediate projection is applied.

## 3. JSON Format

### 3.1 Envelope

```json
{
  "viewport": "<viewport-name>",
  "year": "<embedding-year>",
  "labels": [ <label>, ... ]
}
```

The importer also accepts a bare array `[<label>, ...]` for backward
compatibility.

**`viewport`** — string, the viewport name that produced the labels.
**`year`** — string, the embedding year used when the labels were created.

### 3.2 Label Object

| Field            | Type          | Required | Description |
|------------------|---------------|----------|-------------|
| `id`             | string        | yes      | Unique identifier, e.g. `"label_1709734200000"` |
| `name`           | string        | yes      | Human-readable label name |
| `color`          | string        | yes      | CSS colour, e.g. `"#e63946"` or `"hsl(0, 70%, 50%)"` |
| `threshold`      | number        | yes      | Euclidean distance threshold in embedding space |
| `source_pixel`   | object\|null  | no       | `{lat, lon}` of the seed pixel (EPSG:4326). Null for static labels. May be omitted if "Hide label locations" was checked on export. |
| `embedding`      | number[]      | no       | Float32 embedding vector at `source_pixel` (length = `embedding_dim`) |
| `pixel_coords`   | number[]      | no       | Flat array of `[px0, py0, px1, py1, ...]` pixel coordinates (raster-space integers) |
| `pixel_count`    | integer       | no       | Count of matched pixels |
| `static`         | boolean       | no       | `true` if label was promoted from segmentation with fixed pixel membership (not re-matchable) |
| `mean_distance`  | number        | no       | Mean embedding distance of matched pixels |
| `min_distance`   | number        | no       | Min embedding distance |
| `max_distance`   | number        | no       | Max embedding distance |
| `created`        | string        | no       | ISO 8601 timestamp |

### 3.3 Import Behaviour

On import, duplicate labels (matching `id`) are silently skipped.
Pixel membership is **not** stored in the export — it is recomputed from
`source_pixel` + `embedding` + `threshold` against the currently loaded
vector data. Labels with `pixel_coords` (promoted clusters) rebuild
pixels directly from the coordinate list.

### 3.4 Redacted Export

When the "Hide label locations" checkbox is ticked, `source_pixel` is
stripped from every label. This makes the file non-round-trippable:
re-imported labels cannot recompute pixel membership unless they carry
`pixel_coords`.

## 4. GeoJSON Format

### 4.1 Structure

```json
{
  "type": "FeatureCollection",
  "features": [ <feature>, ... ]
}
```

Each label's pixel footprint is decomposed into horizontal **run-length
rectangles** — one Feature per contiguous row of pixels.

### 4.2 Feature

```json
{
  "type": "Feature",
  "properties": {
    "label_name": "Forest",
    "label_color": "#38a169"
  },
  "geometry": {
    "type": "Polygon",
    "coordinates": [[ [lonL, latT], [lonR, latT], [lonR, latB], [lonL, latB], [lonL, latT] ]]
  }
}
```

Each polygon is an axis-aligned rectangle covering one horizontal run of
pixels. Coordinates are EPSG:4326 `[lon, lat]` per RFC 7946. Pixel
boundaries are computed as:

```
lonL = c + px_start * a          lonR = c + (px_end + 1) * a
latT = f + py * e                latB = f + (py + 1) * e
```

### 4.3 Notes

- GeoJSON export is blocked when "Hide label locations" is checked.
- Labels with no computed pixels are omitted.
- Multiple features may share the same `label_name` (one per run).
  Consumers should dissolve/merge by `label_name` if a single polygon
  per label is needed.
- The file extension is `.geojson` with MIME type `application/geo+json`.

## 5. localStorage Schema

Labels are persisted per-viewport under the key
`tee_labels_<viewport-name>`. The stored value is a JSON array of label
objects (same schema as §3.2), excluding transient fields `pixels` and
`visible`. On load, `visible` defaults to `true` and `pixels` is
recomputed from vectors.
