// segmentation.js — K-means clustering, cluster list, seg overlay
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──

let segWorker = null;
let segAssignments = null;   // Int32Array, length N
let segK = 5;
let segSeed = 42;          // seed for the last / next k-means run
let segOverlay = null;       // L.imageOverlay instance
let segLabels = [];          // [{id, color, name, count}, ...]
let segRunning = false;      // prevent concurrent runs
let segVectors = null;       // vectors used for current segmentation (may differ from localVectors)
let segVectorsKey = null;    // `${viewport}/${year}` segVectors was loaded for (memo across k-changes)
let segNote = null;          // note shown above the cluster list for the last run, or null

Object.defineProperty(window, 'segAssignments', {
    get: () => segAssignments,
    set: (v) => { segAssignments = v; },
    configurable: true,
});
Object.defineProperty(window, 'segOverlay', {
    get: () => segOverlay,
    set: (v) => { segOverlay = v; },
    configurable: true,
});
Object.defineProperty(window, 'segLabels', {
    get: () => segLabels,
    set: (v) => { segLabels = v; },
    configurable: true,
});
Object.defineProperty(window, 'segRunning', {
    get: () => segRunning,
    set: (v) => { segRunning = v; },
    configurable: true,
});
Object.defineProperty(window, 'segVectors', {
    get: () => segVectors,
    set: (v) => { segVectors = v; },
    configurable: true,
});
Object.defineProperty(window, 'segK', {
    get: () => segK,
    set: (v) => { segK = v; },
    configurable: true,
});
Object.defineProperty(window, 'segSeed', {
    get: () => segSeed,
    set: (v) => { segSeed = v; },
    configurable: true,
});
Object.defineProperty(window, 'segNote', {
    get: () => segNote,
    configurable: true,
});

// ── Seeded PRNG ──

// mulberry32 — tiny deterministic PRNG so auto-labelling is reproducible:
// same seed + k + vectors => identical clusters. Used on the main thread
// (buildSample) and, via a copy embedded in the worker blob below, for
// K-means++ init and empty-cluster re-seeding. Good enough for uniform
// sampling; not cryptographic.
function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
        a |= 0; a = (a + 0x6D2B79F5) | 0;
        let t = Math.imul(a ^ (a >>> 15), 1 | a);
        t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}
if (typeof window !== 'undefined') {
    Object.defineProperty(window, 'mulberry32', { get: () => mulberry32, configurable: true });
}

// ── K-means Worker ──

