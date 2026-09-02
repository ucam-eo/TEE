#!/usr/bin/env node
/**
 * Task-type override control + uploaded-shapefile list (Keshav, 2026-09-02).
 *
 *  - getTaskOverride()        reads #val-task-override ('auto' | 'classification' | 'regression')
 *  - taskForCreateMap()       override when forced, else the last run's detected task
 *  - updateTaskDetectedLabel() writes the hint line: "Forced to X" (amber) or
 *                             "Auto-detected as X" or the generic guidance
 *  - refreshShapefileList()   GET /api/evaluation/list-shapefiles -> a small list
 *                             under the drop zone; blank (no throw) on a 404 /
 *                             unreachable compute server
 *
 * Run:  node validation/test_task_override_and_shapefile_list.mjs
 */

import { parseHTML } from 'linkedom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'evaluation.js'), 'utf8');

function extract(name) {
    let i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found in evaluation.js: ${name}`);
    if (src.slice(i - 6, i) === 'async ') i -= 6;   // keep the async keyword
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
    '<div id="val-drop-zone"></div>' +
    '<div id="val-shapefile-list"></div>' +
    '<select id="val-task-override">' +
    '<option value="auto">auto</option>' +
    '<option value="classification">classification</option>' +
    '<option value="regression">regression</option>' +
    '</select>' +
    '<div id="val-task-detected"></div>' +
    '</body></html>'
);
global.document = document;
global.window = window;

let fetchImpl = async () => { throw new Error('no fetch stub set'); };
global.fetch = (...a) => fetchImpl(...a);
global.evalUrl = (p) => `/api/evaluation/${p}`;

const factory = new Function(
    `let currentLargeAreaTask = null;\n` +
    `globalThis.__setTask = (t) => { currentLargeAreaTask = t; };\n` +
    [
        extract('getTaskOverride'),
        extract('taskForCreateMap'),
        extract('updateTaskDetectedLabel'),
        extract('refreshShapefileList'),
    ].join('\n\n') +
    `\nreturn { getTaskOverride, taskForCreateMap, updateTaskDetectedLabel, refreshShapefileList };`
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const sel = document.getElementById('val-task-override');
const hint = document.getElementById('val-task-detected');
const list = document.getElementById('val-shapefile-list');
// linkedom computes <select>.value from the option carrying the `selected`
// attribute (and .value is undefined until one does) -- drive it that way.
const setOverride = (v) => {
    for (const o of sel.options) {
        if (o.value === v) o.setAttribute('selected', '');
        else o.removeAttribute('selected');
    }
};

// --- getTaskOverride / taskForCreateMap -------------------------------
setOverride('auto');
ok(api.getTaskOverride() === 'auto', 'getTaskOverride reads the select');
ok(api.taskForCreateMap() === null, 'auto + no run -> no task forced on create-map');
globalThis.__setTask('regression');
ok(api.taskForCreateMap() === 'regression', 'auto + a detected run -> use the detected task');
setOverride('classification');
ok(api.taskForCreateMap() === 'classification', 'a forced override beats the detected task');

// --- updateTaskDetectedLabel ----------------------------------------
setOverride('regression');
api.updateTaskDetectedLabel();
ok(/Forced to regression/.test(hint.textContent), 'forced choice shown in the hint');
ok(hint.style.color === 'rgb(240, 173, 78)' || hint.style.color === '#f0ad4e', 'forced hint is amber');
setOverride('auto');
api.updateTaskDetectedLabel();
ok(/Auto-detected as regression/.test(hint.textContent), 'auto + last run -> shows the detected task');
globalThis.__setTask(null);
api.updateTaskDetectedLabel();
ok(/more than 20 distinct values/.test(hint.textContent), 'auto + no run -> generic guidance');

// --- refreshShapefileList: normal ----------------------------------
fetchImpl = async () => ({
    ok: true,
    json: async () => ({ files: [
        { name: 'austria.zip', features: 42789 },
        { name: 'snowdon.zip', features: 12 },
    ] }),
});
await api.refreshShapefileList();
ok(/Loaded shapefiles \(2\)/.test(list.textContent), 'list header shows the count');
ok(list.textContent.includes('austria.zip') && list.textContent.includes('snowdon.zip'), 'both files listed');
ok(list.textContent.includes('42,789'), 'feature count is formatted');

// --- refreshShapefileList: empty ----------------------------------
fetchImpl = async () => ({ ok: true, json: async () => ({ files: [] }) });
await api.refreshShapefileList();
ok(list.textContent === '', 'empty set clears the list');

// --- refreshShapefileList: older server (404) ---------------------
list.textContent = 'stale';
fetchImpl = async () => ({ ok: false, status: 404, json: async () => ({}) });
await api.refreshShapefileList();
ok(list.textContent === 'stale', '404 leaves the box untouched, no throw');

// --- refreshShapefileList: compute server unreachable ------------
let threw = false;
fetchImpl = async () => { throw new Error('Failed to fetch'); };
try { await api.refreshShapefileList(); } catch (_) { threw = true; }
ok(!threw, 'a network error is swallowed, not thrown');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
