#!/usr/bin/env node
/**
 * Correctness test for the per-class similarity threshold distance cache
 * (vectors.js::localComputeMinDistances / localMatchesFromDistances).
 *
 * The point of that split is purely a performance optimization: instead of
 * localSearchSimilarMulti's single-pass "compute distance AND apply
 * threshold" (which must redo the full O(N * dim * numEmbeddings) walk on
 * every slider drag, since threshold is baked into the same pass), the
 * distance is computed once and cached, then re-thresholded cheaply on
 * every drag (labels.js::rebuildClassOverlay). This test's only job is to
 * prove that split doesn't change the result: for every threshold tried,
 * the cached-and-re-thresholded path must return exactly the same matches
 * as the original single-pass search would have.
 *
 * Run:  node validation/test_distance_cache.mjs
 */

globalThis.window = globalThis;
globalThis.document = { getElementById: () => null };
globalThis.L = { Layer: class {} }; // vectors.js's DirectCanvasLayer extends L.Layer at module scope

const VECTORS_JS = new URL('../public/js/vectors.js', import.meta.url);
await import(VECTORS_JS);

let passed = 0;
let failed = 0;

function assertEq(a, b, msg) {
    const sa = JSON.stringify(a), sb = JSON.stringify(b);
    if (sa !== sb) {
        failed++;
        console.log(`  FAIL: ${msg}\n    got:  ${sa}\n    want: ${sb}`);
    } else {
        passed++;
    }
}

// Synthetic viewport: N pixels, dim-D embeddings, uint8-quantized with a
// realistic (non-identity) dequant range, matching real localVectors shape.
function makeVectors(N, dim, seed) {
    let s = seed;
    const rng = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const values = new Uint8Array(N * dim);
    const coords = new Int32Array(N * 2);
    for (let i = 0; i < N; i++) {
        coords[i * 2] = i % 50;
        coords[i * 2 + 1] = Math.floor(i / 50);
        for (let d = 0; d < dim; d++) values[i * dim + d] = Math.floor(rng() * 256);
    }
    return {
        values, coords, dim, numVectors: N,
        metadata: {
            geotransform: { a: 0.0001, c: 10, e: -0.0001, f: 50 },
            dim_min: new Array(dim).fill(-5),
            dim_max: new Array(dim).fill(5),
        },
    };
}

function makeEmbedding(dim, seed) {
    let s = seed;
    const rng = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    const e = new Float32Array(dim);
    for (let d = 0; d < dim; d++) e[d] = Math.floor(rng() * 256);
    return e;
}

function sortedMatchKeys(matches) {
    return matches.map(m => `${m.lat.toFixed(6)},${m.lon.toFixed(6)}`).sort();
}

console.log('Testing localComputeMinDistances + localMatchesFromDistances against localSearchSimilarMulti...\n');

for (const [N, dim, nEmbeddings] of [[20, 8, 1], [200, 128, 1], [200, 128, 5]]) {
    window.localVectors = makeVectors(N, dim, 42);
    const embeddings = Array.from({ length: nEmbeddings }, (_, i) => makeEmbedding(dim, 100 + i));
    const distances = window.localComputeMinDistances(embeddings);

    assertEq(distances.length, N, `N=${N} dim=${dim} embeddings=${nEmbeddings}: distances array length`);

    // Sweep thresholds from "matches nothing" to "matches everything" and
    // confirm the cached-and-rethresholded path agrees with a fresh direct
    // search at every one -- this is the actual behavioral contract:
    // dragging the slider to any value must give the same result the old,
    // always-fresh search would have.
    for (const threshold of [0.5, 5, 15, 30, 60, 120, 300, 1000]) {
        const direct = sortedMatchKeys(window.localSearchSimilarMulti(embeddings, threshold));
        const cached = sortedMatchKeys(window.localMatchesFromDistances(distances, threshold));
        assertEq(
            cached, direct,
            `N=${N} dim=${dim} embeddings=${nEmbeddings} threshold=${threshold}: matches identical to direct search`
        );
    }
}

// Edge cases
window.localVectors = makeVectors(10, 8, 1);
assertEq(window.localComputeMinDistances([]).length, 0, 'empty embeddings -> empty distances array');
assertEq(window.localMatchesFromDistances(new Float32Array(0), 100).length, 0, 'empty distances -> no matches');
assertEq(
    window.localMatchesFromDistances(window.localComputeMinDistances([makeEmbedding(8, 1)]), 0).length,
    window.localSearchSimilarMulti([makeEmbedding(8, 1)], 0).length,
    'threshold=0 (edge of valid range) still agrees with direct search'
);

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