// Inline Web Worker for k-means clustering (K-means++ init, early stopping, subsampling).
//
// Default (no opts): k-means++ and Lloyd on the sample, then one assignment pass over all N.
// Optional steps, all off by default (see runKMeans / SEG_METHODS):
//   opts.sigma     Gaussian filter of the embedding grid before clustering ([sx, sy] in pixels)
//   opts.K0        over-cluster into K0 > k clusters, merge them to k with Ward's criterion and
//                  refine the k merged centroids with Lloyd on opts.refineIdx
//   opts.fullIters Lloyd iterations over all N after the fit
//   opts.beta      MRF (Potts) smoothing of the labels with ICM on the 8-neighbourhood
// The grid steps need opts.gridW / opts.gridH, and opts.gridIndex (cell -> vector, -1 = no data)
// unless every cell holds a vector in row-major order. The reply says whether they ran (smoothed).
const segWorkerBlob = new Blob([`
    ${mulberry32.toString()}

    // k-means++ seeding and Lloyd iterations on the sample (on all N when sampleIdx is null).
    function fitKMeans(embeddings, N, dim, k, maxIter, sampleIdx, rand) {
        const S = sampleIdx ? sampleIdx.length : N;
        const centroids = new Float32Array(k * dim);

        // --- K-means++ initialization (on sample) ---
        const firstPt = sampleIdx ? sampleIdx[Math.floor(rand() * S)] : Math.floor(rand() * N);
        centroids.set(embeddings.subarray(firstPt * dim, (firstPt + 1) * dim), 0);

        const minDist2 = new Float64Array(S);
        minDist2.fill(Infinity);

        for (let c = 1; c < k; c++) {
            let totalWeight = 0;
            for (let si = 0; si < S; si++) {
                const idx = sampleIdx ? sampleIdx[si] : si;
                const base = idx * dim;
                const cBase = (c - 1) * dim;
                let dist = 0;
                for (let d = 0; d < dim; d++) {
                    const diff = embeddings[base + d] - centroids[cBase + d];
                    dist += diff * diff;
                }
                if (dist < minDist2[si]) minDist2[si] = dist;
                totalWeight += minDist2[si];
            }
            let r = rand() * totalWeight;
            let chosen = 0;
            for (let si = 0; si < S; si++) {
                r -= minDist2[si];
                if (r <= 0) { chosen = si; break; }
            }
            const chosenIdx = sampleIdx ? sampleIdx[chosen] : chosen;
            centroids.set(embeddings.subarray(chosenIdx * dim, (chosenIdx + 1) * dim), c * dim);
        }

        // --- Iterative refinement on sample only ---
        const sampleAssign = new Int32Array(S);
        const counts = new Int32Array(k);
        const sums = new Float64Array(k * dim);

        for (let iter = 0; iter < maxIter; iter++) {
            let changed = 0;
            for (let si = 0; si < S; si++) {
                const idx = sampleIdx ? sampleIdx[si] : si;
                const base = idx * dim;
                let bestC = 0, bestDist = Infinity;
                for (let c = 0; c < k; c++) {
                    let dist = 0;
                    const cBase = c * dim;
                    for (let d = 0; d < dim; d++) {
                        const diff = embeddings[base + d] - centroids[cBase + d];
                        dist += diff * diff;
                    }
                    if (dist < bestDist) { bestDist = dist; bestC = c; }
                }
                if (sampleAssign[si] !== bestC) changed++;
                sampleAssign[si] = bestC;
            }

            sums.fill(0);
            counts.fill(0);
            for (let si = 0; si < S; si++) {
                const idx = sampleIdx ? sampleIdx[si] : si;
                const c = sampleAssign[si];
                counts[c]++;
                const base = idx * dim;
                const cBase = c * dim;
                for (let d = 0; d < dim; d++) {
                    sums[cBase + d] += embeddings[base + d];
                }
            }
            for (let c = 0; c < k; c++) {
                if (counts[c] === 0) {
                    const ri = sampleIdx ? sampleIdx[Math.floor(rand() * S)] : Math.floor(rand() * N);
                    centroids.set(embeddings.subarray(ri * dim, (ri + 1) * dim), c * dim);
                } else {
                    const cBase = c * dim;
                    for (let d = 0; d < dim; d++) {
                        centroids[cBase + d] = sums[cBase + d] / counts[c];
                    }
                }
            }

            if (changed < S * 0.005) break;
        }
        return centroids;
    }

    // Number of sample vectors that are nearest to each centroid.
    function sampleCounts(embeddings, N, dim, sampleIdx, centroids, k) {
        const S = sampleIdx ? sampleIdx.length : N;
        const counts = new Int32Array(k);
        for (let si = 0; si < S; si++) {
            const base = (sampleIdx ? sampleIdx[si] : si) * dim;
            let bestC = 0, bestDist = Infinity;
            for (let c = 0; c < k; c++) {
                let dist = 0;
                const cBase = c * dim;
                for (let d = 0; d < dim; d++) {
                    const diff = embeddings[base + d] - centroids[cBase + d];
                    dist += diff * diff;
                }
                if (dist < bestDist) { bestDist = dist; bestC = c; }
            }
            counts[bestC]++;
        }
        return counts;
    }

    // Greedy agglomerative merge of K0 centroids down to k groups with Ward's criterion:
    // always join the pair whose union increases the within-cluster sum of squares least,
    // n_a n_b / (n_a + n_b) * ||mu_a - mu_b||^2. Returns the k count-weighted group means.
    function wardMerge(centroids, K0, counts, k, dim) {
        const mu = new Float64Array(centroids);
        const n = new Float64Array(K0);
        for (let c = 0; c < K0; c++) n[c] = Math.max(counts[c], 1);
        const alive = new Uint8Array(K0).fill(1);
        for (let left = K0; left > k; left--) {
            let ba = -1, bb = -1, bestCost = Infinity;
            for (let a = 0; a < K0; a++) {
                if (!alive[a]) continue;
                for (let b = a + 1; b < K0; b++) {
                    if (!alive[b]) continue;
                    let d2 = 0;
                    for (let d = 0; d < dim; d++) {
                        const diff = mu[a * dim + d] - mu[b * dim + d];
                        d2 += diff * diff;
                    }
                    const cost = n[a] * n[b] / (n[a] + n[b]) * d2;
                    if (cost < bestCost) { bestCost = cost; ba = a; bb = b; }
                }
            }
            for (let d = 0; d < dim; d++) {
                mu[ba * dim + d] = (n[ba] * mu[ba * dim + d] + n[bb] * mu[bb * dim + d]) / (n[ba] + n[bb]);
            }
            n[ba] += n[bb];
            alive[bb] = 0;
        }
        const merged = new Float32Array(k * dim);
        let g = 0;
        for (let c = 0; c < K0; c++) {
            if (!alive[c]) continue;
            for (let d = 0; d < dim; d++) merged[g * dim + d] = mu[c * dim + d];
            g++;
        }
        return merged;
    }

    // Lloyd iterations from the given centroids, on the sample (sampleIdx) or on all N vectors
    // (sampleIdx null). Stops below 0.5 % changed assignments; an empty cluster keeps its centroid.
    function lloyd(embeddings, N, dim, centroids, k, maxIter, sampleIdx) {
        const S = sampleIdx ? sampleIdx.length : N;
        const assign = new Int32Array(S).fill(-1);
        const counts = new Int32Array(k);
        const sums = new Float64Array(k * dim);
        for (let iter = 0; iter < maxIter; iter++) {
            let changed = 0;
            sums.fill(0);
            counts.fill(0);
            for (let si = 0; si < S; si++) {
                const base = (sampleIdx ? sampleIdx[si] : si) * dim;
                let bestC = 0, bestDist = Infinity;
                for (let c = 0; c < k; c++) {
                    let dist = 0;
                    const cBase = c * dim;
                    for (let d = 0; d < dim; d++) {
                        const diff = embeddings[base + d] - centroids[cBase + d];
                        dist += diff * diff;
                    }
                    if (dist < bestDist) { bestDist = dist; bestC = c; }
                }
                if (assign[si] !== bestC) changed++;
                assign[si] = bestC;
                counts[bestC]++;
                const cBase = bestC * dim;
                for (let d = 0; d < dim; d++) sums[cBase + d] += embeddings[base + d];
            }
            for (let c = 0; c < k; c++) {
                if (counts[c] === 0) continue;
                const cBase = c * dim;
                for (let d = 0; d < dim; d++) centroids[cBase + d] = sums[cBase + d] / counts[c];
            }
            if (changed < S * 0.005) break;
        }
    }

    // Separable Gaussian filter of the embedding grid, in place. sx / sy = sigma in pixels along
    // x / y, kernel cut at 3 sigma, edges replicated. Uses one row and one column as buffer.
    // cellIdx maps grid cell y * W + x to its vector, -1 for a cell without data; null when
    // every cell holds a vector in row-major order. Cells without data are left out of each
    // pass and the remaining weights renormalised; they get no value themselves.
    function gaussFilter(embeddings, W, H, dim, sx, sy, cellIdx = null) {
        const kernel = (sigma) => {
            const r = Math.max(1, Math.ceil(3 * sigma));
            const w = new Float64Array(2 * r + 1);
            let z = 0;
            for (let j = -r; j <= r; j++) { w[j + r] = Math.exp(-j * j / (2 * sigma * sigma)); z += w[j + r]; }
            for (let j = 0; j < w.length; j++) w[j] /= z;
            return w;
        };
        const wx = kernel(sx), rx = (wx.length - 1) / 2;
        const wy = kernel(sy), ry = (wy.length - 1) / 2;
        // On a dense grid no weight is ever skipped, so the arithmetic is exactly the plain filter.
        const row = new Float32Array(W * dim);
        for (let y = 0; y < H; y++) {
            row.fill(0);
            for (let x = 0; x < W; x++) {
                if (cellIdx && cellIdx[y * W + x] < 0) continue;
                const o = x * dim;
                let wsum = 0, skipped = false;
                for (let j = -rx; j <= rx; j++) {
                    const xx = x + j < 0 ? 0 : (x + j >= W ? W - 1 : x + j);
                    const v = cellIdx ? cellIdx[y * W + xx] : y * W + xx;
                    const w = wx[j + rx];
                    if (v < 0) { skipped = true; continue; }
                    const base = v * dim;
                    wsum += w;
                    for (let d = 0; d < dim; d++) row[o + d] += w * embeddings[base + d];
                }
                if (skipped) for (let d = 0; d < dim; d++) row[o + d] /= wsum;
            }
            if (!cellIdx) {
                embeddings.set(row, y * W * dim);
            } else {
                for (let x = 0; x < W; x++) {
                    const v = cellIdx[y * W + x];
                    if (v >= 0) embeddings.set(row.subarray(x * dim, (x + 1) * dim), v * dim);
                }
            }
        }
        const col = new Float32Array(H * dim);
        for (let x = 0; x < W; x++) {
            col.fill(0);
            for (let y = 0; y < H; y++) {
                if (cellIdx && cellIdx[y * W + x] < 0) continue;
                const o = y * dim;
                let wsum = 0, skipped = false;
                for (let j = -ry; j <= ry; j++) {
                    const yy = y + j < 0 ? 0 : (y + j >= H ? H - 1 : y + j);
                    const v = cellIdx ? cellIdx[yy * W + x] : yy * W + x;
                    const w = wy[j + ry];
                    if (v < 0) { skipped = true; continue; }
                    const base = v * dim;
                    wsum += w;
                    for (let d = 0; d < dim; d++) col[o + d] += w * embeddings[base + d];
                }
                if (skipped) for (let d = 0; d < dim; d++) col[o + d] /= wsum;
            }
            for (let y = 0; y < H; y++) {
                const v = cellIdx ? cellIdx[y * W + x] : y * W + x;
                if (v < 0) continue;
                const base = v * dim, o = y * dim;
                for (let d = 0; d < dim; d++) embeddings[base + d] = col[o + d];
            }
        }
    }

    // MRF smoothing with a Potts prior, solved with ICM (Besag 1986): every pixel takes the label c
    // that minimises  D[c] / s + beta * (number of its 8 neighbours with a label other than c),
    // where D are the squared distances to the centroids and s is their mean over all pixels at
    // the nearest centroid, so that beta does not depend on the scale of the embeddings.
    // All pixels are updated at once from the previous labels; at most iters sweeps.
    // labels / D are per vector; cellIdx maps grid cells to vectors as in gaussFilter (null =
    // dense, row-major). Only neighbours with data count, so a pixel next to a gap is judged by
    // fewer votes and an isolated pixel keeps its nearest centroid.
    function icmSmooth(D, labels, W, H, k, beta, iters, cellIdx = null) {
        const N = labels.length;
        let s = 0;
        for (let i = 0; i < N; i++) s += D[i * k + labels[i]];
        s = s / N || 1;
        let cur = labels, next = new Int32Array(N);
        const same = new Int32Array(k);
        for (let it = 0; it < iters; it++) {
            let changed = 0;
            for (let y = 0; y < H; y++) {
                const y0 = y > 0 ? y - 1 : 0, y1 = y < H - 1 ? y + 1 : H - 1;
                for (let x = 0; x < W; x++) {
                    const x0 = x > 0 ? x - 1 : 0, x1 = x < W - 1 ? x + 1 : W - 1;
                    const i = cellIdx ? cellIdx[y * W + x] : y * W + x;
                    if (i < 0) continue;
                    same.fill(0);
                    let nb = 0;
                    for (let yy = y0; yy <= y1; yy++) {
                        for (let xx = x0; xx <= x1; xx++) {
                            if (yy === y && xx === x) continue;
                            const j = cellIdx ? cellIdx[yy * W + xx] : yy * W + xx;
                            if (j < 0) continue;
                            same[cur[j]]++;
                            nb++;
                        }
                    }
                    let bestC = 0, bestE = Infinity;
                    for (let c = 0; c < k; c++) {
                        const energy = D[i * k + c] / s + beta * (nb - same[c]);
                        if (energy < bestE) { bestE = energy; bestC = c; }
                    }
                    next[i] = bestC;
                    if (bestC !== cur[i]) changed++;
                }
            }
            const tmp = cur; cur = next; next = tmp;
            if (changed < N * 1e-4) break;
        }
        if (cur !== labels) labels.set(cur);
    }

    self.onmessage = function(e) {
        const {N, dim, k, maxIter, seed} = e.data;
        const opts = e.data.opts || {};
        const rand = mulberry32((seed >>> 0) ^ 0x9e3779b9);
        const embeddings = new Float32Array(e.data.vectors);
        const sampleIdx = e.data.sampleIdx ? new Uint32Array(e.data.sampleIdx) : null;
        const assignments = new Int32Array(N);
        // Grid for the smoothing steps: dense and row-major (no gridIndex, W * H === N) or
        // gridIndex mapping every cell to its vector, -1 for a cell without data.
        const cellIdx = opts.gridIndex ? new Int32Array(opts.gridIndex) : null;
        const onGrid = opts.gridW > 0 &&
            (cellIdx ? cellIdx.length === opts.gridW * opts.gridH : opts.gridW * opts.gridH === N);
        const smoothed = onGrid && (!!opts.sigma || opts.beta > 0);

        if (opts.sigma && onGrid) {
            gaussFilter(embeddings, opts.gridW, opts.gridH, dim, opts.sigma[0], opts.sigma[1], cellIdx);
        }

        let centroids;
        if (opts.K0 > k) {
            // Over-cluster on the sample, merge to k groups, refine the merged centroids.
            const fine = fitKMeans(embeddings, N, dim, opts.K0, maxIter, sampleIdx, rand);
            const fineCounts = sampleCounts(embeddings, N, dim, sampleIdx, fine, opts.K0);
            centroids = wardMerge(fine, opts.K0, fineCounts, k, dim);
            const refineIdx = opts.refineIdx ? new Uint32Array(opts.refineIdx) : sampleIdx;
            lloyd(embeddings, N, dim, centroids, k, maxIter, refineIdx);
        } else {
            centroids = fitKMeans(embeddings, N, dim, k, maxIter, sampleIdx, rand);
        }
        if (opts.fullIters > 0) lloyd(embeddings, N, dim, centroids, k, opts.fullIters, null);

        // --- Final full assignment pass over ALL N vectors ---
        const nearestIdx = new Int32Array(k);
        const nearestDist = new Float64Array(k);
        const maxDist = new Float64Array(k);
        nearestDist.fill(Infinity);
        // The MRF needs the distance of every pixel to every centroid; keep them from this pass.
        const D = (opts.beta > 0 && onGrid) ? new Float32Array(N * k) : null;

        for (let i = 0; i < N; i++) {
            let bestC = 0, bestDist = Infinity;
            const base = i * dim;
            for (let c = 0; c < k; c++) {
                let dist = 0;
                const cBase = c * dim;
                for (let d = 0; d < dim; d++) {
                    const diff = embeddings[base + d] - centroids[cBase + d];
                    dist += diff * diff;
                }
                if (D) D[i * k + c] = dist;
                if (dist < bestDist) { bestDist = dist; bestC = c; }
            }
            assignments[i] = bestC;
            const dist = Math.sqrt(bestDist);
            if (dist > maxDist[bestC]) maxDist[bestC] = dist;
            if (bestDist < nearestDist[bestC]) { nearestDist[bestC] = bestDist; nearestIdx[bestC] = i; }
        }

        if (D) {
            icmSmooth(D, assignments, opts.gridW, opts.gridH, k, opts.beta, 5, cellIdx);
            // Representative pixel and radius of every cluster for the smoothed labels.
            nearestDist.fill(Infinity);
            maxDist.fill(0);
            for (let i = 0; i < N; i++) {
                const c = assignments[i], d2 = D[i * k + c];
                const dist = Math.sqrt(d2);
                if (dist > maxDist[c]) maxDist[c] = dist;
                if (d2 < nearestDist[c]) { nearestDist[c] = d2; nearestIdx[c] = i; }
            }
        }

        self.postMessage({
            assignments: assignments.buffer,
            centroids: centroids.buffer,
            nearestIdx: nearestIdx.buffer,
            maxDist: maxDist.buffer,
            smoothed
        }, [assignments.buffer, centroids.buffer, nearestIdx.buffer, maxDist.buffer]);
    };
`], {type: 'application/javascript'});
const segWorkerURL = URL.createObjectURL(segWorkerBlob);

