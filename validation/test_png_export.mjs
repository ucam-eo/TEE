#!/usr/bin/env node
/**
 * PNG export helpers for the validation charts / confusion matrix
 * (Keshav, 2026-09-02).
 *
 * linkedom has no <canvas> 2d context, so this only covers the
 * plumbing that doesn't need one: the filename stamp and the
 * "nothing to export" guards. The actual pixel drawing is exercised
 * by hand in the browser.
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
    [extract('_pngName'), extract('_downloadCanvasPng'), extract('downloadChartAsPng'), extract('downloadConfusionMatrixPng')].join('\n\n') +
    '\nreturn { _pngName, downloadChartAsPng, downloadConfusionMatrixPng };'
);
const api = factory();

let passed = 0, failed = 0;
const ok = (c, m) => { if (c) passed++; else { failed++; console.log(`  FAIL: ${m}`); } };

const fn = api._pngName('learning_curve');
ok(/^learning_curve_\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.png$/.test(fn), `_pngName shape: ${fn}`);

ok(api.downloadChartAsPng(null, 'x.png') === false, 'downloadChartAsPng(null) -> false, no throw');
ok(api.downloadChartAsPng({}, 'x.png') === false, 'downloadChartAsPng(chart without canvas) -> false');
ok(api.downloadConfusionMatrixPng([], [], 'x.png') === false, 'downloadConfusionMatrixPng([]) -> false');

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
