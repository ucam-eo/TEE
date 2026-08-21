// vectors.js — Vector data management, client-side search, explorer visualization
// Extracted from viewer.html as an ES module.

import { decodeCodebook, indicesArrayFromParsed, reconstructFloatMosaic } from './vq_reconstruct.js';

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
            // v7: both paths now cache compact uint8 `values` + dim_min/dim_max
            // (legacy used to cache Float32). Consumers dequantize on demand. Bump to
            // drop stale Float32 legacy entries.
            const req = indexedDB.open(this.DB_NAME, 7);
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

// ── VQ format reader (Path A) ──
// Reads the codebooks+indices format produced by tessera-vq fast-path
// viewports, reconstructs a full float32 mosaic, re-quantises to uint8 with
// global per-dim min/max, and hands the result to the rest of vectors.js in
// the same shape the legacy uint8 path uses. The bandwidth win is on the
// wire (~5 MB instead of ~28 MB); browser memory + compute paths downstream
// stay identical. Phase 4 will skip the re-quantise round-trip with a
// codebook-distance LUT.

async function downloadVectorDataVq(viewport, year, vqMeta) {
    const base = `/api/vector-data/${viewport}/${year}`;
    const isRvq = vqMeta.kind === 'rvq';

    const overlay = document.getElementById('progress-overlay');
    const title = document.getElementById('progress-title');
    const message = document.getElementById('progress-message');
    const bar = document.getElementById('progress-bar');
    const percent = document.getElementById('progress-percent');
    const status = document.getElementById('progress-status');
    if (overlay) {
        overlay.style.display = 'flex';
        title.textContent = `Downloading Vector Data (${year})`;
        message.textContent = isRvq ? 'VQ fast path (RVQ): codebooks + indices'
                                    : 'VQ fast path: codebooks + indices';
        status.textContent = 'Starting download...';
        bar.style.width = '0%';
        percent.textContent = '0%';
    }
    const setProgress = (pct, msg) => {
        if (overlay) {
            bar.style.width = `${pct}%`;
            percent.textContent = `${pct}%`;
            if (msg) status.textContent = msg;
        }
    };

    const fetchNpy = async (url) => {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
        return parseNpy(await decompressGzip(await resp.blob()));
    };
    const fetchJson = async (url) => {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`${url}: ${resp.status}`);
        return resp.json();
    };

    try {
        setProgress(10, 'Downloading codebooks + indices...');

        // Concurrent fetch (small files; total ~5 MB).
        const tasks = [
            fetchNpy(`${base}/codebooks1_uint8.npy.gz`),
            fetchNpy(`${base}/codebooks1_scales.npy.gz`),
            fetchNpy(`${base}/indices1.npy.gz`),
            fetchJson(`${base}/tile_index.json`),
        ];
        if (isRvq) {
            tasks.push(
                fetchNpy(`${base}/codebooks2_uint8.npy.gz`),
                fetchNpy(`${base}/codebooks2_scales.npy.gz`),
                fetchNpy(`${base}/indices2.npy.gz`),
            );
        }
        const results = await Promise.all(tasks);
        const cb1Parsed = results[0], cb1ScalesParsed = results[1];
        const idx1Parsed = results[2], tileIndex = results[3];
        let cb2Parsed = null, cb2ScalesParsed = null, idx2Parsed = null;
        if (isRvq) {
            cb2Parsed = results[4]; cb2ScalesParsed = results[5]; idx2Parsed = results[6];
        }

        setProgress(50, 'Reconstructing embeddings...');

        const t = vqMeta.tile_size;
        const k1 = vqMeta.k1;
        const k2 = vqMeta.k2 || 0;
        const [outH, outW] = vqMeta.output_shape;
        const nTileRows = vqMeta.n_tile_rows;
        const nTileCols = vqMeta.n_tile_cols;
        const dim = vqMeta.embedding_dim || 128;
        const nTiles = tileIndex.n_tiles;
        const numPixels = outH * outW;

        const cb1Uint8 = new Uint8Array(cb1Parsed.rawData);
        const cb1Scales = new Float32Array(cb1ScalesParsed.rawData);
        const cb1Float = decodeCodebook(cb1Uint8, cb1Scales, nTiles, k1, dim);
        const idx1 = indicesArrayFromParsed(idx1Parsed);
        let cb2Float = null, idx2 = null;
        if (isRvq) {
            const cb2Uint8 = new Uint8Array(cb2Parsed.rawData);
            const cb2Scales = new Float32Array(cb2ScalesParsed.rawData);
            cb2Float = decodeCodebook(cb2Uint8, cb2Scales, nTiles, k2, dim);
            idx2 = indicesArrayFromParsed(idx2Parsed);
        }

        // Reconstruct full float mosaic via codebook lookup.
        const floatMosaic = reconstructFloatMosaic({
            idx1, cb1Float, idx2, cb2Float, outH, outW, nTileRows, nTileCols, t, k1, k2, dim
        });

        setProgress(80, 'Quantising to uint8...');

        // Keep values compact as uint8 + per-dim min/max (dim_min/dim_max in metadata),
        // so explore-mode panning stays light (a Float32 mosaic of a large viewport is
        // ~570 MB and makes panning laggy). Consumers that need real floats dequantise
        // on demand: the k-means worker rebuilds a transient Float32 buffer from these
        // (see segmentation.js::runKMeans). NOTE: feeding the worker the raw uint8 buffer
        // makes it read 1/4-size garbage and collapse to one cluster — it must dequantise.
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

        // Full-grid pixel coords (matches the legacy save).
        const coords = new Int32Array(numPixels * 2);
        for (let py = 0; py < outH; py++) {
            for (let px = 0; px < outW; px++) {
                const i = py * outW + px;
                coords[i * 2] = px;
                coords[i * 2 + 1] = py;
            }
        }

        const metadata = {
            viewport_id: viewport,
            mosaic_height: outH,
            mosaic_width: outW,
            clipped_height: outH,
            clipped_width: outW,
            num_total_pixels: numPixels,
            embedding_dim: dim,
            pixel_size_meters: 10,
            crs: 'EPSG:4326',
            geotransform: vqMeta.geotransform,
            kind: vqMeta.kind,
            dataset_version: vqMeta.dataset_version,
            dim_min: Array.from(dimMin),
            dim_max: Array.from(dimMax),
            vq: {
                tile_size: t,
                k1, k2,
                n_tile_rows: vqMeta.n_tile_rows,
                n_tile_cols: nTileCols,
            },
        };

        const grid = buildGridLookup(coords, numPixels);
        localVectors = {
            values, coords, metadata,
            gridLookup: grid, numVectors: numPixels, dim,
            viewport, year: String(year),
        };

        setProgress(100, 'Done');
        if (overlay) overlay.style.display = 'none';

        // Cache the re-quantised mosaic (same shape as legacy path).
        try {
            await VectorCache.put(viewport, year, { values, coords, metadata });
        } catch (e) {
            console.warn('[VECTORS] IndexedDB cache write failed:', e);
        }

        const wireMb = (
            cb1Parsed.rawData.byteLength + cb1ScalesParsed.rawData.byteLength +
            idx1Parsed.rawData.byteLength +
            (isRvq ? (cb2Parsed.rawData.byteLength + cb2ScalesParsed.rawData.byteLength + idx2Parsed.rawData.byteLength) : 0)
        ) / (1024 * 1024);
        console.log(`[VECTORS] VQ load complete: ${numPixels} px, wire ~${wireMb.toFixed(1)} MB (was ~28 MB)`);

        return localVectors;
    } catch (err) {
        console.error('[VECTORS] VQ download failed:', err);
        if (overlay && status) status.textContent = `Download failed: ${err.message}`;
        throw err;
    }
}