// ── Utilities ──

// 20 maximally-distinct colours (Kelly 1965 + Tableau, pastels avoided)
const SEG_PALETTE = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#42d4f4', '#f032e6', '#ffe119', '#469990', '#e6beff',
    '#9a6324', '#800000', '#aaffc3', '#808000', '#000075',
    '#fabebe', '#bfef45', '#ffd8b1', '#a9a9a9', '#dcbeff',
];

Object.defineProperty(window, 'SEG_PALETTE', {
    get: () => SEG_PALETTE,
    configurable: true,
});

// Build Fisher-Yates partial-shuffle sample of given size from [0..N).
// `rand` is a seeded PRNG (mulberry32) -- required, so the same seed
// picks the same sample every run.
function buildSample(N, size, rand) {
    if (size >= N) return null; // no subsampling needed
    const indices = new Uint32Array(size);
    const pool = new Uint32Array(N);
    for (let i = 0; i < N; i++) pool[i] = i;
    for (let i = 0; i < size; i++) {
        const j = i + Math.floor(rand() * (N - i));
        const tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
        indices[i] = pool[i];
    }
    return indices;
}

// ── Clustering methods ──

// Options for the worker behind the "method" selector (#seg-method-select).
//   kmeans  the original pipeline: k-means++ and Lloyd on a sample, one assignment pass.
//   stable  over-cluster into K0 clusters, merge them to k with Ward's criterion and refine.
//           Much less dependent on the seed than a single k-means run.
//   smooth  'stable', then MRF smoothing of the labels (weight beta): a pixel whose neighbours
//           agree on another cluster joins them unless its embedding clearly says otherwise.
//           Removes isolated pixels; the centroids stay those of 'stable'.
//   gauss   'stable' on embeddings that were Gaussian-filtered with sigmaM metres. Smooths before
//           clustering instead of after it, so the centroids live in the filtered space.
// Sample sizes: the over-clustering fits on max(5000, 300 K0) vectors, the refinement on the
// usual max(5000, 500 k).
const SEG_METHODS = {
    kmeans: {},
    stable: { K0: 32 },
    smooth: { K0: 32, beta: 0.25 },
    gauss: { K0: 32, sigmaM: 10 },
};
if (typeof window !== 'undefined') {
    Object.defineProperty(window, 'SEG_METHODS', { get: () => SEG_METHODS, configurable: true });
}

