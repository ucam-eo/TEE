// =====================================================================
// app.js — Application init, dependency system, embedding labels,
//           progress tracking.  Extracted from viewer.html Step 8.
// =====================================================================

// Auth check: set demo mode for unauthenticated users
let isLoggedIn = false;
fetch('/api/auth/status').then(r => r.json()).then(data => {
    if (data.logged_in && data.user) {
        isLoggedIn = true;
        document.getElementById('userName').textContent = data.user;
        document.getElementById('userInfo').style.display = 'flex';
    }
    if (data.auth_enabled && !data.logged_in) {
        // Demo mode: show login button instead of redirecting
        document.getElementById('loginBtn').style.display = '';
    }
}).catch(() => {});

function doLogout() {
    fetch('/api/auth/logout', {method: 'POST'}).then(() => {
        window.location.href = '/login.html';
    });
}

// Wrap global fetch to redirect on 401 (only for logged-in users)
const _origFetch = window.fetch;
window.fetch = function(url, opts) {
    return _origFetch(url, opts).then(resp => {
        if (resp.status === 401 && !String(url).includes('/api/auth/') && isLoggedIn) {
            window.location.href = '/login.html?next=' + encodeURIComponent(window.location.pathname + window.location.search);
        }
        return resp;
    });
};

let TILE_SERVER = window.location.origin; // default, overridden by /api/config
// center, zoom, viewportBounds are now in js/maps.js

// Current embedding year
let currentEmbeddingYear = '2025';
let currentViewportName = 'tile_aligned';

// Storage for labels: {panel: [[lat, lon, label], ...]}
let labels = {
    'osm': [],
    'embedding': [],
    'rgb': []
};

// Storage for marker objects: {panel: {key: marker}}
let markers = {
    'osm': {},
    'embedding': {},
    'rgb': {}
};

// Map instances
let maps = {};

// New state variables for 6-panel viewer
let currentPanelMode = 'explore';       // 'explore', 'change-detection', or 'labelling'
let heatmapSatelliteLayer = null;      // Satellite tiles for panel 5 in labelling mode
let explorerCanvasLayer2 = null;      // Explorer viz for Panel 6


// ===== DECLARATIVE PANEL DEPENDENCY SYSTEM =====
// Single source of truth for viewport readiness, polled from /api/viewports/{name}/is-ready
let viewportStatus = {
    has_embeddings: false,
    has_pyramids: false,
    has_vectors: false,
    has_umap: false,
    years_available: [],
    // Client-side flags
    vectors_downloaded: false,
    pca_loaded: false,
    umap_loaded: false
};

let pollTimerId = null;
const POLL_FAST = 2000;   // 2s while server still computing
const POLL_SLOW = 30000;  // 30s when all server work is done

