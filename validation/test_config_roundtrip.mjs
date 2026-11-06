#!/usr/bin/env node
/**
 * Generate Config -> Upload Config round-trips the full model selection
 * and the evaluation method (user report, 2026-09-07).
 *
 * Bugs fixed:
 *  - generateConfig skipped spatial_mlp / spatial_mlp_5x5 / unet entirely,
 *    so they were never restored on upload.
 *  - eval_mode (learning curve vs k-fold) was never written, so k-fold had
 *    to be re-selected by hand after every Upload Config.
 *
 * Run:  node validation/test_config_roundtrip.mjs
 */

import { parseHTML } from 'linkedom';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'evaluation.js'), 'utf8');

function extract(name) {
    let i = src.indexOf(`function ${name}(`);
    if (i === -1) throw new Error(`function not found: ${name}`);
    if (src.slice(i - 6, i) === 'async ') i -= 6;
    let depth = 0, started = false, j = i;
    while (true) {
        const c = src[j];
        if (c === '{') { depth++; started = true; }
        else if (c === '}') { depth--; if (started && depth === 0) return src.slice(i, j + 1); }
        j++;
    }
}

const clf = (val, params = '') =>
    `<div class="val-clf-block"><label class="val-clf-header">` +
    `<input type="checkbox" value="${val}"></label>` +
    `<div class="val-params">${params}</div></div>`;

const p = (c, name, v) =>
    `<input data-clf="${c}" data-param="${name}" data-variant="0" value="${v}">`;

const { document, window } = parseHTML(
    '<!DOCTYPE html><html><body>' +
    '<select id="val-field-select"><option value="species" selected>species</option></select>' +
    '<select id="val-task-override"><option value="auto" selected>auto</option><option value="classification">classification</option></select>' +
    '<input id="val-seed" value="42">' +
    '<select id="val-eval-mode"><option value="learning_curve" selected>lc</option><option value="kfold">kfold</option></select>' +
    '<div id="val-kfold-k-wrap" style="display:none"><input id="val-kfold-k" value="5"></div>' +
    '<select id="val-train-year-select"><option value="2024" selected>2024</option></select>' +
    '<select id="val-test-year-select"><option value="2024" selected>2024</option></select>' +
    '<input id="val-max-train-large" value="200000">' +
    '<div id="val-max-train-pct"></div>' +
    '<select id="val-sampling-select"><option value="sqrt" selected>sqrt</option></select>' +
    '<input id="val-max-patches" value="500">' +
    '<div class="val-classifiers">' +
    clf('nn') + clf('rf') + clf('xgboost') +
    clf('mlp', p('mlp', 'max_iter', '200')) +
    clf('spatial_mlp', p('spatial_mlp', 'hidden_layers', '256,128') + p('spatial_mlp', 'max_iter', '300')) +
    clf('spatial_mlp_5x5', p('spatial_mlp_5x5', 'max_iter', '400')) +
    clf('unet', p('unet', 'epochs', '50') + p('unet', 'depth', '3')) +
    '</div>' +
    '</body></html>'
);
global.document = document;
global.Event = window.Event;   // applyConfig does `new Event('change')`

// The real app wires this on #val-eval-mode at module load; mimic it so
// applyConfig's dispatched 'change' toggles the k-fold input like it does live.
const _em = document.getElementById('val-eval-mode');
_em.addEventListener('change', () => {
    document.getElementById('val-kfold-k-wrap').style.display =
        _em.value === 'kfold' ? '' : 'none';
});

let capturedJson = null;
global.Blob = class { constructor(parts) { capturedJson = parts.join(''); } };
global.URL = { createObjectURL: () => 'blob:x', revokeObjectURL: () => {} };
global.alert = (m) => { throw new Error('alert: ' + m); };

