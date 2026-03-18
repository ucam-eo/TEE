// vectors.js — Vector data management, client-side search, explorer visualization
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──

let localVectors = null;
let _distSqBuf = null;
let explorerResults = null;
let explorerVisualization = null;
let explorerCanvasLayer = null;

Object.defineProperty(window, 'localVectors', {
    get: () => localVectors,
    set: (v) => { localVectors = v; },
    configurable: true,
});
Object.defineProperty(window, 'explorerResults', {
    get: () => explorerResults,
    set: (v) => { explorerResults = v; },
    configurable: true,
});

// ── Grid Lookup ──

// Grid-based pixel lookup: O(1) arithmetic instead of Map with string keys
function buildGridLookup(coordsData, numVectors) {
    // Find grid bounds from first/last coords (regular meshgrid)
    const minX = coordsData[0], minY = coordsData[1];
    // Find gridWidth: count consecutive coords with same Y
    let gridWidth = 1;
    for (let i = 1; i < numVectors; i++) {
        if (coordsData[i * 2 + 1] !== minY) break;
        gridWidth++;
    }
    const gridHeight = numVectors / gridWidth;
    return { minX, minY, w: gridWidth, h: gridHeight };
}
function gridLookupIndex(grid, px, py) {
    const rx = px - grid.minX, ry = py - grid.minY;
    if (rx < 0 || ry < 0 || rx >= grid.w || ry >= grid.h) return -1;
    return ry * grid.w + rx;
}

// ── IndexedDB Cache for Vector Data ──

const VectorCache = {
    DB_NAME: 'tee_vector_cache',
    STORE_NAME: 'vector_data',
    _db: null,

    async open() {
        if (this._db) return this._db;
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(this.DB_NAME, 4);
            req.onupgradeneeded = (e) => {
                const db = req.result;
                // Delete old store on upgrade to invalidate stale cache
                if (db.objectStoreNames.contains(this.STORE_NAME)) {
                    db.deleteObjectStore(this.STORE_NAME);
                }
                db.createObjectStore(this.STORE_NAME);
            };
            req.onsuccess = () => {
                this._db = req.result;
                resolve(this._db);
            };
            req.onerror = () => reject(req.error);
        });
    },

    async get(viewport, year) {
        const db = await this.open();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.STORE_NAME, 'readonly');
            const req = tx.objectStore(this.STORE_NAME).get(`${viewport}/${year}`);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        });
    },

    async put(viewport, year, data) {
        const db = await this.open();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.STORE_NAME, 'readwrite');
            tx.objectStore(this.STORE_NAME).put(data, `${viewport}/${year}`);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    },

    async delete(viewport, year) {
        const db = await this.open();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(this.STORE_NAME, 'readwrite');
            tx.objectStore(this.STORE_NAME).delete(`${viewport}/${year}`);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        });
    }
};

// ── Parsing ──

async function decompressGzip(blob) {
    const ds = new DecompressionStream('gzip');
    const decompressed = new Response(blob.stream().pipeThrough(ds));
    return await decompressed.arrayBuffer();
}

function parseNpy(buffer) {
    // Parse numpy .npy format: magic(6) + version(2) + header_len(2 or 4) + header + data
    const view = new DataView(buffer);
    const major = view.getUint8(6);
    let headerLen, dataOffset;
    if (major >= 2) {
        headerLen = view.getUint32(8, true);
        dataOffset = 12 + headerLen;
    } else {
        headerLen = view.getUint16(8, true);
        dataOffset = 10 + headerLen;
    }
    // Parse header string for dtype, shape, fortran_order
    const headerStr = new TextDecoder().decode(new Uint8Array(buffer, major >= 2 ? 12 : 10, headerLen));
    const descrMatch = headerStr.match(/'descr':\s*'([^']+)'/);
    const shapeMatch = headerStr.match(/'shape':\s*\(([^)]+)\)/);
    const fortranMatch = headerStr.match(/'fortran_order':\s*(True|False)/);
    const dtype = descrMatch ? descrMatch[1] : '<f4';
    const shape = shapeMatch ? shapeMatch[1].split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n)) : [];
    const fortranOrder = fortranMatch ? fortranMatch[1] === 'True' : false;
    const rawData = buffer.slice(dataOffset);
    return {rawData, dtype, shape, fortranOrder};
}

