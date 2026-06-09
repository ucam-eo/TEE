# Data formats

Tessera embeddings are **quantized** on disk to keep them small, then dequantized
to `float32` before any maths. `tessera-eval` reads the two quantizations the
Tessera ecosystem uses. This page documents both, with the exact dequantization
maths and the helpers that implement them.

A Tessera embedding is a **128-dimensional `float32` vector per ~10 m pixel, per
year**. All distance / similarity / classification maths happens in this real-valued
space — *never* in the quantized integer space, because each format scales
dimensions (or pixels) differently.

---

## 1. GeoTessera native — `int8 × per-pixel scale`

This is the on-disk format produced by the Tessera QAT pipeline and served by
[GeoTessera](https://github.com/ucam-eo/geotessera). A tile is two arrays:

| Array | Shape | Dtype | Meaning |
|---|---|---|---|
| embedding | `(H, W, 128)` | `int8` | quantized mantissa, values in `[-128, 127]` |
| scales | `(H, W)` | `float32` | **one scale per pixel**, shared across all 128 channels |

**Dequantize:**

```
float_embedding[y, x, :] = int8_embedding[y, x, :].astype(float32) * scale[y, x]
```

i.e. a single multiply per pixel — there is no per-dimension offset. Two pixels
have *different* scales, so you cannot compare their `int8` vectors directly; you
must dequantize first.

```python
from tessera_eval import dequantize_int8, load_geotessera_tile

# From arrays you already hold:
emb = dequantize_int8(int8_tile, scales)        # (H, W, 128) float32

# Or from a pair of .npy files:
emb = load_geotessera_tile("tile.npy", "tile_scales.npy")
```

`dequantize_int8(quantized, scales)` accepts `scales` of shape `(H, W)` (it adds
the channel axis) or `(H, W, 128)`.

Storage cost: `128 (int8) + 4 (one float32) = 132 bytes/pixel`.

---

## 2. TEE vector directory — per-dim `uint8`

The TEE viewer exports a directory of arrays for an area of interest and year. Each
embedding dimension is independently quantized to `uint8` over its min/max across
the whole area:

| File | Shape | Dtype | Meaning |
|---|---|---|---|
| `all_embeddings_uint8.npy.gz` | `(N, 128)` | `uint8` | per-dim quantized embeddings |
| `quantization.json` | — | JSON | `{"dim_min": [128 floats], "dim_max": [128 floats]}` |
| `pixel_coords.npy.gz` | `(N, 2)` | `int32` | pixel `(x, y)` for each row |
| `metadata.json` | — | JSON | `geotransform`, mosaic dimensions, CRS, … |

`N` is the number of valid pixels (nodata excluded).

**Dequantize (per dimension `d`):**

```
scale[d] = (dim_max[d] - dim_min[d]) / 255          # 0-range dims use scale 1
float[i, d] = uint8[i, d] / 255 * (dim_max[d] - dim_min[d]) + dim_min[d]
```

```python
from tessera_eval import load_tee_vectors, dequantize_uint8

vectors, coords, metadata = load_tee_vectors("/path/to/vectors/aoi/2024")
# vectors: float32 (N, 128); coords: int32 (N, 2); metadata: dict

# Or dequantize arrays directly:
vectors = dequantize_uint8(uint8_array, dim_min, dim_max)
```

### Pixel coordinates → longitude / latitude

`metadata["geotransform"]` is an affine map with keys `c` (origin x / longitude),
`a` (pixel width), `f` (origin y / latitude), `e` (pixel height, negative). For a
pixel `(px, py)` from `coords`:

```python
gt = metadata["geotransform"]
lon = gt["c"] + px * gt["a"]
lat = gt["f"] + py * gt["e"]
```

This is how labels (rasterized in geographic space) line up with embedding rows.

---

## Which format do I have?

- A `.npy` int8 tile **and** a `_scales.npy` file → **GeoTessera native** (§1).
- A directory with `all_embeddings_uint8.npy.gz` + `quantization.json` → **TEE
  vector directory** (§2).
- Other encodings (e.g. a residual-vector-quantized "VQ" bundle used for transport)
  are reconstructed **upstream** to a float mosaic and then re-quantized to one of
  the above before they reach this library — so they aren't a separate ingest path
  here.

## Precision note

Both formats are ~8-bit quantizations of the underlying `float32` embedding. The
quantization error is small relative to embedding magnitude and is generally
negligible for classification; if you need bit-exact embeddings, obtain the
`float32` source directly. Distances computed on dequantized vectors are in the
true embedding space and are comparable across tiles and across both formats.
