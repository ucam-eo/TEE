// evaluation.js — Validation panel: shapefile upload, NDJSON streaming,
// learning curve charts, confusion matrix, model download.
// Extracted from viewer.html as an ES module.

// ── State ──

let valChart = null;
let valFieldData = null;
let valGeoJsonLayer = null;
let valGeoJsonData = null;

const CLASS_PALETTE = [
    '#e6194b', '#3cb44b', '#ffe119', '#4363d8', '#f58231',
    '#911eb4', '#42d4f4', '#f032e6', '#bfef45', '#fabed4',
    '#469990', '#dcbeff', '#9A6324', '#fffac8', '#800000',
    '#aaffc3', '#808000', '#ffd8b1', '#000075', '#a9a9a9',
];

const CLASSIFIER_COLORS = {
    nn:          { line: 'rgba(255, 159, 64, 1)',  fill: 'rgba(255, 159, 64, 0.15)' },
    rf:          { line: 'rgba(75, 192, 192, 1)',  fill: 'rgba(75, 192, 192, 0.15)' },
    xgboost:     { line: 'rgba(153, 102, 255, 1)', fill: 'rgba(153, 102, 255, 0.15)' },
    mlp:         { line: 'rgba(255, 99, 132, 1)',  fill: 'rgba(255, 99, 132, 0.15)' },
    spatial_mlp: { line: 'rgba(54, 162, 235, 1)',  fill: 'rgba(54, 162, 235, 0.15)' },
    spatial_mlp_5x5: { line: 'rgba(255, 206, 86, 1)', fill: 'rgba(255, 206, 86, 0.15)' },
    unet:            { line: 'rgba(0, 200, 83, 1)',   fill: 'rgba(0, 200, 83, 0.15)' },
};
const CLASSIFIER_LABELS = { nn: 'k-NN', rf: 'Random Forest', xgboost: 'XGBoost', mlp: 'MLP', spatial_mlp: 'Spatial MLP (3\u00d73)', spatial_mlp_5x5: 'Spatial MLP (5\u00d75)', unet: 'U-Net' };

let evalAbortController = null;
let lastChartData = null;
let streamDatasetMap = {};
let lastEvalData = null;
let cmShowPct = false;
let cmPopupWindow = null;
let valUploadedFilename = null;
let currentLargeAreaTask = null; // 'classification' or 'regression'
let valUploadedFiles = []; // list of uploaded filenames
let valTotalLabelledPixels = 0; // set by start event, used for % hint

// Regressor labels/colors (extend the classifier palette)
const REGRESSOR_COLORS = {
    nn_reg:      { line: 'rgba(255, 159, 64, 1)',  fill: 'rgba(255, 159, 64, 0.15)' },
    rf_reg:      { line: 'rgba(75, 192, 192, 1)',  fill: 'rgba(75, 192, 192, 0.15)' },
    xgboost_reg: { line: 'rgba(153, 102, 255, 1)', fill: 'rgba(153, 102, 255, 0.15)' },
    mlp_reg:     { line: 'rgba(255, 99, 132, 1)',  fill: 'rgba(255, 99, 132, 0.15)' },
};
const REGRESSOR_LABELS = { nn_reg: 'k-NN (Reg)', rf_reg: 'Random Forest (Reg)', xgboost_reg: 'XGBoost (Reg)', mlp_reg: 'MLP (Reg)' };

// Merge into lookup objects
Object.assign(CLASSIFIER_COLORS, REGRESSOR_COLORS);
Object.assign(CLASSIFIER_LABELS, REGRESSOR_LABELS);

// ── Helper functions ──

function buildClassColorMap(geojson, fieldName) {
    const unique = [...new Set(
        geojson.features
            .map(f => f.properties[fieldName])
            .filter(v => v != null)
    )].sort();
    const map = {};
    unique.forEach((cls, i) => { map[cls] = CLASS_PALETTE[i % CLASS_PALETTE.length]; });
    return map;
}

function addValGeoJsonLayer() {
    const maps = window.maps;
    if (valGeoJsonLayer && maps.rgb) maps.rgb.removeLayer(valGeoJsonLayer);
    if (!valGeoJsonData || !maps.rgb) return;
    const fieldName = document.getElementById('val-field-select').value;
    const colorMap = fieldName ? buildClassColorMap(valGeoJsonData, fieldName) : {};
    valGeoJsonLayer = L.geoJSON(valGeoJsonData, {
        style: function() {
            return { color: '#ff0000', weight: 1.5, fillOpacity: 0.15, fillColor: '#ff0000' };
        },
        onEachFeature: function(feature, layer) {
            if (fieldName && feature.properties[fieldName] != null) {
                layer.bindTooltip(String(feature.properties[fieldName]), {
                    sticky: true, className: 'val-tooltip',
                });
            }
        },
    }).addTo(maps.rgb);

    // Remove viewport bounds constraint and zoom to shapefile extent
    const bounds = valGeoJsonLayer.getBounds();
    if (bounds.isValid()) {
        // Unlock maps from viewport bounds so we can pan to the shapefile
        Object.values(maps).forEach(m => {
            if (m && m.setMaxBounds) {
                m.setMaxBounds(null);
                m.setMinZoom(2);
            }
        });
        maps.rgb.fitBounds(bounds, { padding: [20, 20] });
    }
}

// ── Drop zone ──

const dropZone = document.getElementById('val-drop-zone');
const fileInput = document.getElementById('val-file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));

dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) uploadShapefile(file);
});
fileInput.addEventListener('change', () => {
    if (fileInput.files[0]) uploadShapefile(fileInput.files[0]);
});

// ── Upload shapefile ──