// Size of one pixel in metres [x, y] at the centre of an EPSG:4326 viewport grid.
function pixelSizeMetres(gt, gridH) {
    const lat = (gt.f + 0.5 * gridH * gt.e) * Math.PI / 180;
    return [Math.abs(gt.a) * 111320 * Math.cos(lat), Math.abs(gt.e) * 110574];
}

// Worker options and fitting-sample size for one run. `grid` is segVectors.gridLookup
// ({w, h, index}): index === null means every cell holds a vector, in row-major order;
// otherwise index maps each cell to its vector, -1 where the source data has a gap, and a copy
// goes to the worker. The grid steps are skipped without a grid (the run reports smoothed: false).
function buildSegOptions(method, k, N, seed, grid, gt) {
    const m = SEG_METHODS[method] || SEG_METHODS.kmeans;
    const opts = {};
    const transfer = [];
    let sampleSize = Math.min(Math.max(5000, k * 500), N);
    if (m.K0 > k) {
        opts.K0 = m.K0;
        const refine = buildSample(N, sampleSize, mulberry32((seed + 1000) >>> 0));
        if (refine) { opts.refineIdx = refine.buffer; transfer.push(refine.buffer); }
        sampleSize = Math.min(Math.max(5000, m.K0 * 300), N);
    }
    if (m.fullIters) opts.fullIters = m.fullIters;
    const dense = !!grid && grid.w > 0 && grid.index === null;
    const gapped = !!grid && grid.w > 0 && ArrayBuffer.isView(grid.index) &&
        grid.index.length === grid.w * grid.h;
    if ((dense || gapped) && (m.sigmaM || m.beta)) {
        opts.gridW = grid.w;
        opts.gridH = grid.h;
        if (gapped) {
            const index = Int32Array.from(grid.index);
            opts.gridIndex = index.buffer;
            transfer.push(index.buffer);
        }
        if (m.beta) opts.beta = m.beta;
        if (m.sigmaM && gt) {
            const [mx, my] = pixelSizeMetres(gt, grid.h);
            opts.sigma = [m.sigmaM / mx, m.sigmaM / my];
        }
    }
    return { opts, sampleSize, transfer };
}

