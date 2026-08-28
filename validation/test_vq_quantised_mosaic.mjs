#!/usr/bin/env node
/**
 * Byte-parity test for reconstructQuantisedMosaic (public/js/vq_reconstruct.js).
 *
 * The VQ loader (vectors.js::downloadVectorDataVq) used to reconstruct the whole
 * embedding mosaic as one Float32Array (outH*outW*128*4 -- ~2 GB on a large
 * viewport, the source of "Array buffer allocation failed" crashes), scan it for
 * per-dim min/max, then quantise it to uint8. reconstructQuantisedMosaic does the
 * same result with a dim-sized scratch and two codebook walks -- no giant alloc.
 *
 * This test proves the swap is invisible downstream: for a spread of VQ / RVQ,
 * single- and multi-tile, exact- and non-exact-multiple output shapes, the
 * `values` bytes and the dim_min / dim_max arrays must be IDENTICAL to the old
 * reconstructFloatMosaic -> scan -> quantise path (reproduced verbatim below).
 *
 * Run:  node validation/test_vq_quantised_mosaic.mjs
 */

import { reconstructFloatMosaic, reconstructQuantisedMosaic } from '../public/js/vq_reconstruct.js';

let passed = 0;
let failed = 0;

function fail(msg) { failed++; console.log(`  FAIL: ${msg}`); }
function ok() { passed++; }

// Deterministic LCG, same style as validation/test_distance_cache.mjs.
function rngFactory(seed) {
    let s = seed >>> 0;
    return () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
}

// tessera_vq.client.n_tiles_along: fixed t-stride, last tile pulled back to end
// exactly at `full` (so a non-multiple `full` still gets full coverage).
function nTilesAlong(full, t) {
    if (full <= t) return 1;
    return Math.ceil((full - t) / t) + 1;
}

// Verbatim copy of the OLD vectors.js::downloadVectorDataVq reconstruction:
// reconstructFloatMosaic -> per-dim min/max scan -> quantise. This is the
// behaviour reconstructQuantisedMosaic must reproduce bit-for-bit.
function oldPath({ idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim }) {
    const floatMosaic = reconstructFloatMosaic({
        idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim,
    });
    const numPixels = outH * outW;
    const dimMin = new Float32Array(dim).fill(Infinity);
    const dimMax = new Float32Array(dim).fill(-Infinity);
    for (let i = 0; i < numPixels; i++) {
        const off = i * dim;
        for (let d = 0; d < dim; d++) {
            const v = floatMosaic[off + d];
            if (v < dimMin[d]) dimMin[d] = v;
            if (v > dimMax[d]) dimMax[d] = v;
        }
    }
    const dimScale = new Float32Array(dim);
    for (let d = 0; d < dim; d++) dimScale[d] = (dimMax[d] - dimMin[d]) || 1;
    const values = new Uint8Array(numPixels * dim);
    for (let i = 0; i < numPixels; i++) {
        const off = i * dim;
        for (let d = 0; d < dim; d++) {
            const q = Math.round((floatMosaic[off + d] - dimMin[d]) / dimScale[d] * 255);
            values[off + d] = q < 0 ? 0 : q > 255 ? 255 : q;
        }
    }
    return { values, dimMin, dimMax };
}

function makeCase({ outH, outW, t, dim, k1, k2, rvq, seed }) {
    const rng = rngFactory(seed);
    const nTileRows = nTilesAlong(outH, t);
    const nTileCols = nTilesAlong(outW, t);
    const nTiles = nTileRows * nTileCols;
    const numPixels = outH * outW;

    // Codebooks: real-ish float values with a per-dim offset so ranges vary.
    const cb1Float = new Float32Array(nTiles * k1 * dim);
    for (let i = 0; i < cb1Float.length; i++) cb1Float[i] = (rng() - 0.5) * 6 + (i % dim) * 0.05;
    let cb2Float = null;
    if (rvq) {
        cb2Float = new Float32Array(nTiles * k2 * dim);
        // Second stage is a smaller residual, like real RVQ.
        for (let i = 0; i < cb2Float.length; i++) cb2Float[i] = (rng() - 0.5) * 1.2;
    }

    const idx1 = new Uint8Array(numPixels);
    for (let i = 0; i < numPixels; i++) idx1[i] = Math.floor(rng() * k1);
    let idx2 = null;
    if (rvq) {
        idx2 = new Uint8Array(numPixels);
        for (let i = 0; i < numPixels; i++) idx2[i] = Math.floor(rng() * k2);
    }

    return { idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim };
}

