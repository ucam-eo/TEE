#!/usr/bin/env node
/**
 * Logic validation tests for viewer.html JavaScript functions.
 *
 * Extracts JS from the HTML, evaluates pure functions in a minimal
 * stub environment, and tests their correctness.
 *
 * Run:  node validation/test_viewer_logic.js
 */

const fs = require('fs');
const path = require('path');

const VIEWER = path.join(__dirname, '..', 'public', 'viewer.html');
const html = fs.readFileSync(VIEWER, 'utf8');

let passed = 0;
let failed = 0;
const failures = [];

function assert(cond, msg) {
    if (!cond) {
        failed++;
        failures.push(msg);
        console.log(`  FAIL: ${msg}`);
    } else {
        passed++;
    }
}

function assertEq(a, b, msg) {
    if (JSON.stringify(a) !== JSON.stringify(b)) {
        failed++;
        const detail = `${msg}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`;
        failures.push(detail);
        console.log(`  FAIL: ${detail}`);
    } else {
        passed++;
    }
}

// ──────────────────────────────────────────
// Extract standalone functions from the JS
// ──────────────────────────────────────────

function extractFunction(name, src) {
    // Match "function name(" or "async function name("
    const re = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
    const idx = src.search(re);
    if (idx === -1) return null;

    // Find balanced braces
    let braceStart = src.indexOf('{', idx);
    let depth = 0;
    let end = braceStart;
    for (let i = braceStart; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break; } }
    }
    return src.slice(idx, end + 1);
}

// Get all script blocks
const scripts = [];
let re = /<script>([\s\S]*?)<\/script>/g;
let m;
while ((m = re.exec(html)) !== null) scripts.push(m[1]);
const allJS = scripts.join('\n');


// ──────────────────────────────────────────
// Test 1: gridLookupIndex correctness
// ──────────────────────────────────────────
console.log('\n--- gridLookupIndex ---');
{
    const fn = new Function('return ' + extractFunction('gridLookupIndex', allJS))();
    // Grid: 10x10 starting at (5, 3)
    const grid = { minX: 5, minY: 3, w: 10, h: 10 };

    assertEq(fn(grid, 5, 3), 0, 'top-left corner');
    assertEq(fn(grid, 6, 3), 1, 'second pixel in first row');
    assertEq(fn(grid, 5, 4), 10, 'first pixel in second row');
    assertEq(fn(grid, 14, 12), 99, 'bottom-right corner');
    assertEq(fn(grid, 4, 3), -1, 'out of bounds left');
    assertEq(fn(grid, 5, 2), -1, 'out of bounds top');
    assertEq(fn(grid, 15, 3), -1, 'out of bounds right');
    assertEq(fn(grid, 5, 13), -1, 'out of bounds bottom');
}


// ──────────────────────────────────────────
// Test 2: buildGridLookup correctness
// ──────────────────────────────────────────
console.log('\n--- buildGridLookup ---');
{
    const fn = new Function('return ' + extractFunction('buildGridLookup', allJS))();
    // Simulate a 4x3 grid: coords are (x,y) pairs in row-major order
    // Row 0: (10,20), (11,20), (12,20), (13,20)
    // Row 1: (10,21), (11,21), (12,21), (13,21)
    // Row 2: (10,22), (11,22), (12,22), (13,22)
    const coords = new Int32Array([
        10,20, 11,20, 12,20, 13,20,
        10,21, 11,21, 12,21, 13,21,
        10,22, 11,22, 12,22, 13,22,
    ]);
    const result = fn(coords, 12);
    assertEq(result.minX, 10, 'minX');
    assertEq(result.minY, 20, 'minY');
    assertEq(result.w, 4, 'gridWidth');
    assertEq(result.h, 3, 'gridHeight');
}


// ──────────────────────────────────────────
// Test 3: calculateAverageEmbedding
// ──────────────────────────────────────────
console.log('\n--- calculateAverageEmbedding ---');
{
    const fn = new Function('return ' + extractFunction('calculateAverageEmbedding', allJS))();

    const emb1 = [1.0, 2.0, 3.0];
    const emb2 = [3.0, 4.0, 5.0];
    const avg = fn([emb1, emb2]);
    assertEq(avg.length, 3, 'avg length');
    assert(Math.abs(avg[0] - 2.0) < 1e-6, 'avg[0] == 2.0');
    assert(Math.abs(avg[1] - 3.0) < 1e-6, 'avg[1] == 3.0');
    assert(Math.abs(avg[2] - 4.0) < 1e-6, 'avg[2] == 4.0');

    // Single embedding → identity
    const single = fn([[5, 10, 15]]);
    assertEq(single, [5, 10, 15], 'single embedding is identity');

    // Empty → null
    assertEq(fn([]), null, 'empty returns null');
}


