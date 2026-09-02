#!/usr/bin/env node
/**
 * K-fold CV option in the Validation panel (Keshav, 2026-09-02).
 *
 *  - getEvalMode()  reads #val-eval-mode ('learning_curve' | 'kfold')
 *  - getKfoldK()    reads #val-kfold-k, clamped to 2..20
 *  - appendFoldResultRow(n, models)   one results-table row per fold; the
 *      per-fold metric key differs by task (classification: mean_f1;
 *      regression: r2 -- run_kfold_cv)
 *  - renderKfoldClassificationTable(aggregate)  a bold "Mean ± std" summary
 *      row (mean_f1 ± std_f1 per model)
 *
 * Run:  node validation/test_kfold_results.mjs
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
    if (src.slice(i - 6, i) === 'async ') i -= 6;
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
    '<select id="val-eval-mode"><option value="learning_curve">lc</option><option value="kfold">kfold</option></select>' +
    '<input id="val-kfold-k" value="5">' +
    '<input id="val-seed" value="42">' +
    '<table><tbody id="val-results-tbody"></tbody></table>' +
    '</body></html>'
);
global.document = document;
global.window = window;

const factory = new Function(
    `let _resultsTableModels = [];\n` +
    `let currentLargeAreaTask = 'classification';\n` +
    `globalThis.__setModels = (m) => { _resultsTableModels = m; };\n` +
    `globalThis.__setTask = (t) => { currentLargeAreaTask = t; };\n` +
    [
        extract('getEvalMode'),
        extract('getKfoldK'),
        extract('getSeed'),
        extract('appendFoldResultRow'),
        extract('renderKfoldClassificationTable'),
    ].join('\n\n') +
    `\nreturn { getEvalMode, getKfoldK, getSeed, appendFoldResultRow, renderKfoldClassificationTable };`
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };
const modeSel = document.getElementById('val-eval-mode');
const kInput = document.getElementById('val-kfold-k');
const tbody = document.getElementById('val-results-tbody');
const setMode = (v) => {
    // linkedom computes <select>.value from the option carrying the
    // `selected` attribute (undefined until one does).
    for (const o of modeSel.options) {
        if (o.value === v) o.setAttribute('selected', '');
        else o.removeAttribute('selected');
    }
};

// --- getEvalMode / getKfoldK -----------------------------------------
setMode('learning_curve');
ok(api.getEvalMode() === 'learning_curve', 'getEvalMode default');
setMode('kfold');
ok(api.getEvalMode() === 'kfold', 'getEvalMode kfold');
kInput.setAttribute('value', '7'); kInput.value = '7';
ok(api.getKfoldK() === 7, 'getKfoldK reads the input');
kInput.value = '999';
ok(api.getKfoldK() === 20, 'getKfoldK clamps high to 20');
kInput.value = '1';
ok(api.getKfoldK() === 2, 'getKfoldK clamps low to 2');
kInput.value = '';
ok(api.getKfoldK() === 5, 'getKfoldK falls back to 5 on empty');

// --- getSeed ------------------------------------------------------
const seedInput = document.getElementById('val-seed');
seedInput.value = '7';
ok(api.getSeed() === 7, 'getSeed reads the input');
seedInput.value = '-3';
ok(api.getSeed() === 42, 'getSeed rejects a negative value');
seedInput.value = '';
ok(api.getSeed() === 42, 'getSeed falls back to 42 on empty');

// --- appendFoldResultRow: classification ----------------------------
globalThis.__setModels(['rf', 'nn']);
globalThis.__setTask('classification');
api.appendFoldResultRow(1, { rf: { mean_f1: 0.812345 }, nn: { mean_f1: 0.5 } });
api.appendFoldResultRow(2, { rf: { mean_f1: 0.9 }, nn: {} });
ok(tbody.children.length === 2, 'two fold rows added');
ok(/Fold 1/.test(tbody.children[0].textContent), 'row labelled by fold number');
ok(/0\.8123/.test(tbody.children[0].textContent), 'classification fold shows mean_f1');
ok(tbody.children[1].textContent.includes('—'), 'missing model metric shows an em dash');

// --- appendFoldResultRow: regression uses r2 -----------------------
tbody.innerHTML = '';
globalThis.__setTask('regression');
api.appendFoldResultRow(1, { rf: { r2: 0.7654, rmse: 1.2 }, nn: { r2: -0.1 } });
ok(/0\.7654/.test(tbody.children[0].textContent), 'regression fold shows r2');
ok(/-0\.1000/.test(tbody.children[0].textContent), 'negative r2 rendered');

// --- renderKfoldClassificationTable summary row -------------------
tbody.innerHTML = '';
globalThis.__setModels(['rf']);
globalThis.__setTask('classification');
api.renderKfoldClassificationTable({ rf: { mean_f1: 0.85, std_f1: 0.03 } });
ok(tbody.children.length === 1, 'one summary row');
ok(/Mean ± std/.test(tbody.children[0].textContent), 'summary row is labelled');
ok(/0\.8500 ± 0\.0300/.test(tbody.children[0].textContent), 'summary shows mean ± std');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