// ── Download ──

async function downloadVectorData(viewport, year) {
    // Skip if already loaded in memory for this viewport/year
    if (localVectors && localVectors.viewport === viewport && localVectors.year === String(year)) {
        return localVectors;
    }

    // Fetch metadata first (small, needed for cache validation)
    const metaResp0 = await fetch(`/api/vector-data/${viewport}/${year}/metadata.json`);
    const serverMetadata = metaResp0.ok ? await metaResp0.json() : null;

    // Check IndexedDB cache
    const cached = await VectorCache.get(viewport, year);
    if (cached) {
        // Validate cached data matches current viewport (bounds may have changed
        // if viewport was deleted and recreated with the same name)
        let cacheValid = true;
        if (serverMetadata && cached.metadata) {
            const cgt = cached.metadata.geotransform;
            const sgt = serverMetadata.geotransform;
            if (cgt && sgt && (cgt.c !== sgt.c || cgt.f !== sgt.f || cgt.a !== sgt.a)) {
                console.warn(`[VECTORS] Cache stale for ${viewport}/${year} — geotransform mismatch, re-downloading`);
                cacheValid = false;
            }
        }

        // Validate cached embeddings aren't all zeros (corrupt data)
        if (cacheValid) {
            let hasNonZero = false;
            for (let i = 0; i < Math.min(1000, cached.embeddings.length); i++) {
                if (cached.embeddings[i] !== 0) { hasNonZero = true; break; }
            }
            if (!hasNonZero) {
                console.warn(`[VECTORS] Cached data for ${viewport}/${year} is all zeros — purging`);
                cacheValid = false;
            }
        }

        if (cacheValid) {
            console.log(`[VECTORS] Cache hit for ${viewport}/${year}`);
            const numVectors = cached.embeddings.length / 128;
            const grid = buildGridLookup(cached.coords, numVectors);
            localVectors = {
                embeddings: cached.embeddings,
                coords: cached.coords,
                metadata: cached.metadata,
                gridLookup: grid,
                numVectors,
                dim: 128,
                viewport,
                year: String(year)
            };
            return localVectors;
        }
        await VectorCache.delete(viewport, year);
    }

    console.log(`[VECTORS] Downloading vector data for ${viewport}/${year}...`);

    // Show download progress
    const overlay = document.getElementById('progress-overlay');
    const title = document.getElementById('progress-title');
    const message = document.getElementById('progress-message');
    const bar = document.getElementById('progress-bar');
    const percent = document.getElementById('progress-percent');
    const status = document.getElementById('progress-status');
    overlay.style.display = 'flex';
    title.textContent = `Downloading Vector Data (${year})`;
    message.textContent = 'For local similarity search (one-time download)';
    status.textContent = 'Starting download...';
    bar.style.width = '0%';
    percent.textContent = '0%';

    try {
        // Reuse metadata from cache validation (or fetch if not available)
        const metadata = serverMetadata || await (async () => {
            const r = await fetch(`/api/vector-data/${viewport}/${year}/metadata.json`);
            if (!r.ok) throw new Error(`metadata.json: ${r.status}`);
            return r.json();
        })();
        status.textContent = 'Downloading pixel coordinates...';
        bar.style.width = '5%';
        percent.textContent = '5%';

        // Fetch coords (try .gz first, fall back to raw .npy)
        let coordsBuf;
        const coordsGzResp = await fetch(`/api/vector-data/${viewport}/${year}/pixel_coords.npy.gz`);
        if (coordsGzResp.ok) {
            coordsBuf = await decompressGzip(await coordsGzResp.blob());
        } else {
            const coordsResp = await fetch(`/api/vector-data/${viewport}/${year}/pixel_coords.npy`);
            if (!coordsResp.ok) throw new Error(`pixel_coords.npy: ${coordsResp.status}`);
            coordsBuf = await coordsResp.arrayBuffer();
        }
        const coordsParsed = parseNpy(coordsBuf);
        const coordsData = new Int32Array(coordsParsed.rawData);
        status.textContent = 'Downloading embeddings...';
        bar.style.width = '10%';
        percent.textContent = '10%';

        // Fetch embeddings with progress tracking (large file)
        // Try uint8 quantized first (~5x smaller), fall back to raw float32
        let embResp = null;
        let isGzipped = false;
        let quantParams = null;

        // Try uint8 quantized version (~28MB vs ~130MB)
        const quantResp = await fetch(`/api/vector-data/${viewport}/${year}/quantization.json`);
        if (quantResp.ok) {
            quantParams = await quantResp.json();
            embResp = await fetch(`/api/vector-data/${viewport}/${year}/all_embeddings_uint8.npy.gz`);
            if (embResp.ok) {
                isGzipped = true;
                console.log(`[VECTORS] Using uint8 quantized embeddings`);
            } else {
                quantParams = null; // fall through
            }
        }
        // Fall back to raw float32
        if (!embResp || !embResp.ok) {
            embResp = await fetch(`/api/vector-data/${viewport}/${year}/all_embeddings.npy`);
            if (!embResp.ok) throw new Error(`all_embeddings.npy: ${embResp.status}`);
        }

        const contentLength = parseInt(embResp.headers.get('Content-Length') || '0');
        const reader = embResp.body.getReader();
        const chunks = [];
        let received = 0;

        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            chunks.push(value);
            received += value.length;
            if (contentLength > 0) {
                const pct = Math.min(95, 10 + Math.round((received / contentLength) * 85));
                bar.style.width = pct + '%';
                percent.textContent = pct + '%';
                status.textContent = `Downloading embeddings... ${(received / 1048576).toFixed(1)} / ${(contentLength / 1048576).toFixed(1)} MB`;
            }
        }

        // Combine chunks into single buffer
        const embBuffer = new Uint8Array(received);
        let offset = 0;
        for (const chunk of chunks) {
            embBuffer.set(chunk, offset);
            offset += chunk.length;
        }

        // Decompress if gzipped
        let embArrayBuffer;
        if (isGzipped) {
            status.textContent = 'Decompressing embeddings...';
            embArrayBuffer = await decompressGzip(new Blob([embBuffer]));
        } else {
            embArrayBuffer = embBuffer.buffer;
        }
        const embParsed = parseNpy(embArrayBuffer);
        const embDim = 128;
        let embeddingsData;

        // Handle dtype: uint8 with dequantization
        if (embParsed.dtype === '|u1' || embParsed.dtype === '<u1') {
            const raw = new Uint8Array(embParsed.rawData);
            const N = embParsed.shape[0];
            embeddingsData = new Float32Array(N * embDim);
            if (quantParams) {
                // Pre-compute per-dimension scale/offset for row-major dequant
                const scales = new Float32Array(embDim);
                const mins = new Float32Array(embDim);
                for (let d = 0; d < embDim; d++) {
                    scales[d] = (quantParams.dim_max[d] - quantParams.dim_min[d]) / 255.0;
                    mins[d] = quantParams.dim_min[d];
                }
                status.textContent = 'Dequantizing embeddings...';
                if (embParsed.fortranOrder) {
                    for (let i = 0; i < N; i++) {
                        const outBase = i * embDim;
                        for (let d = 0; d < embDim; d++) {
                            embeddingsData[outBase + d] = raw[d * N + i] * scales[d] + mins[d];
                        }
                    }
                } else {
                    // Row-major: sequential reads and writes for cache efficiency
                    for (let i = 0; i < N; i++) {
                        const base = i * embDim;
                        for (let d = 0; d < embDim; d++) {
                            embeddingsData[base + d] = raw[base + d] * scales[d] + mins[d];
                        }
                    }
                }
            } else {
                // Legacy uint8 without quantization params — copy as-is
                if (embParsed.fortranOrder) {
                    for (let i = 0; i < N; i++) {
                        for (let d = 0; d < embDim; d++) {
                            embeddingsData[i * embDim + d] = raw[d * N + i];
                        }
                    }
                } else {
                    for (let k = 0; k < raw.length; k++) embeddingsData[k] = raw[k];
                }
            }
        } else if (embParsed.dtype === '<f4') {
            const raw = new Float32Array(embParsed.rawData);
            const N = embParsed.shape[0];
            if (embParsed.fortranOrder) {
                embeddingsData = new Float32Array(N * embDim);
                for (let i = 0; i < N; i++) {
                    for (let d = 0; d < embDim; d++) {
                        embeddingsData[i * embDim + d] = raw[d * N + i];
                    }
                }
            } else {
                embeddingsData = raw;
            }
        } else {
            throw new Error(`Unsupported embedding dtype: ${embParsed.dtype}`);
        }

        const numVectors = embeddingsData.length / embDim;

        // Validate embeddings aren't all zeros (indicates corrupt source data)
        let hasNonZero = false;
        for (let i = 0; i < Math.min(1000, embeddingsData.length); i++) {
            if (embeddingsData[i] !== 0) { hasNonZero = true; break; }
        }
        if (!hasNonZero) {
            console.error(`[VECTORS] Downloaded embeddings for ${viewport}/${year} are all zeros — data is corrupt on server`);
            status.textContent = `Error: embeddings for ${year} are corrupt (all zeros)`;
            throw new Error(`Embeddings for ${year} are all zeros`);
        }

        // Grid lookup: O(1) arithmetic, no Map needed
        const grid = buildGridLookup(coordsData, numVectors);

        bar.style.width = '100%';
        percent.textContent = '100%';
        status.textContent = `Ready! ${numVectors.toLocaleString()} vectors loaded.`;

        // Cache in IndexedDB off the critical path (don't block UI)
        VectorCache.put(viewport, year, {
            embeddings: embeddingsData,
            coords: coordsData,
            metadata
        }).catch(e => console.warn('[VECTORS] Cache write failed:', e));

        localVectors = {
            embeddings: embeddingsData,
            coords: coordsData,
            metadata,
            gridLookup: grid,
            numVectors,
            dim: 128,
            viewport,
            year: String(year)
        };

        console.log(`[VECTORS] Downloaded and cached: ${numVectors} vectors for ${viewport}/${year}`);
        return localVectors;

    } catch (error) {
        console.error('[VECTORS] Download failed:', error);
        status.textContent = `Download failed: ${error.message}`;
        throw error;
    } finally {
        setTimeout(() => { overlay.style.display = 'none'; }, 1500);
    }
}

