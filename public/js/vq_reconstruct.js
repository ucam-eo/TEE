// vq_reconstruct.js — pure, dependency-free VQ codebook decode + reconstruction.
//
// Extracted out of vectors.js so it can be shared between the main viewer
// (vectors.js::downloadVectorDataVq, reading the persisted per-viewport
// codebook/index files) and postcard.html (reading an ad-hoc, nothing-
// persisted bundle for one bbox). No DOM references, no module-level state --
// safe to import from either a full app page or a standalone one.

// uint8Buf shape: (nTiles, k, dim); scalesBuf shape: (nTiles, dim, 2).
// Decode each codebook entry to float using the per-tile per-dim (min, max).
export function decodeCodebook(uint8Buf, scalesBuf, nTiles, k, dim) {
    const out = new Float32Array(nTiles * k * dim);
    for (let t = 0; t < nTiles; t++) {
        const scaleBase = t * dim * 2;
        const tileBase = t * k * dim;
        for (let i = 0; i < k; i++) {
            const inBase = tileBase + i * dim;
            for (let d = 0; d < dim; d++) {
                const mn = scalesBuf[scaleBase + d * 2];
                const mx = scalesBuf[scaleBase + d * 2 + 1];
                const span = (mx - mn) || 1.0;
                out[inBase + d] = mn + (uint8Buf[inBase + d] / 255.0) * span;
            }
        }
    }
    return out;
}

// Respects npy/array dtype -- k > 256 needs uint16, otherwise uint8.
export function indicesArrayFromParsed(parsed) {
    if (parsed.dtype === '<u2' || parsed.dtype === '>u2' || parsed.dtype === 'u2') {
        return new Uint16Array(parsed.rawData);
    }
    return new Uint8Array(parsed.rawData);
}

// Which tile index along one axis covers pixel p, given n tiles total and the
// axis's true (fullDim, t)? Mirrors tessera_vq's tile_pixel_offset inverse
// (see tessera_vq.sweep / tessera_vq.client, tessera-vq >=0.6.0): fixed
// t-stride, except the last tile is pulled back to end exactly at fullDim
// instead of a remainder strip going uncovered when fullDim isn't itself a
// multiple of t. Pixels in the resulting overlap between the last two tiles
// resolve to the *last* tile here, matching how the per-pixel index arrays
// were assembled server-side (later tile positions overwrite earlier ones
// for shared pixels -- see tessera_vq.sweep.quantize_window_for_serving /
// _assemble_indices_from_tiles in process_viewport.py).
function tileIndexForPixel(p, n, fullDim, t) {
    if (p >= fullDim - t) return n - 1;
    return Math.floor(p / t);
}

// Reconstructs a (outH*outW, dim) float32 mosaic -- or, if `crop` is given,
// just the {top, left, height, width} rectangle of it -- via per-pixel
// codebook lookup: codebooks1[tileId][idx1[pixel]] (+ codebooks2[tileId]
// [idx2[pixel]] for RVQ). idx1/idx2 are still the *full* flat (outH*outW)
// tile-local index arrays either way (they're cheap: 1-2 bytes/pixel, not
// dim*4) -- only the float output is ever restricted to the crop. cb1Float/
// cb2Float are flat (nTiles*k*dim) decoded codebooks (see decodeCodebook).
//
// The crop option exists because outH*outW can be far bigger than any
// caller actually wants: geotessera's read_region() rounds a bbox fetch out
// to whole source tiles, so a real postcard bbox can come back needing an
// output_shape like 2030x2040 -- reconstructing that in full is
// 2030*2040*128*4 bytes, ~2GB, and allocating that in one contiguous
// Float32Array reliably threw "Array buffer allocation failed" on
// memory-constrained (mostly mobile) browsers in production, even though
// postcard.html only ever keeps a ~1000x600px crop of it afterward. Pass
// `crop` to skip ever materializing the discarded majority of the mosaic.
// Omit it (every other caller -- the main viewer's full-mosaic downloads)
// for the original full-mosaic behaviour.
//
// nTileRows/nTileCols + outH/outW must all come from the *same* source
// (meta.n_tile_rows/n_tile_cols/output_shape from a postcard bundle, or
// vqMeta's equivalents for a persisted viewport) -- tileIndexForPixel's rule
// only pulls the last tile back when outH/outW isn't itself a multiple of t.
// Viewports written before tessera-vq 0.6.0's tiling fix (process_viewport.py
// ::save_vectors_rvq) always have output_shape as an *exact* t multiple (the
// old (full_h // t) * t truncation), so this reduces to plain
// Math.floor(p / t) for them automatically -- no separate code path needed,
// old viewports keep reading correctly and only newly-created ones (or a
// postcard, which is never persisted) actually exercise the pulled-back
// last tile.
export function reconstructFloatMosaic({ idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim, crop }) {
    const top = crop ? crop.top : 0;
    const left = crop ? crop.left : 0;
    const cropH = crop ? crop.height : outH;
    const cropW = crop ? crop.width : outW;
    const floatMosaic = new Float32Array(cropH * cropW * dim);
    for (let ly = 0; ly < cropH; ly++) {
        const py = top + ly;
        const tileRow = tileIndexForPixel(py, nTileRows, outH, t);
        for (let lx = 0; lx < cropW; lx++) {
            const px = left + lx;
            const pixel = py * outW + px; // still indexes into the *full* idx1/idx2
            const tileCol = tileIndexForPixel(px, nTileCols, outW, t);
            const tileId = tileRow * nTileCols + tileCol;
            const i1 = idx1[pixel];
            const cb1Off = tileId * k1 * dim + i1 * dim;
            const outOff = (ly * cropW + lx) * dim;
            if (cb2Float) {
                const i2 = idx2[pixel];
                const cb2Off = tileId * k2 * dim + i2 * dim;
                for (let d = 0; d < dim; d++) {
                    floatMosaic[outOff + d] = cb1Float[cb1Off + d] + cb2Float[cb2Off + d];
                }
            } else {
                for (let d = 0; d < dim; d++) {
                    floatMosaic[outOff + d] = cb1Float[cb1Off + d];
                }
            }
        }
    }
    return floatMosaic;
}

