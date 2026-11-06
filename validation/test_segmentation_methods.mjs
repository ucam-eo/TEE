#!/usr/bin/env node
/**
 * Auto-label clustering methods (segmentation.js, #seg-method-select).
 *
 * The k-means worker gained optional steps: over-clustering + Ward merge +
 * refinement ("Stable"), an MRF/ICM smoothing of the labels ("Stable +
 * smooth"), a Gaussian filter of the embedding grid before clustering
 * ("Stable + Gaussian") and Lloyd iterations over all vectors. All of them
 * are off by default.
 *
 * Checked here, with the worker code run directly in Node:
 *   - the default path still returns exactly what it returned before the
 *     change (hashes recorded from the previous worker)
 *   - each building block on an input small enough to verify by hand
 *   - the "Stable" pipeline end to end on well-separated groups
 *   - buildSegOptions(): sample sizes and the metre -> pixel conversion
 *   - grids with gaps (gridLookup.index, -1 = no data): the Gaussian filter
 *     renormalises over pixels with data, the MRF counts only neighbours with
 *     data, and on a grid without gaps both give exactly the old output
 *
 * Run:  node validation/test_segmentation_methods.mjs
 */

import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'segmentation.js'), 'utf8');

function extract(name) {
    const i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found in segmentation.js: ${name}`);
    let depth = 0, started = false, j = i;
    while (true) {
        const c = src[j];
        if (c === '{') { depth++; started = true; }
        else if (c === '}') { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
        j++;
    }
}

const methodsSrc = src.match(/const SEG_METHODS = \{[\s\S]*?\n\};/)[0];
const api = new Function(
    extract('mulberry32') + '\n' + extract('buildSample') + '\n' + methodsSrc + '\n' +
    extract('pixelSizeMetres') + '\n' + extract('buildSegOptions') + '\n' + extract('segSmoothingNote') + '\n' +
    'return { mulberry32, buildSample, SEG_METHODS, pixelSizeMetres, buildSegOptions, segSmoothingNote };'
)();

// The worker blob, with mulberry32 spliced in as the browser does it.
const workerSrc = src.match(/new Blob\(\[`([\s\S]*?)`\], \{type/)[1]
    .replace('${mulberry32.toString()}', extract('mulberry32'));
// Handles on the worker's helper functions.
const fn = new Function('self', workerSrc +
    '; return { fitKMeans, sampleCounts, wardMerge, lloyd, gaussFilter, icmSmooth };')({});

function runWorker(emb, N, dim, k, seed, method = 'kmeans', grid = null, gt = null) {
    let out;
    const self = { postMessage: (m) => { out = m; } };
    new Function('self', workerSrc)(self);
    const { opts, sampleSize } = api.buildSegOptions(method, k, N, seed >>> 0, grid, gt);
    const sample = api.buildSample(N, sampleSize, api.mulberry32(seed >>> 0));
    self.onmessage({ data: { vectors: emb.slice().buffer, N, dim, k, maxIter: 20,
        sampleIdx: sample ? sample.buffer : null, seed: seed >>> 0, opts } });
    return { lab: new Int32Array(out.assignments), cent: new Float32Array(out.centroids),
        nearestIdx: new Int32Array(out.nearestIdx), maxDist: new Float64Array(out.maxDist), raw: out };
}

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;
const sha = (buf) => crypto.createHash('sha1').update(Buffer.from(buf)).digest('hex').slice(0, 12);

// Seeded test data: `groups` Gaussian groups in `dim` dimensions.
function mixture(N, dim, groups, spread, seed) {
    const rnd = api.mulberry32(seed);
    const gauss = () => Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(2 * Math.PI * rnd());
    const centres = Array.from({ length: groups }, () => Array.from({ length: dim }, () => spread * gauss()));
    const emb = new Float32Array(N * dim), truth = new Int32Array(N);
    for (let i = 0; i < N; i++) {
        truth[i] = Math.floor(rnd() * groups);
        for (let d = 0; d < dim; d++) emb[i * dim + d] = centres[truth[i]][d] + gauss();
    }
    return { emb, truth };
}
// Two labelings describe the same partition (cluster ids may be permuted).
function samePartition(a, b) {
    const fwd = new Map(), back = new Map();
    for (let i = 0; i < a.length; i++) {
        if (!fwd.has(a[i])) fwd.set(a[i], b[i]);
        if (!back.has(b[i])) back.set(b[i], a[i]);
        if (fwd.get(a[i]) !== b[i] || back.get(b[i]) !== a[i]) return false;
    }
    return true;
}

// --- default path is unchanged ---------------------------------------
{
    // Hashes of the assignments returned by the worker before the methods
    // were added (commit 5c2c3d8), for this exact input.
    const { emb } = mixture(12000, 8, 7, 3, 123);
    ok(sha(runWorker(emb, 12000, 8, 6, 42).raw.assignments) === '3fe135762760', 'k-means, seed 42: same assignments as before');
    ok(sha(runWorker(emb, 12000, 8, 6, 7).raw.assignments) === '100d3fbfde15', 'k-means, seed 7: same assignments as before');
    const { opts, sampleSize } = api.buildSegOptions('kmeans', 6, 12000, 42, { w: 120, h: 100, index: null }, null);
    ok(Object.keys(opts).length === 0 && sampleSize === 5000, 'k-means sends no options and keeps the sample rule');
}

// --- Ward merge: two cases worked out by hand ------------------------
{
    // 1-d centroids 0, 1, 10, 12 with equal counts -> {0, 1} and {10, 12}:
    // cost(0,1) = 1*1/2 * 1 = 0.5, then cost(10,12) = 2 < cost({0,1},10) = 2/3 * 9.5^2
    const m = fn.wardMerge(new Float32Array([0, 1, 10, 12]), 4, new Int32Array([1, 1, 1, 1]), 2, 1);
    ok(near(m[0], 0.5) && near(m[1], 11), 'Ward: equal counts merge the two close pairs');
    // centroids 0, 3, 5: equal counts merge {3, 5} (cost 2 < 4.5); with counts 1, 100, 100
    // cost(0,3) = 100/101 * 9 = 8.9 < cost(3,5) = 50 * 4 = 200, so {0, 3} merge instead
    const e = fn.wardMerge(new Float32Array([0, 3, 5]), 3, new Int32Array([1, 1, 1]), 2, 1);
    ok(near(e[0], 0) && near(e[1], 4), 'Ward: equal counts merge 3 and 5');
    const w = fn.wardMerge(new Float32Array([0, 3, 5]), 3, new Int32Array([1, 100, 100]), 2, 1);
    ok(near(w[0], 300 / 101, 1e-5) && near(w[1], 5), 'Ward: pixel counts change which pair merges (weighted mean 300/101)');
}

// --- Lloyd and sample counts ------------------------------------------
{
    const X = new Float32Array([0, 2, 10, 14]);
    const C = new Float32Array([1, 9]);
    fn.lloyd(X, 4, 1, C, 2, 1, null);
    ok(near(C[0], 1) && near(C[1], 12), 'one Lloyd iteration moves the centroids to the means 1 and 12');
    const counts = fn.sampleCounts(X, 4, 1, new Uint32Array([0, 1, 2]), C, 2);
    ok(counts[0] === 2 && counts[1] === 1, 'sampleCounts counts the sampled vectors only');
    const E = new Float32Array([5, 100]);            // second centroid attracts nothing
    fn.lloyd(X, 4, 1, E, 2, 3, null);
    ok(near(E[0], 6.5) && near(E[1], 100), 'an empty cluster keeps its centroid');
}

// --- Gaussian filter ---------------------------------------------------
{
    const W = 21, H = 21;
    const img = new Float32Array(W * H); img[10 * W + 10] = 1;
    fn.gaussFilter(img, W, H, 1, 1.0, 1.0);
    let sum = 0, arg = 0;
    for (let i = 0; i < img.length; i++) { sum += img[i]; if (img[i] > img[arg]) arg = i; }
    ok(near(sum, 1, 1e-5), 'Gaussian filter keeps the total (impulse sums to 1)');
    ok(arg === 10 * W + 10, 'peak stays at the impulse');
    ok(near(img[10 * W + 11], img[11 * W + 10], 1e-7) && near(img[10 * W + 9], img[10 * W + 11], 1e-7), 'symmetric for equal sigmas');
    ok(near(img[10 * W + 11] / img[10 * W + 10], Math.exp(-0.5), 1e-4), 'one pixel away: exp(-1 / (2 sigma^2))');
    const aniso = new Float32Array(41 * 41); aniso[20 * 41 + 20] = 1;
    fn.gaussFilter(aniso, 41, 41, 1, 1.44, 0.77);    // 10 m on Gothenburg's 6.95 m x 13 m pixels
    ok(aniso[20 * 41 + 21] > aniso[21 * 41 + 20], 'wider along x when the pixels are narrower in x');
    const flat = new Float32Array(9 * 7 * 3).fill(4);
    fn.gaussFilter(flat, 9, 7, 3, 1.5, 0.8);
    ok(flat.every((v) => near(v, 4, 1e-5)), 'a constant image stays constant (also at the edges)');
}

// --- MRF / ICM ---------------------------------------------------------
{
    // 7 x 7 grid: left 3 columns cluster 0, right 4 columns cluster 1 (distance 1 to the own
    // centroid, 3 to the other). Pixel (3, 5) is slightly closer to cluster 0 (1.0 vs 1.2).
    // Distances are divided by their mean (1), so the pixel flips when beta * 8 > 0.2.
    const W = 7, H = 7, k = 2;
    const D = new Float32Array(W * H * k), lab = new Int32Array(W * H);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const i = y * W + x, c = x < 3 ? 0 : 1;
        D[i * k + c] = 1; D[i * k + 1 - c] = 3; lab[i] = c;
    }
    const p = 3 * W + 5;
    D[p * k] = 1.0; D[p * k + 1] = 1.2; lab[p] = 0;
    const run = (beta) => { const l = new Int32Array(lab); fn.icmSmooth(D, l, W, H, k, beta, 5); return l; };
    ok(run(0).join() === lab.join(), 'beta = 0 changes nothing');
    ok(run(0.02).join() === lab.join(), 'beta below the threshold (0.16 < 0.2) changes nothing');
    const out = run(0.25);
    ok(out[p] === 1, 'the isolated pixel is outvoted by its 8 neighbours');
    out[p] = 0;
    ok(out.join() === lab.join(), 'the edge between the two clusters stays');
}

// --- "Stable": over-cluster, merge, refine ----------------------------
{
    const N = 9000, dim = 6, k = 4;
    const { emb, truth } = mixture(N, dim, k, 12, 5);          // 4 well-separated groups
    for (const seed of [1, 7, 42]) {
        const r = runWorker(emb, N, dim, k, seed, 'stable');
        ok(samePartition(r.lab, truth), `stable recovers the 4 groups (seed ${seed})`);
        ok(new Set(r.lab).size === k && r.cent.length === k * dim, `stable returns k clusters and k centroids (seed ${seed})`);
        ok(Array.from(r.nearestIdx).every((ni, c) => r.lab[ni] === c), `representative pixel belongs to its cluster (seed ${seed})`);
    }
    const a = runWorker(emb, N, dim, k, 42, 'stable'), b = runWorker(emb, N, dim, k, 42, 'stable');
    ok(a.lab.join() === b.lab.join(), 'stable is reproducible for a fixed seed');
    const { opts, sampleSize } = api.buildSegOptions('stable', k, N, 42, null, null);
    ok(opts.K0 === 32 && sampleSize === 9000 && new Uint32Array(opts.refineIdx).length === 5000,
        'N = 9000: the over-clustering uses all vectors (9600 > N), the refinement its 5000 sample');
    const big = api.buildSegOptions('stable', 17, 500000, 42, null, null);
    ok(big.sampleSize === 9600 && new Uint32Array(big.opts.refineIdx).length === 8500,
        'sample sizes: max(5000, 300 K0) for the over-clustering, max(5000, 500 k) for the refinement');
    ok(Object.keys(api.buildSegOptions('stable', 40, 500000, 42, null, null).opts).length === 0,
        'K0 <= k falls back to plain k-means');
}

// --- smoothing on a grid: MRF ("Stable + smooth") and Gaussian --------
{
    // 40 x 30 grid, left half group A, right half group B, plus per-pixel noise that makes
    // k-means mislabel single pixels; both smoothers remove most of them.
    const W = 40, H = 30, N = W * H, dim = 4, rnd = api.mulberry32(9);
    const gauss = () => Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(2 * Math.PI * rnd());
    const emb = new Float32Array(N * dim), truth = new Int32Array(N);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const i = y * W + x; truth[i] = x < W / 2 ? 0 : 1;
        for (let d = 0; d < dim; d++) emb[i * dim + d] = (truth[i] ? 1 : -1) + 1.2 * gauss();
    }
    const errors = (lab) => { let e = 0; for (let i = 0; i < N; i++) e += lab[i] !== truth[i]; return Math.min(e, N - e); };
    const gt = { a: 0.0001, c: 11.9, e: -0.0001, f: 57.7 };
    const grid = { w: W, h: H, index: null };   // a gridLookup without gaps
    const plain = errors(runWorker(emb, N, dim, 2, 42, 'stable', grid, gt).lab);
    const mrf = runWorker(emb, N, dim, 2, 42, 'smooth', grid, gt);
    const pre = errors(runWorker(emb, N, dim, 2, 42, 'gauss', grid, gt).lab);
    ok(plain > 20, `noise makes the unsmoothed clustering mislabel pixels (${plain})`);
    ok(errors(mrf.lab) < plain / 2, `the MRF removes most of them (${errors(mrf.lab)} of ${plain} left)`);
    ok(pre < plain / 3, `the Gaussian filter removes most of them (${pre} of ${plain} left)`);
    ok(Array.from(mrf.nearestIdx).every((ni, c) => mrf.lab[ni] === c), 'MRF: representative pixel belongs to its (smoothed) cluster');
    const stableCent = runWorker(emb, N, dim, 2, 42, 'stable', grid, gt).cent;
    ok(mrf.cent.join() === stableCent.join(), 'MRF changes labels only: same centroids as stable');
    // Outputs recorded before the gap handling was added (PR #31 head 0f112a3): on a grid
    // without gaps the filter and the MRF still do exactly the same arithmetic.
    ok(sha(mrf.raw.assignments) === '475fa60a9549' && sha(mrf.raw.centroids) === 'e491ea455ce4',
        'no gaps: Stable + smooth gives exactly the output from before the gap handling');
    const gaussRun = runWorker(emb, N, dim, 2, 42, 'gauss', grid, gt).raw;
    ok(sha(gaussRun.assignments) === 'ddb6eb555920' && sha(gaussRun.centroids) === '57d2efee3543',
        'no gaps: Stable + Gaussian gives exactly the output from before the gap handling');
    ok(mrf.raw.smoothed === true && gaussRun.smoothed === true, 'the worker reports that the smoothing ran');
    const noGrid = runWorker(emb, N, dim, 2, 42, 'smooth', null, gt);
    ok(noGrid.lab.join() === runWorker(emb, N, dim, 2, 42, 'stable', null, gt).lab.join(),
        'no grid -> smooth falls back to stable');
    ok(noGrid.raw.smoothed === false && /Ran as Stable/.test(api.segSmoothingNote('smooth', noGrid.raw.smoothed)),
        'no grid -> the worker says so and the cluster list gets a note');
    ok(api.segSmoothingNote('smooth', true) === null && api.segSmoothingNote('stable', false) === null &&
        api.segSmoothingNote('kmeans', false) === null, 'no note when the smoothing ran or was not asked for');
    // metre -> pixel conversion at Gothenburg: a pixel of 0.000117 degrees is 6.96 m x 12.93 m
    const [mx, my] = api.pixelSizeMetres({ a: 0.00011696237616832214, e: -0.00011696237616832214, f: 57.73132766836905 }, 385);
    ok(near(mx, 6.96, 0.02) && near(my, 12.93, 0.02), `pixel size at Gothenburg (${mx.toFixed(2)} m x ${my.toFixed(2)} m)`);
    const o = api.buildSegOptions('gauss', 5, N, 42, grid, gt).opts;
    ok(o.gridW === W && o.gridH === H && o.sigma && o.sigma[0] > o.sigma[1], 'gauss sends the grid and sigma in pixels (wider along x)');
    ok(!api.buildSegOptions('gauss', 5, N, 42, null, gt).opts.sigma, 'no grid -> no filter');
    const s = api.buildSegOptions('smooth', 5, N, 42, grid, gt).opts;
    ok(s.beta === 0.25 && s.gridW === W && !s.sigma, 'smooth sends beta and the grid, no filter');
}

// --- grids with gaps (gridLookup.index, -1 = no data) -----------------
{
    // A grid of W x H cells holding the vectors of the cells with mask = 1, in the given order.
    // Returns the cell -> vector index (-1 = gap) and the vector -> cell list.
    function gappedGrid(W, H, mask, order) {
        const cells = [];
        for (let c = 0; c < W * H; c++) if (mask[c]) cells.push(c);
        const vecCell = order ? order.map((i) => cells[i]) : cells;
        const index = new Int32Array(W * H).fill(-1);
        vecCell.forEach((c, v) => { index[c] = v; });
        return { index, vecCell };
    }
    const kernel = (sigma) => {
        const r = Math.max(1, Math.ceil(3 * sigma)), w = [];
        let z = 0;
        for (let j = -r; j <= r; j++) { w.push(Math.exp(-j * j / (2 * sigma * sigma))); z += w[w.length - 1]; }
        return w.map((v) => v / z);
    };

    // Gaussian filter, one row with gaps: each pixel with data becomes the weighted mean of the
    // pixels with data in its window (edges replicated), weights renormalised.
    {
        const W = 9, vals = [0, 1, 4, 9, 16, 25, 36, 49, 64], mask = [1, 1, 0, 1, 1, 1, 0, 0, 1];
        const { index, vecCell } = gappedGrid(W, 1, mask);
        const img = new Float32Array(vecCell.map((c) => vals[c]));
        fn.gaussFilter(img, W, 1, 1, 1.0, 0.5, index);
        const w = kernel(1.0), r = (w.length - 1) / 2;
        let worst = 0;
        vecCell.forEach((c, v) => {
            let num = 0, den = 0;
            for (let j = -r; j <= r; j++) {
                const xx = Math.min(W - 1, Math.max(0, c + j));
                if (mask[xx]) { num += w[j + r] * vals[xx]; den += w[j + r]; }
            }
            worst = Math.max(worst, Math.abs(img[v] - num / den));
        });
        ok(worst < 1e-4, `gaps: weighted mean over the pixels with data, renormalised (max error ${worst.toExponential(1)})`);
    }
    {
        // constant image with 30 % gaps and a hole stays constant; vector order does not matter
        const W = 23, H = 17, dim = 3, rnd = api.mulberry32(3);
        const mask = Array.from({ length: W * H }, (_, c) => (rnd() > 0.3 && !(c % W > 8 && c % W < 14 && c / W > 5 && c / W < 11)) ? 1 : 0);
        const n = mask.reduce((a, b) => a + b, 0);
        const order = Array.from({ length: n }, (_, i) => i);
        for (let i = n - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
        const { index } = gappedGrid(W, H, mask, order);
        const flat = new Float32Array(n * dim).fill(4);
        fn.gaussFilter(flat, W, H, dim, 1.5, 0.8, index);
        ok(flat.every((v) => near(v, 4, 1e-5)), 'gaps: a constant image stays constant next to gaps and holes');
        // same values in row-major order vs shuffled order: identical per cell
        const rowMajor = gappedGrid(W, H, mask), shuffled = gappedGrid(W, H, mask, order);
        const val = (c, d) => Math.sin(c * 0.37 + d);
        const a = new Float32Array(n * dim), b = new Float32Array(n * dim);
        rowMajor.vecCell.forEach((c, v) => { for (let d = 0; d < dim; d++) a[v * dim + d] = val(c, d); });
        shuffled.vecCell.forEach((c, v) => { for (let d = 0; d < dim; d++) b[v * dim + d] = val(c, d); });
        fn.gaussFilter(a, W, H, dim, 1.2, 0.9, rowMajor.index);
        fn.gaussFilter(b, W, H, dim, 1.2, 0.9, shuffled.index);
        let same = true;
        shuffled.vecCell.forEach((c, v) => {
            const u = rowMajor.index[c];
            for (let d = 0; d < dim; d++) same = same && a[u * dim + d] === b[v * dim + d];
        });
        ok(same, 'gaps: the result per cell does not depend on the order of the vectors');
        // a full grid given through an identity index is exactly the dense filter
        const full = new Float32Array(W * H * dim).map((_, i) => Math.cos(i * 0.11));
        const viaIndex = full.slice();
        fn.gaussFilter(full, W, H, dim, 1.2, 0.9);
        fn.gaussFilter(viaIndex, W, H, dim, 1.2, 0.9, Int32Array.from({ length: W * H }, (_, c) => c));
        ok(full.join() === viaIndex.join(), 'an index without gaps gives exactly the dense filter');
    }

    // MRF: only neighbours with data vote. Same 7 x 7 set-up as above; at beta = 0.04 the pixel
    // flips with its 8 neighbours (8 * 0.04 = 0.32 > 0.2) but not when 4 of them are gaps
    // (4 * 0.04 = 0.16 < 0.2).
    {
        const W = 7, H = 7, k = 2, p = 3 * W + 5;
        const build = (mask) => {
            const { index, vecCell } = gappedGrid(W, H, mask);
            const D = new Float32Array(vecCell.length * k), lab = new Int32Array(vecCell.length);
            vecCell.forEach((cell, v) => {
                const c = cell % W < 3 ? 0 : 1;
                D[v * k + c] = 1; D[v * k + 1 - c] = 3; lab[v] = c;
            });
            const vp = index[p];
            D[vp * k] = 1.0; D[vp * k + 1] = 1.2; lab[vp] = 0;
            return { index, D, lab, vp };
        };
        const full = build(new Array(W * H).fill(1));
        const l1 = new Int32Array(full.lab); fn.icmSmooth(full.D, l1, W, H, k, 0.04, 5);
        const l2 = new Int32Array(full.lab); fn.icmSmooth(full.D, l2, W, H, k, 0.04, 5, full.index);
        ok(l1[full.vp] === 1 && l1.join() === l2.join(), 'MRF, no gaps: 8 neighbours outvote the pixel; an identity index changes nothing');
        const mask = new Array(W * H).fill(1);
        for (const c of [2 * W + 4, 2 * W + 5, 2 * W + 6, 3 * W + 4]) mask[c] = 0;
        const gap = build(mask);
        const l3 = new Int32Array(gap.lab); fn.icmSmooth(gap.D, l3, W, H, k, 0.04, 5, gap.index);
        ok(l3[gap.vp] === 0, 'MRF, gaps: with 4 of its 8 neighbours missing the same beta no longer flips the pixel');
        const lone = new Array(W * H).fill(1);
        for (const c of [2 * W + 4, 2 * W + 5, 2 * W + 6, 3 * W + 4, 3 * W + 6, 4 * W + 4, 4 * W + 5, 4 * W + 6]) lone[c] = 0;
        const iso = build(lone);
        const l4 = new Int32Array(iso.lab); fn.icmSmooth(iso.D, l4, W, H, k, 10, 5, iso.index);
        ok(l4[iso.vp] === 0, 'MRF, gaps: a pixel without neighbours keeps its nearest centroid, whatever beta');
    }

    // End to end on the noisy two-halves grid of above with 25 % of the cells missing and the
    // vectors shuffled: both smoothers still work, through gridLookup.index.
    {
        const W = 40, H = 30, dim = 4, rnd = api.mulberry32(9);
        const gauss = () => Math.sqrt(-2 * Math.log(1 - rnd())) * Math.cos(2 * Math.PI * rnd());
        const mask = Array.from({ length: W * H }, () => (rnd() > 0.25 ? 1 : 0));
        const n = mask.reduce((a, b) => a + b, 0);
        const order = Array.from({ length: n }, (_, i) => i);
        for (let i = n - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [order[i], order[j]] = [order[j], order[i]]; }
        const { index, vecCell } = gappedGrid(W, H, mask, order);
        const emb = new Float32Array(n * dim), truth = new Int32Array(n);
        vecCell.forEach((c, v) => {
            truth[v] = c % W < W / 2 ? 0 : 1;
            for (let d = 0; d < dim; d++) emb[v * dim + d] = (truth[v] ? 1 : -1) + 1.2 * gauss();
        });
        const errors = (lab) => { let e = 0; for (let i = 0; i < n; i++) e += lab[i] !== truth[i]; return Math.min(e, n - e); };
        const gt = { a: 0.0001, c: 11.9, e: -0.0001, f: 57.7 };
        const grid = { w: W, h: H, index };
        const plain = errors(runWorker(emb, n, dim, 2, 42, 'stable', grid, gt).lab);
        const mrf = runWorker(emb, n, dim, 2, 42, 'smooth', grid, gt);
        const pre = runWorker(emb, n, dim, 2, 42, 'gauss', grid, gt);
        ok(plain > 15, `gaps: noise makes the unsmoothed clustering mislabel pixels (${plain} of ${n})`);
        ok(errors(mrf.lab) < plain / 2 && mrf.raw.smoothed === true, `gaps: the MRF still removes most of them (${errors(mrf.lab)} of ${plain} left)`);
        ok(errors(pre.lab) < plain / 3 && pre.raw.smoothed === true, `gaps: the Gaussian filter still removes most of them (${errors(pre.lab)} of ${plain} left)`);
        ok(api.segSmoothingNote('smooth', mrf.raw.smoothed) === null, 'gaps: no "Ran as Stable" note');

        // buildSegOptions: the dense test is index === null, not a count comparison
        const g = api.buildSegOptions('smooth', 2, n, 42, grid, gt);
        const sent = new Int32Array(g.opts.gridIndex);
        ok(g.opts.gridW === W && g.opts.gridH === H && sent.length === W * H && sent.join() === index.join(),
            'a grid with gaps sends its cell -> vector index');
        ok(g.opts.gridIndex !== index.buffer && g.transfer.includes(g.opts.gridIndex),
            'the index goes to the worker as a transferred copy (the viewer keeps its own)');
        ok(api.buildSegOptions('smooth', 2, W * H, 42, { w: W, h: H, index: null }, gt).opts.gridIndex === undefined,
            'a grid without gaps (index === null) sends no index');
        const perm = gappedGrid(W, H, new Array(W * H).fill(1), Array.from({ length: W * H }, (_, i) => (i * 7) % (W * H)));
        ok(new Int32Array(api.buildSegOptions('smooth', 2, W * H, 42, { w: W, h: H, index: perm.index }, gt).opts.gridIndex).join() === perm.index.join(),
            'every cell filled but not in row-major order: the index is used, counts alone are not trusted');
        ok(api.buildSegOptions('smooth', 2, n, 42, { w: W, h: H }, gt).opts.gridW === undefined &&
            api.buildSegOptions('smooth', 2, n, 42, { w: W, h: H, index: new Int32Array(5) }, gt).opts.gridW === undefined,
            'a grid without a usable index is not used');
    }
}

// --- no unseeded randomness in the new code ---------------------------
{
    const region = src.slice(src.indexOf('K-means Worker'), src.indexOf('Segmentation Overlay'));
    ok(!/Math\.random\(/.test(region), 'no bare Math.random() in the worker or the options');
    ok(/seed: seed >>> 0, opts\}/.test(src), 'runKMeans posts the options to the worker');
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