async function uploadShapefile(file) {
    const status = document.getElementById('val-status');
    status.textContent = 'Uploading...';
    status.style.color = '#888';
    dropZone.classList.remove('uploaded');

    const formData = new FormData();
    formData.append('file', file);

    try {
        // Clear previous shapefiles before uploading
        await fetch('/api/evaluation/clear-shapefiles', { method: 'POST' });

        const resp = await fetch('/api/evaluation/upload-shapefile', { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            status.textContent = data.error || 'Upload failed';
            status.style.color = '#dc3545';
            return;
        }

        valFieldData = data.fields;
        valUploadedFilename = file.name;
        dropZone.textContent = file.name;
        dropZone.classList.add('uploaded');

        const sel = document.getElementById('val-field-select');
        sel.innerHTML = '';
        data.fields.forEach(f => {
            const opt = document.createElement('option');
            opt.value = f.name;
            opt.textContent = `${f.name} (${f.unique_count} classes)`;
            sel.appendChild(opt);
        });
        sel.disabled = false;
        document.getElementById('val-run-btn').disabled = false;
        status.textContent = `${data.fields.length} fields found`;
        status.style.color = '#28a745';

        valGeoJsonData = data.geojson;
        addValGeoJsonLayer();
        updateClassSummary();
        updateYearCoverage(data.geojson);

    } catch (e) {
        const msg = e.message || String(e);
        if (msg.includes('string did not match') || msg.includes('Failed to fetch')) {
            status.textContent = 'Upload failed — is the compute server running? (tee-compute on port 8002)';
        } else {
            status.textContent = 'Upload error: ' + msg;
        }
        status.style.color = '#dc3545';
    }
}

// ── Field selection ──

document.getElementById('val-field-select').addEventListener('change', updateClassSummary);

function updateClassSummary() {
    const fieldName = document.getElementById('val-field-select').value;
    const summary = document.getElementById('val-class-summary');
    if (!valFieldData || !fieldName) { summary.textContent = ''; return; }
    const field = valFieldData.find(f => f.name === fieldName);
    if (field) {
        const nonNull = field.non_null !== undefined ? ` (${field.non_null}/${field.total} polygons)` : '';
        summary.textContent = `${field.unique_count} classes${nonNull} \u2014 samples: ${field.samples.slice(0, 5).join(', ')}`;

        // Show class names in panel 1 table from GeoJSON (pixel counts come later from evaluation)
        if (valGeoJsonData && valGeoJsonData.features) {
            const classNames = [...new Set(
                valGeoJsonData.features
                    .map(f => f.properties[fieldName])
                    .filter(v => v != null)
            )].sort();
            // Count features per class
            const featureCounts = {};
            valGeoJsonData.features.forEach(f => {
                const v = f.properties[fieldName];
                if (v != null) featureCounts[v] = (featureCounts[v] || 0) + 1;
            });
            const classData = classNames.map(n => ({ name: String(n), pixels: featureCounts[n] }));
            populateValClassTable(classNames.map(String), classData, false);
        }
    }
    addValGeoJsonLayer();
}

async function updateYearCoverage(geojson) {
    if (!geojson || !geojson.features || geojson.features.length === 0) return;

    // Compute bbox from GeoJSON features
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    for (const f of geojson.features) {
        if (!f.geometry || !f.geometry.coordinates) continue;
        const coords = JSON.stringify(f.geometry.coordinates);
        const nums = coords.match(/-?\d+\.?\d*/g);
        if (!nums) continue;
        for (let i = 0; i < nums.length - 1; i += 2) {
            const lon = parseFloat(nums[i]), lat = parseFloat(nums[i + 1]);
            if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
                minLon = Math.min(minLon, lon);
                minLat = Math.min(minLat, lat);
                maxLon = Math.max(maxLon, lon);
                maxLat = Math.max(maxLat, lat);
            }
        }
    }
    if (!isFinite(minLon)) return;

    try {
        const resp = await fetch('/api/viewports/embedding-coverage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbox: [minLon, minLat, maxLon, maxLat] }),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const coverage = data.coverage || {};

        const sel = document.getElementById('val-year-select');
        const currentVal = sel.value;
        Array.from(sel.options).forEach(opt => {
            const tiles = coverage[opt.value] || 0;
            opt.disabled = tiles === 0;
            opt.textContent = tiles > 0 ? `${opt.value} (${tiles} tiles)` : `${opt.value} (no coverage)`;
        });
        // If current selection has no coverage, pick the first available
        if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) {
            const first = Array.from(sel.options).find(o => !o.disabled);
            if (first) sel.value = first.value;
        }
    } catch (e) {
        console.warn('Failed to check year coverage:', e);
    }
}

async function fetchClassPixelCounts(fieldName) {
    const panel = document.getElementById('val-class-table-panel');
    const table = document.getElementById('val-class-table');
    const placeholder = panel.querySelector('.val-class-placeholder');
    placeholder.textContent = 'Counting pixels...';
    placeholder.style.display = '';
    table.style.display = 'none';

    try {
        const resp = await fetch('/api/evaluation/class-counts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                viewport: window.currentViewportName,
                year: window.currentEmbeddingYear,
                field: fieldName,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            placeholder.textContent = data.error || 'Failed to count pixels';
            return;
        }
        if (data.classes && data.classes.length > 0) {
            const names = data.classes.map(c => c.name);
            populateValClassTable(names, data.classes);
        } else {
            placeholder.textContent = 'No pixels overlap with shapefile';
        }
    } catch (e) {
        placeholder.textContent = 'Error: ' + e.message;
    }
}

function populateValClassTable(classNames, classData, isPixelCounts) {
    const panel = document.getElementById('val-class-table-panel');
    const table = document.getElementById('val-class-table');
    const placeholder = panel.querySelector('.val-class-placeholder');
    if (!panel || !table) return;

    if (!classNames || classNames.length === 0) {
        placeholder.style.display = '';
        table.style.display = 'none';
        return;
    }

    const tbody = table.querySelector('tbody');
    tbody.innerHTML = '';

    const countMap = {};
    if (classData) {
        for (const c of classData) {
            countMap[c.name] = c.pixels;
        }
    }

    const header = document.getElementById('val-class-count-header');
    if (header) header.textContent = isPixelCounts ? 'Pixels' : 'Polygons';

    classNames.sort((a, b) => (countMap[b] || 0) - (countMap[a] || 0));

    for (const name of classNames) {
        const tr = document.createElement('tr');
        const count = countMap[name];
        const td1 = document.createElement('td');
        td1.textContent = name;
        if (isPixelCounts && count !== undefined && count < 50) {
            const note = document.createElement('span');
            note.className = 'val-class-excluded';
            note.textContent = ' (<50 px, excluded)';
            td1.appendChild(note);
        }
        const td2 = document.createElement('td');
        td2.textContent = count !== undefined ? count.toLocaleString() : '\u2014';
        tr.appendChild(td1);
        tr.appendChild(td2);
        tbody.appendChild(tr);
    }

    placeholder.style.display = 'none';
    table.style.display = '';
}

// ── Run evaluation (streaming NDJSON) ──

document.getElementById('val-run-btn').addEventListener('click', runEvaluation);