// Reconstructs the full (outH*outW, dim) mosaic like reconstructFloatMosaic and
// quantises it to uint8 in the same shape the rest of vectors.js expects --
// WITHOUT ever materialising the float mosaic. That float form is
// outH*outW*dim*4 bytes (~2 GB on a national-park-scale viewport) and was the
// source of "Array buffer allocation failed" RangeErrors on memory-constrained
// clients. reconstructFloatMosaic still exists for postcard.html, which only
// ever reconstructs a small `crop` rectangle.
//
// Two walks over the codebooks with a dim-sized scratch instead of one giant
// allocation:
//   pass 1 -- exact per-dim min/max of the reconstructed values
//   pass 2 -- quantise straight into a Uint8Array(outH*outW*dim)
// The RVQ two-codebook sum is rounded to float32 (Math.fround) so it matches
// bit-for-bit what reconstructFloatMosaic's Float32Array store produced -- the
// returned `values` / `dimMin` / `dimMax` are byte-identical to the old
// reconstructFloatMosaic -> min/max scan -> quantise path (see
// validation/test_vq_quantised_mosaic.mjs).
//
// Peak extra memory: dim floats. Compute: ~2x the reconstruction add-loop,
// which is about the same total work the old path did across its build +
// min/max scan + quantise scan (three full N*dim passes, vs two here).
//
// Params match reconstructFloatMosaic minus `crop` -- a caller quantising the
// whole mosaic always wants all of it. Returns { values: Uint8Array(N*dim),
// dimMin: Float32Array(dim), dimMax: Float32Array(dim) }.
export function reconstructQuantisedMosaic({ idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim }) {
    const numPixels = outH * outW;
    const rvq = !!cb2Float;

    const dimMin = new Float32Array(dim).fill(Infinity);
    const dimMax = new Float32Array(dim).fill(-Infinity);

    // pass 1 -- exact per-dim min/max of the reconstructed values
    for (let py = 0; py < outH; py++) {
        const tileRow = tileIndexForPixel(py, nTileRows, outH, t);
        for (let px = 0; px < outW; px++) {
            const pixel = py * outW + px;
            const tileCol = tileIndexForPixel(px, nTileCols, outW, t);
            const tileId = tileRow * nTileCols + tileCol;
            const cb1Off = (tileId * k1 + idx1[pixel]) * dim;
            if (rvq) {
                const cb2Off = (tileId * k2 + idx2[pixel]) * dim;
                for (let d = 0; d < dim; d++) {
                    const v = Math.fround(cb1Float[cb1Off + d] + cb2Float[cb2Off + d]);
                    if (v < dimMin[d]) dimMin[d] = v;
                    if (v > dimMax[d]) dimMax[d] = v;
                }
            } else {
                for (let d = 0; d < dim; d++) {
                    const v = cb1Float[cb1Off + d];
                    if (v < dimMin[d]) dimMin[d] = v;
                    if (v > dimMax[d]) dimMax[d] = v;
                }
            }
        }
    }

    const dimScale = new Float32Array(dim);
    for (let d = 0; d < dim; d++) dimScale[d] = (dimMax[d] - dimMin[d]) || 1;

    // pass 2 -- quantise straight into values
    const values = new Uint8Array(numPixels * dim);
    for (let py = 0; py < outH; py++) {
        const tileRow = tileIndexForPixel(py, nTileRows, outH, t);
        for (let px = 0; px < outW; px++) {
            const pixel = py * outW + px;
            const tileCol = tileIndexForPixel(px, nTileCols, outW, t);
            const tileId = tileRow * nTileCols + tileCol;
            const cb1Off = (tileId * k1 + idx1[pixel]) * dim;
            const outOff = pixel * dim;
            if (rvq) {
                const cb2Off = (tileId * k2 + idx2[pixel]) * dim;
                for (let d = 0; d < dim; d++) {
                    const v = Math.fround(cb1Float[cb1Off + d] + cb2Float[cb2Off + d]);
                    const q = Math.round((v - dimMin[d]) / dimScale[d] * 255);
                    values[outOff + d] = q < 0 ? 0 : q > 255 ? 255 : q;
                }
            } else {
                for (let d = 0; d < dim; d++) {
                    const q = Math.round((cb1Float[cb1Off + d] - dimMin[d]) / dimScale[d] * 255);
                    values[outOff + d] = q < 0 ? 0 : q > 255 ? 255 : q;
                }
            }
        }
    }

    return { values, dimMin, dimMax };
}
