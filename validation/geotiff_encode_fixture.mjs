/**
 * Writes GeoTIFFs with the browser encoder (public/js/geotiff_export.js) so
 * test_geotiff_encoder.py can open them with rasterio/GDAL -- what QGIS uses.
 *
 *   node validation/geotiff_encode_fixture.mjs <outdir>
 *
 * classification.tif goes through labels.js classificationRaster() (the
 * Labelling map export) on a small grid with an offset origin and a gap;
 * rgba.tif and float.tif are the two Change Detection export kinds.
 */
import fs from 'fs';
import path from 'path';
const out = process.argv[2];
const read = (f) => fs.readFileSync(new URL(f, import.meta.url), 'utf8');
const fn = (src, name) => {
    const a = src.indexOf(`function ${name}(`);
    let i = src.indexOf('{', a), depth = 0;
    for (; i < src.length; i++) {
        if (src[i] === '{') depth++;
        else if (src[i] === '}' && --depth === 0) break;
    }
    return src.slice(src.lastIndexOf('\n', a) + 1, i + 1);
};

globalThis.window = {};
const enc = read('../public/js/geotiff_export.js');
new Function(enc.slice(0, enc.indexOf('function downloadBlob')) + '\nwindow.encodeGeoTiff = encodeGeoTiff;')();

// 4 x 3 vector grid at pixel offset (10, 20) of the viewport geotransform;
// cell (1, 1) has no vector (a gap in the source data).
const gt = { a: 0.0001, c: 16.7, e: -0.0001, f: 48.2 };
const cells = [];
for (let y = 0; y < 3; y++) for (let x = 0; x < 4; x++) if (!(x === 1 && y === 1)) cells.push([10 + x, 20 + y]);
window.localVectors = { coords: Int32Array.from(cells.flat()), metadata: { geotransform: gt } };
// classes: A everywhere in row 0, B in row 2, unclassified in row 1
const assignments = Int8Array.from(cells.map(([, y]) => (y === 20 ? 0 : y === 22 ? 1 : -1)));
const computeManualClassification = () => ({
    classNames: ['A', 'B'], classColorMap: { A: '#ff0000', B: '#00a0ff' }, assignments, N: cells.length,
});
const classificationRaster = new Function('computeManualClassification',
    fn(read('../public/js/labels.js'), 'classificationRaster') + '\nreturn classificationRaster;')(computeManualClassification);

const r = classificationRaster();
const write = async (name, o) => fs.writeFileSync(path.join(out, name), Buffer.from(await window.encodeGeoTiff(o)));
await write('classification.tif', {
    width: r.width, height: r.height, data: r.data, bands: 1, geotransform: r.geotransform,
    nodata: '0', colormap: r.colormap, metadata: { class_1: 'A #ff0000', class_2: 'B #00a0ff' },
});
const rgba = new Uint8Array(5 * 2 * 4).map((_, i) => (i % 4 === 3 ? 255 : i));
await write('rgba.tif', { width: 5, height: 2, data: rgba, bands: 4, geotransform: gt });
const fl = Float32Array.from([0.5, NaN, 1.25, 2, 3, 4]);
await write('float.tif', { width: 3, height: 2, data: fl, bands: 1, geotransform: gt, nodata: 'nan' });
console.log(JSON.stringify({ classified: r.classified, width: r.width, height: r.height }));