// Note for the cluster list when a method's smoothing step could not run (no pixel grid for
// these vectors), so that the user knows the clusters are those of plain 'stable'. Gaps in
// the source data do not cause this: the smoothing then uses only the pixels with data.
function segSmoothingNote(method, smoothed) {
    const m = SEG_METHODS[method];
    if (!m || !(m.sigmaM || m.beta) || smoothed) return null;
    return 'Ran as Stable: the smoothing step needs the pixel grid of the viewport, which is not available for these vectors.';
}

// ── K-means Execution ──

async function runKMeans(k, seed, method) {
    if (!window.localVectors || segRunning) return;
    segRunning = true;
    segK = k;
    if (!method) {
        const mEl = document.getElementById('seg-method-select');
        method = mEl ? mEl.value : 'kmeans';
    }
    // One seed drives the whole run (subsample + K-means++ init): same
    // seed + k + vectors => identical clusters. Default 42, matching the
    // Validation panel.
    if (seed === undefined || seed === null || isNaN(seed)) {
        const sEl = document.getElementById('seg-seed-input');
        seed = sEl ? (parseInt(sEl.value, 10) || 42) : 42;
    }
    segSeed = seed;
    const btn = document.getElementById('seg-run-btn');
    btn.disabled = true;
    btn.style.opacity = '0.5';
    document.getElementById('seg-k-input').value = k;
    { const sEl = document.getElementById('seg-seed-input'); if (sEl) sEl.value = seed; }

    // Clear old overlay immediately so user sees a clean map during recomputation
    if (segOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(segOverlay)) {
        window.maps.panel5.removeLayer(segOverlay);
    }
    segOverlay = null;
    segLabels = [];
    segNote = null;
    showSegmentationPanel(); // clears both floating popup and panel 6

    // Always segment the year shown in panel 3
    const segYear = document.getElementById('embedding-year-selector').value || window.localVectors.year;
    const segKey = `${window.currentViewportName}/${segYear}`;

    // Load vectors for the selected year (may differ from localVectors). Memoise
    // across k-changes: bumping the cluster count must not re-download / re-decode
    // the same year's vectors (and flash the loading overlay) every time.
    if (segVectors && segVectorsKey === segKey) {
        // reuse the vectors already held for this viewport/year
    } else if (String(segYear) !== String(window.localVectors.year)) {
        const saved = window.localVectors;
        try {
            await window.downloadVectorData(window.currentViewportName, segYear);
            segVectors = window.localVectors;
        } finally {
            window.localVectors = saved;
        }
        segVectorsKey = segKey;
    } else {
        segVectors = window.localVectors;
        segVectorsKey = segKey;
    }

    const N = segVectors.numVectors;
    const dim = segVectors.dim;

    // The worker clusters on Float32. Build a transient dequantized buffer via the
    // shared getDequant() (uint8 -> real via dim_min/dim_max; Float32 -> identity);
    // never keep a resident float array. (Passing raw uint8 to the worker makes it
    // read 1/4-size garbage -> one cluster.)
    const sv = segVectors.values;
    let embCopy;
    if (sv.BYTES_PER_ELEMENT === 1) {
        const { scale, min } = window.getDequant(segVectors);
        // N*dim*4 bytes in one contiguous block -- this is the second big
        // allocation on the VQ path (after the loader), and can hit the
        // "Array buffer allocation failed" ceiling on a very large viewport.
        // Probe on failure so we know if this is a real problem in production
        // (see the VQ-loader plan); still rethrow so the worker's onerror shows
        // the user a failure rather than a silent hang.
        let f;
        try {
            f = new Float32Array(sv.length);
        } catch (e) {
            if (window.reportMemoryPressure) {
                window.reportMemoryPressure('kmeans-dequant-buffer', {
                    viewport: segVectors.viewport || null, N, dim, bytes: sv.length * 4,
                });
            }
            throw e;
        }
        for (let i = 0; i < N; i++) {
            const b = i * dim;
            for (let d = 0; d < dim; d++) f[b + d] = sv[b + d] * scale[d] + min[d];
        }
        embCopy = f.buffer;
    } else {
        // Already Float32 — copy before transferring (don't neuter the live buffer).
        embCopy = sv.buffer.slice(0);
    }
    const transferables = [embCopy];

    const { opts, sampleSize, transfer } = buildSegOptions(
        method, k, N, seed >>> 0, segVectors.gridLookup, segVectors.metadata.geotransform);
    transferables.push(...transfer);
    const sample = buildSample(N, sampleSize, mulberry32(seed >>> 0));
    const sampleBuf = sample ? sample.buffer : null;
    if (sampleBuf) transferables.push(sampleBuf);

    if (segWorker) segWorker.terminate();
    segWorker = new Worker(segWorkerURL);

    segWorker.onerror = (err) => {
        console.error('[SEG] Worker error:', err);
        alert('Segmentation failed: ' + (err.message || 'unknown error'));
        segRunning = false;
        btn.disabled = false;
        btn.style.opacity = '';
    };

    segWorker.onmessage = (e) => {
        try {
            segAssignments = new Int32Array(e.data.assignments);
            const centroids = new Float32Array(e.data.centroids);
            const nearestIdxArr = new Int32Array(e.data.nearestIdx);
            const maxDistArr = new Float64Array(e.data.maxDist);
            segNote = segSmoothingNote(method, e.data.smoothed);

            const counts = new Int32Array(k);
            for (let i = 0; i < N; i++) counts[segAssignments[i]]++;

            const gt = segVectors.metadata.geotransform;
            segLabels = [];
            for (let c = 0; c < k; c++) {
                if (counts[c] === 0) continue;
                const hex = SEG_PALETTE[c % SEG_PALETTE.length];
                const color = hex;
                const ni = nearestIdxArr[c];
                const px = segVectors.coords[ni * 2], py = segVectors.coords[ni * 2 + 1];
                segLabels.push({
                    id: c, color, hex, name: `Cluster ${c + 1}`, count: counts[c],
                    embedding: Array.from(window.dequantSlice(segVectors, ni)),
                    sourcePixel: { lat: gt.f + py * gt.e, lon: gt.c + px * gt.a },
                    threshold: maxDistArr[c],
                    centroid: Array.from(centroids.subarray(c * dim, (c + 1) * dim))
                });
            }

            showSegmentationOverlay();
            showSegmentationPanel();

            // Update k controls
            document.getElementById('seg-k-input').value = k;
            document.getElementById('seg-k-minus').disabled = (k <= 2);
            document.getElementById('seg-k-plus').disabled = (k >= 20);
            document.getElementById('seg-clear-btn').style.display = '';
        } catch (err) {
            console.error('[SEG] onmessage error:', err);
        } finally {
            segRunning = false;
            btn.disabled = false;
            btn.style.opacity = '';
        }
    };

    segWorker.postMessage(
        {vectors: embCopy, N, dim, k, maxIter: 20, sampleIdx: sampleBuf, seed: seed >>> 0, opts},
        transferables
    );
}

