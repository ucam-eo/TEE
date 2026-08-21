#!/usr/bin/env node
/**
 * Regression test: the per-class threshold controls (+/-, slider,
 * click-to-type) must update every rendered copy of a class's row, not
 * just the first DOM match.
 *
 * renderManualLabelsList() deliberately mirrors identical markup into two
 * containers -- #manual-labels-list and #panel6-labels-list (the
 * auto-label view's copy of the same list) -- so a class name can match
 * two elements with the same data-class-count attribute at once. Confirmed
 * live (Keshav, 2026-08-21): clicking the +/- buttons visibly changed the
 * map (rebuildClassOverlay always reads the real, correctly-updated
 * label.threshold) while the slider position and displayed number on
 * screen never moved -- because _manualClassRow used a plain
 * document.querySelector(), which silently updated whichever copy
 * happened to come first in document order, not necessarily the one the
 * button lived in. Dragging the slider was unaffected (it updates its own
 * display via sliderEl.parentElement, correctly scoped to the copy you're
 * actually touching), which is why only the buttons/click-to-type looked
 * broken.
 *
 * This test renders both containers (matching viewer.html's real
 * structure), clicks the button that lives in #manual-labels-list
 * specifically, and asserts *both* copies end up in sync -- proven to
 * fail pre-fix via `git stash` + rerun.
 *
 * Run:  node validation/test_manual_label_dom_sync.mjs
 */

import { parseHTML } from 'linkedom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LABELS_JS = path.join(__dirname, '..', 'public', 'js', 'labels.js');
const src = fs.readFileSync(LABELS_JS, 'utf8');

function extract(name) {
    const i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found in labels.js: ${name}`);
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
    '<div id="panel6-labels-list"></div>' +   // matches viewer.html's real order: panel6 first
    '<div id="manual-labels-list"></div>' +
    '</body></html>'
);
global.document = document;
global.window = window;
global.CSS = { escape: s => s.replace(/[^a-zA-Z0-9_-]/g, c => '\\' + c) };
global.requestAnimationFrame = () => {}; // overlay rebuild is irrelevant to this test
global.cancelAnimationFrame = () => {};
global.setTimeout = () => {};
global.clearTimeout = () => {};

let manualLabels = [];
let collapsedClasses = new Set();
let currentManualLabel = null;
function saveManualLabelsToStorage() {}
function rebuildClassOverlay() {}

const fns = [
    'renderManualLabelsList', 'getClassLabels', '_manualClassRows',
    'stepClassThreshold', 'onManualClassSliderInput', 'updateManualClassThreshold',
    '_applyClassThreshold', 'getClassThreshold', 'activateManualClass', 'toggleClassExpand',
].map(extract).join('\n\n');

const factory = new Function(
    'manualLabels', 'collapsedClasses', 'currentManualLabel',
    'saveManualLabelsToStorage', 'rebuildClassOverlay',
    `let _thresholdRAF = null;\nlet _thresholdSaveTimer = null;\n${fns}\n` +
    'return { renderManualLabelsList, stepClassThreshold, onManualClassSliderInput };'
);
const api = factory(manualLabels, collapsedClasses, currentManualLabel, saveManualLabelsToStorage, rebuildClassOverlay);

for (let i = 0; i < 8; i++) {
    manualLabels.push({
        id: i + 1, name: 'canal', type: 'point', color: '#8e44ad',
        lat: 52.46 + i * 0.001, lon: -0.21 + i * 0.001,
        embedding: new Array(128).fill(0.1), visible: true, threshold: 0,
    });
}
api.renderManualLabelsList();

let passed = 0, failed = 0;
function assertEq(a, b, msg) {
    if (a !== b) { failed++; console.log(`  FAIL: ${msg} (got ${JSON.stringify(a)}, want ${JSON.stringify(b)})`); }
    else passed++;
}

function readContainer(id) {
    const c = document.getElementById(id);
    return {
        slider: c.querySelector('input[type="range"]')?.value,
        display: c.querySelector('.threshold-display')?.textContent,
    };
}

// Sanity: both copies exist and start at 0.
assertEq(readContainer('manual-labels-list').slider, '0', 'manual-labels-list starts at 0');
assertEq(readContainer('panel6-labels-list').slider, '0', 'panel6-labels-list starts at 0');

// Click "+" 5 times via stepClassThreshold directly (the function an
// onclick="stepClassThreshold(...)" attribute resolves to).
for (let i = 0; i < 5; i++) api.stepClassThreshold('canal', 1);

const main = readContainer('manual-labels-list');
const mirror = readContainer('panel6-labels-list');
assertEq(main.slider, '5', 'manual-labels-list slider after 5 clicks of +');
assertEq(main.display, '5', 'manual-labels-list display after 5 clicks of +');
assertEq(mirror.slider, '5', 'panel6-labels-list slider stays in sync');
assertEq(mirror.display, '5', 'panel6-labels-list display stays in sync');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