// ──────────────────────────────────────────
// Test 4: localSearchSimilar correctness
// ──────────────────────────────────────────
console.log('\n--- localSearchSimilar ---');
{
    // Build a minimal localVectors stub
    // 3 vectors of dim=4, on a 3x1 grid at pixel coords (0,0), (1,0), (2,0)
    const dim = 4;
    const N = 3;
    const embeddings = new Float32Array([
        1,0,0,0,   // vec 0: unit x
        0,1,0,0,   // vec 1: unit y
        1,0,0,0,   // vec 2: same as vec 0
    ]);
    const coords = new Int32Array([0,0, 1,0, 2,0]);
    const metadata = { geotransform: { c: -1.0, a: 0.01, f: 52.0, e: -0.01 } };
    const gridLookup = { minX: 0, minY: 0, w: 3, h: 1 };

    // Construct localSearchSimilar with bound localVectors
    const fnSrc = extractFunction('localSearchSimilar', allJS);
    const wrapper = new Function('localVectors', `
        ${fnSrc}
        return localSearchSimilar;
    `);
    const localSearchSimilar = wrapper({ embeddings, coords, metadata, gridLookup, numVectors: N, dim });

    const query = new Float32Array([1,0,0,0]); // identical to vec 0 and vec 2

    // Threshold 0 → only exact matches (distance 0)
    const exact = localSearchSimilar(query, 0);
    assertEq(exact.length, 2, 'exact match count');

    // Threshold 2 → should include vec 1 (distance = sqrt(2) ≈ 1.41)
    const wide = localSearchSimilar(query, 2);
    assertEq(wide.length, 3, 'wide threshold match count');

    // Verify distances
    for (const m of wide) {
        assert(m.distance >= 0, 'distance non-negative');
        assert(typeof m.lat === 'number', 'lat is number');
        assert(typeof m.lon === 'number', 'lon is number');
    }
    // vec 0 and vec 2 should have distance 0
    const zeroDistCount = wide.filter(m => m.distance < 0.001).length;
    assertEq(zeroDistCount, 2, 'two zero-distance matches');
}


// ──────────────────────────────────────────
// Test 5: localExtract correctness
// ──────────────────────────────────────────
console.log('\n--- localExtract ---');
{
    // localExtract hardcodes dim=128, so we must use 128-dim embeddings
    const dim = 128;
    const N = 4;
    // 2x2 grid at pixels (0,0),(1,0),(0,1),(1,1)
    // Each embedding is 128 floats; we fill with recognisable patterns
    const embeddings = new Float32Array(N * dim);
    for (let v = 0; v < N; v++) {
        for (let d = 0; d < dim; d++) {
            embeddings[v * dim + d] = v * 1000 + d; // e.g. vec0: 0,1,2,...  vec1: 1000,1001,...
        }
    }
    const coords = new Int32Array([0,0, 1,0, 0,1, 1,1]);
    // Geotransform: lon = c + px * a, lat = f + py * e
    // c=-1.0, a=0.01, f=52.0, e=-0.01
    // So pixel (0,0) → lon=-1.0, lat=52.0
    //    pixel (1,0) → lon=-0.99, lat=52.0
    const metadata = { geotransform: { c: -1.0, a: 0.01, f: 52.0, e: -0.01 } };
    const gridLookup = { minX: 0, minY: 0, w: 2, h: 2 };

    const fnSrc = extractFunction('localExtract', allJS);
    const gridFnSrc = extractFunction('gridLookupIndex', allJS);
    const wrapper = new Function('localVectors', `
        ${gridFnSrc}
        ${fnSrc}
        return localExtract;
    `);
    const localExtract = wrapper({ embeddings, coords, metadata, gridLookup, numVectors: N, dim });

    // Exact pixel (0,0) at lat=52.0, lon=-1.0
    const e00 = localExtract(52.0, -1.0);
    assert(e00 !== null, 'extract at (0,0) not null');
    assertEq(e00.length, 128, 'extract returns 128-dim vector');
    assertEq(e00[0], 0, 'extract at (0,0) first element correct');
    assertEq(e00[127], 127, 'extract at (0,0) last element correct');

    // Pixel (1,0) at lat=52.0, lon=-0.99
    const e10 = localExtract(52.0, -0.99);
    assert(e10 !== null, 'extract at (1,0) not null');
    assertEq(e10[0], 1000, 'extract at (1,0) correct pattern');

    // Pixel (0,1) at lat=51.99, lon=-1.0
    // py = trunc((51.99 - 52.0) / -0.01) = trunc(1.0) = 1 (but floating point → 0.999... → 0)
    // So use exact lat = 52.0 + 1 * (-0.01) = 51.99 won't work due to FP.
    // Instead, use a lat deep inside pixel row 1: f + py*e + e/2 = 52.0 + 1*(-0.01) + (-0.005) = 51.985
    const e01 = localExtract(51.985, -1.0);
    assert(e01 !== null, 'extract at (0,1) not null');
    assertEq(e01[0], 2000, 'extract at (0,1) correct pattern');

    // Way out of bounds → null (or neighbour found)
    const far = localExtract(10.0, 10.0);
    assertEq(far, null, 'extract far out of bounds returns null');
}


