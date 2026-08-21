// evaluation.js — Validation panel: shapefile upload, NDJSON streaming,
// learning curve charts, confusion matrix, model download.
// Extracted from viewer.html as an ES module.

// ── Compute server ──

function evalUrl(path) {
    return '/api/evaluation/' + path;
}

// Auto-detect compute server via Django proxy
(function() {
    setTimeout(() => {
        fetch('/api/evaluation/health').then(r => r.json()).then(data => {
            if (data.compute_host) {
                const status = document.getElementById('val-compute-status');
                if (status) {
                    status.textContent = data.compute_host;
                    status.style.background = '#28a745';
                    status.style.color = '#fff';
                }
            }
        }).catch(() => {});
    }, 1000);
})();

// ── State ──

let valChart = null;
let valScatterChart = null;  // separate canvas from valChart -- predicted-vs-actual, regression only
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
let valEstimatedLabelledPixels = 0;
let currentLargeAreaTask = null; // 'classification' or 'regression'
let valUploadedFiles = []; // list of uploaded filenames
let valTotalLabelledPixels = 0; // set by start event, used for % hint

// ── Spatial bounding boxes (Phase 1) ──
const BBOX_COLORS = {
    train: { color: '#3388ff', fillColor: '#3388ff', fillOpacity: 0.15, weight: 2 },
    test:  { color: '#ffcc00', fillColor: '#ffcc00', fillOpacity: 0.15, weight: 2 },
    map:   { color: '#44bb44', fillColor: '#44bb44', fillOpacity: 0.15, weight: 2 },
};
let spatialBboxes = { train: [], test: [], map: [] };
let bboxFeatureGroup = null;
let currentBboxType = 'train';
let bboxDrawHandler = null;

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

// ── Hyperparameter variant helpers ──

/**
 * Strip variant suffix from a classifier name (e.g., "mlp_v2" -> "mlp").
 */
function variantBaseName(name) {
    return name.replace(/_v\d+$/, '');
}

/**
 * Parse variant index from a name (e.g., "mlp_v2" -> 2, "mlp" -> 0).
 */
function variantIndex(name) {
    const m = name.match(/_v(\d+)$/);
    return m ? parseInt(m[1]) : 0;
}

/**
 * Get color for a variant name. Base classifiers use their standard color.
 * Variants derive lighter/darker shades from the base color.
 */
function getVariantColor(name) {
    // Check if there's a direct entry (base name or already registered)
    if (CLASSIFIER_COLORS[name]) return CLASSIFIER_COLORS[name];

    const base = variantBaseName(name);
    const baseColor = CLASSIFIER_COLORS[base];
    if (!baseColor) return { line: '#888', fill: 'rgba(136,136,136,0.15)' };

    const idx = variantIndex(name);
    // Parse base RGBA
    const m = baseColor.line.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!m) return baseColor;

    let r = parseInt(m[1]), g = parseInt(m[2]), b = parseInt(m[3]);
    // Shift hue by alternating lighter/darker shades
    const shift = (idx % 2 === 0) ? 40 * Math.ceil(idx / 2) : -40 * Math.ceil(idx / 2);
    r = Math.max(0, Math.min(255, r + shift));
    g = Math.max(0, Math.min(255, g + shift));
    b = Math.max(0, Math.min(255, b + shift));

    const color = { line: `rgba(${r}, ${g}, ${b}, 1)`, fill: `rgba(${r}, ${g}, ${b}, 0.15)` };
    // Cache for future lookups
    CLASSIFIER_COLORS[name] = color;
    return color;
}

/**
 * Get a human-readable label for a variant name.
 * E.g., "mlp_v2" -> "MLP (v2)", "rf" -> "Random Forest".
 */
function getVariantLabel(name) {
    if (CLASSIFIER_LABELS[name]) return CLASSIFIER_LABELS[name];

    const base = variantBaseName(name);
    const idx = variantIndex(name);
    const baseLabel = CLASSIFIER_LABELS[base] || base;
    const label = idx > 0 ? `${baseLabel} (v${idx})` : baseLabel;
    // Cache for future lookups
    CLASSIFIER_LABELS[name] = label;
    return label;
}

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
    // Real survey shapefiles (thousands of polygons) can take a visible
    // moment to upload+parse -- the drop zone otherwise just sits there
    // looking idle the whole time, easy to miss the small status text and
    // assume nothing happened. .uploading also blocks a second drop/click
    // from starting an overlapping upload.
    dropZone.classList.add('uploading');

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch(evalUrl('upload-shapefile'), { method: 'POST', body: formData });
        const data = await resp.json();
        if (!resp.ok) {
            status.textContent = data.error || 'Upload failed';
            status.style.color = '#dc3545';
            return;
        }

        valFieldData = data.fields;
        valUploadedFilename = file.name;
        const fileList = data.files || [file.name];
        dropZone.textContent = fileList.length > 1 ? `${fileList.length} files: ${fileList.join(', ')}` : file.name;
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
        const estK = valEstimatedLabelledPixels > 0
            ? ` (~${(valEstimatedLabelledPixels / 1e6).toFixed(1)}M labelled pixels at 10m)`
            : '';
        status.textContent = `${data.fields.length} fields found${estK}`;
        status.style.color = '#28a745';

        valGeoJsonData = data.geojson;
        valEstimatedLabelledPixels = data.estimated_labelled_pixels || 0;
        addValGeoJsonLayer();
        updateClassSummary();
        updateYearCoverage(data.geojson);

    } catch (e) {
        const msg = e.message || String(e);
        if (msg.includes('string did not match') || msg.includes('Failed to fetch')) {
            status.textContent = 'Upload failed — is the compute server running? Run: ./scripts/deploy-compute.sh --local (or gpu-box for a GPU server)';
        } else {
            status.textContent = 'Upload error: ' + msg;
        }
        status.style.color = '#dc3545';
    } finally {
        // Runs on every exit path (success, the early return on a non-OK
        // response, and the catch above) so a failed/errored upload doesn't
        // leave the drop zone permanently stuck looking busy.
        dropZone.classList.remove('uploading');
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

        // Show class names + polygon counts from full shapefile (not truncated GeoJSON)
        if (field.class_counts) {
            const classNames = Object.keys(field.class_counts).sort();
            const classData = classNames.map(n => ({ name: n, pixels: field.class_counts[n] }));
            populateValClassTable(classNames, classData, false);
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

        _annotateYearSelectCoverage(document.getElementById('val-train-year-select'), coverage);
        _annotateYearSelectCoverage(document.getElementById('val-test-year-select'), coverage);
        _annotateYearSelectCoverage(document.getElementById('val-map-year-select'), coverage);
    } catch (e) {
        console.warn('Failed to check year coverage:', e);
    }
}