// Dependency registry — each entry declares when a panel/element is ready
const dependencyRegistry = [
    {
        id: 'panel3-tiles',
        test: (s) => s.has_pyramids && s.years_available.length > 0,
        onReady: (s) => {
            console.log('[DEP] panel3-tiles: creating tile layer');
            const yearToUse = applyYearSelector(
                'embedding-year-selector', s.years_available, currentEmbeddingYear,
                (year) => window.switchEmbeddingYear(parseInt(year))
            );
            currentEmbeddingYear = String(yearToUse);
            refreshEmbeddingTileLayer('embedding', currentEmbeddingYear);
        },
        satisfied: false
    },
    {
        id: 'panel6-tiles',
        test: (s) => s.has_pyramids && s.years_available.length > 0,
        onReady: (s) => {
            console.log('[DEP] panel6-tiles: creating tile layer');
            const sorted = [...s.years_available].map(String).sort();
            if (!window.currentEmbeddingYear2 || !sorted.includes(window.currentEmbeddingYear2)) {
                window.currentEmbeddingYear2 = sorted[sorted.length - 1];
            }
            // Pick a different year than Panel 3 so the change heatmap can load
            if (s.years_available.length >= 2 && window.currentEmbeddingYear2 === currentEmbeddingYear) {
                const alt = s.years_available.find(y => String(y) !== currentEmbeddingYear);
                if (alt) window.currentEmbeddingYear2 = String(alt);
            }
            refreshEmbeddingTileLayer('embedding2', window.currentEmbeddingYear2);
        },
        satisfied: false
    },
    {
        id: 'year-selectors',
        test: (s) => s.years_available.length > 1,
        onReady: (s) => {
            console.log('[DEP] year-selectors: multiple years available, showing selectors');
            applyYearSelector(
                'embedding-year-selector', s.years_available, currentEmbeddingYear,
                (year) => window.switchEmbeddingYear(parseInt(year))
            );
        },
        onNotReady: () => {
            document.getElementById('embedding-year-selector').style.display = 'none';
        },
        satisfied: false
    },
    {
        id: 'year-selector-2-visibility',
        test: (s) => currentPanelMode === 'change-detection' && s.years_available.length > 1,
        onReady: (s) => {
            applyYearSelector(
                'embedding-year-selector-2', s.years_available, window.currentEmbeddingYear2,
                (year) => window.switchEmbeddingYear2(year)
            );
        },
        onNotReady: () => {
            const sel2 = document.getElementById('embedding-year-selector-2');
            if (sel2) sel2.style.display = 'none';
        },
        satisfied: false
    },
    {
        id: 'vectors-download',
        test: (s) => s.has_vectors && !s.vectors_downloaded,
        onReady: async (s) => {
            const year = currentEmbeddingYear || s.years_available[0];
            console.log(`[DEP] vectors-download: downloading vector data for ${currentViewportName}/${year}`);
            try {
                await window.downloadVectorData(currentViewportName, year);
                viewportStatus.vectors_downloaded = true;
                evaluateDependencies();
            } catch (e) {
                console.error('[DEP] vectors-download failed:', e);
                // Reset so next poll re-triggers the download
                const dep = dependencyRegistry.find(d => d.id === 'vectors-download');
                if (dep) dep.satisfied = false;
            }
        },
        satisfied: false
    },
    {
        id: 'label-controls',
        test: (s) => s.vectors_downloaded,
        onReady: () => {
            console.log('[DEP] label-controls: vectors ready, enabling controls');
            document.getElementById('similarity-threshold').disabled = false;
            document.getElementById('similarity-controls').style.opacity = '1';
            document.getElementById('label-controls-bar').style.opacity = '1';
            if (window.savedLabels.length > 0) {
                window.recomputeLabelPixels();
            }
            // Rebuild manual label overlays now that vectors are available
            if (window.labelMode === 'manual' && window.manualLabels.length > 0) {
                window.rebuildManualOverlays();
            }
            // Enable segmentation controls and populate year dropdown
            document.getElementById('seg-run-btn').disabled = false;
            document.getElementById('seg-k-input').disabled = false;
            document.getElementById('seg-k-minus').disabled = false;
            document.getElementById('seg-k-plus').disabled = false;
            document.getElementById('seg-controls').style.opacity = '1';
        },
        onNotReady: () => {
            document.getElementById('similarity-threshold').disabled = true;
            document.getElementById('similarity-controls').style.opacity = '0.4';
            document.getElementById('label-controls-bar').style.opacity = '0.4';
            document.querySelectorAll('#label-controls-bar button').forEach(b => b.disabled = true);
            // Disable segmentation controls
            document.getElementById('seg-run-btn').disabled = true;
            document.getElementById('seg-k-input').disabled = true;
            document.getElementById('seg-k-minus').disabled = true;
            document.getElementById('seg-k-plus').disabled = true;
            document.getElementById('seg-controls').style.opacity = '0.4';
        },
        satisfied: false
    },
    {
        id: 'panel4-pca',
        test: (s) => s.vectors_downloaded && !s.pca_loaded && window.currentDimReduction === 'pca',
        onReady: async () => {
            console.log('[DEP] panel4-pca: loading PCA visualization');
            await window.loadDimReduction('pca');
            viewportStatus.pca_loaded = true;
        },
        satisfied: false
    },
    {
        id: 'panel4-umap',
        test: (s) => s.vectors_downloaded && !s.umap_loaded && window.currentDimReduction === 'umap',
        onReady: async () => {
            console.log('[DEP] panel4-umap: loading UMAP visualization');
            await window.loadDimReduction('umap');
            viewportStatus.umap_loaded = true;
        },
        satisfied: false
    },
    {
        id: 'panel5-heatmap',
        test: (s) => s.has_vectors && s.years_available.length >= 2,
        onReady: () => {
            console.log('[DEP] panel5-heatmap: vectors ready, loading heatmap');
            window.loadHeatmap();
        },
        satisfied: false
    }
];

// Compare two status snapshots for changes (server-side flags only)
function hasStatusChanged(current, incoming) {
    return current.has_embeddings !== (incoming.has_embeddings || false) ||
           current.has_pyramids !== (incoming.has_pyramids || false) ||
           current.has_vectors !== (incoming.has_vectors || false) ||
           current.has_umap !== (incoming.has_umap || false) ||
           JSON.stringify(current.years_available) !== JSON.stringify(incoming.years_available || []);
}

// Evaluate all dependencies, fire callbacks on false→true / true→false transitions
let _evaluating = false;
let _reevaluateQueued = false;

function evaluateDependencies() {
    if (_evaluating) {
        _reevaluateQueued = true;
        return;
    }
    _evaluating = true;

    for (const dep of dependencyRegistry) {
        const nowSatisfied = dep.test(viewportStatus);
        if (nowSatisfied && !dep.satisfied) {
            console.log(`[DEP] ${dep.id}: ready`);
            dep.satisfied = true;
            if (dep.onReady) dep.onReady(viewportStatus);
        } else if (!nowSatisfied && dep.satisfied) {
            console.log(`[DEP] ${dep.id}: not ready`);
            dep.satisfied = false;
            if (dep.onNotReady) dep.onNotReady();
        }
    }

    _evaluating = false;
    if (_reevaluateQueued) {
        _reevaluateQueued = false;
        evaluateDependencies();
    }
}