const CASES = [
    { name: 'vq  · single tile',              outH: 6,  outW: 6,  t: 8,  dim: 8,  k1: 12,  k2: 0,   rvq: false, seed: 1 },
    { name: 'vq  · exact-multiple tiling',    outH: 10, outW: 10, t: 5,  dim: 8,  k1: 20,  k2: 0,   rvq: false, seed: 2 },
    { name: 'vq  · pulled-back last tile',    outH: 10, outW: 13, t: 4,  dim: 16, k1: 20,  k2: 0,   rvq: false, seed: 3 },
    { name: 'rvq · single tile',              outH: 7,  outW: 5,  t: 8,  dim: 8,  k1: 10,  k2: 16,  rvq: true,  seed: 4 },
    { name: 'rvq · exact-multiple tiling',    outH: 12, outW: 12, t: 4,  dim: 32, k1: 20,  k2: 64,  rvq: true,  seed: 5 },
    { name: 'rvq · pulled-back last tile',    outH: 11, outW: 14, t: 4,  dim: 128, k1: 20, k2: 256, rvq: true,  seed: 6 },
    { name: 'rvq · dim=128, wide',            outH: 5,  outW: 40, t: 16, dim: 128, k1: 20, k2: 256, rvq: true,  seed: 7 },
];

console.log('Testing reconstructQuantisedMosaic == old reconstructFloatMosaic -> scan -> quantise ...\n');

for (const spec of CASES) {
    const args = makeCase(spec);
    const want = oldPath(args);
    const got = reconstructQuantisedMosaic(args);

    if (got.values.length !== want.values.length) {
        fail(`${spec.name}: values length ${got.values.length} != ${want.values.length}`);
        continue;
    }
    let firstDiff = -1;
    for (let i = 0; i < want.values.length; i++) {
        if (got.values[i] !== want.values[i]) { firstDiff = i; break; }
    }
    if (firstDiff >= 0) {
        const d = firstDiff % spec.dim, px = Math.floor(firstDiff / spec.dim);
        fail(`${spec.name}: values differ at byte ${firstDiff} (pixel ${px}, dim ${d}): got ${got.values[firstDiff]}, want ${want.values[firstDiff]}`);
    } else {
        ok();
    }

    let rangeDiff = false;
    for (let d = 0; d < spec.dim; d++) {
        if (got.dimMin[d] !== want.dimMin[d] || got.dimMax[d] !== want.dimMax[d]) { rangeDiff = true; break; }
    }
    if (rangeDiff) fail(`${spec.name}: dim_min/dim_max differ from the old scan`);
    else ok();
}

// A "large-ish" case to exercise a wider grid without a huge test runtime.
{
    const spec = { name: 'rvq · 200x180, dim=128', outH: 200, outW: 180, t: 64, dim: 128, k1: 20, k2: 256, rvq: true, seed: 42 };
    const args = makeCase(spec);
    const want = oldPath(args);
    const got = reconstructQuantisedMosaic(args);
    let same = got.values.length === want.values.length;
    for (let i = 0; same && i < want.values.length; i++) same = got.values[i] === want.values[i];
    if (same) ok(); else fail(`${spec.name}: values not byte-identical`);
}

// ── Viewport-bounds crop: idx1/idx2 sliced to [r0:r1, c0:c1] of the full
// tile grid, reconstructed with cropTop/cropLeft/fullH/fullW. Must equal the
// [r0:r1, c0:c1] sub-rectangle of a full reconstruction, re-scanned for its
// own min/max and re-quantised (exactly what a clipped persisted bundle
// should produce). See process_viewport.py::save_vectors_rvq(crop_window=...).

function sliceIndices(full, fw, r0, c0, r1, c1) {
    const ch = r1 - r0, cw = c1 - c0;
    const out = new full.constructor(ch * cw);
    for (let ly = 0; ly < ch; ly++)
        for (let lx = 0; lx < cw; lx++)
            out[ly * cw + lx] = full[(r0 + ly) * fw + (c0 + lx)];
    return out;
}

