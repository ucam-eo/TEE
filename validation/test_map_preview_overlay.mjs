#!/usr/bin/env node
/**
 * Regression test for the in-browser Create Map preview (Louis feature 5,
 * lightweight version).
 *
 * tessera-eval >= v1.8.2 attaches a `preview` to each map_ready event: a
 * base64 EPSG:4326 PNG, [[s,w],[n,e]] bounds, and a legend (per-class
 * name/colour list for classification, {min,max,ramp} for regression).
 * evaluation.js must drop it on Panel 2 as an L.imageOverlay with a
 * dismissable control (opacity slider + legend), and clearMapPreview()
 * must remove both the overlay(s) and the control.
 *
 * Run:  node validation/test_map_preview_overlay.mjs
 */

import { parseHTML } from 'linkedom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'evaluation.js'), 'utf8');

function extract(name) {
    const i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found in evaluation.js: ${name}`);
    let depth = 0, started = false, j = i;
    while (true) {
        const c = src[j];
        if (c === '{') { depth++; started = true; }
        else if (c === '}') { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
        j++;
    }
}

const { document, window } = parseHTML(
    '<!DOCTYPE html><html><body>' +
    '<div class="panel"><div id="map-rgb" class="map"></div></div>' +
    '</body></html>'
);
global.document = document;
global.window = window;

// Minimal Leaflet + map stubs.
const rgb = {
    _layers: [],
    removeLayer(l) { this._layers = this._layers.filter(x => x !== l); },
};
global.L = {
    imageOverlay(png, bounds, opts) {
        return {
            png, bounds, opts, _opacity: opts.opacity,
            setOpacity(o) { this._opacity = o; },
            addTo(m) { m._layers.push(this); return this; },
        };
    },
};
window.maps = { rgb };

const factory = new Function(
    `let valMapPreviewLayers = [];\nlet valMapPreviewOpacity = 0.75;\n` +
    [extract('clearMapPreview'), extract('addMapPreview'), extract('renderMapPreviewControl')].join('\n\n') +
    `\nreturn { clearMapPreview, addMapPreview, renderMapPreviewControl,` +
    ` layers: () => valMapPreviewLayers, opacity: () => valMapPreviewOpacity };`
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const ctl = () => document.getElementById('val-map-preview-ctl');

// --- classification preview ---------------------------------------------
api.addMapPreview({
    png: 'data:image/png;base64,AAAA',
    bounds: [[48.2, 16.6], [48.25, 16.65]],
    legend: [
        { value: 1, label: 'Water', color: '#4363d8' },
        { value: 2, label: 'Fen <mix>', color: '#3cb44b' },
    ],
    is_classification: true,
});

ok(rgb._layers.length === 1, 'image overlay added to Panel 2');
ok(rgb._layers[0].png === 'data:image/png;base64,AAAA', 'overlay uses the preview PNG');
ok(JSON.stringify(rgb._layers[0].bounds) === '[[48.2,16.6],[48.25,16.65]]', 'overlay uses preview bounds');
ok(ctl() !== null, 'preview control is created');
ok(/Water/.test(ctl().innerHTML), 'legend lists a class label');
ok(/Fen &lt;mix&gt;/.test(ctl().innerHTML), 'legend label is HTML-escaped');
ok(/#4363d8/.test(ctl().innerHTML), 'legend shows the class colour');
ok(ctl().querySelector('#val-map-preview-opacity') !== null, 'control has an opacity slider');

// opacity slider drives every overlay
const slider = ctl().querySelector('#val-map-preview-opacity');
slider.value = '0.3';
slider.oninput({ target: slider });
ok(rgb._layers[0]._opacity === 0.3, 'opacity slider updates the overlay opacity');

// --- a second map area adds a second overlay, one shared control --------
api.addMapPreview({
    png: 'data:image/png;base64,BBBB',
    bounds: [[48.3, 16.7], [48.35, 16.75]],
    legend: [{ value: 1, label: 'Water', color: '#4363d8' }],
    is_classification: true,
});
ok(rgb._layers.length === 2, 'second map area adds a second overlay');
ok(document.querySelectorAll('#val-map-preview-ctl').length === 1, 'still a single shared control');
ok(/Map preview \(2\)/.test(ctl().innerHTML), 'control shows the overlay count');
ok(rgb._layers[1]._opacity === 0.3, 'new overlay inherits the current opacity');

// --- close button clears everything -----------------------------------
ctl().querySelector('#val-map-preview-close').onclick();
ok(rgb._layers.length === 0, 'close removes every overlay');
ok(ctl() === null, 'close removes the control');
ok(api.layers().length === 0, 'internal layer list is emptied');

// --- regression legend renders a ramp --------------------------------
api.addMapPreview({
    png: 'data:image/png;base64,CCCC',
    bounds: [[48.2, 16.6], [48.25, 16.65]],
    legend: { min: 0.2, max: 34.1, ramp: ['#2b4abd', '#26b25c', '#ffe119', '#e63c3c'] },
    is_classification: false,
});
ok(/linear-gradient/.test(ctl().innerHTML), 'regression legend draws a gradient bar');
ok(/0\.2/.test(ctl().innerHTML) && /34\.1/.test(ctl().innerHTML), 'regression legend shows min and max');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
