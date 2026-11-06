#!/usr/bin/env node
/**
 * Manual-label export -> import round trip.
 *
 * A label set exported as ESRI Shapefile (ZIP) and imported again used to
 * come back incomplete, without any error:
 *
 *  - shp-write writes one layer per geometry type (POINT.shp, POLYGON.shp)
 *    and importShapefile() read only the first .shp, so one pin among the
 *    labels dropped every polygon;
 *  - importGeoJSON() skipped MultiPolygon features and filled the holes of
 *    polygons. The shapefile reader returns the rings of a polygon written
 *    with the wrong winding as a MultiPolygon, which is what promoted
 *    auto-label clusters with holes turned into;
 *  - every imported label saved the whole label store and re-rendered the
 *    list (addManualLabel), so import time grew quadratically;
 *  - the JSON (full) export left out the pixels of promoted auto-label
 *    clusters (pixel_coords), so they came back as a threshold search;
 *  - polygon vertices were rounded to whole pixels before rasterising, so
 *    the exported polygons of a pixel set (vertices on the midpoints of
 *    pixel edges) came back with pixels lost and gained along the outline.
 *
 * The geometry is checked on a 12 x 10 pixel grid with shapes whose pixel
 * counts can be verified by hand: a 6 x 6 square (36 px) with a 2 x 2 hole
 * (4 px) covers 32 px.
 *
 * Run:  node validation/test_label_import_roundtrip.mjs
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'labels.js'), 'utf8');

function extract(name) {
    let i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found in labels.js: ${name}`);
    if (src.slice(i - 6, i) === 'async ') i -= 6;
    let depth = 0, started = false, j = i;
    while (true) {
        const c = src[j];
        if (c === '{') { depth++; started = true; }
        else if (c === '}') { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
        j++;
    }
}

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const eq = (a, b, m) => ok(JSON.stringify(a) === JSON.stringify(b), `${m} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`);

// --- synthetic viewport: 12 x 10 pixels, north-up, 1 degree per pixel ----
// pixel (px, py) spans lon [px, px+1], lat [10-py-1, 10-py]
const W = 12, H = 10, dim = 2;
const gt = { a: 1, c: 0, e: -1, f: 10 };
globalThis.window = globalThis;
window.localVectors = {
    dim, numVectors: W * H, values: new Float32Array(W * H * dim),
    gridLookup: { minX: 0, minY: 0, w: W, h: H },
    metadata: { geotransform: gt },
};
window.gridLookupIndex = (grid, px, py) => (px < 0 || py < 0 || px >= grid.w || py >= grid.h) ? -1 : py * grid.w + px;
window.getDequant = () => ({ scale: new Array(dim).fill(1), min: new Array(dim).fill(0) });
window.localExtract = () => new Float32Array(dim);
window.SEG_PALETTE = ['#111111', '#222222', '#333333'];

// A fresh copy of the import/export functions over its own label store,
// with the storage / DOM side effects replaced by counters.
function makeApp(globals = {}) {
    const calls = { save: 0, render: 0, rebuild: 0, dirty: 0, alerts: [] };
    const fns = [
        'pushManualLabel', 'addManualLabel', 'pointInPolygon', 'polygonHolesToPixels',
        'rasterizePolygon', 'expandLabelToFeatures', 'orientRingsForShapefile',
        '_geoJsonLabelName', '_polygonParts', 'importGeoJSON', 'importLabelsJSON', 'importShapefile',
    ].map(extract).join('\n\n');
    const api = new Function('calls', 'JSZip', 'shapefile', 'alert', 'console', `
        let manualLabels = [];
        let manualLabelIdCounter = 0;
        function saveManualLabelsToStorage() { calls.save++; }
        function renderManualLabelsList() { calls.render++; }
        function rebuildManualOverlays() { calls.rebuild++; }
        function markExportDirty() { calls.dirty++; }
        ${fns}
        return { importGeoJSON, importLabelsJSON, importShapefile, rasterizePolygon, pointInPolygon,
                 polygonHolesToPixels, expandLabelToFeatures, orientRingsForShapefile,
                 addManualLabel, labels: () => manualLabels };
    `)(calls, globals.JSZip, globals.shapefile, (m) => calls.alerts.push(m), { log() {}, warn() {}, error() {} });
    return { api, calls };
}

// rings in [lon, lat], closed; cw=true walks the square clockwise (north-up)
function square(x0, y0, x1, y1, cw = false) {
    const ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]];   // counter-clockwise
    return cw ? ring.reverse() : ring;
}
const feature = (type, coordinates, properties = { name: 'field' }) =>
    ({ type: 'Feature', properties, geometry: { type, coordinates } });
const collection = (...features) => ({ type: 'FeatureCollection', features });
const signedArea = (ring) => {
    let a = 0;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    return a / 2;   // > 0: counter-clockwise
};
// pixels a polygon label covers, with the rule the viewer uses (rebuildClassOverlay)
function labelPixels(api, label) {
    const g = window.localVectors.metadata.geotransform;
    const pixVerts = label.vertices.map(v => [(v[1] - g.c) / g.a, (v[0] - g.f) / g.e]);
    return api.rasterizePolygon(pixVerts, api.polygonHolesToPixels(label, g)).map(m => `${m.px},${m.py}`).sort();
}

const OUTER = square(1, 2, 7, 8);        // 6 x 6 = 36 px
const HOLE = square(3, 4, 5, 6);         // 2 x 2 =  4 px, inside OUTER
const OTHER = square(8, 2, 11, 5);       // 3 x 3 =  9 px, beside OUTER

// --- 1. rasterizePolygon: holes, whatever their winding ------------------
{
    const { api } = makeApp();
    const px = (ring) => ring.slice(0, -1).map(([lon, lat]) => [lon, 10 - lat]);
    eq(api.rasterizePolygon(px(OUTER)).length, 36, 'polygon without holes: 36 px (unchanged)');
    eq(api.rasterizePolygon(px(OUTER), [px(HOLE)]).length, 32, 'hole is left out: 36 - 4 = 32 px');
    eq(api.rasterizePolygon(px(OUTER), [px(square(3, 4, 5, 6, true))]).length, 32, 'same with the hole wound the other way');
    const inHole = api.rasterizePolygon(px(OUTER), [px(HOLE)]).filter(m => m.px >= 3 && m.px < 5 && m.py >= 4 && m.py < 6);
    eq(inHole.length, 0, 'no pixel inside the hole');

    // rasterizePolygon works row by row; it must pick exactly the pixels whose
    // centre pointInPolygon puts inside an odd number of rings. Random rings
    // on pixel corners (callers round the vertices), self-intersecting, partly
    // off the grid.
    let seed = 42;
    const rnd = () => (seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296;
    const ring = (n) => Array.from({ length: n }, () => [Math.round(rnd() * 16 - 2), Math.round(rnd() * 14 - 2)]);
    let differ = 0, pixels = 0;
    for (let t = 0; t < 500; t++) {
        const outer = ring(3 + Math.floor(rnd() * 7)), holes = Array.from({ length: t % 4 }, () => ring(3 + Math.floor(rnd() * 4)));
        const want = [];
        for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
            const inside = [outer, ...holes].filter(r => api.pointInPolygon(x + 0.5, y + 0.5, r)).length % 2 === 1;
            const inBox = x >= Math.min(...outer.map(v => v[0])) && x <= Math.max(...outer.map(v => v[0]))
                && y >= Math.min(...outer.map(v => v[1])) && y <= Math.max(...outer.map(v => v[1]));
            if (inside && inBox) want.push(`${x},${y}`);
        }
        const got = api.rasterizePolygon(outer, holes).map(m => `${m.px},${m.py}`);
        pixels += want.length;
        if (JSON.stringify(got) !== JSON.stringify(want)) differ++;
    }
    eq(differ, 0, '500 random polygons: same pixels as a per-pixel pointInPolygon test');
    ok(pixels > 5000, 'and they are not trivially empty');
}

// --- 2. importGeoJSON: Polygon with a hole -------------------------------
{
    const { api } = makeApp();
    api.importGeoJSON(collection(feature('Polygon', [OUTER, HOLE]), feature('Polygon', [OTHER])));
    const [a, b] = api.labels();
    eq(api.labels().length, 2, 'two polygons -> two labels');
    eq(a.holes.length, 1, 'the hole is kept on the label');
    eq(a.pixelCount, 32, 'polygon with hole covers 32 px');
    eq(labelPixels(api, a).length, 32, 'and is rasterised the same way afterwards');
    ok(!('holes' in b), 'a polygon without holes gets no holes field (label format unchanged)');
    eq(b.pixelCount, 9, 'polygon without holes covers 9 px');
}

// --- 3. importGeoJSON: MultiPolygon --------------------------------------
{
    const { api } = makeApp();
    api.importGeoJSON(collection(feature('MultiPolygon', [[OUTER, HOLE], [OTHER]], { name: 'multi', color: '#abcdef' })));
    const L = api.labels();
    eq(L.length, 2, 'MultiPolygon of two polygons -> two labels (it used to be skipped)');
    eq(L.map(l => l.pixelCount), [32, 9], 'pixel counts 32 (with hole) and 9');
    eq(L.map(l => l.name + l.color), ['multi#abcdef', 'multi#abcdef'], 'both carry the feature properties');

    // What the shapefile reader returns for a polygon whose rings were written
    // with the wrong winding: every ring as its own polygon, holes first.
    const garbled = makeApp().api;
    garbled.importGeoJSON(collection(feature('MultiPolygon', [[HOLE], [OUTER]])));
    eq(garbled.labels().length, 1, 'rings split into separate polygons are regrouped into one label');
    eq(garbled.labels()[0].pixelCount, 32, 'with the hole left out: 32 px');
    eq(garbled.labels()[0].holes.length, 1, 'and stored as a hole');

    // An island inside a hole is a polygon of its own (nesting depth 2).
    const island = makeApp().api;
    island.importGeoJSON(collection(feature('MultiPolygon', [[square(0, 0, 10, 10)], [square(2, 2, 8, 8, true)], [square(4, 4, 6, 6)]])));
    eq(island.labels().map(l => l.pixelCount), [64, 4], 'ring + island: 100 - 36 = 64 px and 2 x 2 = 4 px');
}

// --- 4. bulk import saves and renders once -------------------------------
{
    const { api, calls } = makeApp();
    const many = [];
    for (let i = 0; i < 300; i++) many.push(feature(i % 3 ? 'Polygon' : 'Point', i % 3 ? [OTHER] : [i % 12, 5], { name: `c${i % 4}` }));
    api.importGeoJSON(collection(...many));
    eq(api.labels().length, 300, '300 features -> 300 labels');
    eq([calls.save, calls.render, calls.rebuild], [1, 1, 1], 'importGeoJSON saves, renders and rebuilds overlays once');
    eq(calls.dirty, 1, 'and marks the export button dirty');
    eq(new Set(api.labels().map(l => l.id)).size, 300, 'every label has its own id');
    ok(api.labels().every(l => l.visible === true), 'every label is visible');

    api.addManualLabel({ name: 'pin', type: 'point', lat: 5, lon: 5 });
    eq([calls.save, calls.render], [2, 2], 'addManualLabel (single label) still saves and renders');
    eq(api.labels()[300].id, 301, 'and continues the id sequence');

    const json = makeApp();
    json.api.importLabelsJSON(api.labels().map(l => ({ ...l })));
    eq(json.api.labels().length, 301, 'importLabelsJSON: 301 labels');
    eq([json.calls.save, json.calls.render, json.calls.rebuild], [1, 1, 1], 'importLabelsJSON saves, renders and rebuilds overlays once');
}

// --- 5. export keeps the holes: GeoJSON round trip -----------------------
{
    const { api } = makeApp();
    api.importGeoJSON(collection(feature('Polygon', [OUTER, HOLE]), feature('Polygon', [OTHER]), feature('Point', [9.5, 8.5], { name: 'pin' })));
    // points are imported before polygons
    const exported = api.labels().flatMap(l => api.expandLabelToFeatures(l));
    eq(exported.map(f => f.geometry.type + f.geometry.coordinates.length), ['Point2', 'Polygon2', 'Polygon1'], 'export writes the hole as a second ring');

    const again = makeApp().api;
    again.importGeoJSON(collection(...exported));
    eq(again.labels().length, 3, 'GeoJSON export -> import: same number of labels');
    for (const i of [1, 2]) {
        eq(labelPixels(again, again.labels()[i]), labelPixels(api, api.labels()[i]), `polygon ${i} covers the same pixels after the round trip`);
    }
    eq(labelPixels(again, again.labels()[1]).length, 32, 'the polygon with the hole still covers 32 px');

    // JSON (full) export keeps vertices and holes as they are
    const json = makeApp().api;
    json.importLabelsJSON(JSON.parse(JSON.stringify(api.labels())));
    eq(labelPixels(json, json.labels()[1]).length, 32, 'JSON import keeps the hole');
}

// --- 6. shapefile export: ring winding -----------------------------------
{
    const { api } = makeApp();
    // d3-contour polygons come with the outer ring counter-clockwise and the holes clockwise
    const d3like = feature('Polygon', [square(1, 2, 7, 8), square(3, 4, 5, 6, true)]);
    const good = feature('Polygon', [square(1, 2, 7, 8, true), square(3, 4, 5, 6)]);
    const goodRings = [...good.geometry.coordinates];
    const pin = feature('Point', [1, 2]);
    const out = api.orientRingsForShapefile([d3like, good, pin]);
    eq(out.length, 3, 'all features are returned');
    for (const f of [d3like, good]) {
        ok(signedArea(f.geometry.coordinates[0]) < 0, 'outer ring is clockwise (shapefile spec)');
        ok(signedArea(f.geometry.coordinates[1]) > 0, 'hole is counter-clockwise');
        eq(Math.abs(signedArea(f.geometry.coordinates[0])), 36, 'outer ring keeps its area');
    }
    ok(good.geometry.coordinates[0] === goodRings[0] && good.geometry.coordinates[1] === goodRings[1], 'correctly wound rings are left as they are');
    eq(pin.geometry.coordinates, [1, 2], 'points are untouched');
}

// --- 7. importShapefile reads every layer of the zip ---------------------
{
    // Stand-ins for JSZip and the shapefile library: a zip as written by
    // shp-write (one layer per geometry type), zipped on macOS (which adds
    // "._" copies), with the polygon layer holding a wrongly wound polygon.
    const layers = {
        'POINT': [feature('Point', [9.5, 8.5], { name: 'pin' })],
        'POLYGON': [feature('MultiPolygon', [[HOLE], [OUTER]]), feature('Polygon', [OTHER])],
    };
    const opened = [];
    const entry = (name) => ({ async: async () => name });   // the "buffer" is just the file name
    const files = {};
    for (const n of ['POINT.shp', 'POINT.shx', 'POINT.dbf', 'POINT.prj', 'POLYGON.shp', 'POLYGON.shx', 'POLYGON.dbf',
        '__MACOSX/', '__MACOSX/._POLYGON.shp', '__MACOSX/._POLYGON.dbf']) files[n] = entry(n);
    const JSZip = { loadAsync: async () => ({ files }) };
    const shapefile = {
        open: async (shp, dbf) => {
            opened.push([shp, dbf]);
            const rows = [...layers[shp.replace('.shp', '')]];
            return { read: async () => rows.length ? { done: false, value: rows.shift() } : { done: true } };
        },
    };
    const { api, calls } = makeApp({ JSZip, shapefile });
    await api.importShapefile(new ArrayBuffer(0));
    eq(opened, [['POINT.shp', 'POINT.dbf'], ['POLYGON.shp', 'POLYGON.dbf']], 'every .shp layer is opened with its own .dbf; macOS "._" copies are skipped');
    eq(calls.alerts, [], 'no error');
    eq(api.labels().map(l => l.type), ['point', 'polygon', 'polygon'], 'pin and polygons are all imported');
    eq(api.labels().map(l => l.pixelCount || 0), [0, 32, 9], 'polygon pixel counts 32 and 9');
    eq([calls.save, calls.render], [1, 1], 'one save and one render for the whole zip');

    const empty = makeApp({ JSZip: { loadAsync: async () => ({ files: { 'readme.txt': entry('readme.txt') } }) }, shapefile });
    await empty.api.importShapefile(new ArrayBuffer(0));
    eq(empty.calls.alerts.length, 1, 'a zip without any .shp still reports an error');
}

// --- 8. JSON (full): pixels of a promoted cluster ------------------------
{
    // what doExportManualLabels('json') writes for a promoted cluster
    const cluster = { name: 'water', type: 'similarity', lat: 5, lon: 5, embedding: [0, 0], threshold: 3, pixel_coords: [1, 1, 2, 1, 2, 2] };
    const same = makeApp().api;
    same.importLabelsJSON([{ ...cluster }], { ...gt });
    eq(same.labels()[0].pixel_coords, [1, 1, 2, 1, 2, 2], 'pixel_coords are restored on the grid they were exported from');

    const shifted = makeApp().api;
    shifted.importLabelsJSON([{ ...cluster }], { ...gt, c: gt.c + 0.25 });
    ok(!('pixel_coords' in shifted.labels()[0]), 'but not on another grid (they would land on other pixels)');
    eq(shifted.labels()[0].threshold, 3, 'there the label keeps its embedding + threshold');

    const old = makeApp().api;
    old.importLabelsJSON([{ ...cluster }]);
    ok(!('pixel_coords' in old.labels()[0]), 'nor from a file that does not say which grid it is from');
}

// --- 9. vertices are not rounded to whole pixels -------------------------
{
    // d3-contour (smooth off) draws a pixel set through the midpoints of the
    // pixel edges: one pixel becomes a diamond around its centre, a block a
    // rectangle with cut corners. Rounded to pixel corners those collapse.
    const lonlat = (ring) => ring.map(([x, y]) => [x, 10 - y]);           // pixel -> lon/lat on this grid
    const diamond = (px, py) => [[px + 0.5, py], [px + 1, py + 0.5], [px + 0.5, py + 1], [px, py + 0.5], [px + 0.5, py]];
    const block = [[3.5, 2], [4.5, 2], [5, 2.5], [5, 3.5], [4.5, 4], [3.5, 4], [3, 3.5], [3, 2.5], [3.5, 2]];   // pixels 3..4 x 2..3
    const { api } = makeApp();
    api.importGeoJSON(collection(feature('Polygon', [lonlat(diamond(7, 6))]), feature('Polygon', [lonlat(block)])));
    eq(labelPixels(api, api.labels()[0]), ['7,6'], 'the contour of one pixel covers exactly that pixel');
    eq(labelPixels(api, api.labels()[1]), ['3,2', '3,3', '4,2', '4,3'], 'the contour of a 2 x 2 block covers exactly its 4 pixels');
    eq(api.labels().map(l => l.pixelCount), [1, 4], 'and the import counts them the same way');

    // A drawn polygon: pixel centres inside the outline as drawn. Under the
    // edge from (4.4, 0) to (0, 4.4) lie the centres with x + y <= 3: 10 pixels
    // (rounding the corners to 4 leaves 6 and puts 4 more exactly on the edge).
    const tri = makeApp().api;
    tri.importGeoJSON(collection(feature('Polygon', [lonlat([[0, 0], [4.4, 0], [0, 4.4], [0, 0]])])));
    eq(tri.labels()[0].pixelCount, 10, 'triangle with a diagonal edge covers the 10 pixels whose centre is inside');

    // Same on a real-world geotransform, where (lon - c) / a is only almost
    // x.5: every single-pixel contour must still give exactly its own pixel.
    const saved = window.localVectors.metadata.geotransform;
    const real = { a: 0.00012735369878844578, c: 11.925, e: -0.00012735369878844578, f: 57.7297 };
    window.localVectors.metadata.geotransform = real;
    const noisy = makeApp().api;
    const cells = [];
    for (let py = 0; py < H; py++) for (let px = 0; px < W; px++) cells.push([px, py]);
    noisy.importGeoJSON(collection(...cells.map(([px, py]) =>
        feature('Polygon', [diamond(px, py).map(([x, y]) => [real.c + x * real.a, real.f + y * real.e])]))));
    const wrong = noisy.labels().filter((l, i) => JSON.stringify(labelPixels(noisy, l)) !== JSON.stringify([`${cells[i][0]},${cells[i][1]}`]));
    eq(wrong.length, 0, `all ${cells.length} single-pixel contours on a real geotransform give their own pixel`);
    window.localVectors.metadata.geotransform = saved;
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