// ── Client-Side Search Functions ──

function localExtract(lat, lon) {
    if (!localVectors) return null;
    const gt = localVectors.metadata.geotransform;
    const grid = localVectors.gridLookup;
    // Affine transform: c=originX, a=pixelWidth, f=originY, e=pixelHeight(negative)
    const px = Math.trunc((lon - gt.c) / gt.a);
    const py = Math.trunc((lat - gt.f) / gt.e);

    // Try exact match first
    let idx = gridLookupIndex(grid, px, py);
    // Try 8-neighborhood if not found
    if (idx < 0) {
        const offsets = [[-1,0],[1,0],[0,-1],[0,1],[-1,-1],[-1,1],[1,-1],[1,1]];
        for (const [dx, dy] of offsets) {
            idx = gridLookupIndex(grid, px + dx, py + dy);
            if (idx >= 0) break;
        }
    }
    if (idx < 0) return null;

    // Return 128-dim embedding slice
    return localVectors.embeddings.slice(idx * 128, (idx + 1) * 128);
}

// Pre-allocated distance buffer (reused across calls to avoid GC pressure)

function localSearchSimilar(embedding, threshold) {
    if (!localVectors) return [];
    const gt = localVectors.metadata.geotransform;
    const N = localVectors.numVectors;
    const dim = localVectors.dim;
    const emb = localVectors.embeddings;
    const coords = localVectors.coords;
    const threshSq = threshold * threshold;

    // Allocate or reuse distance buffer
    if (!_distSqBuf || _distSqBuf.length < N) {
        _distSqBuf = new Float32Array(N);
    }
    const distBuf = _distSqBuf;

    // Tight distance computation — no object alloc in hot loop
    // Process 4 dimensions at a time (loop unrolling for 128-dim)
    const dim4 = dim & ~3; // round down to multiple of 4
    for (let i = 0; i < N; i++) {
        let s = 0;
        const base = i * dim;
        let d = 0;
        for (; d < dim4; d += 4) {
            const d0 = emb[base + d]     - embedding[d];
            const d1 = emb[base + d + 1] - embedding[d + 1];
            const d2 = emb[base + d + 2] - embedding[d + 2];
            const d3 = emb[base + d + 3] - embedding[d + 3];
            s += d0*d0 + d1*d1 + d2*d2 + d3*d3;
        }
        for (; d < dim; d++) {
            const diff = emb[base + d] - embedding[d];
            s += diff * diff;
        }
        distBuf[i] = s;
    }

    // Collect matches — only allocate objects for hits
    const matches = [];
    for (let i = 0; i < N; i++) {
        if (distBuf[i] <= threshSq) {
            const px = coords[i * 2];
            const py = coords[i * 2 + 1];
            matches.push({
                lat: gt.f + py * gt.e,
                lon: gt.c + px * gt.a,
                distance: Math.sqrt(distBuf[i])
            });
        }
    }
    return matches;
}