// Annotates one <select>'s options with tile-coverage counts and disables
// years with no coverage at all. Shared by both the train-year and
// test-year selects (see updateYearCoverage above).
function _annotateYearSelectCoverage(sel, coverage) {
    if (!sel) return;
    Array.from(sel.options).forEach(opt => {
        // Skip non-year placeholder options (e.g. Map year's "Same as
        // training year", value="") -- they don't correspond to a real
        // year to look up coverage for, and must never be disabled.
        if (!opt.value) return;
        const tiles = coverage[opt.value] || 0;
        opt.disabled = tiles === 0;
        opt.textContent = tiles > 0 ? `${opt.value} (${tiles} tiles)` : `${opt.value} (no coverage)`;
    });
    // If current selection has no coverage, pick the first available
    if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) {
        const first = Array.from(sel.options).find(o => !o.disabled);
        if (first) sel.value = first.value;
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
        const color = getVariantColor(name);
        const baseIdx = datasets.length;
        streamDatasetMap[name] = baseIdx;

        datasets.push({
            label: getVariantLabel(name),
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
    const isRegressionRun = currentLargeAreaTask === 'regression';
    const metricLabel = isRegressionRun ? 'R\u00b2' : (metric === 'weighted' ? 'Weighted F1' : 'Macro F1');

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
                    type: 'logarithmic',
                    title: { display: true, text: '% of labelled pixels used for training', color: '#aaa' },
                    ticks: { color: '#aaa', callback: v => v < 1 ? v.toFixed(1) + '%' : Math.round(v) + '%' },
                    min: 0.01, max: 100,
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    // R\u00b2 (unlike F1) can go negative for a bad model -- don't
                    // force a [0,1] floor/ceiling, let it autoscale down.
                    min: isRegressionRun ? undefined : 0, max: isRegressionRun ? undefined : 1,
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
        const color = getVariantColor(name);
        const btn = document.createElement('button');
        btn.id = 'finish-' + name;
        btn.textContent = 'Finish ' + getVariantLabel(name);
        btn.style.cssText = `padding:4px 12px;border:1px solid ${color.line};border-radius:12px;background:transparent;color:${color.line};font-size:12px;cursor:pointer;`;
        btn.onclick = () => {
            fetch(evalUrl('finish-classifier'), {
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
    // Live progress-during-run chart: regression has no macro/weighted split
    // (that's an F1-only concept), so it always plots R² regardless of the
    // metric selector -- which is hidden for regression anyway, see
    // updateMetricSelectVisibility().
    const isRegressionRun = currentLargeAreaTask === 'regression';
    const meanKey = isRegressionRun ? 'mean_r2' : (metric === 'weighted' ? 'mean_f1w' : 'mean_f1');
    const stdKey = isRegressionRun ? 'std_r2' : (metric === 'weighted' ? 'std_f1w' : 'std_f1');

    if (ev.event === 'start') {
        // Don't rely solely on 'field_start' having set this -- it's only
        // emitted on a cache miss (gated server-side by the tile cache key
        // changing), so a run that hits the in-memory/disk cache (e.g. same
        // field/year/sampling as a prior run, just different classifiers)
        // never sees it, leaving currentLargeAreaTask stuck at whatever the
        // *previous* run left it at (or null, from runLargeAreaEvaluation's
        // own reset, on some paths). Confirmed live (Louis Driver): R² and
        // the learning curve stopped showing in the GUI for every
        // evaluation after the first one in a session -- this dispatch
        // used to silently take the classification branch on every
        // affected run. 'start' is unconditional regardless of cache
        // state (tessera-eval v1.7.2+ carries task on it directly), so set
        // it here too rather than trusting event-ordering alone.
        if (ev.task) currentLargeAreaTask = ev.task;
        // Same cache-hit-skips-field_start issue as above -- redo the
        // metric-selector visibility here too rather than trusting it was
        // already set correctly for *this* run's task.
        if (ev.task) {
            const metricWrap = document.getElementById('val-metric-select-wrap');
            if (metricWrap) metricWrap.style.display = (ev.task === 'regression') ? 'none' : '';
        }
        stopResultsLog();
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
            // _x: the actual "% of labelled pixels used for training" value
            // plotted for each point, parallel to the metric arrays below --
            // NOT the same as lastChartData.training_pcts (the nominal
            // requested pct). See the 'progress' handler for why these two
            // can differ, and renderChart for why that difference matters.
            lastChartData.classifiers[name] = (currentLargeAreaTask === 'regression')
                ? { mean_r2: [], std_r2: [], mean_rmse: [], std_rmse: [], mean_mae: [], std_mae: [], _x: [] }
                : { mean_f1: [], std_f1: [], mean_f1w: [], std_f1w: [], _x: [] };
        });
        createStreamChart(ev.classifiers);
        // Show results table in panel 3 with progress
        {
            const pixels = ev.total_labelled_pixels || 0;
            valTotalLabelledPixels = pixels;
            updateMaxTrainPctHint();
            const stats = ev.stats || {};
            const yearNote = (ev.train_year && ev.test_year && ev.train_year !== ev.test_year)
                ? ` Trained ${ev.train_year} → tested ${ev.test_year}.`
                : '';
            initResultsTable(ev.classifiers, currentLargeAreaTask || 'classification');
            setResultsStatus(
                `${pixels.toLocaleString()} labelled pixels from ${stats.tiles_with_data || '?'}/${stats.tile_count || '?'} tiles.${yearNote} Running learning curve...`
            );
        }

        if (ev.spatial_split) {
            lastChartData.spatial_split = true;
            lastChartData.train_count = ev.train_count;
            lastChartData.test_count = ev.test_count;
        }
        lastChartData.train_year = ev.train_year;
        lastChartData.test_year = ev.test_year;
        if (ev.year_split) {
            lastChartData.year_split = true;
            lastChartData.train_count = ev.train_count;
            lastChartData.test_count = ev.test_count;
        }

        if (ev.classes) {
            const names = ev.classes.map(c => c.name);
            populateValClassTable(names, ev.classes, true);
        }

    } else if (ev.event === 'progress' && !ev.classifiers) {
        // Tile fetch progress (no classifiers field)
        status.dataset.updated = '1';
        status.textContent = ev.message;
        showResultsPanel(ev.message);

    } else if (ev.event === 'progress') {
        lastChartData.training_pcts.push(ev.pct);

        // Unified x-axis: all classifiers plotted as fraction of total labelled
        // pixels. Denominator = the real total_labelled_pixels count from this
        // run's 'start' event -- the backend's own denominator for turning a
        // nominal pct into a sample size (training_pcts = [1, 3, 5, 10, 20, 30,
        // 50, 80] in server.py), so trainPx/totalLabels*100 lands exactly on
        // the nominal pct. valEstimatedLabelledPixels (a rough polygon-area/
        // 100m² guess made at upload time, before real label extraction) is
        // only a last-resort fallback for the sliver of time before a run's
        // 'start' event has set lastChartData.total_labelled_pixels.
        // Using the upload-time estimate as primary (as this used to) meant
        // every point's x was off by whatever the estimate diverged from the
        // real count -- confirmed live, Louis Driver: "x-axis still initially
        // plots with a misalignment", persisting even on the very first plot
        // with no rebuild/view-switch involved, so it was never explained by
        // the rebuild-vs-live desync fixed on 2026-08-20 (that fix was real
        // but only addressed *consistency* between rebuild and live points,
        // not whether either was using the right denominator to begin with).
        const totalLabels = lastChartData.total_labelled_pixels || valEstimatedLabelledPixels || ev.total_unet_pixels || ev.total_pixels || 1;

        for (const [name, vals] of Object.entries(ev.classifiers)) {
            const acc = lastChartData.classifiers[name];
            if (!acc) continue;
            // Both use same denominator: total labelled pixels. Computed once
            // per classifier per pct (not just when a chart happens to be
            // live) and stored in acc._x so it stays parallel to the metric
            // arrays -- renderChart (the metric-switch rebuild path) reads
            // this back instead of recomputing/guessing, which is what used
            // to cause rebuilt points and freshly-streamed points to land at
            // different x positions on the same chart (Louis Driver).
            const trainPx = (name === 'unet') ? ev.unet_train_count : ev.pixel_train_count;
            const x = trainPx / totalLabels * 100;
            acc._x.push(x);
            if (isRegressionRun) {
                acc.mean_r2.push(vals.mean_r2);
                acc.std_r2.push(vals.std_r2);
                acc.mean_rmse.push(vals.mean_rmse);
                acc.std_rmse.push(vals.std_rmse);
                acc.mean_mae.push(vals.mean_mae);
                acc.std_mae.push(vals.std_mae);
            } else {
                acc.mean_f1.push(vals.mean_f1);
                acc.std_f1.push(vals.std_f1);
                acc.mean_f1w.push(vals.mean_f1w);
                acc.std_f1w.push(vals.std_f1w);
            }

            const baseIdx = streamDatasetMap[name];
            if (baseIdx !== undefined && valChart) {
                const mean = vals[meanKey];
                const std = vals[stdKey];
                valChart.data.datasets[baseIdx].data.push({ x, y: mean });
                valChart.data.datasets[baseIdx + 1].data.push({ x, y: Math.min(1, mean + std) });
                // R² (unlike F1) can go negative for a bad model -- don't floor it at 0.
                valChart.data.datasets[baseIdx + 2].data.push({
                    x, y: isRegressionRun ? mean - std : Math.max(0, mean - std)
                });
            }
        }
        if (valChart) valChart.update();
        // Show training pixel counts
        const pixelK = ev.pixel_train_count ? `${(ev.pixel_train_count / 1000).toFixed(1)}K` : '0';
        const unetK = ev.unet_train_count ? `${(ev.unet_train_count / 1000).toFixed(0)}K` : '';
        const totalK = `${(totalLabels / 1000).toFixed(0)}K`;
        const trainInfo = unetK ? `${pixelK} pixel + ${unetK} patch of ${totalK} labels` : `${pixelK} of ${totalK} labels`;
        const planned = lastChartData._plannedPcts || [];
        const doneIdx = planned.indexOf(ev.pct);
        if (doneIdx >= 0 && doneIdx < planned.length - 1) {
            status.textContent = `Training: ${trainInfo}`;
        } else {
            status.textContent = `Done: ${trainInfo}`;
        }
        appendResultsRow(ev.pct, ev.classifiers, ev);
        const elapsed = status.dataset.t0 ? ((Date.now() - parseInt(status.dataset.t0)) / 1000).toFixed(0) : '';
        setResultsStatus(`${trainInfo} (${elapsed}s)`);

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
        // renderConfusionMatrix() is what normally sets lastEvalData (for
        // Export Results), but it only ever runs on the classification-only
        // 'confusion_matrices' event -- regression never fires that event,
        // so Export Results silently did nothing after a regression run.
        // 'done' fires for both tasks, so set it here unconditionally too.
        lastEvalData = lastChartData;
        const pixels = lastChartData.total_labelled_pixels || 0;
        const nClasses = (lastChartData.classes || []).length;
        const suffix = nClasses > 0
            ? ` \u2014 ${pixels.toLocaleString()} pixels, ${nClasses} classes`
            : ` \u2014 ${pixels.toLocaleString()} pixels`;
        const yearSuffix = (ev.train_year && ev.test_year && ev.train_year !== ev.test_year)
            ? ` \u2014 trained ${ev.train_year} \u2192 tested ${ev.test_year}`
            : '';
        status.textContent = `Done in ${ev.elapsed_seconds}s${suffix}${yearSuffix}`;
        status.style.color = '#28a745';
        const dlBtnH = document.getElementById('val-download-btn');
        if (dlBtnH) dlBtnH.disabled = false;  // always enable — trains on click
        hideFinishButtons();
        updateCreateMapButton();


    } else if (ev.event === 'heartbeat') {
        // Keep-alive, ignore

    } else if (ev.event === 'status') {
        status.dataset.updated = '1';
        status.textContent = ev.message;
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
        // Macro/weighted F1 is a classification-only distinction; regression
        // always plots R², so the selector would just be a dead control.
        const metricWrap = document.getElementById('val-metric-select-wrap');
        if (metricWrap) metricWrap.style.display = (ev.type === 'regression') ? 'none' : '';
        status.dataset.updated = '1';
        status.textContent = `Loading GeoTessera tile index...`;
        startResultsLog();
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
        // NOT renderRegressionBarChart/renderClassificationBarChart here:
        // both destroy() and replace valChart with a *bar* chart on the
        // same canvas #val-chart the learning curve *line* chart was just
        // built on over the whole run -- so the aggregate event silently
        // clobbered the more informative learning curve with a same-titled
        // ("R2 Score by Model (k-fold CV)") single-bar summary the instant
        // the run finished. Confirmed live, Louis Driver, 2026-08-21: "it
        // looks fine [during the run], but when it finishes it presents a
        // strange graph" -- a box shape, which for a single model is
        // exactly what a lone bar looks like. Those two functions were
        // built for an actual standalone k-fold CV flow (see their
        // hardcoded titles) and aren't otherwise called anywhere in this
        // file; leaving them defined (unused for large-area runs) rather
        // than deleting them, since a guard test asserts they exist.
        if (currentLargeAreaTask === 'regression') {
            renderRegressionResults(ev.models);
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
    // Shape-based, not currentLargeAreaTask-based: this also runs on session
    // restore (from localStorage), where that module-scoped variable may not
    // reflect the restored run's task.
    const isRegression = !!(firstClf && firstClf.mean_r2 !== undefined);
    const hasWeighted = !isRegression && firstClf && firstClf.mean_f1w;
    const isWeighted = metric === 'weighted' && hasWeighted;
    if (!isRegression && metric === 'weighted' && !hasWeighted) {
        document.getElementById('val-metric-select').value = 'macro';
    }
    const meanKey = isRegression ? 'mean_r2' : (isWeighted ? 'mean_f1w' : 'mean_f1');
    const stdKey = isRegression ? 'std_r2' : (isWeighted ? 'std_f1w' : 'std_f1');
    const metricLabel = isRegression ? 'R²' : (isWeighted ? 'Weighted F1' : 'Macro F1');

    const ctx = document.getElementById('val-chart').getContext('2d');

    if (valChart) valChart.destroy();

    const datasets = [];
    streamDatasetMap = {};
    for (const [name, values] of Object.entries(data.classifiers)) {
        const color = getVariantColor(name);
        streamDatasetMap[name] = datasets.length;

        // Use each classifier's own recorded x positions (actual % of
        // labelled pixels used, per point -- see the 'progress' handler)
        // rather than the nominal data.training_pcts. Those two can differ
        // (rounding, and unet vs pixel-count classifiers use different
        // denominators at "the same" pct), and mixing them here is what
        // caused rebuilt points (metric-switch) to land at a different x
        // than freshly-streamed live points on the same chart. Fall back to
        // training_pcts for older exported/localStorage sessions saved
        // before per-classifier _x existed.
        const xs = values._x || data.training_pcts;

        datasets.push({
            label: getVariantLabel(name),
            data: xs.map((x, i) => ({ x, y: values[meanKey][i] })),
            borderColor: color.line,
            backgroundColor: 'transparent',
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: color.line,
            tension: 0.3,
        });

        datasets.push({
            label: name + '_upper',
            data: xs.map((x, i) => ({
                x, y: Math.min(1, values[meanKey][i] + values[stdKey][i])
            })),
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            pointRadius: 0,
            fill: false,
        });

        datasets.push({
            label: name + '_lower',
            data: xs.map((x, i) => ({
                // R² (unlike F1) can go negative for a bad model -- don't floor it at 0.
                x, y: isRegression ? values[meanKey][i] - values[stdKey][i] : Math.max(0, values[meanKey][i] - values[stdKey][i])
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
                    type: 'logarithmic',
                    title: { display: true, text: '% of labelled pixels used for training', color: '#aaa' },
                    ticks: { color: '#aaa', callback: v => v < 1 ? v.toFixed(1) + '%' : Math.round(v) + '%' },
                    min: 0.01, max: 100,
                    grid: { color: 'rgba(255,255,255,0.08)' },
                },
                y: {
                    min: isRegression ? undefined : 0, max: isRegression ? undefined : 1,
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
        opt.textContent = getVariantLabel(n);
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
    const classifierLabel = getVariantLabel(classifierName);

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
        opt.textContent = getVariantLabel(n);
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
        const resp = await fetch(evalUrl('train-models'), { method: 'POST' });
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
                            a.href = evalUrl(`download-model/${encodeURIComponent(name)}`);
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

// ── Create Map (Phase 4: GeoTIFF generation) ──

document.getElementById('val-create-map-btn').addEventListener('click', createMap);

function updateCreateMapButton() {
    const btn = document.getElementById('val-create-map-btn');
    if (!btn) return;
    const hasMapBboxes = spatialBboxes.map.length > 0;
    const hasEvalData = !!(lastChartData && lastChartData.classifiers);
    btn.disabled = !(hasMapBboxes && hasEvalData);
    if (!hasMapBboxes) {
        btn.title = 'Draw green map bounding boxes first';
    } else if (!hasEvalData) {
        btn.title = 'Run evaluation first to cache training data';
    } else {
        btn.title = 'Train on all labels and predict every pixel in green map areas';
    }
}

async function createMap() {
    const mapBboxes = spatialBboxes.map.map(r => rectToBbox(r));
    if (mapBboxes.length === 0) {
        document.getElementById('val-status').textContent = 'Draw green map bounding boxes first';
        document.getElementById('val-status').style.color = '#dc3545';
        return;
    }

    // Pick the classifier: use the first checked pixel-based classifier
    const PIXEL_CLASSIFIERS = ['nn', 'rf', 'xgboost', 'mlp'];
    const checkboxes = document.querySelectorAll('.val-clf-header input:checked');
    const checked = Array.from(checkboxes).map(cb => cb.value);
    const pixelClf = checked.find(c => {
        const base = c.replace(/_v\d+$/, '');
        return PIXEL_CLASSIFIERS.includes(base);
    });

    if (!pixelClf) {
        document.getElementById('val-status').textContent = 'Select a pixel-based classifier (k-NN, RF, XGBoost, or MLP) for map generation';
        document.getElementById('val-status').style.color = '#dc3545';
        return;
    }

    const btn = document.getElementById('val-create-map-btn');
    const cancelBtn = document.getElementById('val-cancel-btn');
    const status = document.getElementById('val-status');
    btn.disabled = true;
    btn.textContent = 'Creating Map...';
    cancelBtn.style.display = '';
    status.style.color = '#888';
    status.textContent = 'Starting map generation...';

    evalAbortController = new AbortController();
    let userCancelled = false;

    cancelBtn.onclick = () => {
        userCancelled = true;
        evalAbortController.abort();
        fetch(evalUrl('cancel'), { method: 'POST' }).catch(() => {});
    };

    // Empty value ("Same as training year", the default) omits map_year
    // entirely -- the backend then uses the model's own training year,
    // today's existing behavior, unchanged for anyone who doesn't touch this.
    const mapYearRaw = document.getElementById('val-map-year-select').value;
    const mapYear = mapYearRaw ? parseInt(mapYearRaw) : null;

    try {
        const resp = await fetch(evalUrl('create-map'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                classifier: pixelClf,
                map_bboxes: mapBboxes,
                ...(mapYear ? { map_year: mapYear } : {}),
            }),
            signal: evalAbortController.signal,
        });

        if (!resp.ok) {
            let msg = 'Map generation failed';
            try { const data = await resp.json(); msg = data.error || msg; }
            catch (_) { msg = `Server error (${resp.status})`; }
            status.textContent = msg;
            status.style.color = '#dc3545';
            btn.disabled = false;
            btn.textContent = 'Create Map';
            cancelBtn.style.display = 'none';
            return;
        }

        // Stream NDJSON progress
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        const readyMaps = [];

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
                    } else if (ev.event === 'map_progress') {
                        status.textContent = ev.message;
                        showResultsPanel(`Map area ${ev.bbox_idx + 1}: chunk ${ev.chunk}/${ev.total_chunks}`);
                    } else if (ev.event === 'map_ready') {
                        readyMaps.push(ev);
                        const yearNote = (ev.train_year && ev.map_year && ev.train_year !== ev.map_year)
                            ? ` — trained ${ev.train_year}, mapped ${ev.map_year}`
                            : '';
                        status.textContent = `Map area ${ev.bbox_idx + 1} ready (${ev.width}x${ev.height} pixels)${yearNote}`;
                        status.style.color = '#28a745';
                    } else if (ev.event === 'done') {
                        // Download all ready maps
                        for (const m of readyMaps) {
                            const a = document.createElement('a');
                            a.href = evalUrl(`download-map/${encodeURIComponent(m.name)}`);
                            a.download = `${m.name}.tif`;
                            a.click();
                        }
                        const elapsed = ev.elapsed_seconds || 0;
                        status.textContent = `${readyMaps.length} map(s) generated in ${elapsed}s and downloading`;
                        status.style.color = '#28a745';
                    } else if (ev.event === 'error') {
                        status.textContent = ev.message || 'Map generation error';
                        status.style.color = '#dc3545';
                    }
                } catch (e) { }
            }
        }
        if (buffer.trim()) {
            try {
                const ev = JSON.parse(buffer);
                if (ev.event === 'error') {
                    status.textContent = ev.message || 'Map generation error';
                    status.style.color = '#dc3545';
                }
            } catch (e) { }
        }

    } catch (e) {
        if (e.name === 'AbortError') {
            status.textContent = userCancelled ? 'Cancelled by user' : 'Map generation timed out';
            status.style.color = '#f0ad4e';
        } else {
            status.textContent = 'Map error: ' + e.message;
            status.style.color = '#dc3545';
        }
    }

    btn.disabled = false;
    btn.textContent = 'Create Map';
    cancelBtn.style.display = 'none';
    evalAbortController = null;
    updateCreateMapButton();
}

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
        // Collect params grouped by variant index
        const variantSets = {};
        document.querySelectorAll(`.val-params input[data-clf="${name}"], .val-params select[data-clf="${name}"]`).forEach(el => {
            const variantIdx = parseInt(el.dataset.variant || '0');
            if (!variantSets[variantIdx]) variantSets[variantIdx] = {};
            const val = el.value.trim();
            if (val === '') return;
            const num = Number(val);
            variantSets[variantIdx][el.dataset.param] = isNaN(num) ? val : num;
        });
        const indices = Object.keys(variantSets).map(Number).sort();
        const paramValue = indices.length <= 1
            ? (variantSets[indices[0]] || {})
            : indices.map(i => variantSets[i] || {});
        // Guess: if name ends with _reg it's a regressor, otherwise classifier
        if (name.endsWith('_reg')) {
            regressors[name] = paramValue;
        } else {
            classifiers[name] = paramValue;
        }
    });

    const config = {
        "$schema": "tee_evaluate_config_v1",
        "shapefile": valUploadedFilename || "/path/to/ground_truth.zip",
        "fields": [{ "name": field, "type": "auto" }],
        "_fields_type": "auto | classification | regression",
        "classifiers": classifiers,
        "_classifiers_available": "nn, rf, xgboost, mlp, spatial_mlp, spatial_mlp_5x5, unet",
        "regressors": regressors,
        "_regressors_available": "nn_reg, rf_reg, mlp_reg, xgboost_reg",
        // The CLI config format (consumed by scripts/tee_evaluate.py, which loops
        // a list of years -- one full single-year eval per year) predates the
        // train/test-year split and isn't train/test-split-aware. Source this
        // from the training year, the closest existing analog, rather than
        // inventing new CLI-config semantics as part of this change.
        "years": [parseInt(document.getElementById('val-train-year-select').value) || 2024],
        "_years_available": "2017-2025 (coverage varies by region)",
        "max_training_samples": parseInt(document.getElementById('val-max-train-large').value.replace(/,/g, '')) || 200000,
        "_max_training_samples": "max random points sampled from labelled polygons for pixel classifiers",
        "sampling": document.getElementById('val-sampling-select').value || 'sqrt',
        "_sampling_choices": "equal | sqrt | proportional",
        "max_patches": parseInt(document.getElementById('val-max-patches').value) || 500,
        "_max_patches": "max 256x256 tile crops for spatial MLP and U-Net (min 100)",
        "output_dir": "./eval_output",
        "dry_run": false,
        "seed": 42,
    };

    // Spatial bounding boxes (if any)
    if (hasSpatialBboxes() || spatialBboxes.map.length > 0) {
        config.spatial_bboxes = {
            train: spatialBboxes.train.map(r => rectToBbox(r)),
            test: spatialBboxes.test.map(r => rectToBbox(r)),
            map: spatialBboxes.map.map(r => rectToBbox(r)),
        };
    }

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

    // Spatial split confirmation: if no bboxes drawn, confirm random split
    if (!hasSpatialBboxes()) {
        if (!confirm('No spatial bounding boxes drawn.\nRun with random train/test split?')) {
            return;
        }
    }

    const checkboxes = document.querySelectorAll('.val-clf-header input:checked');
    const classifiers = Array.from(checkboxes).map(cb => cb.value);
    if (classifiers.length === 0) {
        document.getElementById('val-status').textContent = 'Select at least one classifier';
        document.getElementById('val-status').style.color = '#dc3545';
        return;
    }

    const params = {};
    // Collect params per classifier, grouping by variant index.
    // Elements with data-variant="N" (N >= 1) belong to variant N.
    // Elements without data-variant (or data-variant="0") are the base set.
    const variantSets = {}; // clf -> { variantIdx -> {param: value} }
    document.querySelectorAll('.val-params input, .val-params select').forEach(el => {
        const clf = el.dataset.clf;
        const param = el.dataset.param;
        if (!clf || !param) return;
        if (!classifiers.includes(clf)) return;
        const variantIdx = parseInt(el.dataset.variant || '0');
        if (!variantSets[clf]) variantSets[clf] = {};
        if (!variantSets[clf][variantIdx]) variantSets[clf][variantIdx] = {};
        const val = el.value.trim();
        if (val === '') return;
        const num = Number(val);
        variantSets[clf][variantIdx][param] = isNaN(num) ? val : num;
    });
    // Convert to server format: single object or list of objects
    for (const clf of classifiers) {
        const sets = variantSets[clf] || { 0: {} };
        const indices = Object.keys(sets).map(Number).sort();
        if (indices.length <= 1) {
            params[clf] = sets[indices[0]] || {};
        } else {
            params[clf] = indices.map(i => sets[i] || {});
        }
    }

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

    cancelBtn.onclick = () => {
        userCancelled = true;
        evalAbortController.abort();
        // Tell the compute server to stop
        fetch(evalUrl('cancel'), { method: 'POST' }).catch(() => {});
    };

    try {
        const resp = await fetch(evalUrl('run-large-area'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                field: field,
                train_year: parseInt(document.getElementById('val-train-year-select').value) || 2024,
                test_year: parseInt(document.getElementById('val-test-year-select').value) || 2024,
                classifiers: classifiers,
                classifier_params: params,
                max_training_samples: parseInt(document.getElementById('val-max-train-large').value.replace(/,/g, '')) || 200000,
                sampling: document.getElementById('val-sampling-select').value || 'sqrt',
                max_patches: parseInt(document.getElementById('val-max-patches').value) || 500,
                ...(hasSpatialBboxes() ? getSpatialBboxData() : {}),
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
// In log mode, appends lines; otherwise replaces text.
let _resultsLogMode = false;

function showResultsPanel(message) {
    const el = document.getElementById('val-results-status');
    if (_resultsLogMode) {
        el.textContent += '\n' + message;
        // Auto-scroll the parent container
        const panel = el.closest('[style*="overflow"]') || el.parentElement;
        if (panel) panel.scrollTop = panel.scrollHeight;
    } else {
        el.textContent = message;
    }
}

function startResultsLog() {
    _resultsLogMode = true;
    const el = document.getElementById('val-results-status');
    el.textContent = '';
    el.style.whiteSpace = 'pre-wrap';
    el.style.fontFamily = 'monospace';
    el.style.fontSize = '12px';
    el.style.lineHeight = '1.6';
}

function stopResultsLog() {
    _resultsLogMode = false;
    const el = document.getElementById('val-results-status');
    el.style.whiteSpace = '';
    el.style.fontFamily = '';
    el.style.fontSize = '13px';
    el.style.lineHeight = '';
}

function setResultsStatus(message) {
    document.getElementById('val-results-status').textContent = message;
}

function initResultsTable(modelNames, task) {
    _resultsTableModels = modelNames;
    const thead = document.getElementById('val-results-thead');
    const tbody = document.getElementById('val-results-tbody');

    const metric = task === 'regression' ? 'R²' : 'F1';
    thead.innerHTML = '<th style="text-align:left; padding:6px;">Training labels</th>'
        + modelNames.map(n =>
            `<th style="text-align:right; padding:6px;">${getVariantLabel(n)} (${metric})</th>`
        ).join('');
    tbody.innerHTML = '';
}

function appendResultsRow(pct, classifiers, ev) {
    const tbody = document.getElementById('val-results-tbody');

    // Show pixel and patch training counts
    const pixelK = ev && ev.pixel_train_count ? `${(ev.pixel_train_count / 1000).toFixed(1)}K` : '';
    const unetK = ev && ev.unet_train_count ? `${(ev.unet_train_count / 1000).toFixed(0)}K` : '';
    const labelStr = unetK ? `${pixelK}px + ${unetK}patch` : pixelK || `${pct}%`;

    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid #333';
    let cells = `<td style="padding:6px; font-size:12px;">${labelStr}</td>`;
    for (const name of _resultsTableModels) {
        const m = classifiers[name] || {};
        // initResultsTable already labels this column R² vs F1 by task --
        // read the metric that actually matches (regression events never
        // carry mean_f1, so this used to just show "—" for every row).
        const val = currentLargeAreaTask === 'regression' ? m.mean_r2 : m.mean_f1;
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
        const color = getVariantColor(name);
        tr.innerHTML = `
            <td style="padding:6px;"><span style="color:${color.line}">\u25cf</span> ${getVariantLabel(name)}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_r2.toFixed(4)} \u00b1 ${metrics.std_r2.toFixed(4)}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_rmse.toFixed(4)} \u00b1 ${metrics.std_rmse.toFixed(4)}</td>
            <td style="text-align:right; padding:6px;">${metrics.mean_mae.toFixed(4)} \u00b1 ${metrics.std_mae.toFixed(4)}</td>
        `;
        tbody.appendChild(tr);
    }

    renderRegressionScatter(aggregate);
}

// Predicted-vs-actual scatter plot, one dataset per model plus a dashed
// y=x reference line (perfect prediction). aggregate[name].scatter is
// {y_true, y_pred} -- present only on models that had at least one
// successful fit at the largest training percentage (see
// run_learning_curve's docstring, tessera-eval v1.6.0+); older
// tessera-eval versions simply won't have it, so this quietly no-ops.
function renderRegressionScatter(aggregate) {
    const wrap = document.getElementById('val-regression-scatter-wrap');
    const canvas = document.getElementById('val-regression-scatter-chart');
    if (!wrap || !canvas) return;

    const modelsWithScatter = Object.entries(aggregate).filter(([, m]) => m.scatter && m.scatter.y_true && m.scatter.y_true.length);
    if (valScatterChart) { valScatterChart.destroy(); valScatterChart = null; }
    if (modelsWithScatter.length === 0) {
        wrap.style.display = 'none';
        return;
    }
    wrap.style.display = '';

    let lo = Infinity, hi = -Infinity;
    const datasets = modelsWithScatter.map(([name, m]) => {
        const color = getVariantColor(name);
        const points = m.scatter.y_true.map((yt, i) => {
            const yp = m.scatter.y_pred[i];
            if (yt < lo) lo = yt;
            if (yt > hi) hi = yt;
            if (yp < lo) lo = yp;
            if (yp > hi) hi = yp;
            return { x: yt, y: yp };
        });
        return {
            label: getVariantLabel(name),
            data: points,
            backgroundColor: color.line.replace('1)', '0.55)'),
            borderColor: color.line,
            pointRadius: 3,
            pointHoverRadius: 5,
            showLine: false,
        };
    });

    // y=x reference line, drawn across the full data range.
    const pad = (hi - lo) * 0.05 || 1;
    datasets.push({
        label: 'Perfect prediction (y=x)',
        data: [{ x: lo - pad, y: lo - pad }, { x: hi + pad, y: hi + pad }],
        borderColor: 'rgba(200,200,200,0.6)',
        borderDash: [6, 4],
        borderWidth: 1.5,
        pointRadius: 0,
        showLine: true,
        fill: false,
    });

    const ctx = canvas.getContext('2d');
    valScatterChart = new Chart(ctx, {
        type: 'scatter',
        data: { datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: '#ddd', boxWidth: 10, font: { size: 11 } },
                },
                title: {
                    display: true,
                    text: 'Predicted vs Actual',
                    color: '#eee',
                    font: { size: 15, weight: 'bold' },
                },
            },
            scales: {
                x: {
                    title: { display: true, text: 'Actual', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                    min: lo - pad, max: hi + pad,
                },
                y: {
                    title: { display: true, text: 'Predicted', color: '#aaa' },
                    ticks: { color: '#aaa' },
                    grid: { color: 'rgba(255,255,255,0.08)' },
                    min: lo - pad, max: hi + pad,
                },
            },
        },
    });
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
    const colors = modelNames.map(n => getVariantColor(n).line);

    valChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: modelNames.map(n => getVariantLabel(n)),
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
            // errorBarPlugin's afterDraw reads ds.data[i] (the *final* value)
            // to position its whisker caps, but Chart.js's own bar-growth
            // animation draws the bar at an *interpolated*, still-growing
            // height during the ~1s default transition -- so for the whole
            // animation window the whisker floats above a visibly too-short
            // bar. Confirmed live (Louis Driver, 2026-08-21) and reproduced
            // headlessly: a screenshot/glance during that window shows a bar
            // that looks capped at roughly 80% of its real value with the
            // correct value only marked by a stray line above it. Disabling
            // animation removes the window entirely rather than trying to
            // keep the plugin in sync with an animating value every frame.
            animation: false,
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
    const colors = modelNames.map(n => getVariantColor(n).line);

    valChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: modelNames.map(n => getVariantLabel(n)),
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
            // See the identical comment in renderRegressionBarChart: without
            // this, errorBarPlugin's whisker caps (positioned from the
            // final data value) float above the bar during Chart.js's
            // default ~1s grow animation (the bar itself draws at an
            // interpolated, still-growing height).
            animation: false,
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

    // Set year. The config format isn't train/test-split-aware (see
    // generateConfig's "years" comment) -- both selects default to the same
    // uploaded value.
    if (config.years && config.years.length > 0) {
        const yearVal = String(config.years[0]);
        const trainYearSel = document.getElementById('val-train-year-select');
        const testYearSel = document.getElementById('val-test-year-select');
        if (trainYearSel) trainYearSel.value = yearVal;
        if (testYearSel) testYearSel.value = yearVal;
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

    // Set classifier params (supports both object and list-of-objects format)
    const allParams = { ...(config.classifiers || {}), ...(config.regressors || {}) };
    for (const [clf, paramValue] of Object.entries(allParams)) {
        if (Array.isArray(paramValue)) {
            // Multiple variants: first remove existing variant rows, then create them
            removeAllVariants(clf);
            paramValue.forEach((variantParams, i) => {
                if (i > 0) addVariant(clf);
                for (const [param, value] of Object.entries(variantParams)) {
                    const el = document.querySelector(`.val-params [data-clf="${clf}"][data-param="${param}"][data-variant="${i}"]`);
                    if (el) el.value = value;
                }
            });
        } else {
            for (const [param, value] of Object.entries(paramValue)) {
                const el = document.querySelector(`.val-params [data-clf="${clf}"][data-param="${param}"]`);
                if (el) el.value = value;
            }
        }
    }

    // Set max training samples
    if (config.max_training_samples) {
        const input = document.getElementById('val-max-train-large');
        if (input) input.value = config.max_training_samples;
    }

    // Set sampling strategy
    if (config.sampling) {
        const sel = document.getElementById('val-sampling-select');
        if (sel) sel.value = config.sampling;
    }

    // Set max patches
    if (config.max_patches) {
        const input = document.getElementById('val-max-patches');
        if (input) input.value = config.max_patches;
    }

    // Restore spatial bounding boxes
    if (config.spatial_bboxes) {
        clearAllBboxes();
        for (const [type, bboxes] of Object.entries(config.spatial_bboxes)) {
            if (!Array.isArray(bboxes)) continue;
            for (const bbox of bboxes) {
                if (Array.isArray(bbox) && bbox.length === 4) {
                    addBboxFromCoords(type, bbox);
                }
            }
        }
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

// ── Spatial bounding box drawing (Phase 1) ──

function rectToBbox(rect) {
    const b = rect.getBounds();
    return [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()];
}

function updateBboxSummary() {
    const el = document.getElementById('val-bbox-summary');
    if (!el) return;
    const t = spatialBboxes.train.length;
    const te = spatialBboxes.test.length;
    const m = spatialBboxes.map.length;
    if (t + te + m === 0) {
        el.textContent = 'No rectangles drawn';
        el.style.color = '#888';
    } else {
        const parts = [];
        if (t > 0) parts.push(`Train: ${t}`);
        if (te > 0) parts.push(`Test: ${te}`);
        if (m > 0) parts.push(`Map: ${m}`);
        el.textContent = parts.join(', ');
        el.style.color = '#ccc';
    }
    updateCreateMapButton();
}

function ensureBboxFeatureGroup() {
    if (!bboxFeatureGroup) {
        bboxFeatureGroup = new L.FeatureGroup();
    }
    if (window.maps && window.maps.rgb && !window.maps.rgb.hasLayer(bboxFeatureGroup)) {
        window.maps.rgb.addLayer(bboxFeatureGroup);
    }
}

function addBboxRectangle(type, rect) {
    rect._bboxType = type;
    spatialBboxes[type].push(rect);
    ensureBboxFeatureGroup();
    bboxFeatureGroup.addLayer(rect);

    const label = type.charAt(0).toUpperCase() + type.slice(1);
    rect.bindTooltip(label, { permanent: false, direction: 'center' });

    rect.on('click', function() {
        if (confirm(`Delete this ${label} rectangle?`)) {
            bboxFeatureGroup.removeLayer(rect);
            spatialBboxes[type] = spatialBboxes[type].filter(r => r !== rect);
            updateBboxSummary();
        }
    });

    updateBboxSummary();
}

function addBboxFromCoords(type, bbox) {
    // bbox = [south, west, north, east]
    const bounds = [[bbox[0], bbox[1]], [bbox[2], bbox[3]]];
    const style = BBOX_COLORS[type] || BBOX_COLORS.train;
    const rect = L.rectangle(bounds, style);
    addBboxRectangle(type, rect);
}

function clearAllBboxes() {
    if (bboxDrawHandler) {
        bboxDrawHandler.disable();
        bboxDrawHandler = null;
    }
    if (bboxFeatureGroup) {
        bboxFeatureGroup.clearLayers();
    }
    spatialBboxes = { train: [], test: [], map: [] };
    updateBboxSummary();
    const map = window.maps && window.maps.rgb;
    if (map) { map.dragging.enable(); map.getContainer().style.cursor = ''; }
    // Reset dropdown to placeholder
    const typeSel = document.getElementById('val-bbox-type');
    if (typeSel) typeSel.selectedIndex = 0;
}

function initBboxDrawing() {
    if (!window.maps || !window.maps.rgb || typeof L.Draw === 'undefined') return;
    ensureBboxFeatureGroup();

    window.maps.rgb.on(L.Draw.Event.CREATED, function(e) {
        if (e.layerType !== 'rectangle') return;
        if (window.currentPanelMode !== 'validation') return;
        const rect = e.layer;
        const style = BBOX_COLORS[currentBboxType] || BBOX_COLORS.train;
        rect.setStyle(style);
        addBboxRectangle(currentBboxType, rect);
        // Create a fresh handler so user can keep drawing more rectangles
        bboxDrawHandler = null;
        const typeSel = document.getElementById('val-bbox-type');
        if (typeSel && typeSel.value) {
            toggleBboxDraw();
        }
    });
}

function toggleBboxDraw() {
    const map = window.maps && window.maps.rgb;
    if (bboxDrawHandler) {
        bboxDrawHandler.disable();
        bboxDrawHandler = null;
        if (map) { map.dragging.enable(); map.getContainer().style.cursor = ''; }
        return;
    }
    if (!map || typeof L.Draw === 'undefined') return;
    ensureBboxFeatureGroup();

    const typeSel = document.getElementById('val-bbox-type');
    if (typeSel && typeSel.value) currentBboxType = typeSel.value;

    const style = BBOX_COLORS[currentBboxType] || BBOX_COLORS.train;
    bboxDrawHandler = new L.Draw.Rectangle(map, {
        shapeOptions: { ...style },
    });
    map.dragging.disable();
    map.getContainer().style.cursor = 'crosshair';
    bboxDrawHandler.enable();
}

function hasSpatialBboxes() {
    return spatialBboxes.train.length > 0 || spatialBboxes.test.length > 0;
}

function getSpatialBboxData() {
    return {
        train_bboxes: spatialBboxes.train.map(r => rectToBbox(r)),
        test_bboxes: spatialBboxes.test.map(r => rectToBbox(r)),
    };
}

// Selecting an area type starts drawing immediately
document.getElementById('val-bbox-type').addEventListener('change', function() {
    if (bboxDrawHandler) {
        bboxDrawHandler.disable();
        bboxDrawHandler = null;
    }
    const map = window.maps && window.maps.rgb;
    if (!this.value) {
        // Placeholder selected — stop drawing, re-enable pan
        if (map) { map.dragging.enable(); map.getContainer().style.cursor = ''; }
        return;
    }
    currentBboxType = this.value;
    toggleBboxDraw();
});

// Deferred init — called after maps are ready
function tryInitBboxDrawing() {
    if (window.maps && window.maps.rgb) {
        initBboxDrawing();
    } else {
        // Retry after maps are created
        setTimeout(tryInitBboxDrawing, 500);
    }
}
tryInitBboxDrawing();

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

    // Restore spatial bboxes on the map
    ensureBboxFeatureGroup();
    updateBboxSummary();

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

// ── Hyperparameter variant UI ──

/**
 * Add a variant parameter set to a classifier's params block.
 * Duplicates the base (variant 0) parameter inputs with a new variant index.
 */
function addVariant(clfName) {
    const block = document.querySelector(`.val-clf-block input[value="${clfName}"]`);
    if (!block) return;
    const paramsDiv = block.closest('.val-clf-block').querySelector('.val-params');
    if (!paramsDiv) return;

    // Find current max variant index
    const existing = paramsDiv.querySelectorAll(`[data-clf="${clfName}"]`);
    let maxVariant = 0;
    existing.forEach(el => {
        const v = parseInt(el.dataset.variant || '0');
        if (v > maxVariant) maxVariant = v;
    });
    const newIdx = maxVariant + 1;

    // Find all base (variant 0) inputs to clone
    const baseInputs = paramsDiv.querySelectorAll(`[data-clf="${clfName}"][data-variant="0"], [data-clf="${clfName}"]:not([data-variant])`);
    if (baseInputs.length === 0) return;

    // Create a separator + label
    const sep = document.createElement('div');
    sep.className = 'val-variant-sep';
    sep.dataset.variant = newIdx;
    sep.dataset.clf = clfName;
    sep.style.cssText = 'width:100%; border-top:1px dashed #555; margin:6px 0 2px 0; padding-top:2px; display:flex; align-items:center; justify-content:space-between; font-size:11px; color:#999;';
    const labelSpan = document.createElement('span');
    labelSpan.textContent = `v${newIdx}`;
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.textContent = '\u2212';
    removeBtn.title = 'Remove this variant';
    removeBtn.style.cssText = 'background:none; border:1px solid #666; color:#f66; font-size:14px; line-height:1; width:20px; height:20px; border-radius:50%; cursor:pointer; padding:0;';
    removeBtn.onclick = () => removeVariant(clfName, newIdx);
    sep.appendChild(labelSpan);
    sep.appendChild(removeBtn);
    paramsDiv.appendChild(sep);

    // Clone each base input
    baseInputs.forEach(el => {
        const label = el.closest('label');
        if (!label) return;
        const newLabel = label.cloneNode(true);
        const newInput = newLabel.querySelector('input, select');
        if (newInput) {
            newInput.dataset.variant = String(newIdx);
            // Reset to default value
            if (newInput.tagName === 'SELECT') {
                newInput.selectedIndex = 0;
            }
        }
        newLabel.dataset.variant = newIdx;
        paramsDiv.appendChild(newLabel);
    });

    // Ensure the "+" button is visible (it should already be)
    updateVariantButtonState(clfName);
}

/**
 * Remove a variant parameter set.
 */
function removeVariant(clfName, variantIdx) {
    const block = document.querySelector(`.val-clf-block input[value="${clfName}"]`);
    if (!block) return;
    const paramsDiv = block.closest('.val-clf-block').querySelector('.val-params');
    if (!paramsDiv) return;

    // Remove separator
    const sep = paramsDiv.querySelector(`.val-variant-sep[data-clf="${clfName}"][data-variant="${variantIdx}"]`);
    if (sep) sep.remove();

    // Remove all inputs for this variant
    paramsDiv.querySelectorAll(`[data-variant="${variantIdx}"]`).forEach(el => {
        const label = el.closest('label');
        if (label && label.dataset.variant == variantIdx) {
            label.remove();
        } else if (el.classList && el.classList.contains('val-variant-sep')) {
            el.remove();
        }
    });

    updateVariantButtonState(clfName);
}

/**
 * Remove all variant parameter sets (keep only base variant 0).
 */
function removeAllVariants(clfName) {
    const block = document.querySelector(`.val-clf-block input[value="${clfName}"]`);
    if (!block) return;
    const paramsDiv = block.closest('.val-clf-block').querySelector('.val-params');
    if (!paramsDiv) return;

    // Remove all variant separators and variant-labelled inputs
    paramsDiv.querySelectorAll('.val-variant-sep').forEach(el => {
        if (el.dataset.clf === clfName) el.remove();
    });
    paramsDiv.querySelectorAll('[data-variant]').forEach(el => {
        const v = parseInt(el.dataset.variant);
        if (v > 0) {
            const label = el.closest('label');
            if (label && parseInt(label.dataset.variant) > 0) {
                label.remove();
            }
        }
    });
}

/**
 * Update the "+" button visibility for a classifier.
 */
function updateVariantButtonState(clfName) {
    // No-op for now; the button is always visible
}

window.addVariant = addVariant;
window.removeVariant = removeVariant;

async function clearShapefiles() {
    await fetch(evalUrl('clear-shapefiles'), { method: 'POST' });
    valFieldData = null;
    valUploadedFilename = null;
    valEstimatedLabelledPixels = 0;
    const dropZone = document.getElementById('val-drop-zone');
    if (dropZone) {
        dropZone.textContent = 'Drop .zip shapefiles here (multiple allowed)';
        dropZone.classList.remove('uploaded');
    }
    const sel = document.getElementById('val-field-select');
    if (sel) { sel.innerHTML = '<option value="">-- upload shapefile first --</option>'; sel.disabled = true; }
    document.getElementById('val-run-btn').disabled = true;
    document.getElementById('val-status').textContent = 'Shapefiles cleared';
    // Remove GeoJSON overlay
    if (valGeoJsonLayer && window.maps && window.maps.rgb) {
        window.maps.rgb.removeLayer(valGeoJsonLayer);
        valGeoJsonLayer = null;
    }
    valGeoJsonData = null;
}

window.clearShapefiles = clearShapefiles;
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
window.toggleBboxDraw = toggleBboxDraw;
window.clearAllBboxes = clearAllBboxes;
Object.defineProperty(window, 'lastEvalData', {
    get: () => lastEvalData,
    set: (v) => { lastEvalData = v; },
    configurable: true,
});