function createStreamChart(classifierNames) {
    const ctx = document.getElementById('val-chart').getContext('2d');
    if (valChart) valChart.destroy();

    const datasets = [];
    streamDatasetMap = {};
    classifierNames.forEach(name => {
        const color = CLASSIFIER_COLORS[name] || { line: '#888', fill: 'rgba(136,136,136,0.15)' };
        const baseIdx = datasets.length;
        streamDatasetMap[name] = baseIdx;

        datasets.push({
            label: CLASSIFIER_LABELS[name] || name,
            data: [],
            borderColor: color.line,
            backgroundColor: 'transparent',
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: color.line,
            tension: 0.3,
        });
        datasets.push({
            label: name + '_upper',
            data: [],
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            pointRadius: 0,
            fill: false,
        });
        datasets.push({
            label: name + '_lower',
            data: [],
            borderColor: 'transparent',
            backgroundColor: color.fill,
            pointRadius: 0,
            fill: '-1',
        });
    });

    const metric = document.getElementById('val-metric-select').value;
    const metricLabel = metric === 'weighted' ? 'Weighted F1' : 'Macro F1';

    valChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 300 },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: '#ddd',
                        filter: item => !item.text.includes('_upper') && !item.text.includes('_lower'),
                    },
                },
                title: {
                    display: true,
                    text: `Learning Curves \u2014 ${metricLabel} vs % Labels`,
                    color: '#eee',
                    font: { size: 15, weight: 'bold' },
                },
            },
            scales: {
                x: {
                    type: 'linear',
                    title: { display: true, text: '% of labels', color: '#aaa' },
                    ticks: { color: '#aaa', callback: v => v + '%' },
                    min: 0, max: 100,
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    min: 0, max: 1,
                    title: { display: true, text: metricLabel, color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
            },
        },
    });
}

function showFinishButtons(classifierNames) {
    const container = document.getElementById('val-finish-btns');
    container.innerHTML = '';
    container.style.display = 'flex';
    classifierNames.forEach(name => {
        const color = CLASSIFIER_COLORS[name] || { line: '#888' };
        const btn = document.createElement('button');
        btn.id = 'finish-' + name;
        btn.textContent = 'Finish ' + (CLASSIFIER_LABELS[name] || name);
        btn.style.cssText = `padding:4px 12px;border:1px solid ${color.line};border-radius:12px;background:transparent;color:${color.line};font-size:12px;cursor:pointer;`;
        btn.onclick = () => {
            fetch('/api/evaluation/finish-classifier', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({classifier: name}),
            });
            btn.disabled = true;
            btn.textContent = 'Finishing...';
            btn.style.opacity = '0.6';
            btn.style.cursor = 'default';
        };
        container.appendChild(btn);
    });
}

function hideFinishButtons() {
    const container = document.getElementById('val-finish-btns');
    container.style.display = 'none';
    container.innerHTML = '';
}

function handleStreamEvent(ev) {
    const status = document.getElementById('val-status');
    const metric = document.getElementById('val-metric-select').value;
    const meanKey = metric === 'weighted' ? 'mean_f1w' : 'mean_f1';
    const stdKey = metric === 'weighted' ? 'std_f1w' : 'std_f1';

    if (ev.event === 'start') {
        lastChartData = {
            training_pcts: [],
            _plannedPcts: ev.training_pcts || [],
            classifiers: {},
            classes: ev.classes,
            total_labelled_pixels: ev.total_labelled_pixels,
            confusion_matrix_labels: ev.confusion_matrix_labels,
            confusion_matrices: null,
            models_available: [],
        };
        ev.classifiers.forEach(name => {
            lastChartData.classifiers[name] = {
                mean_f1: [], std_f1: [], mean_f1w: [], std_f1w: [],
            };
        });
        createStreamChart(ev.classifiers);
        // Show results table in panel 3 with progress
        {
            const pixels = ev.total_labelled_pixels || 0;
            valTotalLabelledPixels = pixels;
            updateMaxTrainPctHint();
            const stats = ev.stats || {};
            initResultsTable(ev.classifiers, currentLargeAreaTask || 'classification');
            setResultsStatus(
                `${pixels.toLocaleString()} labelled pixels from ${stats.tiles_with_data || '?'}/${stats.tile_count || '?'} tiles. Running learning curve...`
            );
        }

        if (ev.classes) {
            const names = ev.classes.map(c => c.name);
            populateValClassTable(names, ev.classes, true);
        }

    } else if (ev.event === 'progress') {
        lastChartData.training_pcts.push(ev.pct);

        for (const [name, vals] of Object.entries(ev.classifiers)) {
            const acc = lastChartData.classifiers[name];
            if (!acc) continue;
            acc.mean_f1.push(vals.mean_f1);
            acc.std_f1.push(vals.std_f1);
            acc.mean_f1w.push(vals.mean_f1w);
            acc.std_f1w.push(vals.std_f1w);

            const baseIdx = streamDatasetMap[name];
            if (baseIdx !== undefined && valChart) {
                const mean = vals[meanKey];
                const std = vals[stdKey];
                valChart.data.datasets[baseIdx].data.push({ x: ev.pct, y: mean });
                valChart.data.datasets[baseIdx + 1].data.push({ x: ev.pct, y: Math.min(1, mean + std) });
                valChart.data.datasets[baseIdx + 2].data.push({ x: ev.pct, y: Math.max(0, mean - std) });
            }
        }
        if (valChart) valChart.update();
        // Show completed pct and what's next
        const planned = lastChartData._plannedPcts || [];
        const doneIdx = planned.indexOf(ev.pct);
        if (doneIdx >= 0 && doneIdx < planned.length - 1) {
            status.textContent = `Done ${ev.pct}%, training ${planned[doneIdx + 1]}%...`;
        } else {
            status.textContent = `${ev.pct}% complete`;
        }
        appendResultsRow(ev.pct, ev.classifiers);
        const elapsed = status.dataset.t0 ? ((Date.now() - parseInt(status.dataset.t0)) / 1000).toFixed(0) : '';
        setResultsStatus(`${ev.pct}% of labels complete (${elapsed}s)`);

    } else if (ev.event === 'model_ready') {
        const btn = document.getElementById('finish-' + ev.classifier);
        if (btn) {
            btn.textContent = '\u2713 Saved';
            btn.disabled = true;
            btn.style.opacity = '1';
            btn.style.borderColor = '#28a745';
            btn.style.color = '#28a745';
        }
        if (!lastChartData.models_available.includes(ev.classifier)) {
            lastChartData.models_available.push(ev.classifier);
        }

    } else if (ev.event === 'confusion_matrices') {
        lastChartData.confusion_matrices = ev.confusion_matrices;
        renderConfusionMatrix(lastChartData);


    } else if (ev.event === 'done') {
        if (!lastChartData) return;
        lastChartData.elapsed_seconds = ev.elapsed_seconds;
        lastChartData.models_available = ev.models_available || [];
        const pixels = lastChartData.total_labelled_pixels || 0;
        const nClasses = (lastChartData.classes || []).length;
        const suffix = nClasses > 0
            ? ` \u2014 ${pixels.toLocaleString()} pixels, ${nClasses} classes`
            : ` \u2014 ${pixels.toLocaleString()} pixels`;
        status.textContent = `Done in ${ev.elapsed_seconds}s${suffix}`;
        status.style.color = '#28a745';
        const dlBtnH = document.getElementById('val-download-btn');
        const modelsReady = !!(ev.models_available && ev.models_available.length);
        if (dlBtnH) dlBtnH.disabled = !modelsReady;
        hideFinishButtons();


    } else if (ev.event === 'status') {
        status.dataset.updated = '1';
        showResultsPanel(ev.message);

    } else if (ev.event === 'error') {
        status.textContent = ev.message || 'Evaluation error';
        status.style.color = '#dc3545';

    // ── Large-area events ──

    } else if (ev.event === 'download_progress') {
        status.dataset.updated = '1';
        const elapsed = status.dataset.t0 ? ((Date.now() - parseInt(status.dataset.t0)) / 1000).toFixed(0) : '';
        const suffix = elapsed ? ` (${elapsed}s)` : '';
        const verb = ev.cached ? 'Loading cached' : 'Downloading';
        status.textContent = `${verb} tile ${ev.tile} / ${ev.total}${suffix}`;
        showResultsPanel(`${verb} tile ${ev.tile} / ${ev.total}...`);

    } else if (ev.event === 'field_start') {
        currentLargeAreaTask = ev.type;
        status.dataset.updated = '1';
        status.textContent = `Loading GeoTessera tile index...`;
        showResultsPanel(`Loading embeddings for ${ev.field} (${ev.type})...`);

    } else if (ev.event === 'fold_result') {
        status.textContent = `Fold ${ev.fold} complete`;
        if (lastChartData) {
            if (!lastChartData._foldResults) lastChartData._foldResults = [];
            lastChartData._foldResults.push(ev);
        }

    } else if (ev.event === 'aggregate') {
        if (lastChartData) {
            lastChartData.aggregate = ev.models;
        }
        if (currentLargeAreaTask === 'regression') {
            renderRegressionResults(ev.models);
            renderRegressionBarChart(ev.models);
        } else if (currentLargeAreaTask === 'classification') {
            renderClassificationBarChart(ev.models);
        }
    }
}