// Multi-embedding union search: matches if pixel is within threshold of ANY embedding
function localSearchSimilarMulti(embeddings, threshold) {
    if (!localVectors || embeddings.length === 0) return [];
    const gt = localVectors.metadata.geotransform;
    const N = localVectors.numVectors;
    const dim = localVectors.dim;
    const emb = localVectors.embeddings;
    const coords = localVectors.coords;
    const threshSq = threshold * threshold;

    const dim4 = dim & ~3;
    const matches = [];
    for (let i = 0; i < N; i++) {
        const base = i * dim;
        let hit = false;
        for (let e = 0; e < embeddings.length; e++) {
            const qEmb = embeddings[e];
            let s = 0;
            let d = 0;
            for (; d < dim4; d += 4) {
                const d0 = emb[base + d]     - qEmb[d];
                const d1 = emb[base + d + 1] - qEmb[d + 1];
                const d2 = emb[base + d + 2] - qEmb[d + 2];
                const d3 = emb[base + d + 3] - qEmb[d + 3];
                s += d0*d0 + d1*d1 + d2*d2 + d3*d3;
            }
            for (; d < dim; d++) {
                const diff = emb[base + d] - qEmb[d];
                s += diff * diff;
            }
            if (s <= threshSq) { hit = true; break; }
        }
        if (hit) {
            const px = coords[i * 2];
            const py = coords[i * 2 + 1];
            matches.push({
                lat: gt.f + py * gt.e,
                lon: gt.c + px * gt.a,
                distance: 0
            });
        }
    }
    return matches;
}

