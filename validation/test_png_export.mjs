#!/usr/bin/env node
/**
 * PNG + CSV export helpers for the validation charts / confusion matrix
 * (Keshav, 2026-09-02).
 *
 * linkedom has no <canvas> 2d context, so the PNG side only covers the
 * plumbing that doesn't need one (filename stamp, "nothing to export"
 * guards). The CSV row-builders are pure functions and fully checked here.
 *
 * Run:  node validation/test_png_export.mjs
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

const { document } = parseHTML('<!DOCTYPE html><html><body></body></html>');
global.document = document;

const factory = new Function(
    'const PNG_EXPORT_SCALE = 3;\n' +
    'let currentLargeAreaTask = "classification";\n' +
    'globalThis.__setTask = t => { currentLargeAreaTask = t; };\n' +
    [
        extract('_pngName'),
        extract('_downloadCanvasPng'),
        extract('downloadChartAsPng'),
        extract('downloadConfusionMatrixPng'),
        extract('_csvEscape'),
        extract('learningCurveCsvRows'),
        extract('regressionCsv'),
        extract('confusionMatrixCsvRows'),
    ].join('\n\n') +
    '\nreturn { _pngName, downloadChartAsPng, downloadConfusionMatrixPng, _csvEscape,' +
    ' learningCurveCsvRows, regressionCsv, confusionMatrixCsvRows };'
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };

// --- PNG plumbing ---------------------------------------------------
const fn = api._pngName('learning_curve');
ok(/^learning_curve_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.png$/.test(fn), `_pngName shape: ${fn}`);
ok(api.downloadChartAsPng(null, 'x.png') === false, 'downloadChartAsPng(null) -> false, no throw');
ok(api.downloadChartAsPng({}, 'x.png') === false, 'downloadChartAsPng(chart without canvas) -> false');
ok(api.downloadConfusionMatrixPng([], [], 'x.png') === false, 'downloadConfusionMatrixPng([]) -> false');

// --- _csvEscape --------------------------------------------------
ok(api._csvEscape('plain') === 'plain', 'csv: plain value untouched');
ok(api._csvEscape('a,b') === '"a,b"', 'csv: comma quoted');
ok(api._csvEscape('he said "hi"') === '"he said ""hi"""', 'csv: quotes doubled');
ok(api._csvEscape(null) === '', 'csv: null -> empty');

// --- learningCurveCsvRows: learning curve (classification) --------
globalThis.__setTask('classification');
let rows = api.learningCurveCsvRows({
    classifiers: {
        rf: { mean_f1: [0.5, 0.8], std_f1: [0.1, 0.05], mean_f1w: [0.55, 0.82], std_f1w: [0.1, 0.04], _x: [1.0, 10.0] },
    },
    training_pcts: [1, 10],
});
ok(rows[0].join(',') === 'training_pct,model,mean_f1,std_f1,mean_f1w,std_f1w', 'lc header');
ok(rows.length === 3, 'lc: header + 2 points');
ok(rows[1][0] === '1.000' && rows[1][1] === 'rf' && rows[1][2] === 0.5, 'lc first point row');
ok(rows[2][0] === '10.000' && rows[2][2] === 0.8, 'lc second point uses _x');

// --- learningCurveCsvRows: k-fold (regression) -----------------
globalThis.__setTask('regression');
rows = api.learningCurveCsvRows({
    _mode: 'kfold',
    classifiers: { rf_reg: {} },
    _foldResults: [
        { fold: 1, models: { rf_reg: { r2: 0.7, rmse: 1.2, mae: 0.9 } } },
        { fold: 2, models: { rf_reg: { r2: 0.72, rmse: 1.1, mae: 0.85 } } },
    ],
    aggregate: { rf_reg: { mean_r2: 0.71, std_r2: 0.01, mean_rmse: 1.15, std_rmse: 0.05, mean_mae: 0.875, std_mae: 0.025 } },
});
ok(rows[0].join(',') === 'fold,model,r2,rmse,mae', 'kfold fold header');
ok(rows[1][0] === 1 && rows[1][2] === 0.7, 'kfold fold 1 row');
const summaryIdx = rows.findIndex(r => r[0] === 'summary');
ok(summaryIdx > 0, 'kfold has a summary block');
ok(rows[summaryIdx].join(',') === 'summary,model,mean_r2,std_r2,mean_rmse,std_rmse,mean_mae,std_mae', 'kfold summary header');
ok(rows[summaryIdx + 1][0] === 'mean±std' && rows[summaryIdx + 1][2] === 0.71, 'kfold aggregate row');

// --- regressionCsv: scatter present ---------------------------
let out = api.regressionCsv({ rf_reg: { scatter: { y_true: [1, 2], y_pred: [1.1, 1.9] } } });
ok(out.stem === 'regression_scatter', 'regressionCsv picks scatter when present');
ok(out.rows[0].join(',') === 'model,y_true,y_pred', 'scatter header');
ok(out.rows.length === 3 && out.rows[1][1] === 1 && out.rows[2][2] === 1.9, 'scatter rows');

// --- regressionCsv: fall back to metrics ---------------------
out = api.regressionCsv({ rf_reg: { mean_r2: 0.8, std_r2: 0.02, mean_rmse: 1, std_rmse: 0.1, mean_mae: 0.7, std_mae: 0.05, oor_frac: 0.03, train_range: [0, 42] } });
ok(out.stem === 'regression_metrics', 'regressionCsv falls back to metrics with no scatter');
ok(out.rows[0].includes('outside_range_frac'), 'metrics header has oor column');
ok(out.rows[1][0] === 'rf_reg' && out.rows[1][7] === 0.03 && out.rows[1][9] === 42, 'metrics row');

// --- confusionMatrixCsvRows ---------------------------------
rows = api.confusionMatrixCsvRows([[5, 1], [2, 8]], ['oak', 'pine']);
ok(rows[0].join(',') === 'actual\\predicted,oak,pine', 'cm header row');
ok(rows[1].join(',') === 'oak,5,1' && rows[2].join(',') === 'pine,2,8', 'cm data rows are raw counts');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