// Reference: full float mosaic -> take the window -> window's own min/max -> quantise.
function cropReference(args, r0, c0, r1, c1) {
    const { outW, dim } = args;
    const floatMosaic = reconstructFloatMosaic(args);
    const ch = r1 - r0, cw = c1 - c0;
    const dimMin = new Float32Array(dim).fill(Infinity);
    const dimMax = new Float32Array(dim).fill(-Infinity);
    for (let ly = 0; ly < ch; ly++) {
        for (let lx = 0; lx < cw; lx++) {
            const src = ((r0 + ly) * outW + (c0 + lx)) * dim;
            for (let d = 0; d < dim; d++) {
                const v = floatMosaic[src + d];
                if (v < dimMin[d]) dimMin[d] = v;
                if (v > dimMax[d]) dimMax[d] = v;
            }
        }
    }
    const dimScale = new Float32Array(dim);
    for (let d = 0; d < dim; d++) dimScale[d] = (dimMax[d] - dimMin[d]) || 1;
    const values = new Uint8Array(ch * cw * dim);
    for (let ly = 0; ly < ch; ly++) {
        for (let lx = 0; lx < cw; lx++) {
            const src = ((r0 + ly) * outW + (c0 + lx)) * dim;
            const dst = (ly * cw + lx) * dim;
            for (let d = 0; d < dim; d++) {
                const q = Math.round((floatMosaic[src + d] - dimMin[d]) / dimScale[d] * 255);
                values[dst + d] = q < 0 ? 0 : q > 255 ? 255 : q;
            }
        }
    }
    return { values, dimMin, dimMax };
}

const CROP_CASES = [
    // spec, then [r0, c0, r1, c1] within the full grid
    [{ name: 'vq  · crop, tile-aligned',     outH: 20, outW: 20, t: 5,  dim: 8,   k1: 16, k2: 0,   rvq: false, seed: 11 }, [5, 5, 15, 15]],
    [{ name: 'rvq · crop, tile-unaligned',   outH: 24, outW: 30, t: 8,  dim: 16,  k1: 20, k2: 64,  rvq: true,  seed: 12 }, [3, 7, 19, 26]],
    [{ name: 'rvq · crop spans last tile',   outH: 22, outW: 26, t: 8,  dim: 32,  k1: 20, k2: 128, rvq: true,  seed: 13 }, [10, 12, 22, 26]],
    [{ name: 'rvq · crop, 1px sliver',       outH: 18, outW: 18, t: 6,  dim: 8,   k1: 12, k2: 32,  rvq: true,  seed: 14 }, [9, 0, 10, 18]],
    [{ name: 'rvq · crop, dim=128',          outH: 30, outW: 40, t: 16, dim: 128, k1: 20, k2: 256, rvq: true,  seed: 15 }, [4, 6, 24, 34]],
];

for (const [spec, [r0, c0, r1, c1]] of CROP_CASES) {
    const full = makeCase(spec);
    const fh = spec.outH, fw = spec.outW;
    const ch = r1 - r0, cw = c1 - c0;

    const cropArgs = {
        idx1: sliceIndices(full.idx1, fw, r0, c0, r1, c1),
        cb1Float: full.cb1Float,
        idx2: full.idx2 ? sliceIndices(full.idx2, fw, r0, c0, r1, c1) : null,
        cb2Float: full.cb2Float,
        outH: ch, outW: cw,
        nTileRows: full.nTileRows, nTileCols: full.nTileCols,
        t: full.t, k1: full.k1, k2: full.k2, dim: full.dim,
        cropTop: r0, cropLeft: c0, fullH: fh, fullW: fw,
    };
    const got = reconstructQuantisedMosaic(cropArgs);
    const want = cropReference(full, r0, c0, r1, c1);

    let firstDiff = -1;
    for (let i = 0; i < want.values.length; i++) {
        if (got.values[i] !== want.values[i]) { firstDiff = i; break; }
    }
    if (got.values.length !== want.values.length) {
        fail(`${spec.name}: values length ${got.values.length} != ${want.values.length}`);
    } else if (firstDiff >= 0) {
        fail(`${spec.name}: values differ at byte ${firstDiff} (got ${got.values[firstDiff]}, want ${want.values[firstDiff]})`);
    } else {
        ok();
    }

    let rangeDiff = false;
    for (let d = 0; d < spec.dim; d++)
        if (got.dimMin[d] !== want.dimMin[d] || got.dimMax[d] !== want.dimMax[d]) { rangeDiff = true; break; }
    if (rangeDiff) fail(`${spec.name}: dim_min/dim_max differ from the windowed scan`);
    else ok();
}

// Zero-offset crop params must be a true no-op (regression guard for old,
// unclipped viewports that don't carry crop_offset / mosaic_shape).
{
    const spec = { name: 'rvq · crop params zero == no-crop', outH: 16, outW: 20, t: 6, dim: 16, k1: 20, k2: 64, rvq: true, seed: 21 };
    const args = makeCase(spec);
    const plain = reconstructQuantisedMosaic(args);
    const withZeros = reconstructQuantisedMosaic({
        ...args, cropTop: 0, cropLeft: 0, fullH: spec.outH, fullW: spec.outW,
    });
    let same = plain.values.length === withZeros.values.length;
    for (let i = 0; same && i < plain.values.length; i++) same = plain.values[i] === withZeros.values[i];
    if (same) ok(); else fail(`${spec.name}: zero-offset crop changed the output`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