// ── Cross-Year Vector Helpers ──

// Union search: single pass counts pixels matching ANY of the searches
function searchMultiInVectorData(data, searches) {
    const N = data.numVectors;
    const emb = data.embeddings;
    let count = 0;
    for (let i = 0; i < N; i++) {
        const base = i * 128;
        for (const s of searches) {
            let distSq = 0;
            for (let d = 0; d < 128; d++) {
                const diff = emb[base + d] - s.embedding[d];
                distSq += diff * diff;
            }
            if (distSq <= s.threshSq) { count++; break; }
        }
    }
    return count;
}

async function loadVectorDataOnly(viewport, year) {
    const cached = await VectorCache.get(viewport, year);
    if (cached) {
        let hasNonZero = false;
        for (let i = 0; i < Math.min(1000, cached.embeddings.length); i++) {
            if (cached.embeddings[i] !== 0) { hasNonZero = true; break; }
        }
        if (hasNonZero) {
            return {
                embeddings: cached.embeddings,
                coords: cached.coords,
                metadata: cached.metadata,
                numVectors: cached.embeddings.length / 128
            };
        }
    }
    // Not cached — download via downloadVectorData, then restore localVectors
    const saved = localVectors;
    try {
        await downloadVectorData(viewport, year);
        return {
            embeddings: localVectors.embeddings,
            coords: localVectors.coords,
            metadata: localVectors.metadata,
            numVectors: localVectors.numVectors
        };
    } finally {
        localVectors = saved;
    }
}

// Extract embedding at a lat/lon from a loaded vector data object
function extractFromData(data, lat, lon) {
    const gt = data.metadata.geotransform;
    const px = Math.trunc((lon - gt.c) / gt.a);
    const py = Math.trunc((lat - gt.f) / gt.e);
    // Build grid on demand if not cached on this data object
    if (!data.gridLookup) {
        data.gridLookup = buildGridLookup(data.coords, data.numVectors);
    }
    let idx = gridLookupIndex(data.gridLookup, px, py);
    if (idx < 0) {
        const offsets = [[-1,0],[1,0],[0,-1],[0,1],[-1,-1],[-1,1],[1,-1],[1,1]];
        for (const [dx, dy] of offsets) {
            idx = gridLookupIndex(data.gridLookup, px + dx, py + dy);
            if (idx >= 0) break;
        }
    }
    if (idx < 0) return null;
    return data.embeddings.slice(idx * 128, (idx + 1) * 128);
}

