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

// Reconstructs a full (outH*outW, dim) float32 mosaic via per-pixel codebook
// lookup: codebooks1[tileId][idx1[pixel]] (+ codebooks2[tileId][idx2[pixel]]
// for RVQ). idx1/idx2 are flat (outH*outW) tile-local index arrays; cb1Float/
// cb2Float are flat (nTiles*k*dim) decoded codebooks (see decodeCodebook).
export function reconstructFloatMosaic({ idx1, cb1Float, idx2, cb2Float, outH, outW, nTileCols, t, k1, k2, dim }) {
    const floatMosaic = new Float32Array(outH * outW * dim);
    for (let py = 0; py < outH; py++) {
        const tileRow = Math.floor(py / t);
        for (let px = 0; px < outW; px++) {
            const pixel = py * outW + px;
            const tileCol = Math.floor(px / t);
            const tileId = tileRow * nTileCols + tileCol;
            const i1 = idx1[pixel];
            const cb1Off = tileId * k1 * dim + i1 * dim;
            const outOff = pixel * dim;
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