// Poll /api/viewports/{name}/is-ready and update viewportStatus
async function pollViewportStatus() {
    if (!currentViewportName) return;

    try {
        const resp = await fetch(`/api/viewports/${currentViewportName}/is-ready`);
        const incoming = await resp.json();

        const changed = hasStatusChanged(viewportStatus, incoming);
        const yearsChanged = JSON.stringify(viewportStatus.years_available) !==
                             JSON.stringify(incoming.years_available || []);

        // Update server-side flags (preserve client-side flags)
        viewportStatus.has_embeddings = incoming.has_embeddings || false;
        viewportStatus.has_pyramids = incoming.has_pyramids || false;
        viewportStatus.has_vectors = incoming.has_vectors || false;
        viewportStatus.has_umap = incoming.has_umap || false;
        viewportStatus.years_available = incoming.years_available || [];

        if (changed) {
            console.log('[DEP] Status changed:', JSON.stringify({
                pyramids: viewportStatus.has_pyramids,
                vectors: viewportStatus.has_vectors,
                umap: viewportStatus.has_umap,
                years: viewportStatus.years_available
            }));
            evaluateDependencies();
        }

        // Update year dropdowns even when dependencies are already satisfied
        if (yearsChanged && viewportStatus.years_available.length > 0) {
            applyYearSelector(
                'embedding-year-selector', viewportStatus.years_available, currentEmbeddingYear,
                (year) => window.switchEmbeddingYear(parseInt(year))
            );
        }
    } catch (e) {
        console.warn('[DEP] Poll failed:', e);
    }

    // Schedule next poll — fast while server still computing, slow when done
    const serverBusy = !viewportStatus.has_pyramids || !viewportStatus.has_vectors;
    const interval = serverBusy ? POLL_FAST : POLL_SLOW;
    pollTimerId = setTimeout(pollViewportStatus, interval);
}

function startPoller() {
    console.log('[DEP] Starting dependency poller');
    pollViewportStatus();
}

function stopPoller() {
    if (pollTimerId) {
        clearTimeout(pollTimerId);
        pollTimerId = null;
    }
}

// Shared helper: populate a year dropdown, preserve user selection, show/hide by count
function applyYearSelector(selectorId, years, currentYear, onChange) {
    const selector = document.getElementById(selectorId);
    if (!selector) return currentYear;

    // Preserve user selection if still valid, otherwise pick latest year
    const sorted = [...years].map(String).sort();
    const yearToUse = years.includes(parseInt(currentYear)) || years.includes(String(currentYear))
        ? String(currentYear)
        : sorted[sorted.length - 1];

    // Populate dropdown
    selector.innerHTML = '';
    years.forEach(year => {
        const option = document.createElement('option');
        option.value = year;
        option.textContent = year;
        if (String(year) === yearToUse) option.selected = true;
        selector.appendChild(option);
    });

    // Show/hide based on count
    selector.style.display = years.length > 1 ? 'inline-block' : 'none';

    // Replace element to remove stale listeners, then attach new one
    const clone = selector.cloneNode(true);
    selector.parentNode.replaceChild(clone, selector);
    clone.addEventListener('change', (e) => onChange(e.target.value));

    return yearToUse;
}

// Shared helper: remove old tile layer, create new pixelated layer, invalidateSize
function refreshEmbeddingTileLayer(panelName, year) {
    const layerKey = panelName === 'embedding' ? 'embeddingLayer' : 'embedding2Layer';
    if (window[layerKey] && maps[panelName]) {
        maps[panelName].removeLayer(window[layerKey]);
    }
    window[layerKey] = L.pixelatedTileLayer(
        `${TILE_SERVER}/tiles/${currentViewportName}/${year}/{z}/{x}/{y}.png`,
        {
            tileSize: 256,
            bounds: window.viewportBounds,
            keepBuffer: 5,
            updateWhenZooming: false
        }
    );
    window[layerKey].addTo(maps[panelName]);
    maps[panelName].invalidateSize();
}
// ===== END DEPENDENCY SYSTEM =====

// Satellite tile sources for Panel 2

// Helper function to get viewport-specific localStorage key
function getLabelsStorageKey() {
    return `${currentViewportName}_labels_3panel`;
}

// Save labels to localStorage
function saveLabels() {
    const saveData = {
        // Old system: markers on maps
        labels: labels,
        embeddingYear: currentEmbeddingYear,
        // New system: embeddings for similarity search
        definedLabels: definedLabels,
        embeddingLabels: embeddingLabels,
        labelColors: labelColors
    };
    localStorage.setItem(getLabelsStorageKey(), JSON.stringify(saveData));
    const oldTotal = Object.values(labels).reduce((sum, arr) => sum + arr.length, 0);
    const newTotal = Object.values(embeddingLabels).reduce((sum, arr) => sum + arr.length, 0);
    console.log(`✓ Saved to ${getLabelsStorageKey()}: ${oldTotal} markers + ${newTotal} embeddings with colors`);
}

// Load labels from localStorage
function loadLabels() {
    const storageKey = getLabelsStorageKey();
    let stored = localStorage.getItem(storageKey);

    if (stored) {
        try {
            const saveData = JSON.parse(stored);

            // Restore old marker system
            if (saveData.labels) {
                Object.keys(saveData.labels).forEach(panel => {
                    saveData.labels[panel].forEach(([lat, lon, label]) => {
                        window.addMarker(panel, lat, lon, label);
                    });
                });
                const oldCount = Object.values(saveData.labels).reduce((sum, arr) => sum + arr.length, 0);
                console.log(`✓ Loaded ${oldCount} markers`);
            }

            // Restore new embedding labels system
            if (saveData.definedLabels && saveData.embeddingLabels) {
                definedLabels = saveData.definedLabels;
                embeddingLabels = saveData.embeddingLabels;
                if (saveData.labelColors) {
                    labelColors = saveData.labelColors;
                }
                updateLabelDropdown();
                updateLabelCount();
                const newCount = Object.values(embeddingLabels).reduce((sum, arr) => sum + arr.length, 0);
                console.log(`✓ Loaded ${newCount} embeddings for ${definedLabels.length} labels with colors`);
            }
        } catch (error) {
            console.error('Error loading labels:', error);
        }
    }
}

