#!/usr/bin/env node
/**
 * Regression test for the "Outside range" column in the Regression Metrics
 * table (bug 8, Louis Driver).
 *
 * tessera-eval >= v1.8.1 adds oor_frac (fraction of largest-percentage
 * test predictions beyond the training targets' span) and train_range
 * [min, max] to each regression model's aggregate metrics.
 * renderRegressionResults() must:
 *   - show "—" when the fields are absent (older tessera-eval pin),
 *   - show a percentage otherwise, amber (#e0a44b) once it reaches 1%,
 *   - print the training span in #val-regression-oor-note, and hide that
 *     note when no model reported a range.
 *
 * Run:  node validation/test_regression_oor_column.mjs
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
    '<div id="val-cm-panel"><div class="cm-scroll"></div></div>' +
    '<div id="val-cm-title"></div>' +
    '<div id="val-regression-panel" style="display:none">' +
    '  <table id="val-regression-table"><tbody></tbody></table>' +
    '  <div id="val-regression-oor-note" style="display:none"></div>' +
    '</div>' +
    '</body></html>'
);
global.document = document;
global.window = window;

let valScatterChart = null;
function getVariantColor() { return { line: '#4e79a7' }; }
function getVariantLabel(n) { return n; }
function renderRegressionScatter() { /* needs Chart.js; irrelevant here */ }

const factory = new Function(
    'valScatterChart', 'getVariantColor', 'getVariantLabel', 'renderRegressionScatter',
    `${extract('renderRegressionResults')}\nreturn renderRegressionResults;`
);
const renderRegressionResults = factory(valScatterChart, getVariantColor, getVariantLabel, renderRegressionScatter);

const baseMetrics = {
    mean_r2: 0.7, std_r2: 0.05, mean_rmse: 2.1, std_rmse: 0.3, mean_mae: 1.4, std_mae: 0.2,
};

let passed = 0, failed = 0;
const ok = (cond, msg) => { if (cond) passed++; else { failed++; console.log(`  FAIL: ${msg}`); } };

function rowCells(i) {
    return [...document.querySelectorAll('#val-regression-table tbody tr')[i].querySelectorAll('td')]
        .map(td => td.textContent.trim());
}
function rowHtml(i) {
    return document.querySelectorAll('#val-regression-table tbody tr')[i].innerHTML;
}
const note = () => document.getElementById('val-regression-oor-note');

// --- Case 1: fields present, one clean model, one extrapolating model ----
renderRegressionResults({
    rf_reg: { ...baseMetrics, oor_frac: 0.0, train_range: [0.2, 34.1] },
    mlp_reg: { ...baseMetrics, oor_frac: 0.123, train_range: [0.2, 34.1] },
});

let cells = rowCells(0);
ok(cells.length === 5, 'row has 5 cells (Model, R2, RMSE, MAE, Outside range)');
ok(cells[4] === '0.0%', `clean model shows 0.0% (got ${JSON.stringify(cells[4])})`);
ok(!rowHtml(0).includes('#e0a44b'), 'clean model is not amber');

ok(rowCells(1)[4] === '12.3%', `extrapolating model shows 12.3% (got ${JSON.stringify(rowCells(1)[4])})`);
ok(rowHtml(1).includes('#e0a44b'), 'extrapolating model (>=1%) is amber');

ok(note().style.display !== 'none', 'note is visible when a range is present');
ok(/\[0\.2, 34\.1\]/.test(note().textContent), `note names the training span (got ${JSON.stringify(note().textContent)})`);
ok(/not clamped/i.test(note().textContent), 'note says evaluation scores are not clamped');

// --- Case 2: tiny but non-zero fraction renders as "<0.1%" ---------------
renderRegressionResults({ rf_reg: { ...baseMetrics, oor_frac: 0.0003, train_range: [1, 2] } });
ok(rowCells(0)[4] === '<0.1%', `tiny fraction shows "<0.1%" (got ${JSON.stringify(rowCells(0)[4])})`);

// --- Case 3: older tessera-eval pin, no oor fields ----------------------
renderRegressionResults({ rf_reg: { ...baseMetrics } });
ok(rowCells(0).length === 5, 'still 5 cells without oor fields');
ok(rowCells(0)[4] === '—', `absent fields render as em dash (got ${JSON.stringify(rowCells(0)[4])})`);
ok(note().style.display === 'none', 'note is hidden when no model reported a range');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