// ──────────────────────────────────────────
// Test 6: No 'simple' literals in JS
// ──────────────────────────────────────────
console.log('\n--- No simple literals ---');
{
    const simpleRe = /['"]simple['"]/g;
    const matches = allJS.match(simpleRe);
    assertEq(matches, null, "No 'simple' string literals in JS");
}


// ──────────────────────────────────────────
// Test 7: Required DOM IDs referenced in JS exist in HTML
// ──────────────────────────────────────────
console.log('\n--- DOM ID cross-reference ---');
{
    // Extract all getElementById calls
    const idRefs = new Set();
    const idRe = /getElementById\(\s*['"]([^'"]+)['"]\s*\)/g;
    let match;
    while ((match = idRe.exec(allJS)) !== null) {
        idRefs.add(match[1]);
    }

    // Extract all id="..." from HTML
    const htmlIds = new Set();
    const htmlIdRe = /\bid=["']([^"']+)["']/g;
    while ((match = htmlIdRe.exec(html)) !== null) {
        htmlIds.add(match[1]);
    }

    // Some IDs are created dynamically (e.g., explorer-loading, explorer-stats-overlay)
    const dynamicIds = new Set([
        'explorer-loading', 'explorer-stats-overlay',
        'seg-overlay-panel', 'change-info-overlay',
        'color-picker-backdrop',
    ]);

    for (const id of idRefs) {
        if (!dynamicIds.has(id)) {
            assert(htmlIds.has(id), `JS references #${id} but it's not in the HTML`);
        }
    }
}


// ──────────────────────────────────────────
// Test 8: Nearest-centroid classification algorithm
// ──────────────────────────────────────────
console.log('\n--- nearest-centroid classification ---');
{
    // Simulate the core logic of renderManualClassification
    // 2 classes, 4 pixels, dim=2
    const dim = 2;
    const N = 4;
    const emb = new Float32Array([
        1, 0,   // pixel 0 → near class A
        0.9, 0.1, // pixel 1 → near class A
        0, 1,   // pixel 2 → near class B
        0.1, 0.9, // pixel 3 → near class B
    ]);

    const centroidA = new Float32Array([1, 0]);
    const centroidB = new Float32Array([0, 1]);
    const centroidArrays = [centroidA, centroidB];
    const numClasses = 2;

    const assignments = new Int8Array(N);
    for (let i = 0; i < N; i++) {
        let minDist = Infinity;
        let minClass = 0;
        const base = i * dim;
        for (let c = 0; c < numClasses; c++) {
            const cent = centroidArrays[c];
            let distSq = 0;
            for (let d = 0; d < dim; d++) {
                const diff = emb[base + d] - cent[d];
                distSq += diff * diff;
            }
            if (distSq < minDist) {
                minDist = distSq;
                minClass = c;
            }
        }
        assignments[i] = minClass;
    }

    assertEq(assignments[0], 0, 'pixel 0 assigned to class A');
    assertEq(assignments[1], 0, 'pixel 1 assigned to class A');
    assertEq(assignments[2], 1, 'pixel 2 assigned to class B');
    assertEq(assignments[3], 1, 'pixel 3 assigned to class B');
}


// ──────────────────────────────────────────
// Test 9: Mode-related string consistency
// ──────────────────────────────────────────
console.log('\n--- Mode string consistency ---');
{
    // All four modes should appear in titles dict — grab a generous chunk
    const titlesStart = allJS.indexOf('const titles = {');
    const titlesBlock = allJS.slice(titlesStart, titlesStart + 1000);
    for (const mode of ['explore', 'change-detection', 'labelling', 'validation']) {
        assert(titlesBlock.includes(`'${mode}'`), `titles dict has '${mode}'`);
    }

    // HEATMAP_LAYER_RULES
    const rulesStart = allJS.indexOf('const HEATMAP_LAYER_RULES');
    const rulesBlock = allJS.slice(rulesStart, rulesStart + 500);
    for (const mode of ['explore', 'change-detection', 'labelling', 'validation']) {
        assert(rulesBlock.includes(`'${mode}'`), `HEATMAP_LAYER_RULES has '${mode}'`);
    }
}


// ──────────────────────────────────────────
// Test 10: setLabelMode toggling logic
// ──────────────────────────────────────────
console.log('\n--- setLabelMode references ---');
{
    const fnBody = extractFunction('setLabelMode', allJS);
    assert(fnBody.includes('panel6-autolabel-view'), 'setLabelMode references autolabel view');
    assert(fnBody.includes('panel6-manual-view'), 'setLabelMode references manual view');
    assert(fnBody.includes("labelMode = mode"), 'setLabelMode sets labelMode');
    assert(fnBody.includes("localStorage.setItem"), 'setLabelMode persists to localStorage');
    assert(fnBody.includes('triggerManualClassification'), 'setLabelMode triggers classification');
}


// ──────────────────────────────────────────
// Summary
// ──────────────────────────────────────────
console.log('\n' + '='.repeat(50));
console.log(`Results: ${passed} passed, ${failed} failed`);
if (failures.length > 0) {
    console.log('\nFailures:');
    failures.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
}
console.log('='.repeat(50));
process.exit(failed > 0 ? 1 : 0);