// ── Explorer Visualization ──

function clearExplorerResults() {
    if (explorerVisualization) {
        explorerVisualization.clearLayers();
        window.maps.rgb.removeLayer(explorerVisualization);
        explorerVisualization = null;
    }

    explorerCanvasLayer = null;
    explorerResults = null;

    // Hide stats overlay
    const statsEl = document.getElementById('explorer-stats-overlay');
    if (statsEl) statsEl.style.display = 'none';

    // Clear UMAP highlight marker and similarity highlighting
    if (window.umapCanvasLayer) {
        window.umapCanvasLayer.setHighlight(null);
        if (window.umapCanvasLayer.clearSimilarityHighlight) {
            window.umapCanvasLayer.clearSimilarityHighlight();
        }
    }

    // Clear search cache for persistent labels
    window.currentSearchCache = null;

    console.log('[EXPLORER] Results cleared');
}

// Show explorer loading overlay
function showExplorerLoading() {
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'explorer-loading';
    loadingDiv.style.cssText = `
        position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
        background: rgba(0,0,0,0.8); padding: 20px 30px; border-radius: 8px;
        color: #FFD700; font-weight: 600; z-index: 700; font-size: 16px;
    `;
    loadingDiv.textContent = 'Exploring similarities...';
    document.getElementById('map-embedding').appendChild(loadingDiv);
}

// Hide explorer loading overlay
function hideExplorerLoading() {
    const loadingDiv = document.getElementById('explorer-loading');
    if (loadingDiv) loadingDiv.remove();
}

// Explorer click handler - one-click similarity search
async function explorerClick(lat, lon) {
    console.log(`[EXPLORER] Click detected at ${lat.toFixed(6)}, ${lon.toFixed(6)}`);

    // Clear previous results
    clearExplorerResults();

    // Show loading
    showExplorerLoading();

    try {
        if (!localVectors) {
            if (!window.viewportStatus.has_vectors) {
                alert('Please wait for vectors to be extracted.');
                return;
            }
            console.log('[EXPLORER] Vectors not loaded, downloading now...');
            await downloadVectorData(window.currentViewportName, window.currentEmbeddingYear);
            window.viewportStatus.vectors_downloaded = true;
            window.evaluateDependencies();
        }
        if (!localVectors) {
            alert('Vector data not available for this viewport/year.');
            return;
        }

        // Step 1: Extract embedding locally
        const t0 = performance.now();
        const embedding = localExtract(lat, lon);
        if (!embedding) {
            console.error(`[EXPLORER] No embedding found at ${lat}, ${lon}`);
            alert('No embedding found at this location.');
            return;
        }

        // Step 2: Search similar locally with wide threshold for caching
        const cacheThreshold = 35.0;
        const matches = localSearchSimilar(embedding, cacheThreshold);
        const queryTime = performance.now() - t0;
        console.log(`[EXPLORER] Local search: ${matches.length} matches in ${queryTime.toFixed(1)}ms`);

        // Step 3: Cache results
        explorerResults = {
            sourcePixel: {lat, lon},
            sourceEmbedding: Array.from(embedding),
            allMatches: matches,
            queryTime: queryTime,
            cacheThreshold: cacheThreshold
        };

        // Also cache for persistent label system
        window.currentSearchCache = {
            sourcePixel: {lat, lon},
            embedding: Array.from(embedding),
            allMatches: matches,
            threshold: parseInt(document.getElementById('similarity-threshold').value),
            timestamp: Date.now()
        };

        // Step 4: Visualize with current threshold
        updateExplorerVisualization();

    } catch (error) {
        console.error('[EXPLORER] Error:', error);
        alert('Explorer search failed. Check console.');
    } finally {
        hideExplorerLoading();
    }
}