// ── Segmentation Overlay ──

function showSegmentationOverlay() {
    if (!segAssignments || !segVectors) return;
    const coords = segVectors.coords;
    const N = segVectors.numVectors;
    const gt = segVectors.metadata.geotransform;

    // Find pixel bounding box
    let minPx = Infinity, maxPx = -Infinity, minPy = Infinity, maxPy = -Infinity;
    for (let i = 0; i < N; i++) {
        const px = coords[i * 2], py = coords[i * 2 + 1];
        if (px < minPx) minPx = px;
        if (px > maxPx) maxPx = px;
        if (py < minPy) minPy = py;
        if (py > maxPy) maxPy = py;
    }

    const width = maxPx - minPx + 1;
    const height = maxPy - minPy + 1;

    // Build colour lookup from segLabels
    const colorLookup = new Map();
    for (const cl of segLabels) {
        const hex = cl.hex;
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        colorLookup.set(cl.id, [r, g, b]);
    }

    // Create RGBA image
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(width, height);
    const data = imgData.data;

    for (let i = 0; i < N; i++) {
        const gx = coords[i * 2] - minPx;
        const gy = coords[i * 2 + 1] - minPy;
        const idx = (gy * width + gx) * 4;
        const rgb = colorLookup.get(segAssignments[i]);
        if (rgb) {
            data[idx] = rgb[0];
            data[idx + 1] = rgb[1];
            data[idx + 2] = rgb[2];
            data[idx + 3] = 153; // ~60% opacity
        }
    }
    ctx.putImageData(imgData, 0, 0);

    // Compute geographic bounds from pixel corners via geotransform
    // gt: c=originX(lon), a=pixelWidth, f=originY(lat), e=pixelHeight(negative)
    const lonMin = gt.c + minPx * gt.a;
    const lonMax = gt.c + (maxPx + 1) * gt.a;
    const latMin = gt.f + (maxPy + 1) * gt.e;  // e is negative, so maxPy gives smaller lat
    const latMax = gt.f + minPy * gt.e;

    // Remove old overlay
    if (segOverlay && window.maps.panel5.hasLayer(segOverlay)) {
        window.maps.panel5.removeLayer(segOverlay);
    }

    const dataURL = canvas.toDataURL();
    segOverlay = L.imageOverlay(dataURL, [[latMin, lonMin], [latMax, lonMax]]);

    // Consult the layer rules table — only add to map if current mode allows it
    const segRules = window.PANEL5_LAYER_RULES[window.currentPanelMode] || window.PANEL5_LAYER_RULES['explore'];
    if (segRules.segOverlay) {
        segOverlay.addTo(window.maps.panel5);
        segOverlay.getElement().style.imageRendering = 'pixelated';
    }
}

