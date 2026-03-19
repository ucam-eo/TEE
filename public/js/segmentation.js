// segmentation.js — K-means clustering, cluster list, seg overlay
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──

let segWorker = null;
let segAssignments = null;   // Int32Array, length N
let segK = 5;
let segOverlay = null;       // L.imageOverlay instance
let segLabels = [];          // [{id, color, name, count}, ...]
let segRunning = false;      // prevent concurrent runs
let segVectors = null;       // vectors used for current segmentation (may differ from localVectors)

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

// ── K-means Worker ──

// Inline Web Worker for k-means clustering (K-means++ init, early stopping, subsampling)
const segWorkerBlob = new Blob([`
    self.onmessage = function(e) {
        const {N, dim, k, maxIter} = e.data;
        const embeddings = new Float32Array(e.data.vectors);
        const sampleIdx = e.data.sampleIdx ? new Uint32Array(e.data.sampleIdx) : null;
        const S = sampleIdx ? sampleIdx.length : N;
        const assignments = new Int32Array(N);
        const centroids = new Float32Array(k * dim);

        // --- K-means++ initialization (on sample) ---
        const firstPt = sampleIdx ? sampleIdx[Math.floor(Math.random() * S)] : Math.floor(Math.random() * N);
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
            let r = Math.random() * totalWeight;
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
                    const ri = sampleIdx ? sampleIdx[Math.floor(Math.random() * S)] : Math.floor(Math.random() * N);
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

        // --- Final full assignment pass over ALL N vectors ---
        const nearestIdx = new Int32Array(k);
        const nearestDist = new Float64Array(k);
        const maxDist = new Float64Array(k);
        nearestDist.fill(Infinity);

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
                if (dist < bestDist) { bestDist = dist; bestC = c; }
            }
            assignments[i] = bestC;
            const dist = Math.sqrt(bestDist);
            if (dist > maxDist[bestC]) maxDist[bestC] = dist;
            if (bestDist < nearestDist[bestC]) { nearestDist[bestC] = bestDist; nearestIdx[bestC] = i; }
        }

        self.postMessage({
            assignments: assignments.buffer,
            centroids: centroids.buffer,
            nearestIdx: nearestIdx.buffer,
            maxDist: maxDist.buffer
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

// Build Fisher-Yates partial-shuffle sample of given size from [0..N)
function buildSample(N, size) {
    if (size >= N) return null; // no subsampling needed
    const indices = new Uint32Array(size);
    const pool = new Uint32Array(N);
    for (let i = 0; i < N; i++) pool[i] = i;
    for (let i = 0; i < size; i++) {
        const j = i + Math.floor(Math.random() * (N - i));
        const tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
        indices[i] = pool[i];
    }
    return indices;
}

// ── K-means Execution ──

async function runKMeans(k) {
    if (!window.localVectors || segRunning) return;
    segRunning = true;
    segK = k;
    const btn = document.getElementById('seg-run-btn');
    btn.disabled = true;
    btn.style.opacity = '0.5';
    document.getElementById('seg-k-input').value = k;

    // Clear old overlay immediately so user sees a clean map during recomputation
    if (segOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(segOverlay)) {
        window.maps.panel5.removeLayer(segOverlay);
    }
    segOverlay = null;
    segLabels = [];
    showSegmentationPanel(); // clears both floating popup and panel 6

    // Always segment the year shown in panel 3
    const segYear = document.getElementById('embedding-year-selector').value || window.localVectors.year;

    // Load vectors for selected year (may differ from localVectors)
    if (String(segYear) !== String(window.localVectors.year)) {
        const saved = window.localVectors;
        try {
            await window.downloadVectorData(window.currentViewportName, segYear);
            segVectors = window.localVectors;
        } finally {
            window.localVectors = saved;
        }
    } else {
        segVectors = window.localVectors;
    }

    const N = segVectors.numVectors;
    const dim = segVectors.dim;

    // Slice to avoid detaching segVectors.values
    const embCopy = segVectors.values.buffer.slice(0);
    const transferables = [embCopy];

    const sampleSize = Math.min(Math.max(5000, k * 500), N);
    const sample = buildSample(N, sampleSize);
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
                    embedding: Array.from(segVectors.values.subarray(ni * dim, (ni + 1) * dim)),
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
        {vectors: embCopy, N, dim, k, maxIter: 20, sampleIdx: sampleBuf},
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
                const diff = emb[base + d] - centroid[d];
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
        matchCount: pixels.length
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