// Clear all labels
function clearAllLabels() {
    if (!confirm('Clear all labels?')) return;

    Object.keys(markers).forEach(panel => {
        Object.values(markers[panel]).forEach(marker => {
            maps[panel].removeLayer(marker);
        });
        markers[panel] = {};
        labels[panel] = [];
    });

    updateLabelCount();
    console.log('Cleared all labels');
}

// Export labels to JSON
function exportLabels() {
    const exportData = {
        embeddingYear: currentEmbeddingYear,
        labels: labels,
        timestamp: new Date().toISOString()
    };
    const dataStr = JSON.stringify(exportData, null, 2);
    const dataBlob = new Blob([dataStr], {type: 'application/json'});
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${currentViewportName}_labels_${currentEmbeddingYear}_${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
    console.log('Exported labels to JSON file');
}

// =====================================================================
// EMBEDDING LABELING SYSTEM
// =====================================================================

// Label management
let definedLabels = [];  // List of all defined labels
let embeddingLabels = {}; // DEPRECATED — legacy label system, kept as empty stub
let labelColors = {};    // {label: "#FF0000", ...}
let labelPixels = {}; // {key: {label: 'road', coordinate: {lat, lon}}} for visualization

// Similarity search tracking
let similarPixels = {};          // {key: {label, coordinate, embedding, distance, rectangle, marker}}
let isSimilaritySearchActive = false;
let activeSearchLabel = null;



// Pick a contrastive color not already in use. Never offers yellow or black.
function nextLabelColor() {
    // Curated palette of distinct, saturated hues (no yellow, no black)
    const palette = [
        '#FF6B6B', // red
        '#4ECDC4', // teal
        '#45B7D1', // sky blue
        '#96CEB4', // sage green
        '#DDA15E', // amber/orange
        '#BC6C25', // brown
        '#9B59B6', // purple
        '#E74C3C', // crimson
        '#1ABC9C', // turquoise
        '#3498DB', // blue
        '#E67E22', // orange
        '#2ECC71', // emerald
        '#8E44AD', // dark purple
        '#E84393', // pink
        '#00CEC9', // cyan
        '#6C5CE7', // indigo
        '#FD79A8', // light pink
        '#00B894', // mint
        '#D63031', // dark red
        '#0984E3', // bright blue
    ];

    // Collect all colors currently in use
    const usedColors = new Set();
    for (const c of Object.values(labelColors)) usedColors.add(c.toUpperCase());
    for (const l of window.savedLabels) if (l.color) usedColors.add(l.color.toUpperCase());

    // Return first unused palette color
    for (const c of palette) {
        if (!usedColors.has(c.toUpperCase())) return c;
    }

    // All palette colors used — generate a random saturated hue (avoid yellow 40-70 deg, black)
    let hue;
    do { hue = Math.floor(Math.random() * 360); } while (hue >= 40 && hue <= 70);
    const sat = 65 + Math.floor(Math.random() * 20); // 65-85%
    const lit = 45 + Math.floor(Math.random() * 15); // 45-60%
    // Convert HSL to hex
    const h = hue, s = sat / 100, l = lit / 100;
    const a2 = s * Math.min(l, 1 - l);
    const f = n => { const k = (n + h / 30) % 12; return l - a2 * Math.max(Math.min(k - 3, 9 - k, 1), -1); };
    const toHex = x => Math.round(x * 255).toString(16).padStart(2, '0');
    return '#' + toHex(f(0)) + toHex(f(8)) + toHex(f(4));
}

// Create a new label
function createLabelDialog() {
    const labelName = prompt('Enter new label name (e.g., "road", "building", "tree"):');
    if (labelName && labelName.trim()) {
        const label = labelName.trim();
        if (definedLabels.includes(label)) {
            alert(`Label "${label}" already exists!`);
            return;
        }

        showColorPickerModal(label, nextLabelColor());
    }
}

function showColorPickerModal(label, defaultColor) {
    // Create modal backdrop
    const backdrop = document.createElement('div');
    backdrop.id = 'color-picker-backdrop';
    backdrop.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        background: rgba(0,0,0,0.5);
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
    `;

    // Create modal dialog
    const modal = document.createElement('div');
    modal.style.cssText = `
        background: white;
        padding: 30px;
        border-radius: 10px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        z-index: 10000;
        font-family: Arial, sans-serif;
        max-width: 400px;
        width: 90%;
    `;

    modal.innerHTML = `
        <div style="margin-bottom: 20px;">
            <h3 style="margin: 0 0 20px 0; color: #333; font-size: 18px;">Select color for "${label}"</h3>

            <div style="display: flex; align-items: center; gap: 15px; margin-bottom: 20px;">
                <label style="font-weight: bold; margin: 0;">Color:</label>
                <input type="color" id="label-color-picker" value="${defaultColor}" style="width: 60px; height: 50px; cursor: pointer; border: 2px solid #ddd; border-radius: 6px;">
                <div id="color-preview" style="display: inline-block; width: 60px; height: 50px; background: ${defaultColor}; border: 2px solid #999; border-radius: 6px;"></div>
            </div>

            <div style="display: flex; gap: 10px; justify-content: flex-end;">
                <button onclick="document.getElementById('color-picker-backdrop').remove();" style="padding: 10px 20px; background: #ddd; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">Cancel</button>
                <button onclick="confirmLabelColorSelection('${label}');" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">Create Label</button>
            </div>
        </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Live preview color picker
    const colorPicker = document.getElementById('label-color-picker');
    const preview = document.getElementById('color-preview');
    colorPicker.addEventListener('input', (e) => {
        preview.style.background = e.target.value;
    });

    // Focus color picker
    colorPicker.focus();
}

function confirmLabelColorSelection(label) {
    const selectedColor = document.getElementById('label-color-picker').value;

    if (definedLabels.includes(label)) {
        alert(`Label "${label}" already exists!`);
        return;
    }

    definedLabels.push(label);
    definedLabels.sort();
    embeddingLabels[label] = [];
    labelColors[label] = selectedColor;

    // Close modal
    const backdrop = document.getElementById('color-picker-backdrop');
    if (backdrop) backdrop.remove();

    // Update UI
    updateLabelDropdown();
    saveLabels();
    console.log(`Created label: "${label}" with color ${selectedColor}`);
}

// Update the active label dropdown
function updateLabelDropdown() {            const select = document.getElementById('active-label');
    if (!select) {
        console.error('[DROPDOWN] ERROR: Could not find element with id="active-label"');
        return;
    }
    select.innerHTML = '';
    if (definedLabels.length === 0) {                select.innerHTML = '<option value="">No labels yet</option>';
        select.disabled = true;
    } else {                definedLabels.forEach(label => {
            const option = document.createElement('option');
            option.value = label;
            option.textContent = label;
            select.appendChild(option);
        });
        select.value = definedLabels[0];
        select.disabled = false;
    }
}


// Update coordinates display on mouse move over embedding map
function updateCoordinatesDisplay(lat, lon) {
    const coordsText = document.getElementById('coords-text');
    if (coordsText) {
        coordsText.textContent = `Lat: ${lat.toFixed(6)} | Lon: ${lon.toFixed(6)}`;
    }
}

// Get a color for a label (for visualization)
function getColorForLabel(label) {
    // Use user-selected color if available
    if (labelColors[label]) {
        return labelColors[label];
    }

    // Fallback to predefined colors
    const colors = {
        'road': '#FF6B6B',
        'building': '#4ECDC4',
        'tree': '#45B7D1',
        'water': '#96CEB4',
        'grass': '#FFEAA7',
        'car': '#DDA15E',
        'person': '#BC6C25'
    };
    if (colors[label]) return colors[label];

    // Generate consistent color from label name as last resort
    let hash = 0;
    for (let i = 0; i < label.length; i++) {
        hash = label.charCodeAt(i) + ((hash << 5) - hash);
    }
    const color = '#' + (Math.abs(hash) % 0xFFFFFF).toString(16).padStart(6, '0');
    return color;
}

// Invert RGB color: (R, G, B) -> (255-R, 255-G, 255-B)
function invertColor(hexColor) {
    // Remove # if present
    const color = hexColor.replace('#', '');
    // Parse hex to RGB
    const r = parseInt(color.substr(0, 2), 16);
    const g = parseInt(color.substr(2, 2), 16);
    const b = parseInt(color.substr(4, 2), 16);
    // Invert: 255 - value
    const invR = (255 - r).toString(16).padStart(2, '0');
    const invG = (255 - g).toString(16).padStart(2, '0');
    const invB = (255 - b).toString(16).padStart(2, '0');
    return '#' + invR + invG + invB;
}

// Calculate pixel size in degrees (10m x 10m)

// Delete a labeled pixel
function deleteLabeledPixel(key, pixelData) {
    // Confirm deletion
    if (!confirm(`Delete labeled pixel "${pixelData.label}"?`)) {
        return;
    }

    // Find and remove the embedding from the label's embedding array
    const embeddingIndex = embeddingLabels[pixelData.label].findIndex(emb =>
        JSON.stringify(emb) === JSON.stringify(pixelData.embedding)
    );

    if (embeddingIndex !== -1) {
        embeddingLabels[pixelData.label].splice(embeddingIndex, 1);
        console.log(`[DELETE] Removed embedding from label "${pixelData.label}"`);
    }

    // Remove the visual elements (rectangle and marker)
    if (pixelData.rectangle) {
        maps.embedding.removeLayer(pixelData.rectangle);
    }
    if (pixelData.marker) {
        maps.embedding.removeLayer(pixelData.marker);
    }

    // Remove from tracking
    delete labelPixels[key];

    // Update label count
    updateLabelCount();

    console.log(`[DELETE] Deleted labeled pixel at key "${key}"`);
}

// Highlight labeled pixel on embedding map as rectangle
function highlightLabeledPixel(lat, lon, label, key) {
    const labelColor = getColorForLabel(label);
    const bounds = window.calculatePixelBounds(lat, lon);

    const rectangle = L.rectangle(bounds, {
        color: labelColor,  // Label color outline
        weight: 1,  // Thin outline
        opacity: 1.0,
        fill: true,
        fillColor: labelColor,  // Label color fill
        fillOpacity: 0.8,  // Solid color
        className: 'labeled-pixel'
    }).addTo(maps.embedding);

    // Show label on hover
    const popupContent = `<div style="font-weight: 600; color: ${labelColor}; font-size: 12px;">${label}</div>`;
    rectangle.bindPopup(popupContent);

    rectangle.on('mouseover', function() {
        this.openPopup();
        this.setStyle({fillOpacity: 0.95, weight: 2});  // Brighten on hover
    });
    rectangle.on('mouseout', function() {
        this.closePopup();
        this.setStyle({fillOpacity: 0.8, weight: 1});  // Reset
    });

    // Click to delete
    rectangle.on('click', function() {
        deleteLabeledPixel(key, labelPixels[key]);
    });

    // Add a pin/marker at the center of the labeled pixel for better visibility
    const markerIcon = L.divIcon({
        html: `<div style="
            width: 12px;
            height: 12px;
            background: ${labelColor};
            border: 1px solid rgba(0,0,0,0.3);
            border-radius: 50%;
            box-shadow: 0 0 6px rgba(0,0,0,0.4);
        "></div>`,
        iconSize: [12, 12],
        className: 'labeled-pixel-marker'
    });

    const marker = L.marker([lat, lon], {
        icon: markerIcon,
        title: label
    }).addTo(maps.embedding);

    // Bind popup to marker
    marker.bindPopup(popupContent);

    // Hover behavior for marker
    marker.on('mouseover', function() {
        this.openPopup();
    });
    marker.on('mouseout', function() {
        this.closePopup();
    });

    // Click to delete
    marker.on('click', function(e) {
        L.DomEvent.stopPropagation(e);  // Prevent map click
        deleteLabeledPixel(key, labelPixels[key]);
    });

    // Store references to visual elements in labelPixels for deletion
    labelPixels[key].rectangle = rectangle;
    labelPixels[key].marker = marker;
}

// Update label count display
function updateLabelCount() {
    let totalEmbeddings = 0;
    Object.values(embeddingLabels).forEach(embs => {
        totalEmbeddings += embs.length;
    });
}

// Export labels to JSON file
function exportLabelsJSON() {
    if (definedLabels.length === 0 || Object.values(embeddingLabels).every(e => e.length === 0)) {
        alert('No labeled embeddings to export!');
        return;
    }

    // Build export data sorted by label name
    const exportData = definedLabels.map(label => ({
        label: label,
        embedding: embeddingLabels[label]
    }));

    const dataStr = JSON.stringify(exportData, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });
    const url = URL.createObjectURL(dataBlob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `embedding_labels_${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);

    console.log(`✓ Exported ${definedLabels.length} labels with ${Object.values(embeddingLabels).reduce((sum, e) => sum + e.length, 0)} embeddings`);
}

// Import labels from JSON file
function importLabelsJSON(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const importData = JSON.parse(e.target.result);

            if (!Array.isArray(importData)) {
                alert('Invalid file format. Expected array of label objects.');
                return;
            }

            let totalEmbeddings = 0;
            // Import each label
            importData.forEach(item => {
                const label = item.label;
                const embeddings = item.embedding || [];

                // Create label if it doesn't exist
                if (!definedLabels.includes(label)) {
                    definedLabels.push(label);
                    embeddingLabels[label] = [];
                    console.log(`[IMPORT] Created new label: "${label}"`);
                }

                // Add embeddings
                embeddingLabels[label].push(...embeddings);
                totalEmbeddings += embeddings.length;

                console.log(`[IMPORT] Added ${embeddings.length} embeddings to "${label}"`);
            });
            // Sort labels alphabetically
            definedLabels.sort();
            // Update label dropdown using the standard function                    updateLabelDropdown();
            // Update label count to show imported embeddings
            updateLabelCount();

            // Save to localStorage
            saveLabels();

            alert(`✓ Successfully imported ${definedLabels.length} labels with ${totalEmbeddings} embeddings!`);
            console.log(`✓ Imported ${definedLabels.length} labels with ${totalEmbeddings} total embeddings`);

        } catch (error) {
            alert(`Error importing labels: ${error.message}`);
            console.error('Import error:', error);
        }

        // Reset file input
        event.target.value = '';
    };

    reader.readAsText(file);
}



// ===== DRAGGABLE HELPER =====

function makeDraggable(panel, handle) {
    let startX, startY, startLeft, startTop;
    handle.addEventListener('mousedown', function(e) {
        e.preventDefault();
        startX = e.clientX;
        startY = e.clientY;
        const rect = panel.getBoundingClientRect();
        startLeft = rect.left;
        startTop = rect.top;
        handle.style.cursor = 'grabbing';
        document.addEventListener('mousemove', onDrag);
        document.addEventListener('mouseup', stopDrag);
    });
    function onDrag(e) {
        panel.style.left = (startLeft + e.clientX - startX) + 'px';
        panel.style.top = (startTop + e.clientY - startY) + 'px';
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
    }
    function stopDrag() {
        handle.style.cursor = 'grab';
        document.removeEventListener('mousemove', onDrag);
        document.removeEventListener('mouseup', stopDrag);
    }
}

// Schema functions are in js/schema.js



// ===== PROGRESS TRACKING =====
let progressPollInterval = null;

function showProgressModal(title) {
    console.log(`[PROGRESS] Starting operation: ${title}`);
    document.getElementById('progress-title').textContent = title;
    document.getElementById('progress-overlay').classList.add('active');
}

function hideProgressModal() {
    console.log('[PROGRESS] Hiding progress modal');
    document.getElementById('progress-overlay').classList.remove('active');

    if (progressPollInterval) {
        clearInterval(progressPollInterval);
        progressPollInterval = null;
    }
}

function updateProgressUI(data) {
    // Update message
    document.getElementById('progress-message').textContent = data.message || '';

    // Update current file
    const fileEl = document.getElementById('progress-file');
    if (data.current_file) {
        fileEl.textContent = `File: ${data.current_file}`;
        fileEl.style.display = 'block';
    } else {
        fileEl.style.display = 'none';
    }

    // Update progress bar
    const percent = data.percent || 0;
    const progressBar = document.getElementById('progress-bar');
    const progressBarText = document.getElementById('progress-bar-text');
    progressBar.style.width = percent + '%';
    if (percent > 0) {
        progressBarText.textContent = percent + '%';
    }

    // Update percent display
    document.getElementById('progress-percent').textContent = percent + '%';

    // Update status
    const statusEl = document.getElementById('progress-status');
    statusEl.textContent = (data.status || 'processing').toUpperCase();
    statusEl.className = 'progress-status ' + (data.status || 'processing');
}

async function pollOperationProgress(operationId, title) {
    showProgressModal(title);

    let isComplete = false;

    progressPollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/api/operations/progress/${operationId}`);
            const result = await response.json();

            if (result.success && result.status) {
                const data = result;
                updateProgressUI(data);

                console.log(`[PROGRESS] ${operationId}: ${data.message} (${data.percent}%)`);

                // Check if operation is complete or failed
                if (data.status === 'complete' || data.status === 'error') {
                    isComplete = true;

                    // Show final message for 2 seconds
                    setTimeout(() => {
                        hideProgressModal();
                    }, 2000);

                    // Clear interval
                    clearInterval(progressPollInterval);
                    progressPollInterval = null;

                    // Force immediate dependency poll — new data may be available
                    if (pollTimerId) clearTimeout(pollTimerId);
                    pollViewportStatus();
                }
            }
        } catch (error) {
            console.error('[PROGRESS] Error polling progress:', error);
            // Continue polling even if there's an error
        }
    }, 500); // Poll every 500ms
}