// Update explorer visualization based on current threshold
async function updateExplorerVisualization() {
    if (!explorerResults) {
        console.log('[EXPLORER] No explorer results cached');
        return;
    }

    const currentThreshold = parseFloat(document.getElementById('threshold-display').textContent);
    // If threshold exceeds cache, re-search locally with wider threshold
    if (currentThreshold > explorerResults.cacheThreshold && localVectors) {
        const embedding = explorerResults.sourceEmbedding instanceof Float32Array
            ? explorerResults.sourceEmbedding
            : new Float32Array(explorerResults.sourceEmbedding);
        const matches = localSearchSimilar(embedding, currentThreshold);
        explorerResults.allMatches = matches;
        explorerResults.cacheThreshold = currentThreshold;
    }

    // Filter cached results by current threshold
    const filteredMatches = explorerResults.allMatches.filter(m => m.distance <= currentThreshold);
    // Visualize filtered results
    visualizeExplorerResults(filteredMatches);

    // Update stats
    updateExplorerStats(filteredMatches, currentThreshold);
}

// Update explorer stats display
function updateExplorerStats(matches, threshold) {
    let statsEl = document.getElementById('explorer-stats-overlay');
    if (!statsEl) {
        statsEl = document.createElement('div');
        statsEl.id = 'explorer-stats-overlay';
        statsEl.style.cssText = `
            position: absolute; bottom: 50px; left: 50%; transform: translateX(-50%);
            background: rgba(0,0,0,0.85); color: #FFD700; padding: 8px 16px;
            border-radius: 6px; font-size: 14px; font-weight: 600;
            z-index: 600; pointer-events: none; white-space: nowrap;
            backdrop-filter: blur(5px);
        `;
        document.getElementById('map-embedding').appendChild(statsEl);
    }
    const total = localVectors ? localVectors.numVectors : 0;
    const pct = total > 0 ? (matches.length / total * 100).toFixed(1) : '0.0';
    statsEl.textContent = `${matches.length.toLocaleString()} pixels (${pct}%) at threshold ${threshold.toFixed(1)}`;
    statsEl.style.display = 'block';
}

// Visualize explorer results with adaptive rendering
function visualizeExplorerResults(matches) {
    if (matches.length === 0) {
        console.log('[EXPLORER] No matches to visualize');
        // Clear Panel 4 highlighting if no matches
        if (window.umapCanvasLayer && window.umapCanvasLayer.clearSimilarityHighlight) {
            window.umapCanvasLayer.clearSimilarityHighlight();
        }
        return;
    }

    // If canvas layer exists (slider change), just update matches
    if (explorerCanvasLayer) {
        explorerCanvasLayer.updateMatches(matches);
    } else {
        // New search: create canvas layer and layer group
        const layerGroup = L.layerGroup();

        // Create and add canvas layer to RGB (Bing satellite) panel
        explorerCanvasLayer = new DirectCanvasLayer(matches, window.maps.rgb);
        layerGroup.addLayer(explorerCanvasLayer);

        // Add to RGB panel with 50% opacity
        layerGroup.addTo(window.maps.rgb);
        explorerVisualization = layerGroup;

        // Add source pixel marker to RGB panel
        visualizeSourcePixel(explorerResults.sourcePixel.lat, explorerResults.sourcePixel.lon, layerGroup);
    }

    // Also highlight matches in Panel 4 (PCA/UMAP)
    if (window.umapCanvasLayer && window.umapCanvasLayer.highlightSimilarPoints) {
        window.umapCanvasLayer.highlightSimilarPoints(matches);
    }
}

// Custom canvas layer for direct pixel rendering
class DirectCanvasLayer extends L.Layer {
    constructor(matches, map, color) {
        super();
        this.matches = matches;
        this._map = map;
        this._color = color || null; // hex color string e.g. '#3cb44b', null = yellow
        this._canvas = null;
        this._ctx = null;
        // Compute max distance for opacity scaling
        this._maxDistance = 1;
        this._updateMaxDistance();
    }

    _updateMaxDistance() {
        let maxDist = 0;
        for (const m of this.matches) {
            if (m.distance > maxDist) maxDist = m.distance;
        }
        this._maxDistance = maxDist || 1;
    }

    onAdd(map) {
        this._map = map;

        // Create canvas element directly in map container
        this._canvas = document.createElement('canvas');
        this._canvas.className = 'explorer-direct-canvas';
        this._canvas.style.position = 'absolute';
        this._canvas.style.top = '0';
        this._canvas.style.left = '0';
        this._canvas.style.pointerEvents = 'none';
        this._canvas.style.zIndex = '999';

        const mapContainer = map.getContainer();
        mapContainer.appendChild(this._canvas);

        this._ctx = this._canvas.getContext('2d');
        this._updateCanvasSize();

        // Redraw on any map change
        this._map.on('move zoom resize', this._redraw, this);

        this._redraw();
    }

