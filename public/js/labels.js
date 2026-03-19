// labels.js — Manual labels, persistent labels, overlays, polygon drawing, import/export
// Extracted from viewer.html as an ES module.

// ── State (module-private, exposed on window via defineProperty) ──
// Note: manualLabels, currentManualLabel, savedLabels, currentSearchCache,
// manualClassOverlays, _classMatchCache are bridged below.
// Other state vars (labelMode, polygonDrawHandler, etc.) are module-internal.

function getAvailableYears() {
    const sel = document.getElementById('embedding-year-selector');
    return Array.from(sel.options).map(o => parseInt(o.value)).sort();
}

async function computeLabelTimeline(labelGroup) {
    const years = getAvailableYears();
    const results = [];
    const progressEl = document.getElementById('timeline-progress');
    for (let i = 0; i < years.length; i++) {
        progressEl.textContent = `Loading year ${years[i]} (${i + 1} of ${years.length})...`;
        await new Promise(r => setTimeout(r, 0));
        const data = await window.loadVectorDataOnly(window.currentViewportName, years[i]);
        // Re-extract embeddings from THIS year's data at each source pixel
        const searches = [];
        for (const l of labelGroup) {
            if (!l.source_pixel) continue;
            const emb = window.extractFromData(data, l.source_pixel.lat, l.source_pixel.lon);
            if (emb) {
                searches.push({ embedding: emb, threshSq: l.threshold * l.threshold });
            }
        }
        const count = searches.length > 0 ? window.searchMultiInVectorData(data, searches) : 0;
        results.push({ year: years[i], count });
    }
    return results;
}

async function showLabelTimeline(labelName) {
    const labelGroup = savedLabels.filter(l => l.name === labelName);
    if (labelGroup.length === 0) return;
    // Static labels (from segmentation) have no source_pixel — timeline not supported
    if (!labelGroup.some(l => l.source_pixel)) {
        alert('Timeline is not available for segmentation labels.');
        return;
    }
    const label = labelGroup[0];

    const overlay = document.getElementById('timeline-modal-overlay');
    const nameEl = document.getElementById('timeline-label-name');
    const swatchEl = document.getElementById('timeline-color-swatch');
    const progressEl = document.getElementById('timeline-progress');
    const resultsEl = document.getElementById('timeline-results');
    const bodyEl = document.getElementById('timeline-table-body');
    const summaryEl = document.getElementById('timeline-summary');

    // Setup and show modal
    const markerNote = labelGroup.length > 1 ? ` (${labelGroup.length} markers)` : '';
    nameEl.textContent = label.name + markerNote;
    swatchEl.style.background = label.color;
    progressEl.style.display = 'block';
    progressEl.textContent = 'Starting timeline analysis...';
    resultsEl.style.display = 'none';
    bodyEl.innerHTML = '';
    summaryEl.innerHTML = '';
    overlay.style.display = 'flex';

    try {
        const timeline = await computeLabelTimeline(labelGroup);
        const maxCount = Math.max(...timeline.map(t => t.count), 1);

        // Build table rows with bar chart
        bodyEl.innerHTML = timeline.map(t => {
            const pct = (t.count / maxCount * 100).toFixed(1);
            return `<tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 6px; color: #333; font-weight: 600; font-size: 13px;">${t.year}</td>
                <td style="padding: 6px; text-align: right; color: #333; font-size: 13px;">${t.count.toLocaleString()}</td>
                <td style="padding: 6px;">
                    <div style="background: #eee; border-radius: 3px; height: 18px; position: relative;">
                        <div style="background: ${label.color}; width: ${pct}%; height: 100%; border-radius: 3px; min-width: ${t.count > 0 ? '2px' : '0'};"></div>
                    </div>
                </td>
            </tr>`;
        }).join('');

        // Change summary
        if (timeline.length >= 2) {
            const first = timeline[0];
            const last = timeline[timeline.length - 1];
            if (first.count > 0) {
                const changePct = ((last.count - first.count) / first.count * 100).toFixed(1);
                const direction = changePct > 0 ? 'increase' : changePct < 0 ? 'decrease' : 'no change';
                const arrow = changePct > 0 ? '\u2191' : changePct < 0 ? '\u2193' : '\u2194';
                summaryEl.innerHTML = `<strong>${arrow} ${Math.abs(changePct)}% ${direction}</strong> from ${first.year} (${first.count.toLocaleString()} px) to ${last.year} (${last.count.toLocaleString()} px)`;
            } else {
                summaryEl.innerHTML = `${first.year}: 0 pixels &rarr; ${last.year}: ${last.count.toLocaleString()} pixels`;
            }
        }

        progressEl.style.display = 'none';
        resultsEl.style.display = 'block';
    } catch (err) {
        progressEl.textContent = `Error: ${err.message}`;
        console.error('[TIMELINE] Failed:', err);
    }
}

async function showManualLabelTimeline(className) {
    const classLabels = manualLabels.filter(l => l.name === className && l.embedding);
    if (classLabels.length === 0) return;
    const first = classLabels[0];
    const threshold = first.threshold || 0;
    if (threshold <= 0) {
        alert('Set a similarity threshold > 0 to run timeline analysis.');
        return;
    }

    const overlay = document.getElementById('timeline-modal-overlay');
    const nameEl = document.getElementById('timeline-label-name');
    const swatchEl = document.getElementById('timeline-color-swatch');
    const progressEl = document.getElementById('timeline-progress');
    const resultsEl = document.getElementById('timeline-results');
    const bodyEl = document.getElementById('timeline-table-body');
    const summaryEl = document.getElementById('timeline-summary');

    const markerNote = classLabels.length > 1 ? ` (${classLabels.length} markers)` : '';
    nameEl.textContent = first.name + markerNote;
    swatchEl.style.background = first.color;
    progressEl.style.display = 'block';
    progressEl.textContent = 'Starting timeline analysis...';
    resultsEl.style.display = 'none';
    bodyEl.innerHTML = '';
    summaryEl.innerHTML = '';
    overlay.style.display = 'flex';

    try {
        const years = getAvailableYears();
        const timeline = [];
        for (let i = 0; i < years.length; i++) {
            progressEl.textContent = `Loading year ${years[i]} (${i + 1} of ${years.length})...`;
            await new Promise(r => setTimeout(r, 0));
            const data = await window.loadVectorDataOnly(window.currentViewportName, years[i]);
            const searches = [];
            for (const l of classLabels) {
                if (!l.embedding) continue;
                const emb = window.extractFromData(data, l.lat, l.lon);
                if (emb) {
                    searches.push({ embedding: emb, threshSq: threshold * threshold });
                }
            }
            const count = searches.length > 0 ? window.searchMultiInVectorData(data, searches) : 0;
            timeline.push({ year: years[i], count });
        }

        const maxCount = Math.max(...timeline.map(t => t.count), 1);
        bodyEl.innerHTML = timeline.map(t => {
            const pct = (t.count / maxCount * 100).toFixed(1);
            return `<tr style="border-bottom: 1px solid #eee;">
                <td style="padding: 6px; color: #333; font-weight: 600; font-size: 13px;">${t.year}</td>
                <td style="padding: 6px; text-align: right; color: #333; font-size: 13px;">${t.count.toLocaleString()}</td>
                <td style="padding: 6px;">
                    <div style="background: #eee; border-radius: 3px; height: 18px; position: relative;">
                        <div style="background: ${first.color}; width: ${pct}%; height: 100%; border-radius: 3px; min-width: ${t.count > 0 ? '2px' : '0'};"></div>
                    </div>
                </td>
            </tr>`;
        }).join('');

        if (timeline.length >= 2) {
            const f = timeline[0], last = timeline[timeline.length - 1];
            if (f.count > 0) {
                const changePct = ((last.count - f.count) / f.count * 100).toFixed(1);
                const direction = changePct > 0 ? 'increase' : changePct < 0 ? 'decrease' : 'no change';
                const arrow = changePct > 0 ? '\u2191' : changePct < 0 ? '\u2193' : '\u2194';
                summaryEl.innerHTML = `<strong>${arrow} ${Math.abs(changePct)}% ${direction}</strong> from ${f.year} (${f.count.toLocaleString()} px) to ${last.year} (${last.count.toLocaleString()} px)`;
            } else {
                summaryEl.innerHTML = `${f.year}: 0 pixels &rarr; ${last.year}: ${last.count.toLocaleString()} pixels`;
            }
        }

        progressEl.style.display = 'none';
        resultsEl.style.display = 'block';
    } catch (err) {
        progressEl.textContent = `Error: ${err.message}`;
        console.error('[MANUAL TIMELINE] Failed:', err);
    }
}

// ===== MANUAL LABEL STATE =====
let manualLabels = [];       // [{id, name, color, code, type:'point'|'similarity'|'polygon', lat, lon, embedding, threshold, visible, matchCount, vertices}]
let currentManualLabel = null; // {name, color, code}
let labelMode = 'autolabel';   // 'autolabel' | 'manual'

function _activeLabelKey() {
    return 'currentManualLabel_' + (window.currentViewportName || '');
}
let manualClassifyOverlay = null; // L.imageOverlay on window.maps.panel5
let manualLabelIdCounter = 0;
let manualClassifyDebounceTimer = null;

// Manual label overlay layers on window.maps.rgb (per-class window.DirectCanvasLayer instances)
let manualClassOverlays = {};  // {className: {layerGroup, layer}}
let _classMatchCache = {};     // {className: [{lat, lon}, ...]} cached similarity+polygon matches for Panel 4
let collapsedClasses = new Set();

// Schema state is in js/schema.js (window.activeSchema, activeSchemaMode)

// Polygon drawing state (Leaflet.Draw)
let polygonDrawHandler = null;   // L.Draw.Polygon instance
let isPolygonDrawing = false;
let drawnItems = null;           // L.FeatureGroup for drawn polygons
let polygonSearchMode = 'mean';  // 'mean' or 'union'

function setLabelMode(mode) {
    labelMode = mode;
    const autoView = document.getElementById('panel6-autolabel-view');
    const manualView = document.getElementById('panel6-manual-view');
    const classifyBtn = document.getElementById('manual-classify-btn');
    if (mode === 'manual') {
        autoView.style.display = 'none';
        manualView.style.display = 'flex';
        document.getElementById('panel6-header-text').textContent = 'Manual Label';
        document.getElementById('panel2-title').textContent = 'Create labelled points';
        document.getElementById('panel5-title').textContent = 'Classification results';
        if (classifyBtn) classifyBtn.style.display = '';
        restoreManualLabelState();
        // Hide segmentation overlay if present
        if (window.segOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(window.segOverlay)) {
            window.maps.panel5.removeLayer(window.segOverlay);
        }
    } else {
        autoView.style.display = 'flex';
        manualView.style.display = 'none';
        document.getElementById('panel6-header-text').textContent = 'Auto-label';
        document.getElementById('panel2-title').textContent = 'Satellite';
        document.getElementById('panel5-title').textContent = 'Segmentation';
        if (classifyBtn) classifyBtn.style.display = 'none';
        // Remove manual classification overlay
        if (manualClassifyOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(manualClassifyOverlay)) {
            window.maps.panel5.removeLayer(manualClassifyOverlay);
        }
        manualClassifyOverlay = null;
        // Restore segmentation overlay if it exists
        if (window.segOverlay) {
            const segRules = window.PANEL5_LAYER_RULES[window.currentPanelMode] || window.PANEL5_LAYER_RULES['explore'];
            if (segRules.segOverlay) window.segOverlay.addTo(window.maps.panel5);
        }
    }
    localStorage.setItem('labelMode', mode);
}

function setCurrentManualLabel() {
    const name = document.getElementById('manual-label-name').value.trim();
    const color = document.getElementById('manual-label-color').value;
    if (!name) {
        alert('Please enter a label name.');
        return;
    }
    currentManualLabel = { name, color, code: null };
    // Show active label indicator
    const activeEl = document.getElementById('manual-active-label');
    activeEl.style.display = '';
    document.getElementById('manual-active-label-swatch').style.background = color;
    document.getElementById('manual-active-label-name').textContent = name;
    document.getElementById('manual-label-swatch').style.background = color;
    const acp = document.getElementById('active-label-color-picker');
    if (acp) acp.value = color;
    localStorage.setItem(_activeLabelKey(), JSON.stringify(currentManualLabel));
}