async function downloadVectorData(viewport, year) {
    // Skip if already loaded in memory for this viewport/year
    if (localVectors && localVectors.viewport === viewport && localVectors.year === String(year)) {
        return localVectors;
    }

    // Path A: probe for the VQ format. If the viewport was processed via the
    // tessera-vq fast path, the server emits vq_metadata.json + codebooks +
    // indices (~5 MB) instead of the 28 MB uint8 mosaic. We load that, expand
    // back to a uint8 mosaic in memory, and feed it into the existing
    // localVectors pipeline so search / extract / PCA all work unchanged.
    try {
        const vqResp = await fetch(`/api/vector-data/${viewport}/${year}/vq_metadata.json`);
        if (vqResp.ok) {
            const vqMeta = await vqResp.json();
            console.log(`[VECTORS] Using VQ format (${vqMeta.kind}) for ${viewport}/${year}`);
            return await downloadVectorDataVq(viewport, year, vqMeta);
        }
    } catch (e) {
        console.warn('[VECTORS] VQ probe failed, falling back to uint8 path:', e);
    }

    // Fetch metadata first (small, needed for cache validation)
    const metaResp0 = await fetch(`/api/vector-data/${viewport}/${year}/metadata.json`);
    const serverMetadata = metaResp0.ok ? await metaResp0.json() : null;

    // Check IndexedDB cache
    const cached = await VectorCache.get(viewport, year);
    // Migrate old cache format: .embeddings → .values
    if (cached && !cached.values && cached.embeddings) {
        cached.values = cached.embeddings;
        delete cached.embeddings;
    }
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
            for (let i = 0; i < Math.min(1000, cached.values.length); i++) {
                if (cached.values[i] !== 0) { hasNonZero = true; break; }
            }
            if (!hasNonZero) {
                console.warn(`[VECTORS] Cached data for ${viewport}/${year} is all zeros — purging`);
                cacheValid = false;
            }
        }

        if (cacheValid) {
            console.log(`[VECTORS] Cache hit for ${viewport}/${year}`);
            const numVectors = cached.values.length / 128;
            const grid = buildGridLookup(cached.coords, numVectors);
            localVectors = {
                values: cached.values,
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

        // Handle dtype. Keep uint8 compact (consumers dequantize on demand via
        // metadata.dim_min/dim_max — keeps explore-mode panning light); only the
        // raw-float fallback stays Float32.
        if (embParsed.dtype === '|u1' || embParsed.dtype === '<u1') {
            const raw = new Uint8Array(embParsed.rawData);
            const N = embParsed.shape[0];
            if (embParsed.fortranOrder) {
                // Transpose to row-major uint8
                embeddingsData = new Uint8Array(N * embDim);
                for (let i = 0; i < N; i++) {
                    for (let d = 0; d < embDim; d++) {
                        embeddingsData[i * embDim + d] = raw[d * N + i];
                    }
                }
            } else {
                embeddingsData = raw;
            }
            if (quantParams) {
                // Carry per-dim scale on metadata; getDequant() reads these.
                metadata.dim_min = quantParams.dim_min;
                metadata.dim_max = quantParams.dim_max;
            }
            // (no quantParams → uint8 treated as identity by getDequant, as before)
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
            values: embeddingsData,
            coords: coordsData,
            metadata
        }).catch(e => console.warn('[VECTORS] Cache write failed:', e));

        localVectors = {
            values: embeddingsData,
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

// ── Dequantization (single source of truth) ──
// localVectors.values is stored compact as uint8 + per-dim metadata.dim_min/dim_max
// (keeps explore-mode panning light). Real value = u8 * (max[d]-min[d])/255 + min[d].
// The rare raw-float fallback stores Float32 values and is treated as identity.
// Consumers dequantize inline / on demand; never a resident Float32 mosaic.
function getDequant(lv) {
    if (lv._dequant) return lv._dequant;
    const dim = lv.dim || 128;
    const scale = new Float32Array(dim);
    const min = new Float32Array(dim);
    const md = lv.metadata || {};
    const isUint8 = lv.values && lv.values.BYTES_PER_ELEMENT === 1;
    if (isUint8 && md.dim_min && md.dim_max) {
        for (let d = 0; d < dim; d++) {
            scale[d] = (md.dim_max[d] - md.dim_min[d]) / 255;
            min[d] = md.dim_min[d];
        }
    } else {
        scale.fill(1); // Float32 values (or uint8 without params): identity passthrough
    }
    lv._dequant = { scale, min };
    return lv._dequant;
}

// Dequantized embedding for one pixel index → a fresh Float32Array(dim).
function dequantSlice(lv, idx) {
    const dim = lv.dim || 128;
    const { scale, min } = getDequant(lv);
    const v = lv.values;
    const base = idx * dim;
    const out = new Float32Array(dim);
    for (let d = 0; d < dim; d++) out[d] = v[base + d] * scale[d] + min[d];
    return out;
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

    // Return dequantized 128-dim embedding (real-space float).
    return dequantSlice(localVectors, idx);
}

// Pre-allocated distance buffer (reused across calls to avoid GC pressure)

function localSearchSimilar(embedding, threshold) {
    if (!localVectors) return [];
    const gt = localVectors.metadata.geotransform;
    const N = localVectors.numVectors;
    const dim = localVectors.dim;
    const emb = localVectors.values;
    const coords = localVectors.coords;
    const threshSq = threshold * threshold;

    // Dequantize inline: real = emb*scale[d] + min[d]. Fold min into the query so the
    // inner loop is one multiply + subtract (q = query - min); identity for float.
    const { scale, min } = getDequant(localVectors);
    const q = new Float32Array(dim);
    for (let d = 0; d < dim; d++) q[d] = embedding[d] - min[d];

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
            const d0 = emb[base + d]     * scale[d]     - q[d];
            const d1 = emb[base + d + 1] * scale[d + 1] - q[d + 1];
            const d2 = emb[base + d + 2] * scale[d + 2] - q[d + 2];
            const d3 = emb[base + d + 3] * scale[d + 3] - q[d + 3];
            s += d0*d0 + d1*d1 + d2*d2 + d3*d3;
        }
        for (; d < dim; d++) {
            const diff = emb[base + d] * scale[d] - q[d];
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
    const emb = localVectors.values;
    const coords = localVectors.coords;
    const threshSq = threshold * threshold;

    // Dequantize inline (real = emb*scale[d] + min[d]); fold min into each query.
    const { scale, min } = getDequant(localVectors);
    const qs = embeddings.map(qEmb => {
        const q = new Float32Array(dim);
        for (let d = 0; d < dim; d++) q[d] = qEmb[d] - min[d];
        return q;
    });

    const dim4 = dim & ~3;
    const matches = [];
    for (let i = 0; i < N; i++) {
        const base = i * dim;
        let hit = false;
        for (let e = 0; e < qs.length; e++) {
            const q = qs[e];
            let s = 0;
            let d = 0;
            for (; d < dim4; d += 4) {
                const d0 = emb[base + d]     * scale[d]     - q[d];
                const d1 = emb[base + d + 1] * scale[d + 1] - q[d + 1];
                const d2 = emb[base + d + 2] * scale[d + 2] - q[d + 2];
                const d3 = emb[base + d + 3] * scale[d + 3] - q[d + 3];
                s += d0*d0 + d1*d1 + d2*d2 + d3*d3;
            }
            for (; d < dim; d++) {
                const diff = emb[base + d] * scale[d] - q[d];
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

// Per-pixel minimum squared distance to a set of query embeddings, with no
// threshold applied -- the distance-only half of localSearchSimilarMulti,
// split out so a caller can cache the (expensive) distance computation
// once and re-threshold it (cheap) as many times as it likes. Built for
// the per-class similarity slider: dragging it only changes the cutoff,
// never the query embeddings, so the full O(N * dim * numEmbeddings) walk
// over every pixel only needs to happen when the label set actually
// changes, not on every drag (Louis Driver, 2026-08-21 -- "there would be
// less delay/lag when adjusting the threshold sliders").
//
// Unlike localSearchSimilarMulti's early-exit `break` on the first
// matching embedding, this must check every query embedding for every
// pixel (there's no shortcut to finding a minimum), so it's not simply
// "the same cost minus the threshold check" -- expect it to run a bit
// slower than a single already-matching search, in exchange for never
// needing to repeat that cost while only the threshold changes.
function localComputeMinDistances(embeddings) {
    if (!localVectors || embeddings.length === 0) return new Float32Array(0);
    const N = localVectors.numVectors;
    const dim = localVectors.dim;
    const emb = localVectors.values;
    const { scale, min } = getDequant(localVectors);
    const qs = embeddings.map(qEmb => {
        const q = new Float32Array(dim);
        for (let d = 0; d < dim; d++) q[d] = qEmb[d] - min[d];
        return q;
    });

    const dim4 = dim & ~3;
    const distances = new Float32Array(N);
    for (let i = 0; i < N; i++) {
        const base = i * dim;
        let minDist = Infinity;
        for (let e = 0; e < qs.length; e++) {
            const q = qs[e];
            let s = 0;
            let d = 0;
            for (; d < dim4; d += 4) {
                const d0 = emb[base + d]     * scale[d]     - q[d];
                const d1 = emb[base + d + 1] * scale[d + 1] - q[d + 1];
                const d2 = emb[base + d + 2] * scale[d + 2] - q[d + 2];
                const d3 = emb[base + d + 3] * scale[d + 3] - q[d + 3];
                s += d0*d0 + d1*d1 + d2*d2 + d3*d3;
            }
            for (; d < dim; d++) {
                const diff = emb[base + d] * scale[d] - q[d];
                s += diff * diff;
            }
            if (s < minDist) minDist = s;
        }
        distances[i] = minDist;
    }
    return distances;
}

// Re-threshold a distance array from localComputeMinDistances into the
// same {lat, lon} match shape localSearchSimilarMulti returns -- a single
// cheap comparison pass, no embedding math, safe to call on every slider
// tick once distances are cached.
function localMatchesFromDistances(distances, threshold) {
    if (!localVectors) return [];
    const gt = localVectors.metadata.geotransform;
    const coords = localVectors.coords;
    const threshSq = threshold * threshold;
    const matches = [];
    for (let i = 0; i < distances.length; i++) {
        if (distances[i] <= threshSq) {
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
    const emb = data.values;
    // Dequantize inline (real = emb*scale[d] + min[d]); fold min into each search query.
    const { scale, min } = getDequant(data);
    const qps = searches.map(s => {
        const q = new Float32Array(128);
        for (let d = 0; d < 128; d++) q[d] = s.embedding[d] - min[d];
        return { q, threshSq: s.threshSq };
    });
    let count = 0;
    for (let i = 0; i < N; i++) {
        const base = i * 128;
        for (const s of qps) {
            let distSq = 0;
            for (let d = 0; d < 128; d++) {
                const diff = emb[base + d] * scale[d] - s.q[d];
                distSq += diff * diff;
            }
            if (distSq <= s.threshSq) { count++; break; }
        }
    }
    return count;
}

async function loadVectorDataOnly(viewport, year) {
    const cached = await VectorCache.get(viewport, year);
    if (cached && !cached.values && cached.embeddings) {
        cached.values = cached.embeddings;
        delete cached.embeddings;
    }
    if (cached) {
        let hasNonZero = false;
        for (let i = 0; i < Math.min(1000, cached.values.length); i++) {
            if (cached.values[i] !== 0) { hasNonZero = true; break; }
        }
        if (hasNonZero) {
            return {
                values: cached.values,
                coords: cached.coords,
                metadata: cached.metadata,
                numVectors: cached.values.length / 128
            };
        }
    }
    // Not cached — download via downloadVectorData, then restore localVectors
    const saved = localVectors;
    try {
        await downloadVectorData(viewport, year);
        return {
            values: localVectors.values,
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
    return dequantSlice(data, idx);
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

        for (const match of this.matches) {
            const matchBounds = window.calculatePixelBounds(match.lat, match.lon);
            const sw = map.latLngToContainerPoint(matchBounds[0]);
            const ne = map.latLngToContainerPoint(matchBounds[1]);

            // Skip if completely off-screen
            if (ne.x < 0 || sw.x > size.x || sw.y < 0 || ne.y > size.y) {
                continue;
            }

            visibleCount++;

            // Snap to whole device pixels, rounding each edge outward (floor the
            // low edge, ceil the high edge). A fixed-px overlap used to sit here
            // instead, but a constant screen-px pad can't track how many screen
            // pixels a source pixel actually covers -- fine (over-generous, even
            // bleeding into a neighbouring class's fill) at low zoom where a
            // pixel is only a few screen px wide, but far too thin at high zoom
            // where one pixel can span 15-20+ screen px: the leftover sub-pixel
            // rounding drift between independently-projected neighbours then
            // shows through as a periodic hatch of gaps. Flooring/ceiling instead
            // of padding by a constant means two adjacent same-cluster pixels'
            // rects always share their common boundary regardless of zoom, with
            // no dependence on an arbitrary constant and no bleed into neighbours.
            const x = Math.floor(sw.x);
            const y = Math.floor(ne.y);
            const width = Math.ceil(ne.x) - x;
            const height = Math.ceil(sw.y) - y;

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
window.getDequant = getDequant;
window.dequantSlice = dequantSlice;
window.downloadVectorData = downloadVectorData;
window.localExtract = localExtract;
window.localSearchSimilar = localSearchSimilar;
window.localSearchSimilarMulti = localSearchSimilarMulti;
window.searchMultiInVectorData = searchMultiInVectorData;
window.localComputeMinDistances = localComputeMinDistances;
window.localMatchesFromDistances = localMatchesFromDistances;
window.loadVectorDataOnly = loadVectorDataOnly;
window.extractFromData = extractFromData;
window.clearExplorerResults = clearExplorerResults;
window.explorerClick = explorerClick;
window.updateExplorerVisualization = updateExplorerVisualization;
window.DirectCanvasLayer = DirectCanvasLayer;
window.visualizeSourcePixel = visualizeSourcePixel;
window.calculateAverageEmbedding = calculateAverageEmbedding;