async function runEvaluation() {
    return runLargeAreaEvaluation();
}

async function readNdjsonStream(resp, resetButtons) {
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
            if (!line.trim()) continue;
            try {
                handleStreamEvent(JSON.parse(line));
            } catch (parseErr) {
                console.warn('NDJSON parse error:', parseErr, line);
            }
        }
    }
    if (buffer.trim()) {
        try { handleStreamEvent(JSON.parse(buffer)); } catch(e) {}
    }
    if (resetButtons) resetButtons();
}

// ── Chart rendering (full rebuild, used by metric toggle) ──

function renderChart(data, metric) {
    lastChartData = data;
    if (!metric) metric = document.getElementById('val-metric-select').value;
    const firstClf = Object.values(data.classifiers)[0];
    const hasWeighted = firstClf && firstClf.mean_f1w;
    const isWeighted = metric === 'weighted' && hasWeighted;
    if (metric === 'weighted' && !hasWeighted) {
        document.getElementById('val-metric-select').value = 'macro';
    }
    const meanKey = isWeighted ? 'mean_f1w' : 'mean_f1';
    const stdKey = isWeighted ? 'std_f1w' : 'std_f1';
    const metricLabel = isWeighted ? 'Weighted F1' : 'Macro F1';

    const ctx = document.getElementById('val-chart').getContext('2d');

    if (valChart) valChart.destroy();

    const datasets = [];
    streamDatasetMap = {};
    for (const [name, values] of Object.entries(data.classifiers)) {
        const color = CLASSIFIER_COLORS[name] || { line: '#888', fill: 'rgba(136,136,136,0.15)' };
        streamDatasetMap[name] = datasets.length;

        datasets.push({
            label: CLASSIFIER_LABELS[name] || name,
            data: data.training_pcts.map((x, i) => ({ x, y: values[meanKey][i] })),
            borderColor: color.line,
            backgroundColor: 'transparent',
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: color.line,
            tension: 0.3,
        });

        datasets.push({
            label: name + '_upper',
            data: data.training_pcts.map((x, i) => ({
                x, y: Math.min(1, values[meanKey][i] + values[stdKey][i])
            })),
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            pointRadius: 0,
            fill: false,
        });

        datasets.push({
            label: name + '_lower',
            data: data.training_pcts.map((x, i) => ({
                x, y: Math.max(0, values[meanKey][i] - values[stdKey][i])
            })),
            borderColor: 'transparent',
            backgroundColor: color.fill,
            pointRadius: 0,
            fill: '-1',
        });
    }

    valChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: '#ddd',
                        filter: item => !item.text.includes('_upper') && !item.text.includes('_lower'),
                    },
                },
                title: {
                    display: true,
                    text: `Learning Curves \u2014 ${metricLabel} vs % Labels`,
                    color: '#eee',
                    font: { size: 15, weight: 'bold' },
                },
            },
            scales: {
                x: {
                    type: 'linear',
                    title: { display: true, text: '% of labels', color: '#aaa' },
                    ticks: { color: '#aaa', callback: v => v + '%' },
                    min: 0, max: 100,
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    min: 0, max: 1,
                    title: { display: true, text: metricLabel, color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
            },
        },
    });
}

document.getElementById('val-metric-select').addEventListener('change', function() {
    if (lastChartData) renderChart(lastChartData, this.value);
});

// ── Confusion Matrix ──

function renderConfusionMatrix(data) {
    lastEvalData = data;
    const dlBtnH = document.getElementById('val-download-btn');
    const modelsReady = !!(data.models_available && data.models_available.length);
    if (dlBtnH) dlBtnH.disabled = !modelsReady;
    const panel = document.getElementById('val-cm-panel');
    const sel = document.getElementById('cm-classifier-select');
    const scroll = panel.querySelector('.cm-scroll');
    const note = document.getElementById('cm-filtered-note');

    if (!data.confusion_matrices) {
        scroll.innerHTML = '<div class="cm-placeholder">No confusion matrix data available.</div>';
        note.style.display = 'none';
        return;
    }

    const cmLabels = data.confusion_matrix_labels || [];
    const allClasses = data.classes || [];
    const filtered = allClasses.filter(c => !cmLabels.includes(c.name));
    if (filtered.length > 0) {
        const names = filtered.map(c => `${c.name} (${c.pixels}px)`).join(', ');
        note.textContent = `${filtered.length} class${filtered.length > 1 ? 'es' : ''} excluded (<50 pixels): ${names}`;
        note.style.display = 'block';
    } else {
        note.style.display = 'none';
    }

    const names2 = Object.keys(data.confusion_matrices);
    sel.innerHTML = '';
    names2.forEach(n => {
        const opt = document.createElement('option');
        opt.value = n;
        opt.textContent = CLASSIFIER_LABELS[n] || n;
        sel.appendChild(opt);
    });
    sel.style.display = names2.length > 1 ? '' : 'none';
    sel.onchange = () => renderCMTable(sel.value, data);

    renderCMTable(names2[0], data);
}