// ===== END PROGRESS TRACKING =====

// ===== UMAP: Three.js scene class is defined in the <script type="module"> block near </body> =====
// UMAPScene is attached to window by that module and used here via window.UMAPScene.


// Initialize
window.onload = async function() {
    // Fetch tile server URL from backend config
    try {
        const configResp = await fetch('/api/config');
        const config = await configResp.json();
        if (config.tile_server) {
            TILE_SERVER = config.tile_server;
            console.log('Tile server:', TILE_SERVER);
        }
    } catch (e) {
        console.warn('Failed to fetch config, using default tile server:', e);
    }

    // Disable U-Net when running with a separate tile server (deployed mode)
    if (TILE_SERVER !== window.location.origin) {
        const cb = document.getElementById('unet-checkbox');
        const block = document.getElementById('unet-clf-block');
        if (cb) { cb.disabled = true; cb.checked = false; }
        if (block) { block.style.opacity = '0.4'; block.title = 'U-Net is only available in local mode'; }
    }

    // Load viewport info first (determines map center/zoom)
    await window.updateMapViewport();

    // Then create maps with correct viewport
    window.createMaps();
    window.restorePanelMode();  // Restore panel mode preference

    // Invalidate map sizes after layout is applied
    setTimeout(() => {
        Object.values(maps).forEach(map => {
            if (map && map.invalidateSize) {
                map.invalidateSize();
            }
        });
        // Also resize Three.js scene (Panel 4)
        if (window.umapCanvasLayer && window.umapCanvasLayer.resize) {
            window.umapCanvasLayer.resize();
        }
    }, 100);

    loadLabels();
    await window.loadSavedLabels();  // Load label definitions from localStorage, recompute pixels from vectors
    window.updateThresholdDisplay();  // Initialize threshold display

    // Set up dimensionality reduction selector (PCA/UMAP)
    const dimSelector = document.getElementById('dim-reduction-selector');
    if (dimSelector) {
        dimSelector.addEventListener('change', async (e) => {
            window.currentDimReduction = e.target.value;
            console.log(`[DimReduction] Switching to ${window.currentDimReduction.toUpperCase()}`);
            // Check if this method was already computed (cached);
            // only reset the loaded flag when there's no cache hit,
            // so the dependency system re-triggers computation.
            const key = `${currentViewportName}/${currentEmbeddingYear}/${window.currentDimReduction}`;
            if (window._dimReductionCache[key]) {
                // Cached — loadDimReduction will restore instantly
                await window.loadDimReduction(window.currentDimReduction);
                if (window.currentDimReduction === 'pca') viewportStatus.pca_loaded = true;
                else viewportStatus.umap_loaded = true;
            } else {
                if (window.currentDimReduction === 'pca') {
                    viewportStatus.pca_loaded = false;
                } else {
                    viewportStatus.umap_loaded = false;
                }
                evaluateDependencies();
            }
        });
    }

    // Start unified dependency poller (replaces individual init calls)
    startPoller();

    // Check tile server health
    fetch(`${TILE_SERVER}/tiles/health`)
        .then(resp => {
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return resp.json();
        })
        .then(data => console.log('Tile server status:', data))
        .catch(err => console.warn('Tile server not responding:', err));
};

