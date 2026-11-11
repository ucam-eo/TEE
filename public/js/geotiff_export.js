// Map export: georeferenced GeoTIFF downloads for the Explore and
// Change Detection maps, so users can make proper maps in QGIS/ArcGIS
// instead of taking screen grabs.
//
// - Embedding maps (panels 3 and 6): the server renders the full-resolution
//   image as a GeoTIFF (/tiles/<viewport>/<year>/export.tif).
// - Change heatmap (panel 5): computed in the browser, so it is encoded here,
//   either as coloured RGBA (as seen on screen) or as raw float32 distances.
// - Labelling classification (panel 5): encoded in labels.js with this encoder,
//   as a class-number raster with a colour table.
//
// All TEE grids are EPSG:4326 with a north-up geotransform {a, c, e, f}.

// ── Minimal GeoTIFF encoder ──
// Classic little-endian TIFF, strip layout, pixel-interleaved,
// DEFLATE-compressed when the browser supports CompressionStream.

const TYPE = { ASCII: 2, SHORT: 3, LONG: 4, DOUBLE: 12 };
const TYPE_SIZE = { 2: 1, 3: 2, 4: 4, 12: 8 };

async function deflate(bytes) {
    // 'deflate' = zlib-wrapped, which is what TIFF compression 8 expects.
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
}

/**
 * Encode a raster as GeoTIFF bytes.
 * @param {object} o
 * @param {number} o.width, o.height
 * @param {Uint8Array|Float32Array} o.data  pixel-interleaved, row-major
 * @param {number} o.bands                  samples per pixel (1, 3 or 4)
 * @param {object} o.geotransform           {a, c, e, f} (EPSG:4326, north-up)
 * @param {string} [o.nodata]               GDAL nodata value, e.g. 'nan'
 * @param {number[][]} [o.colormap]         [[r, g, b], ...] (0-255) for values 0, 1, ...:
 *                                          makes a 1-band uint8 palette image
 * @param {object} [o.metadata]             key/value tags (GDAL_METADATA)
 */
async function encodeGeoTiff(o) {
    const { width, height, data, bands, geotransform: gt } = o;
    const isFloat = data instanceof Float32Array;
    const bytesPerSample = isFloat ? 4 : 1;
    const rowBytes = width * bands * bytesPerSample;
    const raw = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);

    // ~256 KB strips
    const rowsPerStrip = Math.max(1, Math.min(height, Math.floor(262144 / rowBytes)));
    const useDeflate = typeof CompressionStream !== 'undefined';
    const strips = [];
    for (let r = 0; r < height; r += rowsPerStrip) {
        const chunk = raw.subarray(r * rowBytes, Math.min(height, r + rowsPerStrip) * rowBytes);
        strips.push(useDeflate ? await deflate(chunk) : chunk);
    }

    const perBand = (v) => new Array(bands).fill(v);
    const tags = [
        [256, TYPE.LONG, [width]],
        [257, TYPE.LONG, [height]],
        [258, TYPE.SHORT, perBand(bytesPerSample * 8)],
        [259, TYPE.SHORT, [useDeflate ? 8 : 1]],
        [262, TYPE.SHORT, [o.colormap ? 3 : (bands >= 3 ? 2 : 1)]],  // Palette / RGB / MinIsBlack
        [273, TYPE.LONG, new Array(strips.length).fill(0)], // patched below
        [277, TYPE.SHORT, [bands]],
        [278, TYPE.LONG, [rowsPerStrip]],
        [279, TYPE.LONG, strips.map(s => s.length)],
        [284, TYPE.SHORT, [1]],
    ];
    if (bands === 4) tags.push([338, TYPE.SHORT, [2]]);     // unassociated alpha
    tags.push([339, TYPE.SHORT, perBand(isFloat ? 3 : 1)]);
    tags.push([33550, TYPE.DOUBLE, [gt.a, Math.abs(gt.e), 0]]);   // ModelPixelScale
    tags.push([33922, TYPE.DOUBLE, [0, 0, 0, gt.c, gt.f, 0]]);    // ModelTiepoint
    tags.push([34735, TYPE.SHORT, [                               // GeoKeyDirectory
        1, 1, 0, 3,
        1024, 0, 1, 2,      // GTModelType = Geographic
        1025, 0, 1, 1,      // GTRasterType = PixelIsArea
        2048, 0, 1, 4326,   // GeographicType = WGS 84
    ]]);
    if (o.metadata) {
        const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const items = Object.entries(o.metadata)
            .map(([k, v]) => `<Item name="${esc(k)}">${esc(v)}</Item>`).join('');
        tags.push([42112, TYPE.ASCII, `<GDALMetadata>${items}</GDALMetadata>`]);
    }
    if (o.nodata !== undefined) tags.push([42113, TYPE.ASCII, String(o.nodata)]);
    if (o.colormap) {
        // TIFF ColorMap: 256 reds, then 256 greens, then 256 blues, 16-bit each.
        const cmap = new Array(3 * 256).fill(0);
        o.colormap.forEach(([r, g, b], i) => {
            cmap[i] = r * 257; cmap[256 + i] = g * 257; cmap[512 + i] = b * 257;
        });
        tags.push([320, TYPE.SHORT, cmap]);
    }
    tags.sort((x, y) => x[0] - y[0]);  // TIFF requires ascending tag order

    // Layout: header | IFD | out-of-line tag values | strips
    const ifdSize = 2 + tags.length * 12 + 4;
    let cursor = 8 + ifdSize;
    const align = (n) => (n + 7) & ~7;
    const entries = tags.map(([tag, type, values]) => {
        const count = type === TYPE.ASCII ? values.length + 1 : values.length;
        const size = count * TYPE_SIZE[type];
        let offset = null;
        if (size > 4) { cursor = align(cursor); offset = cursor; cursor += size; }
        return { tag, type, values, count, size, offset };
    });
    cursor = align(cursor);
    const stripOffsets = [];
    for (const s of strips) { stripOffsets.push(cursor); cursor += s.length; }
    entries.find(e => e.tag === 273).values = stripOffsets;

    const buf = new ArrayBuffer(cursor);
    const dv = new DataView(buf);
    const u8 = new Uint8Array(buf);
    dv.setUint16(0, 0x4949, true);  // 'II'
    dv.setUint16(2, 42, true);
    dv.setUint32(4, 8, true);
    dv.setUint16(8, entries.length, true);

    const writeValues = (pos, type, values) => {
        if (type === TYPE.ASCII) {
            for (let i = 0; i < values.length; i++) u8[pos + i] = values.charCodeAt(i) & 0x7f;
            u8[pos + values.length] = 0;
            return;
        }
        values.forEach((v, i) => {
            if (type === TYPE.SHORT) dv.setUint16(pos + i * 2, v, true);
            else if (type === TYPE.LONG) dv.setUint32(pos + i * 4, v, true);
            else dv.setFloat64(pos + i * 8, v, true);
        });
    };
    entries.forEach((e, i) => {
        const p = 10 + i * 12;
        dv.setUint16(p, e.tag, true);
        dv.setUint16(p + 2, e.type, true);
        dv.setUint32(p + 4, e.count, true);
        if (e.offset === null) writeValues(p + 8, e.type, e.values);
        else { dv.setUint32(p + 8, e.offset, true); writeValues(e.offset, e.type, e.values); }
    });
    dv.setUint32(10 + entries.length * 12, 0, true);  // no next IFD
    // Float32Array bytes are platform-endian; every browser platform TEE
    // targets is little-endian, matching the 'II' header.
    strips.forEach((s, i) => u8.set(s, stripOffsets[i]));
    return buf;
}