const factory = new Function(
    `let valUploadedFilename = 'train.zip';\n` +
    `let spatialBboxes = { train: [], test: [], map: [] };\n` +
    `const hasSpatialBboxes = () => false;\n` +
    `const rectToBbox = r => r;\n` +
    // applyConfig's helper deps -- no-ops for this test
    `const updateClassSummary = () => {};\n` +
    `const updateTaskDetectedLabel = () => {};\n` +
    `const removeAllVariants = () => {};\n` +
    `const addVariant = () => {};\n` +
    `const clearAllBboxes = () => {};\n` +
    `const addBboxFromCoords = () => {};\n` +
    `const updateMapModelOptions = () => {};\n` +
    [
        extract('getTaskOverride'),
        extract('getSeed'),
        extract('getEvalMode'),
        extract('getKfoldK'),
        extract('optionalLimitInput'),
        extract('getGroupByField'),
        extract('getSpatialKfold'),
        extract('getAreaStratifiedSplit'),
        extract('generateConfig'),
        extract('applyConfig'),
    ].join('\n\n') +
    `\nreturn { generateConfig, applyConfig };`
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
// linkedom's :checked selector matches the `checked` attribute, not the property.
const el = v => document.querySelector(`.val-clf-header input[value="${v}"]`);
const check = v => { el(v).setAttribute('checked', ''); el(v).checked = true; };
const uncheck = v => { el(v).removeAttribute('checked'); el(v).checked = false; };
const isChecked = v => el(v).checked || el(v).hasAttribute('checked');
const setSel = (id, v) => {
    const s = document.getElementById(id);
    for (const o of s.options) o.selected = (o.value === v);
};

// --- set up a run that uses spatial models + k-fold ------------------
check('nn');
check('spatial_mlp');
check('spatial_mlp_5x5');
check('unet');
setSel('val-eval-mode', 'kfold');
document.getElementById('val-kfold-k').value = '8';

api.generateConfig();
ok(capturedJson, 'generateConfig produced a config blob');
const cfg = JSON.parse(capturedJson);

// spatial models go in a separate key (the CLI rejects them in `classifiers`)
ok('spatial_mlp' in cfg.spatial_models, 'config includes spatial_mlp');
ok('spatial_mlp_5x5' in cfg.spatial_models, 'config includes spatial_mlp_5x5');
ok('unet' in cfg.spatial_models, 'config includes unet');
ok(!('spatial_mlp' in cfg.classifiers), 'spatial_mlp NOT in classifiers (would break the CLI)');
ok(cfg.spatial_models.spatial_mlp.hidden_layers === '256,128', 'spatial_mlp params saved');
ok(cfg.spatial_models.unet.epochs === 50, 'unet params saved');
ok('nn' in cfg.classifiers, 'pixel model still in classifiers');
ok(cfg.eval_mode === 'kfold', 'config records eval_mode=kfold');
ok(cfg.kfold === 8, 'config records the fold count');

// --- wipe the UI, then restore from the config ----------------------
for (const v of ['nn', 'rf', 'xgboost', 'mlp', 'spatial_mlp', 'spatial_mlp_5x5', 'unet']) uncheck(v);
setSel('val-eval-mode', 'learning_curve');
_em.dispatchEvent(new Event('change'));
document.getElementById('val-kfold-k').value = '5';

api.applyConfig(cfg);

ok(isChecked('nn'), 'restore: nn checked');
ok(isChecked('spatial_mlp'), 'restore: spatial_mlp checked');
ok(isChecked('spatial_mlp_5x5'), 'restore: spatial_mlp_5x5 checked');
ok(isChecked('unet'), 'restore: unet checked');
ok(!isChecked('rf'), 'restore: rf stays unchecked');
ok(document.getElementById('val-eval-mode').value === 'kfold', 'restore: eval method back to k-fold');
ok(document.getElementById('val-kfold-k-wrap').style.display !== 'none', 'restore: k-fold input shown');
ok(document.getElementById('val-kfold-k').value === '8', 'restore: fold count = 8');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