// Handle window resize - invalidate all Leaflet maps and Three.js scene
let resizeTimeout;
window.addEventListener('resize', function() {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
        // Invalidate all Leaflet maps so they resize properly
        Object.values(maps).forEach(map => {
            if (map && map.invalidateSize) {
                map.invalidateSize();
            }
        });
        // Resize Three.js scene (Panel 4)
        if (window.umapCanvasLayer && window.umapCanvasLayer.resize) {
            window.umapCanvasLayer.resize();
        }
    }, 100);  // Debounce to avoid excessive calls
});

// Check for ongoing operations and start polling if found
async function checkAndPollOperations(viewportId) {
    const operations = ['embeddings', 'pyramids', 'vectors'];

    for (const op of operations) {
        const operationId = `${viewportId}_${op}`;
        try {
            const response = await fetch(`/api/operations/progress/${operationId}`);
            const result = await response.json();

            if (result.success && result.status && result.status !== 'complete') {
                console.log(`[PROGRESS] Found ongoing operation: ${operationId}`);
                const titles = {
                    'embeddings': '📥 Downloading Embeddings',
                    'pyramids': '🔨 Creating Pyramids',
                    'vectors': '🔍 Extracting Vectors'
                };
                await pollOperationProgress(operationId, titles[op]);
                // Poll the next operation after this one completes
                await new Promise(resolve => setTimeout(resolve, 500));
            }
        } catch (error) {
            // Operation not found or error checking - continue to next operation
        }
    }
}


