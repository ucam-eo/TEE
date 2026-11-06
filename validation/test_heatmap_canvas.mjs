/**
 * Change heatmap drawing (HeatmapCanvasLayer, public/js/dimreduction.js).
 *
 * The heatmap used to draw one rectangle per pixel, sized from
 * window.localVectors' geotransform. When that wasn't loaded (e.g. opening
 * Change Detection directly) it fell back to assuming 10 m pixels, which at
 * UK latitudes made every row ~72% of its real height: a gap under every row.
 * It now paints its own grid into one image and draws it in strips placed by
 * the map's projection. Checks, on a Downham-sized grid and a Web Mercator
 * mock map: the image has one pixel per grid pixel (no-data transparent);
 * the strips cover every row with no gap or overlap, each at its projected
 * edges; strips outside the view are skipped.
 */
import fs from 'fs';
const src = fs.readFileSync(new URL('../public/js/dimreduction.js', import.meta.url), 'utf8');
const a = src.indexOf('class HeatmapCanvasLayer');
const b = src.indexOf('\n}\n', a) + 2;
const draws = [];
const mkCanvas = () => { const ctx = {
  createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
  putImageData(d) { ctx.img = d; }, clearRect() {}, drawImage: (...args) => draws.push(args) };
  return { style: {}, width: 0, height: 0, getContext: () => ctx }; };
globalThis.document = { createElement: () => mkCanvas() };
globalThis.L = { Layer: class {} };
const Cls = new Function(src.slice(a, b) + '\nreturn HeatmapCanvasLayer;')();

const W = 596, H = 346, gt = { a: 0.0001242141057168511, c: 0.3351387853566857, e: -0.0001242141057168511, f: 52.622856450802495 };
const values = new Float32Array(W * H).map((_, i) => i % 7); values[5] = NaN;
const Z = 256 * 2 ** 18;   // zoom 18: ~ 22 screen px per 10 m pixel
const merc = (lat, lon) => ({ x: (lon + 180) / 360 * Z, y: (1 - Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360)) / Math.PI) / 2 * Z });
const o = merc(52.6, 0.37);
const proj = ([lat, lon]) => { const p = merc(lat, lon); return { x: p.x - o.x + 20000, y: p.y - o.y + 20000 }; };
const map = { getSize: () => ({ x: 1e6, y: 1e6 }), latLngToContainerPoint: proj,
  getContainer: () => ({ insertBefore() {}, firstChild: null }), on() {}, off() {} };
const layer = new Cls([], { max_distance: 6, median_distance: 4 }, { width: W, height: H, values, geotransform: gt });
layer.onAdd(map);

let fails = 0; const check = (c, m) => { if (!c) { fails++; console.log('FAIL', m); } };
const px = layer._image.getContext().img.data;
check(px[3] === 255 && px[5 * 4 + 3] === 0, 'value pixel opaque, NaN pixel transparent');
check(layer._image.width === W && layer._image.height === H, 'image is W x H');
// strips: contiguous, cover all rows, edges where the map projects them
let rowsCovered = 0, prevBottom = null;
for (const [img, sx, sy, sw, sh, dx, dy, dw, dh] of draws) {
  check(sx === 0 && sw === W, 'full width source');
  check(sy === rowsCovered, `strip starts at row ${rowsCovered}`);
  if (prevBottom !== null) check(dy === prevBottom, `no gap/overlap at row ${sy}`);
  const wantTop = Math.round(proj([gt.f + sy * gt.e, gt.c]).y);
  const wantBot = Math.round(proj([gt.f + (sy + sh) * gt.e, gt.c]).y);
  check(dy === wantTop && dy + dh === wantBot, `strip ${sy} placed at projected edges`);
  rowsCovered += sh; prevBottom = dy + dh;
}
check(rowsCovered === H, `all ${H} rows drawn (got ${rowsCovered})`);
const pitch = (prevBottom - draws[0][6]) / H;
console.log(`${draws.length} strips, ~${pitch.toFixed(1)} screen px per row; ${fails} failures`);
// Off-screen culling: a 1000 px tall view only draws the strips it shows
draws.length = 0;
const top = Math.round(proj([gt.f, gt.c]).y); const VH = top + 1000;
map.getSize = () => ({ x: 1e6, y: VH });
layer._redraw();
const vis = draws.every(([, , , , , , dy, , dh]) => dy + dh > 0 && dy < VH);
check(vis && draws.length > 0 && draws.length < 11, `only visible strips drawn (${draws.length})`);
console.log(`culling: ${draws.length} of 11 strips drawn`);
console.log(`\n${fails === 0 ? 'all checks passed' : fails + ' failed'}`);
process.exit(fails > 0 ? 1 : 0);
