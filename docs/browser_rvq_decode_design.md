# Browser-side RVQ decode — design

**Status: LANDED 2026-06-06.** On building it we found the feature was ~90% already
implemented from a prior effort — this doc's "new ArrayBuffer format + new JS decoder"
were redundant. What actually existed vs what was missing:

- **JS decoder — already shipped.** `public/js/vectors.js`: `downloadVectorData()` prefers
  the VQ path when `vq_metadata.json` exists (else falls back to int8), and
  `downloadVectorDataVq()` + `_decodeCodebook()` fetch per-tile `codebooks{1,2}_uint8` +
  `_scales` (per-dim min/max) + `indices{1,2}` + `tile_index.json` and reconstruct.
- **Producer — already shipped and wired.** `process_viewport.save_vectors_rvq()` writes
  exactly those files; it's called at the dual-write site (`if qs is not None:`) whenever a
  viewport is processed via the VQTessera fast path (`fetch_quantized_structure`).
- **The only gap — serving.** `api/views/vector_data.py`'s `ALLOWED_FILES` omitted the VQ
  files, so the frontend got 403s and silently used the ~28 MB uint8 mosaic. **Fixed**
  (commit a8e9d76): the VQ bundle is now served, lighting up the whole path.
- **RLE vs gzip (investigated per request): keep gzip.** Indices are stored gzip'd-raw.
  Measured on realistic idx1: `gzip(raw)` ≈ 0.025–0.25 B/px and **beats explicit RLE**
  (RLE alone ~4× worse; `gzip(RLE)` only ties) — DEFLATE already captures the runs, and
  RLE would need a JS decoder for no gain. No change made.

So the wire format below is **not** what shipped (the shipped format is the multi-`.npy.gz`
bundle the existing JS reads); the rest of this doc is retained as background/rationale and
for the not-yet-built linear fast path (§5).

Remaining optional follow-ups: the **linear fast path** (§5, decode-free for RGB/probes),
and migrating existing int8-only viewports (they keep working via fallback; they gain the
VQ bundle on reprocess).

---

_Original design (pre-discovery) follows._

**Goal:** ship VQ-compressed embeddings over the TEE→browser hop and decode them in
JavaScript, instead of sending raw int8 vectors.

---

## 1. Why

The tessera-vq bolt-on already serves RVQ-compressed embeddings, but it runs on
**michael**, co-located with the TEE backend — so that compression only ever crosses a
**localhost** hop and saves no real bandwidth. The hop that actually costs latency is
**TEE backend → browser over the internet**, where TEE currently ships **int8 (128
bytes/pixel)**. A ~250k-pixel viewport is ~30 MB.