function updateManualLabelColor(color) {
    document.getElementById('manual-label-swatch').style.background = color;
    if (currentManualLabel) {
        currentManualLabel.color = color;
        document.getElementById('manual-active-label-swatch').style.background = color;
        localStorage.setItem(_activeLabelKey(), JSON.stringify(currentManualLabel));
        _syncClassColor(currentManualLabel.name, color);
    }
}

function updateActiveLabelColor(color) {
    if (!currentManualLabel) return;
    currentManualLabel.color = color;
    document.getElementById('manual-active-label-swatch').style.background = color;
    // Also update freetext swatch if present
    const s = document.getElementById('manual-label-swatch');
    if (s) s.style.background = color;
    const c = document.getElementById('manual-label-color');
    if (c) c.value = color;
    localStorage.setItem(_activeLabelKey(), JSON.stringify(currentManualLabel));
    _syncClassColor(currentManualLabel.name, color);
}

function _syncClassColor(className, color) {
    const classLabels = getClassLabels(className);
    if (classLabels.length === 0) return;
    for (const label of classLabels) label.color = color;
    saveManualLabelsToStorage();
    rebuildClassOverlay(className);
    renderManualLabelsList();
}

function restoreManualLabelState() {
    // Clear any stale overlays from prior viewport (handles bfcache restore)
    for (const [cls, entry] of Object.entries(manualClassOverlays)) {
        if (entry.layerGroup && window.maps.rgb) {
            window.maps.rgb.removeLayer(entry.layerGroup);
        }
    }
    manualClassOverlays = {};
    manualLabels = [];

    // Restore schema mode
    const savedSchemaMode = localStorage.getItem('schemaMode');
    if (savedSchemaMode && savedSchemaMode !== 'none' && savedSchemaMode !== window.activeSchemaMode) {
        window.loadSchema(savedSchemaMode);
    }

    // Restore current label
    const saved = localStorage.getItem(_activeLabelKey());
    if (saved) {
        try {
            currentManualLabel = JSON.parse(saved);
            // Ensure code field exists
            if (!('code' in currentManualLabel)) currentManualLabel.code = null;
            const nameInput = document.getElementById('manual-label-name');
            const colorInput = document.getElementById('manual-label-color');
            const swatch = document.getElementById('manual-label-swatch');
            if (nameInput) nameInput.value = currentManualLabel.name;
            if (colorInput) colorInput.value = currentManualLabel.color;
            if (swatch) swatch.style.background = currentManualLabel.color;
            const activeEl = document.getElementById('manual-active-label');
            if (activeEl) {
                activeEl.style.display = '';
                document.getElementById('manual-active-label-swatch').style.background = currentManualLabel.color;
                const nameText = currentManualLabel.code ? `[${currentManualLabel.code}] ${currentManualLabel.name}` : currentManualLabel.name;
                document.getElementById('manual-active-label-name').textContent = nameText;
                const acp = document.getElementById('active-label-color-picker');
                if (acp) acp.value = currentManualLabel.color;
            }
        } catch(e) { /* ignore */ }
    }
    // Restore manual labels
    const savedLabelsStr = localStorage.getItem('manualLabels_' + window.currentViewportName);
    if (savedLabelsStr) {
        try {
            manualLabels = JSON.parse(savedLabelsStr);
            manualLabelIdCounter = manualLabels.reduce((max, l) => Math.max(max, l.id), 0);
        } catch(e) { manualLabels = []; }
    }
    renderManualLabelsList();
    // Rebuild overlays on window.maps.rgb for restored labels
    rebuildManualOverlays();
}

// --- Class-based grouping helpers ---

function getClassLabels(className) {
    return manualLabels.filter(l => l.name === className);
}

function getClassThreshold(className) {
    const existing = manualLabels.find(l => l.name === className && l.threshold > 0);
    return existing ? existing.threshold : 0;
}

function rebuildClassOverlay(className) {
    // Remove old overlay for this class
    if (manualClassOverlays[className]) {
        if (manualClassOverlays[className].layerGroup && window.maps.rgb) {
            window.maps.rgb.removeLayer(manualClassOverlays[className].layerGroup);
        }
        delete manualClassOverlays[className];
    }
    delete _classMatchCache[className];

    const classLabels = getClassLabels(className);
    if (classLabels.length === 0) return;

    const anyVisible = classLabels.some(l => l.visible);
    if (!anyVisible || !window.maps.rgb) return;

    const layerGroup = L.layerGroup();
    const color = classLabels[0].color;
    const threshold = classLabels[0].threshold || 0;
    let totalMatchCount = 0;
    const cachedCoords = [];  // collect all lat/lon for Panel 4

    // Add pin markers for point-type labels
    for (const label of classLabels) {
        if (!label.visible) continue;
        if (label.type === 'point') {
            const pinMarker = L.circleMarker([label.lat, label.lon], {
                radius: 3, fillColor: label.color, color: '#fff', weight: 1, fillOpacity: 0.9
            });
            layerGroup.addLayer(pinMarker);
            cachedCoords.push({ lat: label.lat, lon: label.lon });
        }
        // Add polygon outlines
        if (label.type === 'polygon' && label.vertices) {
            const polyLatLngs = label.vertices.map(v => L.latLng(v[0], v[1]));
            const polyline = L.polygon(polyLatLngs, {
                color: label.color, fillColor: 'transparent',
                fillOpacity: 0, weight: 2
            });
            layerGroup.addLayer(polyline);
        }
    }

    // Rasterize polygon interiors
    if (window.localVectors) {
        const gt = window.localVectors.metadata.geotransform;
        for (const label of classLabels) {
            if (!label.visible || label.type !== 'polygon' || !label.vertices) continue;
            const pixVerts = label.vertices.map(v => [
                Math.round((v[1] - gt.c) / gt.a),
                Math.round((v[0] - gt.f) / gt.e)
            ]);
            const polyMatches = rasterizePolygon(pixVerts);
            label.pixelCount = polyMatches.length;
            if (polyMatches.length > 0) {
                const polyCanvas = new window.DirectCanvasLayer(polyMatches, window.maps.rgb, color);
                layerGroup.addLayer(polyCanvas);
                totalMatchCount += polyMatches.length;
                for (const m of polyMatches) cachedCoords.push({ lat: m.lat, lon: m.lon });
            }
        }
    }

    // Run similarity search with all class embeddings (union)
    if (window.localVectors && threshold > 0) {
        const embeddings = [];
        for (const label of classLabels) {
            if (!label.visible) continue;
            // Union-mode polygon: use all stored individual embeddings
            if (label.embeddings && label.embeddings.length > 0) {
                for (const e of label.embeddings) embeddings.push(new Float32Array(e));
            } else if (label.embedding) {
                embeddings.push(new Float32Array(label.embedding));
            }
        }
        if (embeddings.length > 0) {
            const matches = window.localSearchSimilarMulti(embeddings, threshold);
            totalMatchCount += matches.length;
            if (matches.length > 0) {
                const canvasLayer = new window.DirectCanvasLayer(matches, window.maps.rgb, color);
                layerGroup.addLayer(canvasLayer);
                for (const m of matches) cachedCoords.push({ lat: m.lat, lon: m.lon });
            }
        }
    }

    // Update matchCount on all labels in the class
    for (const label of classLabels) {
        label.matchCount = totalMatchCount;
    }

    _classMatchCache[className] = cachedCoords;
    layerGroup.addTo(window.maps.rgb);
    manualClassOverlays[className] = { layerGroup, layer: null };
    window.updatePanel4ManualLabels();
}


function toggleClassExpand(className) {
    if (collapsedClasses.has(className)) {
        collapsedClasses.delete(className);
    } else {
        collapsedClasses.add(className);
    }
    renderManualLabelsList();
}

function toggleClassVisibility(className) {
    const classLabels = getClassLabels(className);
    const anyVisible = classLabels.some(l => l.visible);
    const newState = !anyVisible;
    for (const label of classLabels) {
        label.visible = newState;
    }
    if (manualClassOverlays[className]) {
        if (newState) {
            rebuildClassOverlay(className);
        } else {
            if (window.maps.rgb && manualClassOverlays[className].layerGroup) {
                window.maps.rgb.removeLayer(manualClassOverlays[className].layerGroup);
            }
            delete manualClassOverlays[className];
            delete _classMatchCache[className];
            window.updatePanel4ManualLabels();
        }
    } else if (newState) {
        rebuildClassOverlay(className);
    }
    saveManualLabelsToStorage();
    renderManualLabelsList();
}

// --- Core CRUD and visibility ---

function rebuildManualOverlays() {
    // Clear all existing class overlays
    for (const name of Object.keys(manualClassOverlays)) {
        if (manualClassOverlays[name].layerGroup && window.maps.rgb) {
            window.maps.rgb.removeLayer(manualClassOverlays[name].layerGroup);
        }
    }
    manualClassOverlays = {};
    _classMatchCache = {};

    if (!window.localVectors || !window.maps.rgb) return;

    // Iterate unique class names
    const classNames = [...new Set(manualLabels.map(l => l.name))];
    for (const className of classNames) {
        rebuildClassOverlay(className);
    }
}

function saveManualLabelsToStorage() {
    if (!window.currentViewportName) return;
    localStorage.setItem('manualLabels_' + window.currentViewportName, JSON.stringify(manualLabels));
}

function markExportDirty() {
    const btn = document.getElementById('labelling-export-btn');
    if (!btn) return;
    btn.style.background = '#c53030';
    btn.style.borderColor = '#c53030';
    btn.innerHTML = '&#9888; Export';
    btn.title = 'Unexported labels';
}

function markExportClean() {
    const btn = document.getElementById('labelling-export-btn');
    if (!btn) return;
    btn.style.background = '#333';
    btn.style.borderColor = '#555';
    btn.innerHTML = 'Export';
    btn.title = '';
}

function addManualLabel(entry) {
    manualLabelIdCounter++;
    entry.id = manualLabelIdCounter;
    entry.visible = true;
    manualLabels.push(entry);
    saveManualLabelsToStorage();
    renderManualLabelsList();
    markExportDirty();
}

function removeManualClass(className) {
    if (manualClassOverlays[className]) {
        if (manualClassOverlays[className].layerGroup && window.maps.rgb) {
            window.maps.rgb.removeLayer(manualClassOverlays[className].layerGroup);
        }
        delete manualClassOverlays[className];
    }
    delete _classMatchCache[className];
    manualLabels = manualLabels.filter(l => l.name !== className);
    saveManualLabelsToStorage();
    renderManualLabelsList();
    window.updatePanel4ManualLabels();
}

function removeManualLabel(id) {
    const label = manualLabels.find(l => l.id === id);
    const className = label ? label.name : null;
    manualLabels = manualLabels.filter(l => l.id !== id);
    saveManualLabelsToStorage();
    if (className) {
        const remaining = getClassLabels(className);
        if (remaining.length === 0) {
            if (manualClassOverlays[className]) {
                if (manualClassOverlays[className].layerGroup && window.maps.rgb) {
                    window.maps.rgb.removeLayer(manualClassOverlays[className].layerGroup);
                }
                delete manualClassOverlays[className];
            }
            delete _classMatchCache[className];
        } else {
            rebuildClassOverlay(className);
        }
    }
    renderManualLabelsList();
    window.updatePanel4ManualLabels();
}

function toggleAllManualLabels() {
    const allVisible = manualLabels.length > 0 && manualLabels.every(l => l.visible);
    const newState = !allVisible;
    for (const label of manualLabels) {
        label.visible = newState;
    }
    for (const className of Object.keys(manualClassOverlays)) {
        if (newState) {
            rebuildClassOverlay(className);
        } else {
            if (manualClassOverlays[className].layerGroup && window.maps.rgb) {
                window.maps.rgb.removeLayer(manualClassOverlays[className].layerGroup);
            }
            delete manualClassOverlays[className];
        }
    }
    // If showing and some classes had no overlay yet, rebuild
    if (newState) rebuildManualOverlays();
    else window.updatePanel4ManualLabels();
    saveManualLabelsToStorage();
    renderManualLabelsList();
    const btn = document.getElementById('manual-hide-all-btn');
    if (btn) btn.textContent = newState ? 'Hide All' : 'Show All';
}

let _thresholdRAF = null;
let _thresholdSaveTimer = null;