// ===== BRIDGE: module-private state → window (for cross-module access) =====
window.maps = maps;
Object.defineProperties(window, {
    currentViewportName:   { get() { return currentViewportName; },  set(v) { currentViewportName = v; },  configurable: true },
    currentEmbeddingYear:  { get() { return currentEmbeddingYear; }, set(v) { currentEmbeddingYear = v; }, configurable: true },
    viewportStatus:        { get() { return viewportStatus; },       set(v) { viewportStatus = v; },       configurable: true },
    currentPanelMode:      { get() { return currentPanelMode; },     set(v) { currentPanelMode = v; },     configurable: true },
    TILE_SERVER:           { get() { return TILE_SERVER; },          set(v) { TILE_SERVER = v; },          configurable: true },
    labels:                { get() { return labels; },               set(v) { labels = v; },               configurable: true },
    markers:               { get() { return markers; },              set(v) { markers = v; },              configurable: true },
    heatmapSatelliteLayer: { get() { return heatmapSatelliteLayer; }, set(v) { heatmapSatelliteLayer = v; }, configurable: true },
    isLoggedIn:            { get() { return isLoggedIn; },           set(v) { isLoggedIn = v; },           configurable: true },
    definedLabels:         { get() { return definedLabels; },        set(v) { definedLabels = v; },        configurable: true },
    labelColors:           { get() { return labelColors; },          set(v) { labelColors = v; },          configurable: true },
    labelPixels:           { get() { return labelPixels; },          set(v) { labelPixels = v; },          configurable: true },
    similarPixels:         { get() { return similarPixels; },        set(v) { similarPixels = v; },        configurable: true },
    isSimilaritySearchActive: { get() { return isSimilaritySearchActive; }, set(v) { isSimilaritySearchActive = v; }, configurable: true },
    activeSearchLabel:     { get() { return activeSearchLabel; },    set(v) { activeSearchLabel = v; },    configurable: true },
});