function buildCMTableHTML(cm, labels, showPct, forPopup) {
    const n = cm.length;
    const compact = n > 8 && !forPopup;
    const rowSums = cm.map(row => row.reduce((a, b) => a + b, 0));

    let html = '<div class="cm-wrapper">';
    html += '<div class="cm-axis-label y-axis">Actual</div>';
    html += '<div class="cm-axis-label x-axis">Predicted</div>';
    html += '<table class="confusion-matrix">';

    html += '<tr><th></th>';
    for (let j = 0; j < n; j++) {
        const lbl = labels[j] || `C${j}`;
        html += `<th class="cm-col-label" title="${lbl}">${compact ? lbl.slice(0, 4) : lbl}</th>`;
    }
    html += '</tr>';

    for (let i = 0; i < n; i++) {
        const rowLabel = labels[i] || `C${i}`;
        html += `<tr><th class="cm-row-label" title="${rowLabel}">${compact ? rowLabel.slice(0, 6) : rowLabel}</th>`;
        for (let j = 0; j < n; j++) {
            const count = cm[i][j];
            const pct = rowSums[i] > 0 ? (count / rowSums[i] * 100) : 0;
            const isDiag = i === j;

            const intensity = Math.min(pct / 100, 1);
            let bg;
            if (isDiag) {
                bg = `rgba(40, 167, 69, ${0.15 + intensity * 0.7})`;
            } else {
                bg = intensity > 0.01 ? `rgba(220, 53, 69, ${0.1 + intensity * 0.6})` : 'transparent';
            }

            const textColor = intensity > 0.5 ? '#fff' : '#ccc';
            const tip = `Actual: ${rowLabel}\nPredicted: ${labels[j] || `C${j}`}\n${count} (${pct.toFixed(1)}%)`;
            const cellText = showPct ? `${pct.toFixed(1)}%` : count;

            if (compact) {
                html += `<td style="background:${bg};color:${textColor}" data-tip="${tip}"><span class="cm-count">${cellText}</span></td>`;
            } else {
                const secondary = showPct ? count : `${pct.toFixed(1)}%`;
                html += `<td style="background:${bg};color:${textColor}" data-tip="${tip}"><span class="cm-count">${cellText}</span><span class="cm-pct">${secondary}</span></td>`;
            }
        }
        html += '</tr>';
    }
    html += '</table></div>';
    return html;
}

function openCMPopup(classifierName, data) {
    const cm = data.confusion_matrices[classifierName];
    const labels = data.confusion_matrix_labels || [];
    if (!cm) return;

    // Create a full-screen modal overlay instead of window.open (blocked by popup blockers)
    let overlay = document.getElementById('cm-modal-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'cm-modal-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.85);z-index:10000;display:flex;align-items:center;justify-content:center;';
        overlay.addEventListener('click', e => { if (e.target === overlay) overlay.style.display = 'none'; });
        document.body.appendChild(overlay);
    }

    const tableHTML = buildCMTableHTML(cm, labels, cmShowPct, true);
    const classifierLabel = CLASSIFIER_LABELS[classifierName] || classifierName;

    overlay.innerHTML = `
        <div style="background:#1a1a2e; border-radius:8px; padding:20px; max-width:90vw; max-height:90vh; overflow:auto; position:relative;">
            <button onclick="document.getElementById('cm-modal-overlay').style.display='none'"
                style="position:absolute;top:8px;right:12px;background:none;border:none;color:#888;font-size:20px;cursor:pointer;">&times;</button>
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
                <h3 style="margin:0;color:#eee;font-size:15px;">Confusion Matrix</h3>
                <select id="modal-cm-select" style="background:#2a2a3e;color:#ccc;border:1px solid #444;border-radius:4px;padding:4px 10px;font-size:13px;"></select>
                <button id="modal-cm-toggle" style="background:#2a2a3e;color:#ccc;border:1px solid #444;border-radius:4px;padding:4px 10px;font-size:13px;cursor:pointer;">${cmShowPct ? '#' : '%'}</button>
            </div>
            <div id="modal-cm-scroll">${tableHTML}</div>
        </div>`;
    overlay.style.display = 'flex';

    // Wire up classifier selector
    const sel = document.getElementById('modal-cm-select');
    Object.keys(data.confusion_matrices).forEach(n => {
        const opt = document.createElement('option');
        opt.value = n;
        opt.textContent = CLASSIFIER_LABELS[n] || n;
        if (n === classifierName) opt.selected = true;
        sel.appendChild(opt);
    });
    sel.style.display = Object.keys(data.confusion_matrices).length > 1 ? '' : 'none';

    let modalShowPct = cmShowPct;
    function refresh() {
        const name = sel.value;
        const cmNow = data.confusion_matrices[name];
        if (!cmNow) return;
        document.getElementById('modal-cm-scroll').innerHTML = buildCMTableHTML(cmNow, labels, modalShowPct, true);
    }
    sel.onchange = refresh;
    document.getElementById('modal-cm-toggle').onclick = function() {
        modalShowPct = !modalShowPct;
        this.textContent = modalShowPct ? '#' : '%';
        refresh();
    };
}

function renderCMTable(classifierName, data) {
    const cm = data.confusion_matrices[classifierName];
    const labels = data.confusion_matrix_labels || [];
    const scroll = document.querySelector('#val-cm-panel .cm-scroll');
    const viewBtn = document.getElementById('cm-view-btn');
    if (!cm) { scroll.innerHTML = '<div class="cm-placeholder">No data.</div>'; if (viewBtn) viewBtn.style.display = 'none'; return; }

    // Always show View button for opening full-size modal
    if (viewBtn) viewBtn.style.display = '';

    scroll.innerHTML = buildCMTableHTML(cm, labels, cmShowPct, false);
}