// ── Clear Segmentation ──

function clearSegmentation() {
    if (segOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(segOverlay)) {
        window.maps.panel5.removeLayer(segOverlay);
    }
    segOverlay = null;
    segAssignments = null;
    segVectors = null;
    segLabels = [];
    segNote = null;
    document.getElementById('seg-clear-btn').style.display = 'none';
    document.getElementById('seg-k-input').value = 5;
    showSegmentationPanel(); // clears both floating popup and panel 6
}

// ── Cluster List Rendering ──

function renderSegListInto(container) {
    if (!container) return;
    const total = segAssignments ? segAssignments.length : 0;
    if (segLabels.length === 0) {
        container.innerHTML = '<div style="color: #999; padding: 10px; text-align: center;">Run segmentation to see clusters</div>';
        return;
    }
    container.innerHTML = '';
    if (segNote) {
        const note = document.createElement('div');
        note.className = 'seg-note';
        note.style.cssText = 'margin: 0 0 8px; padding: 6px 8px; border-radius: 4px; background: #fff7e0; color: #7a5a00; font-size: 11px;';
        note.textContent = segNote;
        container.appendChild(note);
    }
    const hasSchema = !!window.activeSchema;
    for (const cl of segLabels) {
        const pct = total > 0 ? ((cl.count / total) * 100).toFixed(1) : '0.0';
        const countStr = cl.count >= 1000 ? (cl.count / 1000).toFixed(1).replace(/\.0$/, '') + 'K' : String(cl.count);
        const row = document.createElement('div');
        row.className = 'seg-cluster-row';
        row.innerHTML = `
            <span class="seg-color-swatch" title="Click to change colour" style="background: ${cl.color};"></span>
            <input type="color" class="seg-color-picker" value="${cl.hex}" style="position: absolute; width: 0; height: 0; overflow: hidden; opacity: 0; pointer-events: none;">
            <input type="text" placeholder="Label name" data-cluster-id="${cl.id}"
                style="min-width: 0; flex: 1 1 60px; padding: 3px 6px; border: none; border-bottom: 1px solid #ccc; border-radius: 0; font-size: 12px; color: #333; background: transparent; outline: none; overflow: hidden; text-overflow: ellipsis;">
            ${hasSchema ? `<button class="seg-schema-btn" title="Browse schema to pick a label name" style="flex-shrink: 0; background: none; border: 1px solid #888; border-radius: 3px; color: #667eea; cursor: pointer; font-size: 11px; padding: 1px 4px; line-height: 1.2;">S</button>` : ''}
            <span style="color: #555; font-size: 10px; white-space: nowrap; flex-shrink: 0;" title="${cl.count.toLocaleString()} pixels">${countStr} ${pct}%</span>
            <button title="Promote to saved label" data-promote-id="${cl.id}"
                style="flex-shrink: 0; background: none; border: 1px solid #667eea; border-radius: 3px; color: #667eea; cursor: pointer; font-size: 11px; padding: 1px 5px; line-height: 1.2;">&#8594;</button>
        `;
        // Color picker
        const swatch = row.querySelector('.seg-color-swatch');
        const picker = row.querySelector('.seg-color-picker');
        swatch.addEventListener('click', () => picker.click());
        picker.addEventListener('input', (e) => {
            const hex = e.target.value;
            cl.hex = hex;
            cl.color = hex;
            swatch.style.background = hex;
            showSegmentationOverlay();
        });
        row.querySelector('[data-promote-id]').addEventListener('click', () => saveClusterAsLabel(cl.id));
        if (hasSchema) {
            const schemaBtn = row.querySelector('.seg-schema-btn');
            const inputEl = row.querySelector(`input[data-cluster-id="${cl.id}"]`);
            schemaBtn.addEventListener('click', () => window.openSchemaForCluster(inputEl));
        }
        container.appendChild(row);
    }
}