The recommended RVQ config (t=512, k1=20, k2=256, int8 codebooks + RLE'd stage-1 plane)
is **~1.7 bytes/pixel ≈ 73× smaller**. Decoding in the browser turns a ~30 MB viewport
download into **~0.4 MB** — seconds → sub-second on a slow link. The reconstruction is
downstream-lossless (validated: ΔF1 ≈ 0 on the Austria classifier), so anything the
browser does with the vectors is unaffected.

This is purely about moving the *existing* compression to the *hop that matters*; no new
modelling.

---

## 2. Data flow: today vs proposed

```
today:     geotessera ── RVQ ──▶ bolt-on ──(localhost, compressed)──▶ TEE backend
                                                 │  reconstruct → int8 mosaic
                                                 ▼
                                      browser  ◀──(WAN, int8 ~128 B/px)──  TEE backend

proposed:  geotessera ── RVQ ──▶ bolt-on ──(localhost, compressed)──▶ TEE backend
                                                 │  PASS THROUGH (no reconstruct)
                                                 ▼
                                      browser  ◀──(WAN, RVQ ~1.7 B/px)──  TEE backend
                                                 │  JS decode → Float32 mosaic
                                                 ▼  existing browser consumers
```

The one behavioural change on the backend: **stop reconstructing**; forward the compressed
structure. The Python client already supports this — `VQTessera.fetch_quantized_structure()`
returns the codebooks + index maps **without** the float reconstruct that
`fetch_mosaic_for_region()` does.

---

## 3. Browser-facing wire format

Do **not** ship NPZ (a zip of .npy — painful to parse in JS). Define a flat, self-describing
`ArrayBuffer`:

```
[0:4]      uint32 LE   header_len
[4:4+H]    utf-8 JSON  header
[4+H:]                 concatenated little-endian section buffers
```

Header JSON:

```json
{
  "version": 1,
  "t": 512, "k1": 20, "k2": 256,
  "full_h": 1024, "full_w": 1024,        // mosaic shape (pre tile-multiple truncation)
  "n_tiles": 4,
  "idx1_dtype": "uint8", "idx2_dtype": "uint8",   // uint16 if k>256
  "sections": [
    {"name":"positions",     "dtype":"int32",   "shape":[4,2],     "offset":0,    "nbytes":32},
    {"name":"cb1_q",         "dtype":"uint8",   "shape":[4,20,128], "offset":...},
    {"name":"cb1_lo",        "dtype":"float32", "shape":[4,128],    "offset":...},
    {"name":"cb1_hi",        "dtype":"float32", "shape":[4,128],    "offset":...},
    {"name":"idx1_values",   "dtype":"uint8",   "shape":[NV1],      "offset":...},
    {"name":"idx1_lengths",  "dtype":"uint32",  "shape":[NV1],      "offset":...},
    {"name":"idx1_runs",     "dtype":"int32",   "shape":[4],        "offset":...},
    {"name":"cb2_q",         "dtype":"uint8",   "shape":[4,256,128],"offset":...},
    {"name":"cb2_lo",        "dtype":"float32", "shape":[4,128],    "offset":...},
    {"name":"cb2_hi",        "dtype":"float32", "shape":[4,128],    "offset":...},
    {"name":"idx2",          "dtype":"uint8",   "shape":[4,512,512],"offset":...}
  ]
}
```

JS reads `header_len` via `DataView`, the header via `TextDecoder`, then maps each section
to a typed-array view (`new Float32Array(buf, base+offset, count)` etc.) with zero copies.
Serve with HTTP `Content-Encoding: gzip` for a little extra (browsers auto-inflate); RVQ is
already high-entropy so gzip gains are modest, mostly on the codebooks.

A small Python serializer in the TEE backend builds this from the `QuantizedStructure`
(it's a re-pack of the same arrays the NPZ holds — no recomputation).

---

## 4. JS decoder (reference algorithm)

A direct port of `tessera_vq.client.reconstruct_from_structure` +
`codebook_codec.dequantize_codebook_uint8` + `entropy.rle_decode_stack`:

```js
// 1. dequantize codebooks: cb[n,k,d] = lo[n,d] + q/255 * (hi[n,d]-lo[n,d])
function dequant(q, lo, hi, n, k, D) {
  const out = new Float32Array(n*k*D);
  for (let i=0;i<n;i++) for (let c=0;c<k;c++) for (let d=0;d<D;d++) {
    const s = (hi[i*D+d]-lo[i*D+d])/255;
    out[(i*k+c)*D+d] = lo[i*D+d] + q[(i*k+c)*D+d]*s;
  }
  return out;
}
// 2. RLE-expand idx1 per tile (values/lengths sliced by runs) -> Uint8/16Array (t*t)
// 3. reconstruct each tile p: cb1[idx1[p]] + cb2[idx2[p]], write into the mosaic at
//    its tile position (positions[i]*t).
```

**Cost:** per-pixel reconstruct is `n_px · 128` adds — ~32M for a 250k-px viewport, tens of
ms in plain typed-array JS. Reach for **WASM or WebGPU** only when pushing to large
viewports (see §8).

---

## 5. The linear fast path (decode-free for many interactions)

Many browser interactions apply a **linear** map `W` to the embeddings — RGB stretch,
PCA-to-3-bands, a linear probe. By linearity:

```
W·(cb1[i] + cb2[j]) = (W·cb1)[i] + (W·cb2)[j]
```

So apply `W` to the **~276 codebook vectors once** → `Wcb1 (k1×Cout)`, `Wcb2 (k2×Cout)`;
then per pixel is a gather-and-add in the **output** dimension (`Cout` = 3 for RGB, n_classes
for a probe). That's orders of magnitude cheaper than a per-pixel 128-d matmul, and it never
materializes the 128-d mosaic. Provide it as a JS utility; nonlinear models (RF/MLP) fall
back to full reconstruct (§4).

---

## 6. Backend changes (TEE / Django)

- **Content negotiation** on the embedding endpoint: `Accept: application/x-tessera-rvq`
  (or a `?format=rvq` param / per-viewport feature flag) returns the compressed
  ArrayBuffer; default stays int8 so old clients are unaffected and we can A/B it.
- Handler: `struct = provider.fetch_quantized_structure(bbox, year)` →
  `serialize_rvq(struct)` → `Response(..., content_type="application/x-tessera-rvq")` with
  gzip. **No `reconstruct_from_structure` on the backend.**
- Requires the fast-path provider (`VQTessera`); for non-fast-path viewports (plain
  GeoTessera) there's no compressed structure — fall back to int8.

## 7. Browser integration

- Fetch → `ArrayBuffer` → `decodeRVQ()` → the **same representation the current consumer
  expects** (Float32 mosaic, or re-quantized int8 if downstream wants int8). An adapter
  keeps existing viewer code unchanged.
- The compressed blob is tiny → the browser can **cache many more viewports** client-side
  (IndexedDB/in-memory) than it can at 30 MB each.

---

## 8. Performance budget & fallbacks

| viewport | int8 download | RVQ download | JS decode (typed-array) |
|---|---:|---:|---:|
| ~250k px | ~30 MB | ~0.4 MB | tens of ms |
| ~1M px | ~128 MB | ~1.7 MB | ~100–200 ms |

- Decode is the only new client cost; on any bandwidth-limited link the net latency win is
  large. On a LAN/fast link, negotiation lets a client still ask for int8.
- Accel ladder if decode becomes the bottleneck: typed arrays → WASM (SIMD) → WebGPU
  (codebook gather as a shader). Start with typed arrays.

## 9. Correctness

- Golden test: JS `decodeRVQ()` must match Python `reconstruct_from_structure()` on a fixed
  sample (ship a small fixture: structure bytes + expected Float32 mosaic; assert max-abs
  diff ≈ 0 within int8 codebook tolerance). Run in CI (Node).
- Downstream-losslessness of the RVQ itself is already validated (v0.5.0 int8 gate).

## 10. Phasing

1. **Backend serializer + endpoint** (Python): `serialize_rvq(struct)` + negotiated route.
2. **JS decoder** (full reconstruct) + adapter into the existing consumer + golden test.
3. **Linear fast path** JS utility (RGB / probe) + browser-side blob caching.
4. **WASM/WebGPU** only if §8 says decode is the bottleneck.

Phases 1–2 deliver the bandwidth win; 3 adds the compute win; 4 is contingency.

## 11. Open decisions

- **Consumer representation:** does the browser want Float32, or re-quantized int8 to match
  the current pipeline byte-for-byte? (Affects the adapter and a tiny bit of size.)
- **Negotiation mechanism:** `Accept` header vs `?format=` vs per-viewport flag.
- **Where the JS decoder lives:** TEE frontend bundle (vendored module) vs a small npm
  package shared with other consumers.
- **gzip on top:** worth it for the codebooks/scales; confirm it's enabled at nginx/Django.
- **Tiling on the browser:** assemble into one mosaic, or keep per-tile (lazy-decode only
  visible tiles)? Per-tile lazy decode pairs well with the linear fast path.