function exportEvalResults() {
    if (!lastEvalData) return;
    const blob = new Blob([JSON.stringify(lastEvalData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `eval_results_${new Date().toISOString().slice(0, 19).replace(/[:.]/g, '-')}.json`;
    a.click();
    URL.revokeObjectURL(url);
}


async function downloadModels() {
    const dlBtn = document.getElementById('val-download-btn');
    const status = document.getElementById('val-status');

    // First train the models (deferred from evaluation)
    dlBtn.disabled = true;
    dlBtn.textContent = 'Training...';
    status.textContent = 'Training final models for download...';
    status.style.color = '#888';

    try {
        const resp = await fetch('/api/evaluation/train-models', { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            status.textContent = err.error || 'Training failed';
            status.style.color = '#dc3545';
            dlBtn.disabled = false;
            dlBtn.textContent = 'Download Models';
            return;
        }

        // Stream training progress
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        const readyModels = [];

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();
            for (const line of lines) {
                if (!line.trim()) continue;
                try {
                    const ev = JSON.parse(line);
                    if (ev.event === 'status') {
                        status.textContent = ev.message;
                        showResultsPanel(ev.message);
                    } else if (ev.event === 'model_ready') {
                        readyModels.push(ev.classifier);
                    } else if (ev.event === 'done') {
                        // Download all ready models
                        for (const name of readyModels) {
                            const a = document.createElement('a');
                            a.href = `/api/evaluation/download-model/${encodeURIComponent(name)}`;
                            const ext = name === 'unet' ? '.pt' : '.joblib';
                            a.download = `${name}_model${ext}`;
                            a.click();
                        }
                        status.textContent = `${readyModels.length} model(s) trained and downloading`;
                        status.style.color = '#28a745';
                    }
                } catch (e) { }
            }
        }
    } catch (e) {
        status.textContent = 'Training error: ' + e.message;
        status.style.color = '#dc3545';
    }

    dlBtn.disabled = false;
    dlBtn.textContent = 'Download Models';
}
document.getElementById('val-export-btn').addEventListener('click', exportEvalResults);
document.getElementById('val-download-btn').addEventListener('click', downloadModels);

document.getElementById('cm-toggle-pct').addEventListener('click', function() {
    cmShowPct = !cmShowPct;
    this.classList.toggle('active', cmShowPct);
    this.textContent = cmShowPct ? '#' : '%';
    this.title = cmShowPct ? 'Show counts' : 'Show percentages';
    if (lastEvalData && lastEvalData.confusion_matrices) {
        const sel = document.getElementById('cm-classifier-select');
        renderCMTable(sel.value, lastEvalData);
    }
});


function generateConfig() {
    const field = document.getElementById('val-field-select').value;
    if (!field) { alert('Select a field first'); return; }

    const checkboxes = document.querySelectorAll('.val-clf-header input:checked');
    const classifiers = {};
    const regressors = {};
    Array.from(checkboxes).forEach(cb => {
        const name = cb.value;
        // Skip spatial classifiers for large-area mode
        if (name === 'spatial_mlp' || name === 'spatial_mlp_5x5' || name === 'unet') return;
        const params = {};
        document.querySelectorAll(`.val-params input[data-clf="${name}"], .val-params select[data-clf="${name}"]`).forEach(el => {
            const val = el.value.trim();
            if (val === '') return;
            const num = Number(val);
            params[el.dataset.param] = isNaN(num) ? val : num;
        });
        // Guess: if name ends with _reg it's a regressor, otherwise classifier
        if (name.endsWith('_reg')) {
            regressors[name] = params;
        } else {
            classifiers[name] = params;
        }
    });

    const config = {
        "$schema": "tee_evaluate_config_v1",
        "shapefile": valUploadedFilename || "/path/to/ground_truth.zip",
        "fields": [{ "name": field, "type": "auto" }],
        "classifiers": classifiers,
        "regressors": regressors,
        "years": [parseInt(document.getElementById('val-year-select').value) || 2024],
        "max_training_samples": parseInt(document.getElementById('val-max-train-large').value) || 30000,
        "output_dir": "./eval_output",
        "dry_run": false,
        "seed": 42,
    };

    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'eval_config.json';
    a.click();
    URL.revokeObjectURL(url);
}

async function runLargeAreaEvaluation() {
    const field = document.getElementById('val-field-select').value;
    if (!field) return;

    const checkboxes = document.querySelectorAll('.val-clf-header input:checked');
    const classifiers = Array.from(checkboxes).map(cb => cb.value);
    if (classifiers.length === 0) {
        document.getElementById('val-status').textContent = 'Select at least one classifier';
        document.getElementById('val-status').style.color = '#dc3545';
        return;
    }

    const params = {};
    document.querySelectorAll('.val-params input, .val-params select').forEach(el => {
        const clf = el.dataset.clf;
        const param = el.dataset.param;
        if (!clf || !param) return;
        if (!classifiers.includes(clf)) return;
        if (!params[clf]) params[clf] = {};
        const val = el.value.trim();
        if (val === '') return;
        const num = Number(val);
        params[clf][param] = isNaN(num) ? val : num;
    });

    const btn = document.getElementById('val-run-btn');
    const cancelBtn = document.getElementById('val-cancel-btn');
    const status = document.getElementById('val-status');
    btn.disabled = true;
    btn.textContent = 'Running...';
    cancelBtn.style.display = '';
    status.style.color = '#888';
    status.dataset.updated = '';
    status.dataset.t0 = String(Date.now());
    const backBtn = document.getElementById('back-btn');
    if (backBtn) { backBtn.disabled = true; backBtn.style.opacity = '0.4'; }

    lastChartData = null;
    currentLargeAreaTask = null;
    if (valChart) { valChart.destroy(); valChart = null; }
    hideFinishButtons();
    // Reset panel content (visibility controlled by PANEL_LAYOUT, not here)
    document.getElementById('val-results-tbody').innerHTML = '';
    document.getElementById('val-results-status').textContent = '';

    evalAbortController = new AbortController();
    let userCancelled = false;

    const t0 = Date.now();
    showResultsPanel('Waiting for compute server...');
    const timer = setInterval(() => {
        if (!lastChartData) {
            const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
            // Only show generic message if no event has updated the status yet
            if (!status.dataset.updated) {
                status.textContent = `Connecting to GeoTessera... ${elapsed}s`;
                setResultsStatus(`Connecting to GeoTessera... ${elapsed}s`);
            }
        }
    }, 1000);

    function resetButtons() {
        clearInterval(timer);
        btn.disabled = false;
        btn.textContent = 'Run Evaluation';
        cancelBtn.style.display = 'none';
        evalAbortController = null;
        const backBtn = document.getElementById('back-btn');
        if (backBtn) { backBtn.disabled = false; backBtn.style.opacity = ''; }
    }

    cancelBtn.onclick = () => { userCancelled = true; evalAbortController.abort(); };

    try {
        const resp = await fetch('/api/evaluation/run-large-area', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                field: field,
                year: parseInt(document.getElementById('val-year-select').value) || 2024,
                classifiers: classifiers,
                classifier_params: params,
                max_training_samples: parseInt(document.getElementById('val-max-train-large').value) || 30000,
            }),
            signal: evalAbortController.signal,
        });

        if (!resp.ok) {
            let msg = 'Evaluation failed';
            try { const data = await resp.json(); msg = data.error || msg; }
            catch (_) { msg = `Server error (${resp.status})`; }
            resetButtons();
            status.textContent = msg;
            status.style.color = '#dc3545';
            return;
        }

        await readNdjsonStream(resp, resetButtons);

    } catch (e) {
        resetButtons();
        const elapsed = ((Date.now() - t0) / 1000).toFixed(0);
        if (e.name === 'AbortError') {
            status.textContent = userCancelled ? 'Cancelled by user' : `Timed out after ${elapsed}s`;
            status.style.color = '#f0ad4e';
        } else {
            status.textContent = 'Error: ' + e.message;
            status.style.color = '#dc3545';
        }
    }
}