// ===== BRIDGE: functions → window (for HTML onclick & cross-module calls) =====
window.doLogout = doLogout;
window.evaluateDependencies = evaluateDependencies;
window.startPoller = startPoller;
window.stopPoller = stopPoller;
window.refreshEmbeddingTileLayer = refreshEmbeddingTileLayer;
window.makeDraggable = makeDraggable;
window.updateLabelCount = updateLabelCount;
window.saveLabels = saveLabels;
window.loadLabels = loadLabels;
window.clearAllLabels = clearAllLabels;
window.exportLabels = exportLabels;
window.createLabelDialog = createLabelDialog;
window.confirmLabelColorSelection = confirmLabelColorSelection;
window.updateLabelDropdown = updateLabelDropdown;
window.updateCoordinatesDisplay = updateCoordinatesDisplay;
window.getColorForLabel = getColorForLabel;
window.invertColor = invertColor;
window.deleteLabeledPixel = deleteLabeledPixel;
window.highlightLabeledPixel = highlightLabeledPixel;
window.nextLabelColor = nextLabelColor;
window.exportLabelsJSON = exportLabelsJSON;
window.showProgressModal = showProgressModal;
window.hideProgressModal = hideProgressModal;
window.pollOperationProgress = pollOperationProgress;
window.checkAndPollOperations = checkAndPollOperations;