    onRemove(map) {
        this._map.off('move zoom resize', this._redraw, this);

        if (this._canvas) {
            this._canvas.remove();
        }
    }

    _updateCanvasSize() {
        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._canvas.style.width = size.x + 'px';
        this._canvas.style.height = size.y + 'px';
        this._ctx.imageSmoothingEnabled = false;
    }

    _redraw() {
        if (!this._ctx || !this._canvas) return;

        const ctx = this._ctx;
        const map = this._map;
        const size = map.getSize();

        // Clear canvas
        ctx.clearRect(0, 0, size.x, size.y);

        let visibleCount = 0;
        const maxDist = this._maxDistance;

        // Pre-compute fill color
        let cr = 255, cg = 255, cb = 0; // default: yellow
        if (this._color) {
            cr = parseInt(this._color.slice(1,3), 16);
            cg = parseInt(this._color.slice(3,5), 16);
            cb = parseInt(this._color.slice(5,7), 16);
        }
        ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.700)`;

        // Draw each pixel with overlap to eliminate banding from rounding errors
        const OVERLAP = 1.0;  // Pixels overlap by 1px to hide seams from coordinate rounding

        for (const match of this.matches) {
            const matchBounds = window.calculatePixelBounds(match.lat, match.lon);
            const sw = map.latLngToContainerPoint(matchBounds[0]);
            const ne = map.latLngToContainerPoint(matchBounds[1]);

            // Skip if completely off-screen
            if (ne.x < 0 || sw.x > size.x || sw.y < 0 || ne.y > size.y) {
                continue;
            }

            visibleCount++;

            // Calculate exact bounds without rounding, let canvas handle rendering
            const x = sw.x - OVERLAP;
            const y = ne.y - OVERLAP;
            const width = ne.x - sw.x + 2 * OVERLAP;
            const height = sw.y - ne.y + 2 * OVERLAP;

            // Only draw if size is reasonable (avoid zero/negative sizes)
            if (width > 0.1 && height > 0.1) {
                ctx.fillRect(x, y, width, height);
            }
        }

    }

    updateMatches(newMatches) {
        this.matches = newMatches;
        this._updateMaxDistance();
        this._redraw();
    }
}

// Visualize source pixel with distinct marker
function visualizeSourcePixel(lat, lon, layerGroup) {
    const sourceMarker = L.marker([lat, lon], { icon: window.TRIANGLE_ICON });

    // Add to layer group
    if (layerGroup) {
        layerGroup.addLayer(sourceMarker);
    }
}

// Calculate average embedding from array of embeddings
function calculateAverageEmbedding(embeddings) {
    if (embeddings.length === 0) return null;

    const dim = embeddings[0].length;
    const avgEmb = new Array(dim).fill(0);

    // Sum all embeddings
    for (let emb of embeddings) {
        for (let i = 0; i < dim; i++) {
            avgEmb[i] += emb[i];
        }
    }

    // Divide by count - keep as float32, don't clamp!
    // Embeddings are float32 values (e.g., -2.5, 5.8, 1.3), not uint8 (0-255)
    for (let i = 0; i < dim; i++) {
        avgEmb[i] = avgEmb[i] / embeddings.length;
    }
    return avgEmb;
}

// ── Expose on window for onclick handlers and inline script access ──

window.buildGridLookup = buildGridLookup;
window.gridLookupIndex = gridLookupIndex;
window.VectorCache = VectorCache;
window.decompressGzip = decompressGzip;
window.parseNpy = parseNpy;
window.downloadVectorData = downloadVectorData;
window.localExtract = localExtract;
window.localSearchSimilar = localSearchSimilar;
window.localSearchSimilarMulti = localSearchSimilarMulti;
window.searchMultiInVectorData = searchMultiInVectorData;
window.loadVectorDataOnly = loadVectorDataOnly;
window.extractFromData = extractFromData;
window.clearExplorerResults = clearExplorerResults;
window.explorerClick = explorerClick;
window.updateExplorerVisualization = updateExplorerVisualization;
window.DirectCanvasLayer = DirectCanvasLayer;
window.visualizeSourcePixel = visualizeSourcePixel;
window.calculateAverageEmbedding = calculateAverageEmbedding;