// ── Large-area results panel (panel 3) ──

let _resultsTableModels = [];

// Update panel 4 status text. Visibility is controlled by PANEL_LAYOUT, not here.
function showResultsPanel(message) {
    document.getElementById('val-results-status').textContent = message;
}

function setResultsStatus(message) {
    document.getElementById('val-results-status').textContent = message;
}

function initResultsTable(modelNames, task) {
    _resultsTableModels = modelNames;
    const thead = document.getElementById('val-results-thead');
    const tbody = document.getElementById('val-results-tbody');

    const metric = task === 'regression' ? 'R²' : 'F1';
    thead.innerHTML = '<th style="text-align:left; padding:6px;">% Labels</th>'
        + modelNames.map(n =>
            `<th style="text-align:right; padding:6px;">${CLASSIFIER_LABELS[n] || n} (${metric})</th>`
        ).join('');
    tbody.innerHTML = '';
}

function appendResultsRow(pct, classifiers) {
    const tbody = document.getElementById('val-results-tbody');
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid #333';
    let cells = `<td style="padding:6px;">${pct}%</td>`;
    for (const name of _resultsTableModels) {
        const m = classifiers[name] || {};
        const val = m.mean_f1;
        cells += `<td style="text-align:right; padding:6px;">${val !== undefined ? val.toFixed(4) : '—'}</td>`;
    }
    tr.innerHTML = cells;
    tbody.appendChild(tr);
}

function renderRegressionResults(aggregate) {
    const panel = document.getElementById('val-regression-panel');
    const tbody = document.querySelector('#val-regression-table tbody');
    const cmScroll = document.querySelector('#val-cm-panel .cm-scroll');
    const cmTitle = document.getElementById('val-cm-title');

    // Hide CM, show regression
    if (cmScroll) cmScroll.style.display = 'none';
    if (cmTitle) cmTitle.textContent = 'Regression Metrics';
    panel.style.display = '';

    tbody.innerHTML = '';
    for (const [name, metrics] of Object.entries(aggregate)) {
        const tr = document.createElement('tr');
        tr.style.borderBottom = '1px solid #333';
        const color = CLASSIFIER_COLORS[name] || { line: '#888' };
        tr.innerHTML = `
            <td style="padding:6px;"><span style="color:${color.line}">\u25cf</span> ${CLASSIFIER_LABELS[name] || name}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_r2.toFixed(4)} \u00b1 ${metrics.std_r2.toFixed(4)}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_rmse.toFixed(4)} \u00b1 ${metrics.std_rmse.toFixed(4)}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_mae.toFixed(4)} \u00b1 ${metrics.std_mae.toFixed(4)}</td>
        `;
        tbody.appendChild(tr);
    }
}

// Inline Chart.js plugin: draws ±std error bars on bar charts.
// Expects each dataset to have a `_std` array parallel to `data`.
const errorBarPlugin = {
    id: 'errorBars',
    afterDraw(chart) {
        const ctx = chart.ctx;
        chart.data.datasets.forEach((ds, dsIdx) => {
            const stdArr = ds._std;
            if (!stdArr) return;
            const meta = chart.getDatasetMeta(dsIdx);
            meta.data.forEach((bar, i) => {
                const std = stdArr[i];
                if (!std || std === 0) return;
                const yScale = chart.scales.y;
                const val = ds.data[i];
                const yTop = yScale.getPixelForValue(val + std);
                const yBot = yScale.getPixelForValue(val - std);
                const x = bar.x;
                const capW = bar.width ? bar.width * 0.3 : 6;
                ctx.save();
                ctx.strokeStyle = ds.borderColor instanceof Array ? ds.borderColor[i] : (ds.borderColor || '#fff');
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                // vertical line
                ctx.moveTo(x, yTop);
                ctx.lineTo(x, yBot);
                // top cap
                ctx.moveTo(x - capW, yTop);
                ctx.lineTo(x + capW, yTop);
                // bottom cap
                ctx.moveTo(x - capW, yBot);
                ctx.lineTo(x + capW, yBot);
                ctx.stroke();
                ctx.restore();
            });
        });
    },
};

function renderRegressionBarChart(aggregate) {
    const ctx = document.getElementById('val-chart').getContext('2d');
    if (valChart) valChart.destroy();

    const modelNames = Object.keys(aggregate);
    const r2Values = modelNames.map(n => aggregate[n].mean_r2);
    const r2Std = modelNames.map(n => aggregate[n].std_r2);
    const colors = modelNames.map(n => (CLASSIFIER_COLORS[n] || { line: '#888' }).line);

    valChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: modelNames.map(n => CLASSIFIER_LABELS[n] || n),
            datasets: [{
                label: 'R\u00b2',
                data: r2Values,
                backgroundColor: colors.map(c => c.replace('1)', '0.6)')),
                borderColor: colors,
                borderWidth: 2,
                _std: r2Std,
            }],
        },
        plugins: [errorBarPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                title: {
                    display: true,
                    text: 'R\u00b2 Score by Model (k-fold CV)',
                    color: '#eee',
                    font: { size: 15, weight: 'bold' },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    min: 0,
                    title: { display: true, text: 'R\u00b2', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
            },
        },
    });
}

function renderClassificationBarChart(aggregate) {
    const ctx = document.getElementById('val-chart').getContext('2d');
    if (valChart) valChart.destroy();

    const modelNames = Object.keys(aggregate);
    const f1Values = modelNames.map(n => aggregate[n].mean_f1);
    const f1Std = modelNames.map(n => aggregate[n].std_f1);
    const colors = modelNames.map(n => (CLASSIFIER_COLORS[n] || { line: '#888' }).line);

    valChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: modelNames.map(n => CLASSIFIER_LABELS[n] || n),
            datasets: [{
                label: 'Macro F1',
                data: f1Values,
                backgroundColor: colors.map(c => c.replace('1)', '0.6)')),
                borderColor: colors,
                borderWidth: 2,
                _std: f1Std,
            }],
        },
        plugins: [errorBarPlugin],
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                title: {
                    display: true,
                    text: 'Macro F1 Score by Model (k-fold CV)',
                    color: '#eee',
                    font: { size: 15, weight: 'bold' },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    min: 0,
                    max: 1,
                    title: { display: true, text: 'Macro F1', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
            },
        },
    });
}