// ── Segmentation Panel Display ──

function showSegmentationPanel() {
    // Render into floating popup
    renderSegListInto(document.getElementById('seg-cluster-list'));
    // Render into panel 6 left column
    renderSegListInto(document.getElementById('panel6-seg-list'));

    // Show floating popup only if clusters exist and not in labelling mode
    const segPanel = document.getElementById('seg-results-panel');
    if (segLabels.length > 0 && window.currentPanelMode !== 'labelling') {
        segPanel.style.display = '';
    } else {
        segPanel.style.display = 'none';
    }

    // Show/hide promote-all button in panel 6
    const p6btn = document.getElementById('panel6-promote-all-btn');
    if (p6btn) p6btn.style.display = segLabels.length > 0 ? '' : 'none';
}

// ── Save Cluster As Label ──

function saveClusterAsLabel(clusterId) {
    if (!segAssignments || !segVectors) return;
    const cluster = segLabels.find(c => c.id === clusterId);
    if (!cluster) return;

    // Read name from the active container (panel 6 in labelling mode, floating popup otherwise)
    const containerSel = window.currentPanelMode === 'labelling' ? '#panel6-seg-list' : '#seg-cluster-list';
    const input = document.querySelector(`${containerSel} input[data-cluster-id="${clusterId}"]`);
    const name = (input && input.value.trim()) ? input.value.trim() : cluster.name;

    const coords = segVectors.coords;
    const gt = segVectors.metadata.geotransform;
    const N = segVectors.numVectors;

    // Collect pixels, compact pixel_coords, and find max distance from centroid
    const pixels = [];
    const pixel_coords = [];
    const centroid = cluster.embedding;
    const dim = segVectors.dim || 128;
    const emb = segVectors.values;
    // Dequantize inline (real = emb*scale[d] + min[d]); centroid is already real-space.
    const { scale, min } = window.getDequant(segVectors);
    let maxDistSq = 0;
    for (let i = 0; i < N; i++) {
        if (segAssignments[i] !== clusterId) continue;
        const px = coords[i * 2], py = coords[i * 2 + 1];
        pixel_coords.push(px, py);
        pixels.push({
            lat: gt.f + py * gt.e,
            lon: gt.c + px * gt.a,
            distance: 0
        });
        // Compute squared Euclidean distance from centroid
        if (centroid) {
            let s = 0;
            const base = i * dim;
            for (let d = 0; d < dim; d++) {
                const diff = emb[base + d] * scale[d] + min[d] - centroid[d];
                s += diff * diff;
            }
            if (s > maxDistSq) maxDistSq = s;
        }
    }
    const clusterRadius = Math.sqrt(maxDistSq);

    // Reuse color from existing label with the same name, if any
    const existingWithName = window.manualLabels.find(l => l.name === name);
    const color = existingWithName ? existingWithName.color : cluster.hex;

    // Add to manualLabels with exact pixel matches from k-means.
    // Store centroid + cluster radius for later similarity expansion.
    window.addManualLabel({
        name,
        color,
        code: null,
        type: 'similarity',
        lat: cluster.sourcePixel ? cluster.sourcePixel.lat : 0,
        lon: cluster.sourcePixel ? cluster.sourcePixel.lon : 0,
        embedding: centroid ? Array.from(centroid) : null,
        threshold: clusterRadius,
        matchCount: pixels.length,
        pixel_coords: pixel_coords,  // compact [x0,y0,x1,y1,...] for exact cluster pixels
    });

    // Build overlay directly from exact seg pixels (not via similarity search)
    if (window.maps.rgb) {
        if (window.manualClassOverlays[name]) {
            if (window.manualClassOverlays[name].layerGroup && window.maps.rgb) window.maps.rgb.removeLayer(window.manualClassOverlays[name].layerGroup);
            delete window.manualClassOverlays[name];
        }
        const layerGroup = L.layerGroup();
        if (pixels.length > 0) {
            const canvasLayer = new window.DirectCanvasLayer(pixels, window.maps.rgb, color);
            layerGroup.addLayer(canvasLayer);
        }
        layerGroup.addTo(window.maps.rgb);
        window.manualClassOverlays[name] = { layerGroup, layer: null };
        window._classMatchCache[name] = pixels.map(m => ({ lat: m.lat, lon: m.lon }));
        window.updatePanel4ManualLabels();
    }

    // Remove promoted cluster from seg panel
    segLabels = segLabels.filter(c => c.id !== clusterId);
    if (segLabels.length === 0) {
        if (segOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(segOverlay)) {
            window.maps.panel5.removeLayer(segOverlay);
        }
        segOverlay = null;
    }
    showSegmentationPanel(); // refreshes both floating popup and panel 6
}

function saveAllClustersAsLabels() {
    if (!segAssignments || !segVectors) return;
    // Snapshot ids before iterating — saveClusterAsLabel mutates segLabels
    const ids = segLabels.map(cl => cl.id);
    for (const id of ids) {
        saveClusterAsLabel(id);
    }
}

// ── Expose on window for onclick handlers and inline script access ──

window.runKMeans = runKMeans;
window.showSegmentationOverlay = showSegmentationOverlay;
window.clearSegmentation = clearSegmentation;
window.renderSegListInto = renderSegListInto;
window.showSegmentationPanel = showSegmentationPanel;
window.saveClusterAsLabel = saveClusterAsLabel;
window.saveAllClustersAsLabels = saveAllClustersAsLabels;
window.buildSample = buildSample;