function downloadBlob(bytes, filename, type = 'image/tiff') {
    const url = URL.createObjectURL(new Blob([bytes], { type }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ── Exports wired to the panel buttons ──

// Embedding map for the year shown in panel 3 ('embedding') or panel 6 ('embedding2').
function exportEmbeddingMap(which) {
    const sel = document.getElementById(which === 'embedding2' ? 'embedding-year-selector-2' : 'embedding-year-selector');
    const year = sel && sel.value;
    const vp = window.currentViewportName;
    if (!vp || !year) { alert('No embeddings loaded yet.'); return; }
    const a = document.createElement('a');
    a.href = `${window.TILE_SERVER}/tiles/${encodeURIComponent(vp)}/${encodeURIComponent(year)}/export.tif`;
    a.download = `${vp}_${year}_embeddings.tif`;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

// Change heatmap: kind = 'colour' (RGBA, as on screen) or 'values' (float32 distances).
async function exportChangeHeatmap(kind) {
    const layer = window.heatmapCanvasLayer;
    const grid = layer && layer.exportGrid;
    if (window.currentPanelMode !== 'change-detection' || !grid) {
        alert('No change heatmap yet — pick two different years in Change Detection mode first.');
        return;
    }
    const { width, height, values, geotransform, viewport, year1, year2 } = grid;
    const metadata = {
        viewport, year_from: year1, year_to: year2,
        description: 'Tessera change: L2 distance between the two years\' embeddings per pixel',
    };
    let data, bands, nodata;
    if (kind === 'values') {
        data = values; bands = 1; nodata = 'nan';
    } else {
        bands = 4;
        data = new Uint8Array(width * height * 4);
        for (let i = 0; i < values.length; i++) {
            const v = values[i];
            if (Number.isNaN(v)) continue;  // alpha 0 = no data
            const [r, g, b] = layer.colorRGB(v / layer.maxDistance);
            data[i * 4] = r; data[i * 4 + 1] = g; data[i * 4 + 2] = b; data[i * 4 + 3] = 255;
        }
        metadata.colour_scale = `viridis, 0 to ${layer.maxDistance.toFixed(4)} (1.5x median distance); higher = more change`;
    }
    const bytes = await encodeGeoTiff({ width, height, data, bands, geotransform, nodata, metadata });
    const suffix = kind === 'values' ? 'change_distance' : 'change_heatmap';
    downloadBlob(bytes, `${viewport}_${year1}-${year2}_${suffix}.tif`);
}

window.encodeGeoTiff = encodeGeoTiff;
window.downloadBlob = downloadBlob;
window.exportEmbeddingMap = exportEmbeddingMap;
window.exportChangeHeatmap = exportChangeHeatmap;
