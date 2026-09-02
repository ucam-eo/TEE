#!/usr/bin/env node
/**
 * "Map model" dropdown next to Create Map (Keshav, 2026-09-02).
 *
 *  - _checkedPixelClassifiers()  the ticked nn/rf/xgboost/mlp boxes
 *  - _pixelModelScore(base)      best last-run score for that base (across
 *      _v-variants and the regression "_reg" names; k-fold reads aggregate)
 *  - updateMapModelOptions()     rebuild #val-map-model-select, annotate
 *      each option with its score, keep the current pick if still ticked
 *  - getMapModel()               explicit pick > best-scoring ticked model
 *      > first ticked model > null
 *
 * Run:  node validation/test_map_model_picker.mjs
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

const clfBlock = (val, label) =>
    `<div class="val-clf-block"><label class="val-clf-header">` +
    `<input type="checkbox" value="${val}"><span>${label}</span></label></div>`;

const { document } = parseHTML(
    '<!DOCTYPE html><html><body>' +
    '<div class="val-classifiers">' +
    clfBlock('nn', 'k-NN') + clfBlock('rf', 'Random Forest') +
    clfBlock('xgboost', 'XGBoost') + clfBlock('mlp', 'MLP') +
    clfBlock('spatial_mlp', 'Spatial MLP') +
    '</div>' +
    '<select id="val-map-model-select"><option value="">Auto (best from last run)</option></select>' +
    '</body></html>'
);
global.document = document;

const factory = new Function(
    "const PIXEL_MAP_CLASSIFIERS = ['nn', 'rf', 'xgboost', 'mlp'];\n" +
    "let lastChartData = null;\n" +
    "let currentLargeAreaTask = 'classification';\n" +
    "const _LBL = { nn: 'k-NN', rf: 'Random Forest', xgboost: 'XGBoost', mlp: 'MLP' };\n" +
    "function getVariantLabel(n) { return _LBL[n.replace(/_v\\d+$/, '')] || n; }\n" +
    "globalThis.__set = (d, t) => { lastChartData = d; if (t) currentLargeAreaTask = t; };\n" +
    [
        extract('_checkedPixelClassifiers'),
        extract('_pixelModelScore'),
        extract('updateMapModelOptions'),
        extract('getMapModel'),
    ].join('\n\n') +
    '\nreturn { _checkedPixelClassifiers, _pixelModelScore, updateMapModelOptions, getMapModel };'
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const check = v => { document.querySelector(`.val-clf-header input[value="${v}"]`).setAttribute('checked', ''); };
const uncheck = v => { document.querySelector(`.val-clf-header input[value="${v}"]`).removeAttribute('checked'); };
const sel = document.getElementById('val-map-model-select');
const setSel = v => { for (const o of sel.options) { if (o.value === v) o.setAttribute('selected', ''); else o.removeAttribute('selected'); } };

// --- _checkedPixelClassifiers: pixel models only ------------------
check('rf'); check('spatial_mlp');
ok(JSON.stringify(api._checkedPixelClassifiers()) === '["rf"]', 'spatial_mlp is not a pixel map model');
check('nn'); check('mlp');
ok(api._checkedPixelClassifiers().sort().join(',') === 'mlp,nn,rf', 'lists the ticked pixel models');

// --- _pixelModelScore: variants + regression names + k-fold -----
globalThis.__set({
    classifiers: {
        rf: { mean_f1: [0.4, 0.72] },
        rf_v2: { mean_f1: [0.5, 0.81] },      // best rf variant
        nn: { mean_f1: [0.6, 0.66] },
    },
}, 'classification');
ok(Math.abs(api._pixelModelScore('rf') - 0.81) < 1e-9, 'rf score = best variant, last training %');
ok(Math.abs(api._pixelModelScore('nn') - 0.66) < 1e-9, 'nn score = last point');
ok(api._pixelModelScore('xgboost') === null, 'no score for an unrun model');

globalThis.__set({
    _mode: 'kfold',
    classifiers: { rf_reg: {} },
    aggregate: { rf_reg: { mean_r2: 0.77 } },
}, 'regression');
ok(Math.abs(api._pixelModelScore('rf') - 0.77) < 1e-9, 'k-fold regression reads aggregate mean_r2 (via _reg name)');

// --- updateMapModelOptions: annotate + keep selection -----------
globalThis.__set({ classifiers: { rf: { mean_f1: [0.8, 0.9] }, nn: { mean_f1: [0.5, 0.5] } } }, 'classification');
api.updateMapModelOptions();
const opts = Array.from(sel.options).map(o => o.textContent);
ok(opts[0] === 'Auto (best from last run)', 'first option is Auto');
ok(opts.some(t => /Random Forest — F1 0\.900/.test(t)), 'RF option shows its F1');
ok(!opts.some(t => /XGBoost/.test(t)), 'unchecked model not listed');

setSel('nn');
ok(sel.value === 'nn', 'test can select nn now that the option exists');
api.updateMapModelOptions();
ok(sel.value === 'nn', 'prior selection kept when still ticked');

uncheck('nn');
api.updateMapModelOptions();
ok(sel.value !== 'nn', 'an unticked pick is not carried over');
setSel('');

// --- getMapModel: explicit > best-score > first ----------------
setSel('mlp');  // ticked
ok(api.getMapModel() === 'mlp', 'explicit pick wins');
setSel('nn');   // NOT ticked any more
ok(api.getMapModel() === 'rf', 'falls back to best-scoring ticked model (rf 0.9 > mlp)');
globalThis.__set(null);  // no run yet
setSel('');
ok(['mlp', 'rf'].includes(api.getMapModel()), 'no scores -> first ticked pixel model');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
