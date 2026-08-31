#!/usr/bin/env node
/**
 * Regression test: leaving rectangle-draw mode in Validation.
 *
 * Selecting a Spatial-split area type puts Panel 2 into L.Draw.Rectangle
 * mode with map.dragging disabled, and it re-arms after every rectangle so
 * you can draw several. The only documented exit used to be the disabled
 * placeholder option (unselectable) or Clear (which also deletes every
 * rectangle) -- so a user was effectively trapped with no pan (reported by
 * Keshav, 2026-08-31). Now:
 *   - toggleBboxDraw() shows a "press Esc to stop" hint and disables drag,
 *   - exitBboxDrawMode() re-enables drag, resets the dropdown + hides the
 *     hint, and is bound to the Escape key + to leaving validation mode.
 *
 * Run:  node validation/test_bbox_draw_escape.mjs
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
    '<select id="val-bbox-type">' +
    '<option value="" disabled selected>Select area to draw...</option>' +
    '<option value="train">Train area (blue)</option>' +
    '<option value="test">Test area (yellow)</option>' +
    '<option value="map">Map area (green)</option>' +
    '</select>' +
    '<div id="val-bbox-draw-hint" style="display:none;"></div>' +
    '</body></html>'
);
global.document = document;
global.window = window;
window.currentPanelMode = 'validation';

// Leaflet / map stubs.
const drag = { _on: true, enable() { this._on = true; }, disable() { this._on = false; } };
const container = { style: {} };
window.maps = { rgb: { dragging: drag, getContainer: () => container } };
let lastRect = null;
global.L = {
    Draw: {
        Rectangle: class {
            constructor() { lastRect = this; this._enabled = false; }
            enable() { this._enabled = true; }
            disable() { this._enabled = false; }
        },
    },
};

const factory = new Function(
    `let bboxDrawHandler = null;
     let currentBboxType = 'train';
     const BBOX_COLORS = { train: {}, test: {}, map: {} };
     const BBOX_TYPE_LABELS = { train: 'Train', test: 'Test', map: 'Map' };
     function ensureBboxFeatureGroup() {}
     ${[extract('setBboxDrawHint'), extract('exitBboxDrawMode'), extract('toggleBboxDraw')].join('\n')}
     return {
        toggleBboxDraw, exitBboxDrawMode, setBboxDrawHint,
        handler: () => bboxDrawHandler,
        setType: (t) => { currentBboxType = t; },
     };`
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const sel = () => document.getElementById('val-bbox-type');
const hint = () => document.getElementById('val-bbox-draw-hint');

// --- enter draw mode --------------------------------------------------
sel().selectedIndex = 3;  // "Map area (green)"
api.setType('map');
api.toggleBboxDraw();

ok(api.handler() !== null, 'draw handler is armed');
ok(lastRect && lastRect._enabled === true, 'L.Draw.Rectangle was enabled');
ok(drag._on === false, 'map dragging is disabled while drawing');
ok(container.style.cursor === 'crosshair', 'cursor is a crosshair while drawing');
ok(hint().style.display !== 'none', 'the Esc hint is visible');
ok(/Esc/.test(hint().innerHTML), 'the hint names the Esc key');
ok(/Map/.test(hint().innerHTML), 'the hint names the area type being drawn');

// --- Escape (what the keydown listener calls) ------------------------
api.exitBboxDrawMode();

ok(api.handler() === null, 'Esc drops the draw handler');
ok(drag._on === true, 'Esc re-enables map dragging (this is the bug fix)');
ok(container.style.cursor === '', 'Esc restores the cursor');
ok(hint().style.display === 'none', 'Esc hides the hint');
ok(sel().selectedIndex === 0, 'Esc resets the area-type dropdown to the placeholder');

// --- exitBboxDrawMode is a no-op when not drawing -------------------
api.exitBboxDrawMode();
ok(drag._on === true && api.handler() === null, 'calling exit again is harmless');

// --- source wiring: Escape key + mode-switch both call it ----------
ok(/addEventListener\(['"]keydown['"]/.test(src) && /e\.key === ['"]Escape['"][\s\S]{0,120}exitBboxDrawMode\(\)/.test(src),
   'a keydown listener calls exitBboxDrawMode on Escape');
ok(/window\.exitBboxDrawMode = exitBboxDrawMode/.test(src), 'exitBboxDrawMode is exported for the mode-switch hook');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