function updateManualClassThreshold(className, newThreshold) {
    const classLabels = getClassLabels(className);
    for (const label of classLabels) {
        label.threshold = newThreshold;
    }

    // Debounce the expensive search+overlay to one per animation frame
    if (_thresholdRAF) cancelAnimationFrame(_thresholdRAF);
    _thresholdRAF = requestAnimationFrame(() => {
        _thresholdRAF = null;
        _applyClassThreshold(className, newThreshold);
    });

    // Debounce localStorage save until sliding stops (200ms)
    if (_thresholdSaveTimer) clearTimeout(_thresholdSaveTimer);
    _thresholdSaveTimer = setTimeout(() => {
        _thresholdSaveTimer = null;
        saveManualLabelsToStorage();
    }, 200);
}

function _applyClassThreshold(className, newThreshold) {
    rebuildClassOverlay(className);
    // Update px count in-place via data-class-count attribute
    const countEl = document.querySelector(`[data-class-count="${CSS.escape(className)}"]`);
    if (countEl) {
        const classLabels = getClassLabels(className);
        const count = classLabels.length > 0 ? (classLabels[0].matchCount || 0) : 0;
        countEl.textContent = count + 'px';
    }
}

function renderManualLabelsList() {
    const container = document.getElementById('manual-labels-list');
    if (!container) return;

    if (manualLabels.length === 0) {
        container.innerHTML = '<div style="color: #999; font-size: 12px; padding: 10px; text-align: center;">Set a label above, then click to place pins. Use the similarity slider to expand.</div>';
        return;
    }

    // Group labels by class name, preserving order of first appearance
    const classOrder = [];
    const classMap = {};
    for (const label of manualLabels) {
        if (!classMap[label.name]) {
            classMap[label.name] = [];
            classOrder.push(label.name);
        }
        classMap[label.name].push(label);
    }

    let html = '';
    for (const className of classOrder) {
        const labels = classMap[className];
        const first = labels[0];
        const isMulti = labels.length > 1;
        const hasEmbedding = labels.some(l => l.embedding != null);
        const pxCount = first.matchCount || 0;
        const threshold = first.threshold || 0;
        const codeTag = first.code ? `<code style="font-size: 10px; color: #667eea; background: #f0f0ff; padding: 1px 4px; border-radius: 2px; margin-right: 2px;">${first.code}</code>` : '';
        const anyVisible = labels.some(l => l.visible);
        const visIcon = anyVisible ? '\ud83d\udc41' : '\ud83d\udc41\u200d\ud83d\udde8';
        const isActive = currentManualLabel && currentManualLabel.name === className;
        const activateColor = isActive ? '#222' : '#667eea';
        const escapedName = className.replace(/'/g, "\\'").replace(/"/g, '&quot;');
        const jsName = className.replace(/\\/g, '\\\\').replace(/'/g, "\\'");

        // Slider (shared for entire class)
        let sliderHtml = '';
        if (hasEmbedding) {
            sliderHtml = `<input type="range" min="0" max="35" value="${threshold}" style="width: 60px; cursor: pointer; vertical-align: middle;" oninput="updateManualClassThreshold('${jsName}', parseFloat(this.value)); this.nextElementSibling.textContent=this.value">
                 <span style="font-size: 10px; color: #666; min-width: 20px;">${threshold}</span>`;
        }

        if (isMulti) {
            // Multi-member class: collapsible group
            const expanded = !collapsedClasses.has(className);
            const arrow = expanded ? '\u25bc' : '\u25b6';
            html += `<div class="manual-label-item" style="display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid #eee; flex-wrap: wrap;">
                <span style="cursor: pointer; font-size: 14px; color: ${activateColor}; flex-shrink: 0;" onclick="activateManualClass('${jsName}')" title="Set as active label">&#9678;</span>
                <span style="cursor: pointer; font-size: 16px; color: #555; user-select: none; width: 14px; text-align: center; flex-shrink: 0;" onclick="toggleClassExpand('${jsName}')" title="Expand/collapse ${labels.length} labels">${arrow}</span>
                <div style="width: 14px; height: 14px; border-radius: 2px; background: ${first.color}; border: 1px solid rgba(0,0,0,0.2); flex-shrink: 0;"></div>
                ${codeTag}<span style="font-size: 12px; color: #333; font-weight: 500; min-width: 50px;">${className}</span>
                ${sliderHtml}
                <span data-class-count="${escapedName}" style="font-size: 10px; color: #888;">${pxCount}px</span>
                ${hasEmbedding ? `<span style="cursor: pointer; font-size: 14px; color: #5500cc; margin-left: auto;" onclick="showManualLabelTimeline('${jsName}')" title="Show coverage timeline across years">&#128339;</span>` : `<span style="margin-left: auto;"></span>`}
                <span style="cursor: pointer; font-size: 13px; color: #e53e3e;" onclick="removeManualClass('${jsName}')" title="Delete all ${labels.length} labels">&#x1f5d1;</span>
                <span style="cursor: pointer; font-size: 13px;" onclick="toggleClassVisibility('${jsName}')" title="Toggle visibility">${visIcon}</span>
            </div>`;

            // Expanded children
            if (expanded) {
                for (const label of labels) {
                    const childIcon = label.type === 'polygon' ? '\u2b20' : '\ud83d\udccd';
                    const childDetail = label.type === 'polygon'
                        ? `polygon ${label.pixelCount || 0}px`
                        : `${label.lat.toFixed(4)}, ${label.lon.toFixed(4)}`;
                    html += `<div class="manual-label-item" data-id="${label.id}" style="display: flex; align-items: center; gap: 6px; padding: 2px 0 2px 24px; font-size: 11px; color: #666;">
                        <span>${childIcon}</span>
                        <span>${childDetail}</span>
                        <span style="cursor: pointer; font-size: 13px; color: #e53e3e; margin-left: auto;" onclick="removeManualLabel(${label.id})" title="Delete">&times;</span>
                    </div>`;
                }
            }
        } else {
            // Single-member class: flat row
            const label = first;
            html += `<div class="manual-label-item" data-id="${label.id}" style="display: flex; align-items: center; gap: 6px; padding: 4px 0; border-bottom: 1px solid #eee; flex-wrap: wrap;">
                <span style="cursor: pointer; font-size: 14px; color: ${activateColor}; flex-shrink: 0;" onclick="activateManualClass('${jsName}')" title="Set as active label">&#9678;</span>
                <div style="width: 14px; height: 14px; border-radius: 2px; background: ${label.color}; border: 1px solid rgba(0,0,0,0.2); flex-shrink: 0;"></div>
                ${codeTag}<span style="font-size: 12px; color: #333; font-weight: 500; min-width: 50px;">${className}</span>
                ${sliderHtml}
                <span data-class-count="${escapedName}" style="font-size: 10px; color: #888;">${pxCount}px</span>
                ${hasEmbedding ? `<span style="cursor: pointer; font-size: 14px; color: #5500cc; margin-left: auto;" onclick="showManualLabelTimeline('${jsName}')" title="Show coverage timeline across years">&#128339;</span>` : `<span style="margin-left: auto;"></span>`}
                <span style="cursor: pointer; font-size: 13px; color: #e53e3e;" onclick="removeManualLabel(${label.id})" title="Delete">&#x1f5d1;</span>
                <span style="cursor: pointer; font-size: 13px;" onclick="toggleClassVisibility('${jsName}')" title="Toggle visibility">${visIcon}</span>
            </div>`;
        }
    }
    container.innerHTML = html;

    // Mirror into the autolabel view's labels list
    const p6list = document.getElementById('panel6-labels-list');
    if (p6list && p6list !== container) p6list.innerHTML = html || '<div style="color: #999; font-size: 12px; padding: 10px;">No labels yet.</div>';
}

// ===== PANEL 5: NEAREST-CENTROID CLASSIFICATION OVERLAY =====
function triggerManualClassification() {
    if (manualClassifyDebounceTimer) clearTimeout(manualClassifyDebounceTimer);
    manualClassifyDebounceTimer = setTimeout(renderManualClassification, 300);
}

function renderManualClassification() {
    // Only render in manual label mode
    if (window.currentPanelMode !== 'labelling' || labelMode !== 'manual') {
        if (manualClassifyOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(manualClassifyOverlay)) {
            window.maps.panel5.removeLayer(manualClassifyOverlay);
        }
        manualClassifyOverlay = null;
        return;
    }

    // Collect visible labels that have embeddings
    const activeLabels = manualLabels.filter(l => l.visible && l.embedding);
    if (activeLabels.length === 0 || !window.localVectors) {
        if (manualClassifyOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(manualClassifyOverlay)) {
            window.maps.panel5.removeLayer(manualClassifyOverlay);
        }
        manualClassifyOverlay = null;
        return;
    }

    // Build class name → index + color window.maps
    const classNames = [];   // unique label names
    const classColorMap = {};
    for (const label of activeLabels) {
        if (!classColorMap[label.name]) {
            classColorMap[label.name] = label.color;
            classNames.push(label.name);
        }
    }
    if (classNames.length === 0) return;

    const dim = window.localVectors.dim;
    const N = window.localVectors.numVectors;
    const coords = window.localVectors.coords;
    const emb = window.localVectors.values;
    const gt = window.localVectors.metadata.geotransform;
    const grid = window.localVectors.gridLookup;

    // -1 = unassigned
    const assignments = new Int8Array(N).fill(-1);

    // Pass 1: rasterize polygon interiors — always assigned
    for (const label of activeLabels) {
        if (label.type !== 'polygon' || !label.vertices) continue;
        const classIdx = classNames.indexOf(label.name);
        const pixVerts = label.vertices.map(v => [
            Math.round((v[1] - gt.c) / gt.a),
            Math.round((v[0] - gt.f) / gt.e)
        ]);
        const matches = rasterizePolygon(pixVerts);
        for (const m of matches) {
            if (m.vectorIndex >= 0 && m.vectorIndex < N) {
                assignments[m.vectorIndex] = classIdx;
            }
        }
    }

    // Pass 2: threshold-gated nearest-centroid for remaining pixels
    const sources = [];
    for (const label of activeLabels) {
        if (!label.embedding) continue;
        const t = label.threshold || 0;
        if (t <= 0) continue;
        const classIdx = classNames.indexOf(label.name);
        sources.push({
            emb: new Float32Array(label.embedding),
            threshSq: t * t,
            classIdx
        });
    }

    if (sources.length > 0) {
        const numSources = sources.length;
        for (let i = 0; i < N; i++) {
            if (assignments[i] >= 0) continue; // already assigned by polygon
            let bestDist = Infinity;
            let bestClass = -1;
            const base = i * dim;
            for (let s = 0; s < numSources; s++) {
                const src = sources[s];
                let distSq = 0;
                for (let d = 0; d < dim; d++) {
                    const diff = emb[base + d] - src.emb[d];
                    distSq += diff * diff;
                }
                if (distSq <= src.threshSq && distSq < bestDist) {
                    bestDist = distSq;
                    bestClass = src.classIdx;
                }
            }
            if (bestClass >= 0) assignments[i] = bestClass;
        }
    }

    // Pre-parse colors
    const classColors = classNames.map(n => {
        const hex = classColorMap[n];
        return [parseInt(hex.slice(1,3), 16), parseInt(hex.slice(3,5), 16), parseInt(hex.slice(5,7), 16)];
    });

    // Render to offscreen canvas — only pixels with assignments >= 0
    let minPx = Infinity, maxPx = -Infinity, minPy = Infinity, maxPy = -Infinity;
    for (let i = 0; i < N; i++) {
        const px = coords[i * 2], py = coords[i * 2 + 1];
        if (px < minPx) minPx = px;
        if (px > maxPx) maxPx = px;
        if (py < minPy) minPy = py;
        if (py > maxPy) maxPy = py;
    }

    const width = maxPx - minPx + 1;
    const height = maxPy - minPy + 1;
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    const imgData = ctx.createImageData(width, height);
    const data = imgData.data;

    for (let i = 0; i < N; i++) {
        if (assignments[i] < 0) continue; // unmatched — leave transparent
        const gx = coords[i * 2] - minPx;
        const gy = coords[i * 2 + 1] - minPy;
        const idx = (gy * width + gx) * 4;
        const rgb = classColors[assignments[i]];
        data[idx] = rgb[0];
        data[idx + 1] = rgb[1];
        data[idx + 2] = rgb[2];
        data[idx + 3] = 140; // ~55% opacity
    }
    ctx.putImageData(imgData, 0, 0);

    // Compute geographic bounds
    const lonMin = gt.c + minPx * gt.a;
    const lonMax = gt.c + (maxPx + 1) * gt.a;
    const latMin = gt.f + (maxPy + 1) * gt.e;
    const latMax = gt.f + minPy * gt.e;

    // Remove old overlay
    if (manualClassifyOverlay && window.maps.panel5 && window.maps.panel5.hasLayer(manualClassifyOverlay)) {
        window.maps.panel5.removeLayer(manualClassifyOverlay);
    }

    const dataURL = canvas.toDataURL();
    manualClassifyOverlay = L.imageOverlay(dataURL, [[latMin, lonMin], [latMax, lonMax]]);
    manualClassifyOverlay.addTo(window.maps.panel5);
    manualClassifyOverlay.getElement().style.imageRendering = 'pixelated';
}

function activateManualClass(className) {
    const label = manualLabels.find(l => l.name === className);
    if (!label) return;
    currentManualLabel = { name: label.name, color: label.color, code: label.code || '' };
    const activeEl = document.getElementById('manual-active-label');
    if (activeEl) {
        activeEl.style.display = '';
        document.getElementById('manual-active-label-swatch').style.background = label.color;
        document.getElementById('manual-active-label-name').textContent =
            `${label.code ? '[' + label.code + '] ' : ''}${label.name}`;
        const acp = document.getElementById('active-label-color-picker');
        if (acp) acp.value = label.color;
    }
    localStorage.setItem(_activeLabelKey(), JSON.stringify(currentManualLabel));
    renderManualLabelsList();
}

// ===== POLYGON DRAWING (Phase 2B) =====

function initPolygonDrawing() {
    if (!window.maps.rgb || typeof L.Draw === 'undefined') return;
    drawnItems = new L.FeatureGroup();
    window.maps.rgb.addLayer(drawnItems);
    window.maps.rgb.on(L.Draw.Event.CREATED, function(e) {
        if (e.layerType === 'polygon') {
            const latlngs = e.layer.getLatLngs();
            // Leaflet may return nested arrays: [[latlng,...]] or [[[latlng,...]]]
            let ring = latlngs;
            while (ring.length && Array.isArray(ring[0]) && !ring[0].lat) {
                ring = ring[0];
            }
            handlePolygonComplete(ring);
        }
        isPolygonDrawing = false;
        polygonDrawHandler = null;
    });
}

function startPolygonDrawing(latlng) {
    if (!currentManualLabel) {
        alert('Please set a label name first (top of Panel 6).');
        return;
    }
    if (!window.maps.rgb || typeof L.Draw === 'undefined') return;
    if (isPolygonDrawing) return;

    polygonDrawHandler = new L.Draw.Polygon(window.maps.rgb, {
        shapeOptions: {
            color: currentManualLabel.color,
            fillColor: currentManualLabel.color,
            fillOpacity: 0.3,
            weight: 2
        },
        allowIntersection: false
    });
    polygonDrawHandler.enable();
    isPolygonDrawing = true;

    // Place the first vertex at the double-click location
    if (latlng) {
        polygonDrawHandler.addVertex(latlng);
    }
}

function cancelPolygonDrawing() {
    if (polygonDrawHandler) {
        polygonDrawHandler.disable();
        polygonDrawHandler = null;
    }
    isPolygonDrawing = false;
}

function handlePolygonComplete(latLngs) {
    if (!currentManualLabel || !window.localVectors) return;

    const gt = window.localVectors.metadata.geotransform;
    // Convert lat/lng vertices to pixel coords
    const pixelVertices = latLngs.map(ll => {
        const px = Math.round((ll.lng - gt.c) / gt.a);
        const py = Math.round((ll.lat - gt.f) / gt.e);
        return [px, py];
    });

    // Store geo vertices for export
    const geoVertices = latLngs.map(ll => [ll.lat, ll.lng]);

    // Rasterize polygon to find interior pixels
    const matches = rasterizePolygon(pixelVertices);

    if (matches.length === 0) {
        console.warn('[POLYGON] No pixels inside polygon');
        return;
    }

    const dim = window.localVectors.dim;

    // Inherit class threshold if other labels with same name exist
    const classThreshold = getClassThreshold(currentManualLabel.name);

    const entry = {
        name: currentManualLabel.name,
        color: currentManualLabel.color,
        code: currentManualLabel.code || null,
        type: 'polygon',
        lat: geoVertices.reduce((s, v) => s + v[0], 0) / geoVertices.length,
        lon: geoVertices.reduce((s, v) => s + v[1], 0) / geoVertices.length,
        vertices: geoVertices,
        threshold: classThreshold,
        matchCount: matches.length,
        pixelCount: matches.length,
        polygonMode: polygonSearchMode
    };

    if (polygonSearchMode === 'union') {
        // Store all individual pixel embeddings for union search
        const allEmbs = [];
        for (const m of matches) {
            const emb = new Float32Array(dim);
            const base = m.vectorIndex * dim;
            for (let d = 0; d < dim; d++) emb[d] = window.localVectors.values[base + d];
            allEmbs.push(Array.from(emb));
        }
        entry.embeddings = allEmbs;
        entry.embedding = null;
    } else {
        // Compute centroid (mean) embedding from interior pixels
        const centroid = new Float32Array(dim);
        for (const m of matches) {
            const base = m.vectorIndex * dim;
            for (let d = 0; d < dim; d++) centroid[d] += window.localVectors.values[base + d];
        }
        for (let d = 0; d < dim; d++) centroid[d] /= matches.length;
        entry.embedding = Array.from(centroid);
        entry.embeddings = null;
    }

    addManualLabel(entry);

    // Rebuild class overlay (union of all embeddings + polygon outlines)
    rebuildClassOverlay(currentManualLabel.name);
}

function pointInPolygon(px, py, polygon) {
    // Ray-casting algorithm
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const xi = polygon[i][0], yi = polygon[i][1];
        const xj = polygon[j][0], yj = polygon[j][1];
        if (((yi > py) !== (yj > py)) &&
            (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
            inside = !inside;
        }
    }
    return inside;
}

function rasterizePolygon(pixelVertices) {
    if (!window.localVectors || !pixelVertices || pixelVertices.length < 3) return [];

    // Compute bounding box
    let minPx = Infinity, maxPx = -Infinity, minPy = Infinity, maxPy = -Infinity;
    for (const [px, py] of pixelVertices) {
        if (px < minPx) minPx = px;
        if (px > maxPx) maxPx = px;
        if (py < minPy) minPy = py;
        if (py > maxPy) maxPy = py;
    }

    // Clamp to grid bounds
    const grid = window.localVectors.gridLookup;
    minPx = Math.max(minPx, grid.minX);
    maxPx = Math.min(maxPx, grid.minX + grid.w - 1);
    minPy = Math.max(minPy, grid.minY);
    maxPy = Math.min(maxPy, grid.minY + grid.h - 1);

    const gt = window.localVectors.metadata.geotransform;
    const matches = [];

    for (let py = minPy; py <= maxPy; py++) {
        for (let px = minPx; px <= maxPx; px++) {
            if (pointInPolygon(px, py, pixelVertices)) {
                const gridIdx = window.gridLookupIndex(grid, px, py);
                if (gridIdx >= 0) {
                    const lon = gt.c + px * gt.a;
                    const lat = gt.f + py * gt.e;
                    matches.push({ lat, lon, px, py, vectorIndex: gridIdx });
                }
            }
        }
    }
    return matches;
}

// ===== EXPORT / IMPORT (Phase 3) =====

function exportManualLabels() {
    if (manualLabels.length === 0) {
        alert('No manual labels to export.');
        return;
    }

    // Create export dropdown if it doesn't exist
    const btn = document.getElementById('labelling-export-btn');
    let menu = document.getElementById('labelling-export-menu');
    if (menu) {
        menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        return;
    }

    // Create dropdown menu
    menu = document.createElement('div');
    menu.id = 'labelling-export-menu';
    menu.style.cssText = 'position: absolute; top: 100%; left: 0; min-width: 160px; background: #2a2a2a; border: 1px solid #555; border-radius: 4px; z-index: 1000; box-shadow: 0 4px 12px rgba(0,0,0,0.3); overflow: hidden;';
    menu.innerHTML = `
        <button style="display: block; width: 100%; padding: 8px 12px; background: none; border: none; color: #ccc; font-size: 12px; text-align: left; cursor: pointer;" onmouseover="this.style.background='#444'" onmouseout="this.style.background='none'" onclick="doExportManualLabels('json')" title="Export labels as JSON with embeddings">JSON (full)</button>
        <button style="display: block; width: 100%; padding: 8px 12px; background: none; border: none; color: #ccc; font-size: 12px; text-align: left; cursor: pointer;" onmouseover="this.style.background='#444'" onmouseout="this.style.background='none'" onclick="doExportManualLabels('geojson')" title="Export labels as GeoJSON points">GeoJSON</button>
        <button style="display: block; width: 100%; padding: 8px 12px; background: none; border: none; color: #ccc; font-size: 12px; text-align: left; cursor: pointer; border-top: 1px solid #444;" onmouseover="this.style.background='#444'" onmouseout="this.style.background='none'" onclick="doExportManualLabels('shapefile')" title="Export labels as ESRI Shapefile">ESRI Shapefile (ZIP)</button>
        <button style="display: block; width: 100%; padding: 8px 12px; background: none; border: none; color: #ccc; font-size: 12px; text-align: left; cursor: pointer; border-top: 1px solid #444;" onmouseover="this.style.background='#444'" onmouseout="this.style.background='none'" onclick="document.getElementById('labelling-export-menu').style.display='none'; exportMapAsJPG()" title="Save current map view as image">Map (JPG)</button>
    `;
    btn.parentElement.style.position = 'relative';
    btn.parentElement.appendChild(menu);
}

function doExportManualLabels(format) {
    const menu = document.getElementById('labelling-export-menu');
    if (menu) menu.style.display = 'none';
    markExportClean();

    if (format === 'json') {
        const data = {
            viewport: window.currentViewportName,
            year: window.currentEmbeddingYear,
            schema: window.activeSchemaMode,
            labels: manualLabels.map(l => ({
                name: l.name,
                color: l.color,
                code: l.code || null,
                type: l.type,
                lat: l.lat,
                lon: l.lon,
                vertices: l.vertices || null,
                embedding: l.embedding || null,
                embeddings: l.embeddings || null,
                polygonMode: l.polygonMode || null,
                threshold: l.threshold,
                matchCount: l.matchCount,
                pixelCount: l.pixelCount || null
            }))
        };
        downloadFile(JSON.stringify(data, null, 2), `manual-labels-${window.currentViewportName || 'export'}.json`, 'application/json');
    } else if (format === 'geojson') {
        const features = manualLabels.map(l => {
            if (l.type === 'polygon' && l.vertices) {
                // GeoJSON polygon: [[[lng, lat], ...]]
                const ring = l.vertices.map(v => [v[1], v[0]]);
                ring.push(ring[0]); // close ring
                return {
                    type: 'Feature',
                    geometry: { type: 'Polygon', coordinates: [ring] },
                    properties: { name: l.name, color: l.color, code: l.code || '', type: l.type, matchCount: l.matchCount }
                };
            } else {
                return {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [l.lon, l.lat] },
                    properties: { name: l.name, color: l.color, code: l.code || '', type: l.type, threshold: l.threshold, matchCount: l.matchCount }
                };
            }
        });
        const geojson = { type: 'FeatureCollection', features };
        downloadFile(JSON.stringify(geojson, null, 2), `manual-labels-${window.currentViewportName || 'export'}.geojson`, 'application/geo+json');
    } else if (format === 'shapefile') {
        exportManualLabelsShapefile();
    }
}

function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

async function exportManualLabelsShapefile() {
    // Use shp-write for ESRI Shapefile export
    try {
        if (typeof shpwrite === 'undefined') {
            // Dynamically load shp-write
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://unpkg.com/@mapbox/shp-write@0.3.2/shpwrite.js';
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
        }

        const points = [];
        const polygons = [];

        for (const l of manualLabels) {
            const props = { name: l.name, color: l.color, code: l.code || '', type: l.type };
            if (l.type === 'polygon' && l.vertices) {
                const ring = l.vertices.map(v => [v[1], v[0]]);
                ring.push(ring[0]);
                polygons.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: props });
            } else {
                points.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [l.lon, l.lat] }, properties: props });
            }
        }

        // Export as GeoJSON-based ZIP (shp-write generates .shp, .shx, .dbf, .prj)
        const gj = {
            type: 'FeatureCollection',
            features: [...points, ...polygons]
        };

        if (typeof shpwrite !== 'undefined' && shpwrite.zip) {
            const content = await shpwrite.zip(gj);
            downloadFile(content, `manual-labels-${window.currentViewportName || 'export'}.zip`, 'application/zip');
        } else {
            // Fallback: download as GeoJSON instead
            console.warn('[EXPORT] shp-write not available, falling back to GeoJSON');
            doExportManualLabels('geojson');
        }
    } catch (e) {
        console.error('[EXPORT] Shapefile export failed:', e);
        alert('Shapefile export failed. Exporting as GeoJSON instead.');
        doExportManualLabels('geojson');
    }
}

function importManualLabels(file) {
    if (!file) return;
    const reader = new FileReader();
    const ext = file.name.split('.').pop().toLowerCase();

    reader.onload = function(e) {
        try {
            if (ext === 'json' || ext === 'geojson') {
                const data = JSON.parse(e.target.result);
                if (data.type === 'FeatureCollection' && data.features) {
                    // GeoJSON import
                    importGeoJSON(data);
                } else if (data.labels && Array.isArray(data.labels)) {
                    // Full JSON import
                    importLabelsJSON(data.labels);
                } else if (Array.isArray(data)) {
                    // Array of labels (anonymous or located)
                    importLabelsJSON(data);
                } else {
                    alert('Unrecognized JSON format.');
                }
            } else if (ext === 'zip') {
                importShapefile(e.target.result);
            } else {
                alert('Unsupported file format. Use .json, .geojson, or .zip (shapefile).');
            }
        } catch (err) {
            console.error('[IMPORT]', err);
            alert('Failed to import file: ' + err.message);
        }
    };

    if (ext === 'zip') {
        reader.readAsArrayBuffer(file);
    } else {
        reader.readAsText(file);
    }
}

function importGeoJSON(geojson) {
    let count = 0;
    for (const feature of geojson.features) {
        const props = feature.properties || {};
        const geom = feature.geometry;
        if (!geom) continue;

        if (geom.type === 'Point') {
            const [lon, lat] = geom.coordinates;
            const entry = {
                name: props.name || props.label || 'Imported',
                color: props.color || window.SEG_PALETTE[count % window.SEG_PALETTE.length],
                code: props.code || null,
                type: props.type || 'point',
                lat, lon,
                embedding: null,
                threshold: props.threshold || 0,
                matchCount: 0
            };
            // Re-extract embedding if vectors available
            if (window.localVectors) {
                const emb = window.localExtract(lat, lon);
                if (emb) entry.embedding = Array.from(emb);
            }
            addManualLabel(entry);
            count++;
        } else if (geom.type === 'Polygon') {
            const ring = geom.coordinates[0];
            const geoVertices = ring.slice(0, -1).map(c => [c[1], c[0]]); // [lat, lon]
            const entry = {
                name: props.name || props.label || 'Imported',
                color: props.color || window.SEG_PALETTE[count % window.SEG_PALETTE.length],
                code: props.code || null,
                type: 'polygon',
                lat: geoVertices.reduce((s, v) => s + v[0], 0) / geoVertices.length,
                lon: geoVertices.reduce((s, v) => s + v[1], 0) / geoVertices.length,
                vertices: geoVertices,
                embedding: null,
                threshold: 0,
                matchCount: 0,
                pixelCount: 0
            };
            addManualLabel(entry);
            // Compute embedding for polygon
            if (window.localVectors) {
                const gt = window.localVectors.metadata.geotransform;
                const pixVerts = geoVertices.map(v => [Math.round((v[1] - gt.c) / gt.a), Math.round((v[0] - gt.f) / gt.e)]);
                const matches = rasterizePolygon(pixVerts);
                const lbl = manualLabels[manualLabels.length - 1];
                lbl.matchCount = matches.length;
                lbl.pixelCount = matches.length;
                if (matches.length > 0) {
                    const dim = window.localVectors.dim;
                    const centroid = new Float32Array(dim);
                    for (const m of matches) {
                        const base = m.vectorIndex * dim;
                        for (let d = 0; d < dim; d++) centroid[d] += window.localVectors.values[base + d];
                    }
                    for (let d = 0; d < dim; d++) centroid[d] /= matches.length;
                    lbl.embedding = Array.from(centroid);
                }
            }
            count++;
        }
    }
    saveManualLabelsToStorage();
    rebuildManualOverlays();
    renderManualLabelsList();
    console.log(`[IMPORT] Imported ${count} features from GeoJSON`);
}

function importLabelsJSON(labels) {
    let count = 0;
    for (const l of labels) {
        const entry = {
            name: l.name || 'Imported',
            color: l.color || window.SEG_PALETTE[count % window.SEG_PALETTE.length],
            code: l.code || null,
            type: l.type || 'point',
            lat: l.lat || 0,
            lon: l.lon || 0,
            vertices: l.vertices || null,
            embedding: l.embedding || null,
            threshold: l.threshold || 0,
            matchCount: l.matchCount || 0,
            pixelCount: l.pixelCount || null
        };
        addManualLabel(entry);
        count++;
    }
    rebuildManualOverlays();
    console.log(`[IMPORT] Imported ${count} labels from JSON`);
}

async function importShapefile(arrayBuffer) {
    // Try to load shapefile reader dynamically
    try {
        if (typeof shapefile === 'undefined') {
            await new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://unpkg.com/shapefile@0.6.6/dist/shapefile.js';
                script.onload = resolve;
                script.onerror = reject;
                document.head.appendChild(script);
            });
        }

        const source = await shapefile.open(arrayBuffer);
        const features = [];
        let result = await source.read();
        while (!result.done) {
            features.push(result.value);
            result = await source.read();
        }
        importGeoJSON({ type: 'FeatureCollection', features });
    } catch (e) {
        console.error('[IMPORT] Shapefile import failed:', e);
        alert('Shapefile import failed: ' + e.message);
    }
}

function updateThresholdDisplay() {
    const slider = document.getElementById('similarity-threshold');
    const display = document.getElementById('threshold-display');
    // Map slider 0-25 to threshold 0.0-25.0
    // Embeddings are 128D float32; L2 distances: min=0, median=28, mean=27.36, max~=45
    const value = parseInt(slider.value);
    display.textContent = value.toFixed(2);

    // If we have cached search results, update visualization in real-time
    if (window.explorerResults) {
        window.updateExplorerVisualization();
    }
}

// ===== PERSISTENT LABEL SYSTEM =====
// Global variables for label management
let savedLabels = [];  // Array of label objects (stored in localStorage)
let currentSearchCache = null;  // Cache of current vector search results

// Persist labels to localStorage (metadata only, no pixel arrays)
function persistLabels() {
    const toStore = savedLabels.map(l => {
        const {pixels, visible, _clusterInfo, ...meta} = l;
        return meta;
    });
    localStorage.setItem('tee_labels_' + window.currentViewportName, JSON.stringify(toStore));
}

// Load saved labels from localStorage, then recompute pixels from vectors
async function loadSavedLabels() {
    try {
        const raw = localStorage.getItem('tee_labels_' + window.currentViewportName);
        savedLabels = raw ? JSON.parse(raw).map(l => ({...l, visible: true, pixels: []})) : [];
        updateLabelsUI();
        console.log(`✓ Loaded ${savedLabels.length} label definitions from localStorage`);
        if (savedLabels.length > 0) {
            await recomputeLabelPixels();
        }
    } catch (error) {
        console.error('Error loading labels:', error);
        savedLabels = [];
    }
}

// Recompute pixel coverage for all labels from vector data
async function recomputeLabelPixels() {
    if (!window.localVectors) {
        console.warn('[LABEL] Vector data not loaded, skipping recompute');
        return;
    }
    const gt = window.localVectors.metadata.geotransform;
    for (const label of savedLabels) {
        try {
            // Labels with stored pixel_coords: reconstruct directly
            if (label.pixel_coords && label.pixel_coords.length > 0) {
                const pc = label.pixel_coords;
                label.pixels = [];
                for (let i = 0; i < pc.length; i += 2) {
                    label.pixels.push({
                        lat: gt.f + pc[i + 1] * gt.e,
                        lon: gt.c + pc[i] * gt.a,
                        distance: 0
                    });
                }
                label.pixel_count = label.pixels.length;
                continue;
            }

            // No pixel_coords: re-search using embedding + threshold
            let embedding = label.embedding;
            if (embedding) {
                if (!(embedding instanceof Float32Array)) {
                    embedding = new Float32Array(embedding);
                }
            } else if (label.source_pixel) {
                embedding = window.localExtract(label.source_pixel.lat, label.source_pixel.lon);
                if (!embedding) {
                    console.warn(`[LABEL] No embedding for ${label.name} at source pixel`);
                    continue;
                }
                label.embedding = Array.from(embedding);
            } else {
                continue;
            }
            const matches = window.localSearchSimilar(embedding, label.threshold);
            label.pixels = matches;
            label.pixel_count = matches.length;
        } catch (error) {
            console.error(`[LABEL] Error recomputing ${label.name}:`, error);
        }
    }
    persistLabels();
    updateOverlay();
    updateLabelsUI();
}

// Update labels UI list
let labelsExportedSinceChange = false;

function renderLabelsInto(container) {
    if (!container) return;
    // Group labels by name
    const groups = new Map();
    for (const label of savedLabels) {
        if (!groups.has(label.name)) {
            groups.set(label.name, []);
        }
        groups.get(label.name).push(label);
    }

    if (savedLabels.length === 0) {
        container.innerHTML = '<div style="color: #999; font-size: 12px; padding: 10px;">No labels yet.</div>';
        return;
    }

    container.innerHTML = Array.from(groups.entries()).map(([name, members]) => renderLabelGroup(name, members)).join('');

    // Attach event listeners
    container.querySelectorAll('.timeline-label-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const labelName = e.currentTarget.dataset.labelName;
            showLabelTimeline(labelName);
        });
    });
    container.querySelectorAll('.toggle-label-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const labelName = e.currentTarget.dataset.labelName;
            const members = savedLabels.filter(l => l.name === labelName);
            const newVisible = !members.some(l => l.visible);
            members.forEach(l => l.visible = newVisible);
            updateOverlay();
            updateLabelsUI();
        });
    });
    container.querySelectorAll('.delete-label-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const labelName = e.currentTarget.dataset.labelName;
            deleteLabelGroup(labelName);
        });
    });
    container.querySelectorAll('.rename-label-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const labelName = e.currentTarget.dataset.labelName;
            renameLabel(labelName);
        });
    });
}

function updateLabelsUI() {

    // Render into floating popup
    renderLabelsInto(document.getElementById('labels-list'));
    // Render into panel 6 right column
    renderLabelsInto(document.getElementById('panel6-labels-list'));

    // Update panel 6 "Show All / Hide All" button
    const toggleAllBtn = document.getElementById('panel6-toggle-all-btn');
    if (toggleAllBtn) {
        if (savedLabels.length === 0) {
            toggleAllBtn.style.display = 'none';
        } else {
            toggleAllBtn.style.display = '';
            const allVisible = savedLabels.every(l => l.visible);
            toggleAllBtn.textContent = allVisible ? 'Hide All' : 'Show All';
        }
    }
}

function escapeHtml(s) {
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function renameLabel(oldName) {
    const newName = prompt('Rename label:', oldName);
    if (!newName || !newName.trim() || newName.trim() === oldName) return;
    const trimmed = newName.trim();
    for (const label of savedLabels) {
        if (label.name === oldName) label.name = trimmed;
    }
    labelsExportedSinceChange = false;
    persistLabels();
    updateOverlay();
    updateLabelsUI();
    window.updateUMAPColorsFromLabels();
}

// Render grouped label card
function renderLabelGroup(name, members) {
    const color = members[0].color;
    const totalPixels = members.reduce((sum, l) => sum + l.pixel_count, 0);
    const markerCount = members.length;
    const markerNote = markerCount > 1 ? ` (${markerCount} markers)` : '';
    const anyVisible = members.some(l => l.visible);
    const visIcon = anyVisible ? '\ud83d\udc41' : '\ud83d\udc41\u200d\ud83d\udde8';
    const hasSourcePixel = members.some(l => l.source_pixel);
    const thresholdStr = hasSourcePixel ? `, threshold: ${parseFloat(members[0].threshold.toPrecision(3))}` : '';
    return `
        <div class="label-item" data-label-name="${escapeHtml(name)}" style="padding: 10px; margin-bottom: 10px; border: 1px solid #ddd; border-radius: 4px; background: white; border-left: 4px solid ${color};">
          <div style="display: flex; align-items: flex-start; justify-content: space-between;">
            <div style="flex: 1; min-width: 0;">
              <div style="display: flex; align-items: center; margin-bottom: 4px;">
                <div style="width: 14px; height: 14px; background: ${color}; border-radius: 2px; margin-right: 8px; flex-shrink: 0;"></div>
                <div style="font-weight: 600; font-size: 13px; color: #333; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(name)}${markerNote}</div>
                <span class="rename-label-btn" data-label-name="${escapeHtml(name)}" style="cursor: pointer; font-size: 12px; color: #999; margin-left: 4px; flex-shrink: 0;" title="Rename">&#9998;</span>
              </div>
              <div style="font-size: 11px; color: #999; margin-left: 22px;">${totalPixels.toLocaleString()} pixels${thresholdStr}</div>
            </div>
            <div style="display: flex; flex-direction: column; gap: 4px; flex-shrink: 0;">
              <div style="display: flex; gap: 4px; align-items: center;">
                ${hasSourcePixel ? `<span class="timeline-label-btn" data-label-name="${escapeHtml(name)}" style="cursor: pointer; font-size: 14px; color: #5500cc;" title="Show coverage timeline across years">&#128339;</span>` : ''}
                <span class="delete-label-btn" data-label-name="${escapeHtml(name)}" style="cursor: pointer; font-size: 13px; color: #cc0000;" title="Delete label">&#128465;</span>
                <span class="toggle-label-btn" data-label-name="${escapeHtml(name)}" style="cursor: pointer; font-size: 13px;" title="Toggle visibility">${visIcon}</span>
              </div>
            </div>
          </div>
        </div>
    `;
}

// Show save label modal
function saveCurrentSearchAsLabel() {
    if (!currentSearchCache || currentSearchCache.allMatches.length === 0) {
        alert('No search results to save. Click a pixel first.');
        return;
    }

    // Clear form and show modal with a contrastive color
    const suggestedColor = nextLabelColor();
    document.getElementById('label-name-input').value = '';
    document.getElementById('label-color-input').value = suggestedColor;
    document.getElementById('color-hex-display').textContent = suggestedColor.toUpperCase();

    // Populate existing labels list
    const listContainer = document.getElementById('existing-labels-list');
    const rowsContainer = document.getElementById('existing-labels-rows');
    rowsContainer.innerHTML = '';

    // Collect unique name+color pairs from saved labels
    const seen = new Set();
    const uniqueLabels = [];
    for (const l of savedLabels) {
        const key = l.name + '|' + l.color;
        if (!seen.has(key)) {
            seen.add(key);
            uniqueLabels.push({ name: l.name, color: l.color });
        }
    }

    if (uniqueLabels.length > 0) {
        listContainer.style.display = 'block';
        for (const ul of uniqueLabels) {
            const row = document.createElement('div');
            row.style.cssText = 'display: flex; align-items: center; gap: 10px; padding: 8px 10px; cursor: pointer; border-radius: 4px; border: 2px solid transparent; margin-bottom: 4px; background: #f5f5f5;';
            row.innerHTML = `<span style="width: 18px; height: 18px; border-radius: 3px; background: ${ul.color}; border: 1px solid #999; flex-shrink: 0;"></span><span style="color: #333; font-size: 14px;">${escapeHtml(ul.name)}</span>`;
            row.addEventListener('click', function() {
                document.getElementById('label-name-input').value = ul.name;
                document.getElementById('label-color-input').value = ul.color;
                document.getElementById('color-hex-display').textContent = ul.color.toUpperCase();
                // Highlight selected row
                rowsContainer.querySelectorAll('div').forEach(r => r.style.borderColor = 'transparent');
                row.style.borderColor = ul.color;
            });
            row.addEventListener('mouseenter', function() { row.style.background = '#e8e8e8'; });
            row.addEventListener('mouseleave', function() { row.style.background = '#f5f5f5'; });
            rowsContainer.appendChild(row);
        }
    } else {
        listContainer.style.display = 'none';
    }

    document.getElementById('save-label-modal-overlay').style.display = 'flex';
    document.getElementById('label-name-input').focus();
}

// Handle save label confirmation
function confirmSaveLabel() {
    const name = document.getElementById('label-name-input').value.trim();
    const color = document.getElementById('label-color-input').value;

    if (!name) {
        alert('Please enter a label name');
        return;
    }

    // Get current threshold from slider
    const currentThreshold = parseFloat(document.getElementById('threshold-display').textContent);

    // Filter matches by current threshold
    const filteredMatches = currentSearchCache.allMatches.filter(m => m.distance <= currentThreshold);

    // Compute similarity statistics
    let minDist = Infinity, maxDist = -Infinity, sumDist = 0;
    for (const m of filteredMatches) {
        if (m.distance < minDist) minDist = m.distance;
        if (m.distance > maxDist) maxDist = m.distance;
        sumDist += m.distance;
    }
    const meanDist = sumDist / (filteredMatches.length || 1);

    const labelData = {
        id: 'label_' + Date.now(),
        name: name,
        color: color,
        threshold: currentThreshold,
        mean_distance: meanDist,
        min_distance: minDist,
        max_distance: maxDist,
        source_pixel: currentSearchCache.sourcePixel,
        embedding: currentSearchCache.embedding,
        pixel_count: filteredMatches.length,
        created: new Date().toISOString(),
        visible: true,
        pixels: filteredMatches.map(m => ({
            lat: m.lat,
            lon: m.lon,
            distance: m.distance
        }))
    };

    savedLabels.push(labelData);
    labelsExportedSinceChange = false;
    persistLabels();
    updateLabelsUI();
    updateOverlay();

    // Clear the explorer preview (yellow overlay) and search markers
    window.clearExplorerResults();
    window.clearCrossPanelMarkers();

    // Place persistent colored markers at the source pixel location on all panels
    const coloredIcon = window.makeColoredTriangleIcon(color);
    const srcLat = labelData.source_pixel.lat;
    const srcLon = labelData.source_pixel.lon;
    const entry = { labelId: labelData.id, markers: {} };
    ['osm', 'rgb', 'embedding', 'heatmap', 'embedding2'].forEach(key => {
        if (window.maps[key]) {
            entry.markers[key] = L.marker([srcLat, srcLon], { icon: coloredIcon })
                .bindTooltip(name, { direction: 'top', offset: [0, -20] })
                .addTo(window.maps[key]);
        }
    });
    persistentLabelMarkers.push(entry);

    closeSaveLabelModal();
    console.log(`✓ Saved label: ${name} (${labelData.pixel_count} pixels)`);
}

// Close save label modal
function closeSaveLabelModal() {
    document.getElementById('save-label-modal-overlay').style.display = 'none';
}

// Delete all labels with a given name
function deleteLabelGroup(name) {
    const members = savedLabels.filter(l => l.name === name);
    if (!confirm(`Delete "${name}" (${members.length} marker${members.length > 1 ? 's' : ''})?`)) return;
    const ids = new Set(members.map(l => l.id));

    // Collect cluster info from promoted labels before removing them
    const restorable = members.filter(l => l._clusterInfo && segAssignments);

    savedLabels = savedLabels.filter(l => !ids.has(l.id));
    labelsExportedSinceChange = false;
    persistentLabelMarkers = persistentLabelMarkers.filter(entry => {
        if (ids.has(entry.labelId)) {
            for (const key of Object.keys(entry.markers)) {
                if (entry.markers[key] && window.maps[key]) window.maps[key].removeLayer(entry.markers[key]);
            }
            return false;
        }
        return true;
    });
    persistLabels();

    // Restore promoted clusters back to temporary seg list
    if (restorable.length > 0) {
        for (const label of restorable) {
            const ci = label._clusterInfo;
            // Only restore if this cluster id isn't already in window.segLabels
            if (!window.segLabels.some(c => c.id === ci.id)) {
                window.segLabels.push({
                    id: ci.id, color: ci.color, hex: ci.hex,
                    name: label.name, count: ci.count,
                    threshold: ci.threshold, sourcePixel: ci.sourcePixel,
                    embedding: ci.embedding, centroid: ci.centroid
                });
            }
        }
        window.showSegmentationPanel();
        window.showSegmentationOverlay();
    }

    updateLabelsUI();
    updateOverlay();
    console.log(`✓ Deleted label group: ${name} (${ids.size} markers)`);
}

// Toggle all overlays
function toggleAllOverlays() {
    const allVisible = savedLabels.every(l => l.visible);
    savedLabels.forEach(l => l.visible = !allVisible);
    updateOverlay();
    updateLabelsUI();
}

// Export saved labels as JSON file (metadata only - source points + attributes)
function exportSavedLabels() {
    if (savedLabels.length === 0) {
        alert('No labels to export.');
        return;
    }
    const hideLocations = document.getElementById('export-hide-locations').checked;
    const data = {
        viewport: window.currentViewportName,
        year: window.currentEmbeddingYear,
        labels: savedLabels.map(l => {
            const {pixels, visible, _clusterInfo, ...meta} = l;
            if (hideLocations) {
                delete meta.source_pixel;
            }
            return meta;
        })
    };
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${window.currentViewportName}_labels.json`;
    a.click();
    URL.revokeObjectURL(url);
    labelsExportedSinceChange = true;
    updateLabelsUI();
}

// Export saved labels as GeoJSON FeatureCollection
// Uses same zoom-18 Mercator projection as the JPG export so polygons
// align pixel-perfectly with the exported map image.
function exportSavedLabelsGeoJSON() {
    if (savedLabels.length === 0) {
        alert('No labels to export.');
        return;
    }
    if (document.getElementById('export-hide-locations').checked) {
        alert('GeoJSON export requires locations. Uncheck "Hide label locations" to use this format.');
        return;
    }
    if (!window.localVectors) {
        alert('Vector data not loaded. Load embeddings first.');
        return;
    }
    const gt = window.localVectors.metadata.geotransform;
    const features = [];
    for (const label of savedLabels) {
        if (!label.pixels || label.pixels.length === 0) continue;

        // Build {px, py} array from pixel_coords or pixels
        const pxArr = [];
        if (label.pixel_coords) {
            const pc = label.pixel_coords;
            for (let i = 0; i < pc.length; i += 2) {
                pxArr.push({px: pc[i], py: pc[i + 1]});
            }
        } else {
            for (const p of label.pixels) {
                pxArr.push({
                    px: Math.round((p.lon - gt.c) / gt.a),
                    py: Math.round((p.lat - gt.f) / gt.e)
                });
            }
        }

        // Sort by (py, px) for row-run encoding
        pxArr.sort((a, b) => a.py - b.py || a.px - b.px);

        // Merge into horizontal runs
        const runs = [];
        let runStart = pxArr[0].px, runEnd = pxArr[0].px, runY = pxArr[0].py;
        for (let i = 1; i < pxArr.length; i++) {
            const p = pxArr[i];
            if (p.py === runY && p.px === runEnd + 1) {
                runEnd = p.px;
            } else {
                runs.push({px_start: runStart, px_end: runEnd, py: runY});
                runStart = p.px;
                runEnd = p.px;
                runY = p.py;
            }
        }
        runs.push({px_start: runStart, px_end: runEnd, py: runY});

        // Convert each run to a geographic rectangle
        for (const run of runs) {
            const lonL = gt.c + run.px_start * gt.a;
            const lonR = gt.c + (run.px_end + 1) * gt.a;
            const latT = gt.f + run.py * gt.e;
            const latB = gt.f + (run.py + 1) * gt.e;

            features.push({
                type: 'Feature',
                properties: {
                    label_name: label.name,
                    label_color: label.color
                },
                geometry: {
                    type: 'Polygon',
                    coordinates: [[
                        [lonL, latT], [lonR, latT], [lonR, latB], [lonL, latB], [lonL, latT]
                    ]]
                }
            });
        }
    }
    const geojson = {type: 'FeatureCollection', features};
    const blob = new Blob([JSON.stringify(geojson)], {type: 'application/geo+json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${window.currentViewportName}_labels.geojson`;
    a.click();
    URL.revokeObjectURL(url);
    labelsExportedSinceChange = true;
    updateLabelsUI();
}

// Import saved labels from JSON file
async function importSavedLabels(file) {
    const reader = new FileReader();
    reader.onload = async function(e) {
        try {
            const raw = JSON.parse(e.target.result);
            // Support both new format {viewport, labels: [...]} and old format [...]
            const imported = Array.isArray(raw) ? raw : (raw.labels || []);
            if (!Array.isArray(imported)) {
                alert('Invalid label file.');
                return;
            }
            const existingIds = new Set(savedLabels.map(l => l.id));
            let added = 0;
            for (const label of imported) {
                if (!label.id || !label.name) continue;
                if (existingIds.has(label.id)) continue;
                label.visible = true;
                label.pixels = [];
                savedLabels.push(label);
                added++;
            }
            persistLabels();
            labelsExportedSinceChange = true;
            updateLabelsUI();
            console.log(`✓ Imported ${added} labels (${imported.length - added} skipped as duplicates)`);
            if (added > 0) {
                await recomputeLabelPixels();
            }
        } catch (err) {
            alert('Failed to parse label file: ' + err.message);
        }
    };
    reader.readAsText(file);
}

// PersistentLabelOverlay class - renders all labels as colored pixels on RGB map
// Uses same geographic-pixel rendering as window.DirectCanvasLayer for crisp results.
const PersistentLabelOverlay = L.Layer.extend({
    initialize: function(labelsArray, options) {
        L.setOptions(this, options);
        this.labels = labelsArray;
    },

    onAdd: function(map) {
        this._map = map;

        // Use a custom Leaflet pane so the canvas sits inside the map pane's
        // stacking context — between overlayPane (400) and markerPane (600).
        // This lets triangle markers render above the label overlay.
        if (!map.getPane('labelOverlayPane')) {
            map.createPane('labelOverlayPane');
            map.getPane('labelOverlayPane').style.zIndex = '550';
            map.getPane('labelOverlayPane').style.pointerEvents = 'none';
        }

        this._canvas = document.createElement('canvas');
        this._canvas.style.position = 'absolute';
        this._canvas.style.pointerEvents = 'none';

        map.getPane('labelOverlayPane').appendChild(this._canvas);
        this._ctx = this._canvas.getContext('2d');
        this._ctx.imageSmoothingEnabled = false;
        this._updateCanvasSize();

        map.on('move zoom resize', this._redraw, this);
        this._redraw();
    },

    onRemove: function(map) {
        map.off('move zoom resize', this._redraw, this);
        if (this._canvas) this._canvas.remove();
    },

    _updateCanvasSize: function() {
        const size = this._map.getSize();
        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._canvas.style.width = size.x + 'px';
        this._canvas.style.height = size.y + 'px';
        this._ctx.imageSmoothingEnabled = false;
    },

    _redraw: function() {
        if (!this._ctx || !this._canvas || !this._map) return;

        const ctx = this._ctx;
        const map = this._map;
        const size = map.getSize();

        this._updateCanvasSize();

        // Position canvas so its (0,0) aligns with the container's (0,0)
        const topLeft = map.containerPointToLayerPoint(L.point(0, 0));
        this._canvas.style.transform = `translate(${topLeft.x}px, ${topLeft.y}px)`;

        ctx.clearRect(0, 0, size.x, size.y);

        const OVERLAP = 1.0;

        this.labels.forEach(label => {
            if (!label.visible || !label.pixels) return;

            // Parse color once, apply alpha
            const c = label.color;
            const r = parseInt(c.substr(1,2), 16);
            const g = parseInt(c.substr(3,2), 16);
            const b = parseInt(c.substr(5,2), 16);
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, 0.7)`;

            for (const pixel of label.pixels) {
                const pb = window.calculatePixelBounds(pixel.lat, pixel.lon);
                const sw = map.latLngToContainerPoint(pb[0]);
                const ne = map.latLngToContainerPoint(pb[1]);

                if (ne.x < 0 || sw.x > size.x || sw.y < 0 || ne.y > size.y) continue;

                const x = sw.x - OVERLAP;
                const y = ne.y - OVERLAP;
                const w = ne.x - sw.x + 2 * OVERLAP;
                const h = sw.y - ne.y + 2 * OVERLAP;

                if (w > 0.1 && h > 0.1) {
                    ctx.fillRect(x, y, w, h);
                }
            }
        });
    },

    updateLabels: function(newLabels) {
        this.labels = newLabels;
        this._redraw();
    }
});

// Global variable to hold the overlay layer
let labelOverlay = null;

// Update the overlay visualization
function updateOverlay() {
    if (!window.maps.panel5) return;

    const visibleLabels = savedLabels.filter(l => l.visible);

    if (visibleLabels.length > 0) {
        // If overlay exists, update it in place (avoids remove/re-add timing issues)
        if (labelOverlay && window.maps.panel5.hasLayer(labelOverlay)) {
            labelOverlay.updateLabels(savedLabels);
        } else {
            // Create new overlay if it doesn't exist yet
            labelOverlay = new PersistentLabelOverlay(savedLabels);
            labelOverlay.addTo(window.maps.panel5);
        }
    } else {
        // No visible labels - remove overlay if it exists
        if (labelOverlay && window.maps.panel5.hasLayer(labelOverlay)) {
            window.maps.panel5.removeLayer(labelOverlay);
            labelOverlay = null;
        }
    }

    // Update UMAP colors to match labels
    window.updateUMAPColorsFromLabels();
}

// Export map as high-resolution JPG with labels and legend.
// Fetches satellite tiles at max zoom (18) for the current view,
// composites them with label overlays and a colour legend.
async function exportMapAsJPG() {
    if (!window.maps.rgb) return;

    const btn = document.getElementById('labelling-export-btn');
    if (btn) btn.disabled = true;
    const MAX_ZOOM = 18;
    const TILE_SZ = 256;

    try {
        const map = window.maps.rgb;
        const bounds = map.getBounds();

        // Convert visible bounds to pixel coordinates at max zoom
        const nwPoint = map.project(bounds.getNorthWest(), MAX_ZOOM);
        const sePoint = map.project(bounds.getSouthEast(), MAX_ZOOM);

        // Tile range at max zoom
        const tileMinX = Math.floor(nwPoint.x / TILE_SZ);
        const tileMinY = Math.floor(nwPoint.y / TILE_SZ);
        const tileMaxX = Math.floor(sePoint.x / TILE_SZ);
        const tileMaxY = Math.floor(sePoint.y / TILE_SZ);

        const tilesX = tileMaxX - tileMinX + 1;
        const tilesY = tileMaxY - tileMinY + 1;
        const totalTiles = tilesX * tilesY;

        // Canvas sized to cover these tiles exactly
        const canvasW = tilesX * TILE_SZ;
        const canvasH = tilesY * TILE_SZ;

        // Offset: how many pixels into the first tile the view starts
        const offsetX = nwPoint.x - tileMinX * TILE_SZ;
        const offsetY = nwPoint.y - tileMinY * TILE_SZ;

        // The actual visible area in pixels at max zoom
        const viewW = sePoint.x - nwPoint.x;
        const viewH = sePoint.y - nwPoint.y;

        btn.textContent = `Fetching 0/${totalTiles} tiles...`;
        await new Promise(r => setTimeout(r, 50));

        const canvas = document.createElement('canvas');
        canvas.width = canvasW;
        canvas.height = canvasH;
        const ctx = canvas.getContext('2d');
        ctx.imageSmoothingEnabled = false;

        // Step 1: Fetch and draw all satellite tiles at max zoom
        let loaded = 0;
        const BATCH = 24; // concurrent fetches
        const tileJobs = [];
        for (let ty = tileMinY; ty <= tileMaxY; ty++) {
            for (let tx = tileMinX; tx <= tileMaxX; tx++) {
                tileJobs.push({tx, ty});
            }
        }

        for (let i = 0; i < tileJobs.length; i += BATCH) {
            const batch = tileJobs.slice(i, i + BATCH);
            await Promise.all(batch.map(({tx, ty}) => new Promise(resolve => {
                const img = new Image();
                img.crossOrigin = 'anonymous';
                img.onload = () => {
                    const dx = (tx - tileMinX) * TILE_SZ;
                    const dy = (ty - tileMinY) * TILE_SZ;
                    ctx.drawImage(img, dx, dy, TILE_SZ, TILE_SZ);
                    loaded++;
                    btn.textContent = `Fetching ${loaded}/${totalTiles} tiles...`;
                    resolve();
                };
                img.onerror = () => { loaded++; resolve(); };
                img.src = window.satelliteSources[window.currentSatelliteSource].exportUrl(MAX_ZOOM, ty, tx);
            })));
        }

        btn.textContent = 'Rendering labels...';
        await new Promise(r => setTimeout(r, 50));

        // Step 2: Draw label pixels at max-zoom pixel coordinates
        const visibleLabels = savedLabels.filter(l => l.visible && l.pixels && l.pixels.length > 0);
        for (const label of visibleLabels) {
            const c = label.color;
            const cr = parseInt(c.substr(1,2), 16);
            const cg = parseInt(c.substr(3,2), 16);
            const cb = parseInt(c.substr(5,2), 16);
            ctx.fillStyle = `rgba(${cr}, ${cg}, ${cb}, 0.7)`;

            for (const pixel of label.pixels) {
                const pb = window.calculatePixelBounds(pixel.lat, pixel.lon);
                // Project pixel bounds to max-zoom pixel coords
                const swPx = map.project(L.latLng(pb[0][0], pb[0][1]), MAX_ZOOM);
                const nePx = map.project(L.latLng(pb[1][0], pb[1][1]), MAX_ZOOM);
                // Convert to canvas coords (relative to tile grid origin)
                const x = swPx.x - tileMinX * TILE_SZ;
                const y = nePx.y - tileMinY * TILE_SZ;
                const w = nePx.x - swPx.x + 1;
                const h = swPx.y - nePx.y + 1;
                if (x + w < 0 || x > canvasW || y + h < 0 || y > canvasH) continue;
                if (w > 0.1 && h > 0.1) ctx.fillRect(x, y, w, h);
            }
        }

        // Step 3: Crop to visible area only
        const outCanvas = document.createElement('canvas');
        outCanvas.width = Math.round(viewW);
        outCanvas.height = Math.round(viewH);
        const outCtx = outCanvas.getContext('2d');
        outCtx.imageSmoothingEnabled = false;
        outCtx.drawImage(canvas, -offsetX, -offsetY);

        // Step 4: Draw legend (scaled for high-res output)
        if (visibleLabels.length > 0) {
            const s = Math.max(1, Math.round(outCanvas.width / 2400)); // scale factor for legend (25% of original)
            const pad = 12 * s;
            const rowH = 28 * s;
            const swatchSz = 16 * s;
            const fontSize = 15 * s;
            const legendW = 200 * s;
            const legendH = visibleLabels.length * rowH + pad * 2;
            const legendX = pad;
            const legendY = outCanvas.height - pad - legendH;

            outCtx.fillStyle = 'rgba(255, 255, 255, 0.92)';
            outCtx.strokeStyle = 'rgba(0, 0, 0, 0.3)';
            outCtx.lineWidth = s;
            outCtx.fillRect(legendX, legendY, legendW, legendH);
            outCtx.strokeRect(legendX, legendY, legendW, legendH);

            outCtx.font = `bold ${fontSize}px Arial, sans-serif`;
            outCtx.textBaseline = 'middle';
            for (let i = 0; i < visibleLabels.length; i++) {
                const label = visibleLabels[i];
                const ey = legendY + pad + i * rowH + rowH / 2;

                outCtx.fillStyle = label.color;
                outCtx.fillRect(legendX + pad, ey - swatchSz/2, swatchSz, swatchSz);
                outCtx.strokeStyle = 'rgba(0,0,0,0.4)';
                outCtx.lineWidth = s * 0.5;
                outCtx.strokeRect(legendX + pad, ey - swatchSz/2, swatchSz, swatchSz);

                outCtx.fillStyle = '#333';
                outCtx.fillText(label.name, legendX + pad + swatchSz + 10 * s, ey);
            }
        }

        // Step 5: Download as JPG
        btn.textContent = 'Saving...';
        outCanvas.toBlob(function(blob) {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${window.currentViewportName}_labels_z${MAX_ZOOM}.jpg`;
            a.click();
            URL.revokeObjectURL(url);
            if (btn) btn.disabled = false;
        }, 'image/jpeg', 0.95);

    } catch (e) {
        console.error('[EXPORT] Error exporting map:', e);
        alert('Error exporting map. Check console for details.');
        if (btn) btn.disabled = false;
    }
}

// Attach event listeners to sidebar buttons and modal
document.addEventListener('DOMContentLoaded', () => {

    // Modal button listeners
    document.getElementById('label-save-confirm').addEventListener('click', confirmSaveLabel);
    document.getElementById('label-save-cancel').addEventListener('click', closeSaveLabelModal);

    // Color picker change - update hex display
    document.getElementById('label-color-input').addEventListener('change', function() {
        document.getElementById('color-hex-display').textContent = this.value.toUpperCase();
    });
    document.getElementById('label-color-input').addEventListener('input', function() {
        document.getElementById('color-hex-display').textContent = this.value.toUpperCase();
    });

    // Close modal when clicking overlay background
    document.getElementById('save-label-modal-overlay').addEventListener('click', function(e) {
        if (e.target === this) {
            closeSaveLabelModal();
        }
    });

    // Timeline modal close handlers
    document.getElementById('timeline-close-btn').addEventListener('click', function() {
        document.getElementById('timeline-modal-overlay').style.display = 'none';
    });
    document.getElementById('timeline-modal-overlay').addEventListener('click', function(e) {
        if (e.target === this) {
            this.style.display = 'none';
        }
    });

    // Allow Enter key to confirm
    document.getElementById('label-name-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            confirmSaveLabel();
        }
    });

    // Labels close button
    document.getElementById('labels-close-btn').addEventListener('click', function() {
        document.getElementById('labels-details-panel').style.display = 'none';
    });

    // Segmentation event wiring
    document.getElementById('seg-run-btn').addEventListener('click', () => {
        const k = parseInt(document.getElementById('seg-k-input').value) || 5;
        window.runKMeans(Math.max(2, Math.min(20, k)));
    });
    document.getElementById('seg-k-minus').addEventListener('click', () => {
        const input = document.getElementById('seg-k-input');
        const k = Math.max(2, (parseInt(input.value) || 5) - 1);
        input.value = k;
        if (!window.segRunning) window.runKMeans(k);
    });
    document.getElementById('seg-k-plus').addEventListener('click', () => {
        const input = document.getElementById('seg-k-input');
        const k = Math.min(20, (parseInt(input.value) || 5) + 1);
        input.value = k;
        if (!window.segRunning) window.runKMeans(k);
    });
    document.getElementById('seg-k-input').addEventListener('change', () => {
        const input = document.getElementById('seg-k-input');
        let k = parseInt(input.value);
        if (isNaN(k) || k < 2) k = 2;
        if (k > 20) k = 20;
        input.value = k;
    });
    document.getElementById('seg-clear-btn').addEventListener('click', window.clearSegmentation);
    document.getElementById('seg-export-btn').addEventListener('click', window.saveAllClustersAsLabels);
    document.getElementById('panel6-promote-all-btn').addEventListener('click', window.saveAllClustersAsLabels);
    document.getElementById('panel6-toggle-all-btn').addEventListener('click', toggleAllOverlays);
    document.getElementById('seg-panel-close-btn').addEventListener('click', function() {
        document.getElementById('seg-results-panel').style.display = 'none';
    });

    // Make segmentation panel draggable by header
    (function() {
        const panel = document.getElementById('seg-results-panel');
        const header = document.getElementById('seg-panel-header');
        let dragging = false, startX, startY, startLeft, startTop;
        header.addEventListener('mousedown', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            dragging = true;
            startX = e.clientX;
            startY = e.clientY;
            const rect = panel.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            header.style.cursor = 'grabbing';
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            panel.style.left = (startLeft + e.clientX - startX) + 'px';
            panel.style.top = (startTop + e.clientY - startY) + 'px';
            panel.style.right = 'auto';
        });
        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            header.style.cursor = 'grab';
        });
    })();

    // Make labels panel draggable by header
    (function() {
        const panel = document.getElementById('labels-details-panel');
        const header = document.getElementById('labels-panel-header');
        let dragging = false, startX, startY, startLeft, startTop;
        header.addEventListener('mousedown', (e) => {
            if (e.target.tagName === 'BUTTON') return;
            dragging = true;
            startX = e.clientX;
            startY = e.clientY;
            const rect = panel.getBoundingClientRect();
            startLeft = rect.left;
            startTop = rect.top;
            header.style.cursor = 'grabbing';
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            panel.style.left = (startLeft + e.clientX - startX) + 'px';
            panel.style.top = (startTop + e.clientY - startY) + 'px';
            panel.style.right = 'auto';
            panel.style.bottom = 'auto';
        });
        document.addEventListener('mouseup', () => {
            if (!dragging) return;
            dragging = false;
            header.style.cursor = 'grab';
        });
    })();

    // Help button toggle
    function toggleHelpPopup() {
        const popup = document.getElementById('help-popup');
        popup.style.display = popup.style.display === 'block' ? 'none' : 'block';
    }
    document.getElementById('help-btn').addEventListener('click', toggleHelpPopup);
    document.getElementById('help-close-btn').addEventListener('click', toggleHelpPopup);

    // Show help popup on first visit
    if (!localStorage.getItem('helpShown')) {
        document.getElementById('help-popup').style.display = 'block';
        localStorage.setItem('helpShown', '1');
    }

    // Status button - show/hide popup and fetch status
    document.getElementById('status-btn').addEventListener('click', async function() {
        const popup = document.getElementById('status-popup');
        const content = document.getElementById('status-content');

        if (popup.style.display === 'block') {
            popup.style.display = 'none';
            return;
        }

        // Show popup with loading state
        popup.style.display = 'block';
        content.innerHTML = 'Loading...';

        try {
            const response = await fetch(`/api/viewports/${window.currentViewportName}/is-ready`);
            const status = await response.json();

            // Format the status nicely
            const readyIcon = status.ready ? '✅' : '⏳';
            const embeddingsIcon = status.has_embeddings ? '✅' : '❌';
            const pyramidsIcon = status.has_pyramids ? '✅' : '❌';
            const vectorsIcon = status.has_vectors ? '✅' : '❌';
            const umapIcon = status.has_umap ? '✅' : '❌';
            const years = status.years_available ? status.years_available.join(', ') : 'None';
            const unavailable = status.years_unavailable && status.years_unavailable.length
                ? status.years_unavailable.join(', ')
                : null;

            content.innerHTML = `
                <div style="margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #ddd;">
                    <strong>Viewport:</strong> ${window.currentViewportName}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>Status:</strong> ${readyIcon} ${status.message}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>GeoTIFF Mosaics:</strong> ${embeddingsIcon}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>Pyramids:</strong> ${pyramidsIcon}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>Vectors:</strong> ${vectorsIcon}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>UMAP:</strong> ${umapIcon}
                </div>
                <div style="margin-bottom: 8px;">
                    <strong>Years Available:</strong> ${years}
                </div>
                ${unavailable ? `<div style="margin-bottom: 8px;"><strong>Years Unavailable:</strong> &#10060; ${unavailable}</div>` : ''}
                <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #ddd; font-size: 10px; color: #666;">
                    Last checked: ${new Date().toLocaleTimeString()}
                </div>
            `;
        } catch (error) {
            content.innerHTML = `<div style="color: #d32f2f;">Error fetching status: ${error.message}</div>`;
        }
    });

    // Status close button
    document.getElementById('status-close-btn').addEventListener('click', function() {
        document.getElementById('status-popup').style.display = 'none';
    });
});

// ===== END PERSISTENT LABEL SYSTEM =====


// ── Window bridges for state shared with inline scripts ──

Object.defineProperty(window, 'manualLabels', {
    get: () => manualLabels,
    set: (v) => { manualLabels = v; },
    configurable: true,
});
Object.defineProperty(window, 'currentManualLabel', {
    get: () => currentManualLabel,
    set: (v) => { currentManualLabel = v; },
    configurable: true,
});
Object.defineProperty(window, 'savedLabels', {
    get: () => savedLabels,
    set: (v) => { savedLabels = v; },
    configurable: true,
});
Object.defineProperty(window, 'currentSearchCache', {
    get: () => currentSearchCache,
    set: (v) => { currentSearchCache = v; },
    configurable: true,
});
Object.defineProperty(window, 'manualClassOverlays', {
    get: () => manualClassOverlays,
    set: (v) => { manualClassOverlays = v; },
    configurable: true,
});
Object.defineProperty(window, '_classMatchCache', {
    get: () => _classMatchCache,
    set: (v) => { _classMatchCache = v; },
    configurable: true,
});
Object.defineProperty(window, 'isPolygonDrawing', {
    get: () => isPolygonDrawing,
    set: (v) => { isPolygonDrawing = v; },
    configurable: true,
});
Object.defineProperty(window, 'labelMode', {
    get: () => labelMode,
    set: (v) => { labelMode = v; },
    configurable: true,
});

// ── Expose functions on window for inline script and cross-module access ──

// Timeline
window.getAvailableYears = getAvailableYears;
window.computeLabelTimeline = computeLabelTimeline;
window.showLabelTimeline = showLabelTimeline;
window.showManualLabelTimeline = showManualLabelTimeline;

// Manual label CRUD
window.setLabelMode = setLabelMode;
window.setCurrentManualLabel = setCurrentManualLabel;
window.updateManualLabelColor = updateManualLabelColor;
window.updateActiveLabelColor = updateActiveLabelColor;
window.restoreManualLabelState = restoreManualLabelState;
window.getClassLabels = getClassLabels;
window.getClassThreshold = getClassThreshold;
window.rebuildClassOverlay = rebuildClassOverlay;
window.toggleClassExpand = toggleClassExpand;
window.toggleClassVisibility = toggleClassVisibility;
window.rebuildManualOverlays = rebuildManualOverlays;
window.saveManualLabelsToStorage = saveManualLabelsToStorage;
window.addManualLabel = addManualLabel;
window.removeManualClass = removeManualClass;
window.removeManualLabel = removeManualLabel;
window.toggleAllManualLabels = toggleAllManualLabels;
window.updateManualClassThreshold = updateManualClassThreshold;
window.renderManualLabelsList = renderManualLabelsList;
window.triggerManualClassification = triggerManualClassification;
window.renderManualClassification = renderManualClassification;
window.activateManualClass = activateManualClass;

// Polygon drawing
window.initPolygonDrawing = initPolygonDrawing;
window.startPolygonDrawing = startPolygonDrawing;
window.cancelPolygonDrawing = cancelPolygonDrawing;
window.handlePolygonComplete = handlePolygonComplete;
window.pointInPolygon = pointInPolygon;
window.rasterizePolygon = rasterizePolygon;

// Import/Export
window.exportManualLabels = exportManualLabels;
window.doExportManualLabels = doExportManualLabels;
window.downloadFile = downloadFile;
window.exportManualLabelsShapefile = exportManualLabelsShapefile;
window.importManualLabels = importManualLabels;
window.importGeoJSON = importGeoJSON;
window.importLabelsJSON = importLabelsJSON;
window.importShapefile = importShapefile;
window.updateThresholdDisplay = updateThresholdDisplay;

// Persistent labels
window.persistLabels = persistLabels;
window.loadSavedLabels = loadSavedLabels;
window.recomputeLabelPixels = recomputeLabelPixels;
window.renderLabelsInto = renderLabelsInto;
window.updateLabelsUI = updateLabelsUI;
window.renameLabel = renameLabel;
window.renderLabelGroup = renderLabelGroup;
window.closeSaveLabelModal = closeSaveLabelModal;
window.deleteLabelGroup = deleteLabelGroup;
window.toggleAllOverlays = toggleAllOverlays;
window.exportSavedLabels = exportSavedLabels;
window.exportSavedLabelsGeoJSON = exportSavedLabelsGeoJSON;
window.importSavedLabels = importSavedLabels;
window.PersistentLabelOverlay = PersistentLabelOverlay;
window.updateOverlay = updateOverlay;
window.exportMapAsJPG = exportMapAsJPG;
window.markExportDirty = markExportDirty;
window.markExportClean = markExportClean;
window.confirmSaveLabel = confirmSaveLabel;