function loadResultsFile() {
    const fileInput = document.getElementById('val-results-file');
    const file = fileInput.files[0];
    if (!file) return;

    const status = document.getElementById('val-status');
    status.textContent = 'Loading results...';
    status.style.color = '#888';

    const reader = new FileReader();
    reader.onload = function(e) {
        const lines = e.target.result.split('\n').filter(l => l.trim());
        lastChartData = null;
        currentLargeAreaTask = null;
        if (valChart) { valChart.destroy(); valChart = null; }

        for (const line of lines) {
            try {
                handleStreamEvent(JSON.parse(line));
            } catch (err) {
                console.warn('Parse error in results file:', err);
            }
        }
        status.textContent = `Loaded ${lines.length} events from ${file.name}`;
        status.style.color = '#28a745';
    };
    reader.readAsText(file);
    fileInput.value = '';
}

// Wire up Load Results file input
const _resultsFileInput = document.getElementById('val-results-file');
if (_resultsFileInput) _resultsFileInput.addEventListener('change', loadResultsFile);

// ── Upload Config ──

function loadConfigFile() {
    const fileInput = document.getElementById('val-config-file');
    const file = fileInput.files[0];
    if (!file) return;

    const status = document.getElementById('val-status');
    status.textContent = 'Loading config...';
    status.style.color = '#888';

    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const config = JSON.parse(e.target.result);
            applyConfig(config);
            status.textContent = `Config loaded from ${file.name}`;
            status.style.color = '#28a745';
        } catch (err) {
            status.textContent = 'Invalid config file: ' + err.message;
            status.style.color = '#dc3545';
        }
    };
    reader.readAsText(file);
    fileInput.value = '';
}

function applyConfig(config) {
    // Set field
    if (config.fields && config.fields.length > 0) {
        const fieldName = config.fields[0].name;
        const sel = document.getElementById('val-field-select');
        if (sel) {
            const opt = Array.from(sel.options).find(o => o.value === fieldName);
            if (opt) {
                sel.value = fieldName;
                updateClassSummary();
            }
        }
    }

    // Set year
    if (config.years && config.years.length > 0) {
        const yearSel = document.getElementById('val-year-select');
        if (yearSel) yearSel.value = String(config.years[0]);
    }

    // Set classifiers — uncheck all, then check the ones in config
    document.querySelectorAll('.val-clf-header input[type="checkbox"]').forEach(cb => {
        cb.checked = false;
    });
    const clfNames = Object.keys(config.classifiers || {});
    const regNames = Object.keys(config.regressors || {});
    const allModels = [...clfNames, ...regNames];
    for (const name of allModels) {
        const cb = document.querySelector(`.val-clf-header input[value="${name}"]`);
        if (cb) cb.checked = true;
    }

    // Set classifier params
    const allParams = { ...(config.classifiers || {}), ...(config.regressors || {}) };
    for (const [clf, params] of Object.entries(allParams)) {
        for (const [param, value] of Object.entries(params)) {
            const el = document.querySelector(`.val-params [data-clf="${clf}"][data-param="${param}"]`);
            if (el) el.value = value;
        }
    }

    // Set max training samples
    if (config.max_training_samples) {
        const input = document.getElementById('val-max-train-large');
        if (input) input.value = config.max_training_samples;
    }
}

document.getElementById('val-config-file').addEventListener('change', loadConfigFile);

// Max training samples → % hint
function updateMaxTrainPctHint() {
    const input = document.getElementById('val-max-train-large');
    const hint = document.getElementById('val-max-train-pct');
    if (!input || !hint) return;
    const maxSamples = parseInt(input.value) || 0;
    if (valTotalLabelledPixels > 0 && maxSamples > 0) {
        const pct = Math.min(100, (100 * maxSamples / valTotalLabelledPixels)).toFixed(1);
        hint.textContent = `${maxSamples.toLocaleString()} = ${pct}% of ${valTotalLabelledPixels.toLocaleString()} labelled pixels`;
    } else if (maxSamples > 0) {
        hint.textContent = `${maxSamples.toLocaleString()} pixels (% shown after first run)`;
    } else {
        hint.textContent = '';
    }
}
document.getElementById('val-max-train-large').addEventListener('input', updateMaxTrainPctHint);

// ── Expose on window for onclick handlers and test assertions ──

Object.defineProperty(window, 'valChart', {
    get: () => valChart,
    configurable: true,
});

// Restore validation panel state when returning from another mode
function restoreValidationState() {
    // Restore drop zone filename
    if (valUploadedFilename) {
        const dz = document.getElementById('val-drop-zone');
        if (dz) {
            dz.textContent = valUploadedFilename;
            dz.classList.add('uploaded');
        }
    }

    // Restore field selector
    if (valFieldData && valFieldData.length > 0) {
        const sel = document.getElementById('val-field-select');
        if (sel) {
            const prevValue = sel.value;
            sel.innerHTML = '';
            valFieldData.forEach(f => {
                const opt = document.createElement('option');
                opt.value = f.name;
                opt.textContent = `${f.name} (${f.unique_count} classes)`;
                sel.appendChild(opt);
            });
            // Restore previously selected field
            if (prevValue && Array.from(sel.options).some(o => o.value === prevValue)) {
                sel.value = prevValue;
            }
            sel.disabled = false;
            document.getElementById('val-run-btn').disabled = false;
        }
        updateClassSummary();
    }

    // Restore GeoJSON overlay + zoom
    if (valGeoJsonData) {
        addValGeoJsonLayer();
    }

    // Re-render chart
    if (lastChartData && lastChartData.training_pcts && lastChartData.training_pcts.length > 0) {
        renderChart(lastChartData);
    }

    // Re-render confusion matrix
    if (lastEvalData && lastEvalData.confusion_matrices) {
        renderConfusionMatrix(lastEvalData);
    }

    // Restore max train hint
    updateMaxTrainPctHint();
}
window.restoreValidationState = restoreValidationState;

window.uploadShapefile = uploadShapefile;
window.runEvaluation = runEvaluation;
window.renderConfusionMatrix = renderConfusionMatrix;
window.renderChart = renderChart;
Object.defineProperty(window, 'lastChartData', {
    get: () => lastChartData,
    configurable: true,
});
window.exportEvalResults = exportEvalResults;
window.openCMPopup = openCMPopup;
window.generateConfig = generateConfig;
window.loadConfigFile = loadConfigFile;
window.loadResultsFile = loadResultsFile;
Object.defineProperty(window, 'lastEvalData', {
    get: () => lastEvalData,
    set: (v) => { lastEvalData = v; },
    configurable: true,
});
