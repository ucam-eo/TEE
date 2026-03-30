// maps.js — Map creation, synchronization, tile layers, click handling, panel layout
// Extracted from viewer.html as an ES module.

// ── State (module-private) ──

let center = [12.97, 77.59];  // Default: Bangalore
let zoom = 12;
let viewportBounds = null;  // Set from updateMapViewport()

// Expose viewportBounds on window for inline code (refreshEmbeddingTileLayer) and other modules
Object.defineProperties(window, {
    viewportBounds:          { get: () => viewportBounds,          set: v => { viewportBounds = v; },          configurable: true },
    satelliteSources:        { get: () => satelliteSources,                                                     configurable: true },
    currentSatelliteSource:  { get: () => currentSatelliteSource,  set: v => { currentSatelliteSource = v; },  configurable: true },
    TRIANGLE_ICON:           { get: () => TRIANGLE_ICON,                                                        configurable: true },
    PANEL5_LAYER_RULES:     { get: () => PANEL5_LAYER_RULES,                                                  configurable: true },
    persistentLabelMarkers:  { get: () => persistentLabelMarkers,  set: v => { persistentLabelMarkers = v; },  configurable: true },
});
window.getViewportBounds = () => viewportBounds;

// ── Extracted code ──

const satelliteSources = {
    esri: {
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Esri World Imagery',
        exportUrl: (z, y, x) => `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`
    },
    google: {
        url: 'https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
        attribution: 'Google Satellite',
        exportUrl: (z, y, x) => `https://mt1.google.com/vt/lyrs=s&x=${x}&y=${y}&z=${z}`
    }
};
let currentSatelliteSource = 'esri';
let satelliteTileLayer = null;
// Fetch current viewport and update map center/zoom
async function updateMapViewport() {
    try {
        // Read viewport name from URL parameter (concurrent-safe) or fall back to server
        const urlParams = new URLSearchParams(window.location.search);
        const vpParam = urlParams.get('viewport');
        const response = vpParam
            ? await fetch(`/api/viewports/${encodeURIComponent(vpParam)}/info`)
            : await fetch('/api/viewports/current');
        const data = await response.json();

        if (data.success && data.viewport) {
            const vp = data.viewport;
            window.currentViewportName = vp.name;

            // Update page title and heading with viewport name
            const viewportDisplayName = window.currentViewportName.charAt(0).toUpperCase() + window.currentViewportName.slice(1);
            document.title = `${viewportDisplayName} - TEE Viewer`;

            // Parse bounds from viewport: [lon_min, lat_min, lon_max, lat_max]
            if (vp.bounds_tuple && vp.bounds_tuple.length === 4) {
                const [lonMin, latMin, lonMax, latMax] = vp.bounds_tuple;

                // Store viewport bounds in Leaflet format: [[latMin, lonMin], [latMax, lonMax]]
                viewportBounds = [[latMin, lonMin], [latMax, lonMax]];

                // Calculate center (Leaflet expects [lat, lon])
                center = [(latMin + latMax) / 2, (lonMin + lonMax) / 2];

                // Calculate zoom level based on bounds
                // Roughly: larger bounds = lower zoom, smaller bounds = higher zoom
                const latSpan = latMax - latMin;
                const lonSpan = lonMax - lonMin;
                const maxSpan = Math.max(latSpan, lonSpan);

                if (maxSpan > 0.5) zoom = 11;
                else if (maxSpan > 0.2) zoom = 12;
                else if (maxSpan > 0.1) zoom = 13;
                else if (maxSpan > 0.05) zoom = 14;
                else zoom = 15;
            }
        }
    } catch (error) {
        console.warn('[VIEWPORT] Could not fetch viewport, using defaults:', error);
    }
}

// Create all three maps
function createMaps() {
    // OSM Map
    window.maps.osm = L.map('map-osm', {
        center: center,
        zoom: zoom,
        zoomControl: false,
        minZoom: 6,
        maxZoom: 18,
        doubleClickZoom: false  // We use dblclick for similarity search
    });
    L.control.zoom({position: 'bottomright'}).addTo(window.maps.osm);

    // Apply same bounds as embedding/RGB maps so all maps stay in sync
    if (viewportBounds) {
        window.maps.osm.setMaxBounds(viewportBounds);
        window.maps.osm.options.maxBoundsViscosity = 1.0;
    }

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        maxZoom: 19,
        referrerPolicy: 'origin'
    }).addTo(window.maps.osm);

    // Embedding Map (Tessera)
    window.maps.embedding = L.map('map-embedding', {
        center: center,
        zoom: zoom,
        zoomControl: false,
        minZoom: 6,
        maxZoom: 18,
        doubleClickZoom: false  // We use dblclick for similarity search
    });
    L.control.zoom({position: 'bottomright'}).addTo(window.maps.embedding);

    let embeddingLayer = L.pixelatedTileLayer(`${window.TILE_SERVER}/tiles/${window.currentViewportName}/${window.currentEmbeddingYear}/{z}/{x}/{y}.png`, {
        attribution: 'Tessera Embeddings',
        opacity: 1.0,
        maxZoom: 18,
        maxNativeZoom: 17,
        minZoom: 6,
        tileSize: 256,
        keepBuffer: 5,
        updateWhenZooming: false
    }).addTo(window.maps.embedding);

    // Clip embedding map to viewport bounds
    if (viewportBounds) {
        window.maps.embedding.setMaxBounds(viewportBounds);
        window.maps.embedding.options.maxBoundsViscosity = 1.0;  // Prevent bouncing when hitting edges
    }

    // RGB Satellite Map
    window.maps.rgb = L.map('map-rgb', {
        center: center,
        zoom: zoom,
        zoomControl: false,
        minZoom: 6,
        maxZoom: 18,
        doubleClickZoom: false  // We use dblclick for similarity search
    });
    L.control.zoom({position: 'bottomright'}).addTo(window.maps.rgb);

    // Initialize satellite tile layer
    satelliteTileLayer = L.tileLayer(satelliteSources.esri.url, {
        attribution: satelliteSources.esri.attribution,
        opacity: 1.0,
        maxZoom: 18,
        minZoom: 6,
        crossOrigin: 'anonymous'
    });
    satelliteTileLayer.addTo(window.maps.rgb);

    // Satellite source selector
    const satSelector = document.getElementById('satellite-source-selector');
    if (satSelector) {
        satSelector.addEventListener('change', function() {
            const src = satelliteSources[this.value];
            if (!src) return;
            currentSatelliteSource = this.value;
            window.maps.rgb.removeLayer(satelliteTileLayer);
            satelliteTileLayer = L.tileLayer(src.url, {
                attribution: src.attribution,
                opacity: 1.0,
                maxZoom: 18,
                minZoom: 6,
                crossOrigin: 'anonymous'
            });
            satelliteTileLayer.addTo(window.maps.rgb);

            // Sync panel 5 satellite layer (used in labelling mode)
            const wasOnHeatmap = window.maps.panel5 && window.maps.panel5.hasLayer(window.panel5SatelliteLayer);
            if (wasOnHeatmap) window.maps.panel5.removeLayer(window.panel5SatelliteLayer);
            window.panel5SatelliteLayer = L.tileLayer(src.url, {
                attribution: src.attribution, opacity: 1.0,
                maxZoom: 18, minZoom: 6, crossOrigin: 'anonymous'
            });
            if (wasOnHeatmap) window.panel5SatelliteLayer.addTo(window.maps.panel5);
        });
    }

    // Fetch and display satellite imagery acquisition date
    let _acqDateTimer = null;
    function fetchSatelliteAcqDate() {
        const el = document.getElementById('satellite-acq-date');
        if (!el) return;
        if (currentSatelliteSource === 'google') {
            el.textContent = '';
            return;
        }
        const center = window.maps.rgb.getCenter();
        const bounds = window.maps.rgb.getBounds();
        const ext = `${bounds.getWest()},${bounds.getSouth()},${bounds.getEast()},${bounds.getNorth()}`;
        const url = `https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/identify?geometryType=esriGeometryPoint&geometry=${center.lng},${center.lat}&sr=4326&layers=visible:0&tolerance=2&mapExtent=${ext}&imageDisplay=800,800,96&returnGeometry=false&f=json`;
        fetch(url).then(r => r.json()).then(data => {
            if (currentSatelliteSource !== 'esri') return;
            const results = data.results || [];
            for (const r of results) {
                const d = r.attributes && r.attributes.SRC_DATE2;
                if (d && d !== 'Null') {
                    const date = new Date(d);
                    if (!isNaN(date)) {
                        el.textContent = date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
                        return;
                    }
                }
            }
            el.textContent = '';
        }).catch(() => { el.textContent = ''; });
    }
    function debouncedAcqDate() {
        if (_acqDateTimer) clearTimeout(_acqDateTimer);
        _acqDateTimer = setTimeout(fetchSatelliteAcqDate, 500);
    }
    window.maps.rgb.on('moveend', debouncedAcqDate);
    // Also fetch on source change
    if (satSelector) {
        satSelector.addEventListener('change', debouncedAcqDate);
    }
    // Initial fetch
    debouncedAcqDate();

    // Clip RGB satellite map to viewport bounds
    if (viewportBounds) {
        window.maps.rgb.setMaxBounds(viewportBounds);
        window.maps.rgb.options.maxBoundsViscosity = 1.0;  // Prevent bouncing when hitting edges
    }

    // Initialize polygon drawing (Leaflet.Draw) on maps.rgb
    window.initPolygonDrawing();

    // Panel 4: UMAP — Three.js scene, initialised in loadUMAP()

    // Panel 5: Heatmap (geographic, synced to OSM)
    window.maps.panel5 = L.map('map-panel5', {
        center: center,
        zoom: zoom,
        zoomControl: false,
        minZoom: 6,
        maxZoom: 18,
        doubleClickZoom: false  // We use dblclick for similarity search
    });
    L.control.zoom({position: 'bottomright'}).addTo(window.maps.panel5);

    // Satellite layer for panel 5 (used in labelling mode only — not added to map yet)
    window.panel5SatelliteLayer = L.tileLayer(satelliteSources[currentSatelliteSource].url, {
        attribution: satelliteSources[currentSatelliteSource].attribution,
        opacity: 1.0, maxZoom: 18, minZoom: 6, crossOrigin: 'anonymous'
    });

    if (viewportBounds) {
        window.maps.panel5.setMaxBounds(viewportBounds);
        window.maps.panel5.options.maxBoundsViscosity = 1.0;
    }

    // HeatmapCanvasLayer handles its own redraw on move/zoom/resize,
    // so no need to recompute distances on zoom.

    // Panel 6: Second Year Embeddings (geographic, synced to OSM)
    window.maps.embedding2 = L.map('map-embedding2', {
        center: center,
        zoom: zoom,
        zoomControl: false,
        minZoom: 6,
        maxZoom: 18,
        doubleClickZoom: false  // We use dblclick for similarity search
    });
    L.control.zoom({position: 'bottomright'}).addTo(window.maps.embedding2);

    let embedding2Layer = L.pixelatedTileLayer(`${window.TILE_SERVER}/tiles/${window.currentViewportName}/${window.currentEmbeddingYear2}/{z}/{x}/{y}.png`, {
        attribution: 'Tessera Embeddings',
        opacity: 1.0,
        maxZoom: 18,
        maxNativeZoom: 17,
        tileSize: 256,
        keepBuffer: 5,
        updateWhenZooming: false
    }).addTo(window.maps.embedding2);

    if (viewportBounds) {
        window.maps.embedding2.setMaxBounds(viewportBounds);
        window.maps.embedding2.options.maxBoundsViscosity = 1.0;
    }

    window.embedding2Layer = embedding2Layer;

    // Click handling with dblclick detection
    // We need to delay single-click to distinguish from double-click
    let clickTimeout = null;

    Object.keys(window.maps).forEach(panel => {
        window.maps[panel].on('click', function(e) {
            // Clear any pending click - might be start of double-click
            if (clickTimeout) {
                clearTimeout(clickTimeout);
                clickTimeout = null;
            }

            if (window.isPolygonDrawing) return;

            // Delay ALL single-click actions so dblclick can cancel them
            const lat = e.latlng.lat, lon = e.latlng.lng;
            const isCtrl = e.originalEvent.ctrlKey || e.originalEvent.metaKey;
            clickTimeout = setTimeout(() => {
                clickTimeout = null;
                if (window.isPolygonDrawing) return;
                // Ctrl/Cmd+click in manual label mode: drop a pin
                if (isCtrl && window.currentPanelMode === 'labelling' && window.labelMode === 'manual') {
                    handleManualPinDrop(lat, lon);
                    return;
                }
                handleUnifiedClick(lat, lon);
            }, 250);
        });

        // Double-click triggers similarity search
        // _polygonJustCompleted suppresses the dblclick that fires after polygon finish
        let _polygonJustCompleted = false;
        if (panel === 'rgb') {
            window.maps.rgb.on(L.Draw.Event.CREATED, function() {
                _polygonJustCompleted = true;
                setTimeout(() => { _polygonJustCompleted = false; }, 100);
            });
        }
        window.maps[panel].on('dblclick', function(e) {
            // Cancel pending single-click (pin drop or unified click)
            if (clickTimeout) {
                clearTimeout(clickTimeout);
                clickTimeout = null;
            }
            if (window.isPolygonDrawing || _polygonJustCompleted) return;
            // Ctrl/Cmd+double-click in manual label mode: start polygon drawing
            if ((e.originalEvent.ctrlKey || e.originalEvent.metaKey) &&
                window.currentPanelMode === 'labelling' && window.labelMode === 'manual') {
                window.startPolygonDrawing(e.latlng);
                return;
            }
            handleSimilaritySearch(e.latlng.lat, e.latlng.lng);
        });

        // Show lat/lon in header on mousemove
        window.maps[panel].on('mousemove', function(e) {
            const el = document.getElementById('header-coords');
            if (el) el.textContent = `${e.latlng.lat.toFixed(6)}, ${e.latlng.lng.toFixed(6)}`;
        });
        window.maps[panel].on('mouseout', function() {
            const el = document.getElementById('header-coords');
            if (el) el.textContent = '';
        });
    });

    // Synchronize maps
    syncMaps();

    // Escape key: cancel polygon drawing
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && window.isPolygonDrawing) {
            window.cancelPolygonDrawing();
        }
    });

    // Close schema/export dropdown on outside click
    document.addEventListener('click', function(e) {
        if (!e.target.closest('#schema-dropdown-btn') && !e.target.closest('#schema-dropdown-menu')) {
            const menu = document.getElementById('schema-dropdown-menu');
            if (menu) menu.style.display = 'none';
        }
        if (!e.target.closest('#labelling-export-btn') && !e.target.closest('#labelling-export-menu')) {
            const menu = document.getElementById('labelling-export-menu');
            if (menu) menu.style.display = 'none';
        }
    });

    // Store reference to embedding layer for switching years
    window.embeddingLayer = embeddingLayer;

    return embeddingLayer;
}

// Switch to different embedding year
async function switchEmbeddingYear(year) {
    console.log(`📅 Switching to year ${year}`);
    window.currentEmbeddingYear = year.toString();

    // Remove old layer
    if (window.maps.embedding.hasLayer(window.embeddingLayer)) {
        window.maps.embedding.removeLayer(window.embeddingLayer);
    }

    // Create new layer with selected year
    window.embeddingLayer = L.pixelatedTileLayer(
        `${window.TILE_SERVER}/tiles/${window.currentViewportName}/${window.currentEmbeddingYear}/{z}/{x}/{y}.png`,
        {
            tileSize: 256,
            bounds: viewportBounds,
            keepBuffer: 5,
            updateWhenZooming: false
        }
    );

    window.embeddingLayer.addTo(window.maps.embedding);
    console.log(`✓ Switched to year ${year}`);

    // Clear stale segmentation from previous year
    window.clearSegmentation();

    // Mark vectors as needing re-download for new year
    window.viewportStatus.vectors_downloaded = false;
    window.viewportStatus.pca_loaded = false;
    window.viewportStatus.umap_loaded = false;
    if (window.umapWorker) { window.umapWorker.terminate(); window.umapWorker = null; }

    // Always reload vector data for the new year (needed for similarity search)
    if (window.localVectors && window.localVectors.year !== String(year)) {
        try {
            await window.downloadVectorData(window.currentViewportName, year);
            window.viewportStatus.vectors_downloaded = true;
        } catch (e) {
            console.error(`[VECTORS] Failed to load data for year ${year}:`, e);
        }
    } else {
        window.viewportStatus.vectors_downloaded = true;
    }

    // Update labels (re-compute with new year's embeddings)
    if (window.savedLabels.length > 0) {
        console.log(`🔄 Updating ${window.savedLabels.length} saved labels for year ${year}`);
        await refreshLabelsForYear(year);
    }

    // Re-run explorer search if one is active
    if (window.currentSearchCache && window.currentSearchCache.sourcePixel) {
        const sourcePixel = window.currentSearchCache.sourcePixel;
        console.log(`🔄 Re-running search for ${sourcePixel.lat.toFixed(6)}, ${sourcePixel.lon.toFixed(6)} with year ${year}`);
        window.explorerClick(sourcePixel.lat, sourcePixel.lon);
    }

    // Update heatmap (compares Panel 3 and Panel 6)
    window.loadHeatmap();

    // Re-evaluate dependencies (label-controls visibility, heatmap)
    window.evaluateDependencies();
}

// Refresh all saved labels for a new year
async function refreshLabelsForYear(year) {
    // Ensure vector data is loaded for the target year
    if (!window.localVectors || window.localVectors.year !== String(year)) {
        try {
            await window.downloadVectorData(window.currentViewportName, year);
        } catch (e) {
            console.error(`[LABEL] Cannot load vector data for year ${year}:`, e);
            return;
        }
    }

    for (const label of window.savedLabels) {
        try {
            if (label.static || !label.source_pixel) continue;
            const embedding = window.localExtract(label.source_pixel.lat, label.source_pixel.lon);
            if (!embedding) {
                console.warn(`[LABEL] No embedding for ${label.name} at source pixel`);
                continue;
            }
            const matches = window.localSearchSimilar(embedding, label.threshold);
            label.pixels = matches;
            label.pixel_count = matches.length;
        } catch (error) {
            console.error(`[LABEL] Error refreshing ${label.name}:`, error);
        }
    }

    // Redraw overlays with updated pixels
    window.updateOverlay();
}

// Switch Panel 6 to different year
function switchEmbeddingYear2(year) {
    console.log(`📅 Switching Panel 6 to year ${year}`);
    window.currentEmbeddingYear2 = year.toString();

    // Remove old layer
    if (window.maps.embedding2.hasLayer(window.embedding2Layer)) {
        window.maps.embedding2.removeLayer(window.embedding2Layer);
    }

    // Create new layer with selected year
    window.embedding2Layer = L.pixelatedTileLayer(
        `${window.TILE_SERVER}/tiles/${window.currentViewportName}/${window.currentEmbeddingYear2}/{z}/{x}/{y}.png`,
        {
            tileSize: 256,
            bounds: viewportBounds,
            keepBuffer: 5,
            updateWhenZooming: false
        }
    );

    window.embedding2Layer.addTo(window.maps.embedding2);
    console.log(`✓ Switched Panel 6 to year ${year}`);

    // Update heatmap (compares Panel 3 and Panel 6)
    window.loadHeatmap();

    // Re-evaluate dependencies
    window.evaluateDependencies();
}

// Synchronize all maps - any panel can trigger sync to all others
function syncMaps() {
    let syncing = false;
    const geoPanels = ['osm', 'embedding', 'rgb', 'panel5', 'embedding2'];

    function doSync(sourcePanel) {
        if (syncing) return;
        syncing = true;

        const sourceMap = window.maps[sourcePanel];
        const center = sourceMap.getCenter();
        const zoom = sourceMap.getZoom();

        // Sync all other geographic panels (not Panel 4 which is Three.js)
        geoPanels.forEach(panel => {
            if (panel !== sourcePanel) {
                window.maps[panel].setView(center, zoom, {animate: false});
            }
        });

        syncing = false;
    }

    // Each geographic panel can trigger sync
    geoPanels.forEach(panel => {
        window.maps[panel].on('move zoom', () => doSync(panel));
    });
}

// Cross-panel triangle markers (one per geographic panel, cleared on each new click)
let crossPanelMarkers = {osm: null, rgb: null, embedding: null, heatmap: null, embedding2: null};
let persistentLabelMarkers = []; // {labelId, markers: {osm, rgb, ...}}

const TRIANGLE_ICON = L.divIcon({
    className: 'triangle-marker',
    html: '<svg width="20" height="20" viewBox="0 0 20 20" style="overflow:visible;"><polygon points="0,0 20,0 10,20" fill="#FFD700" stroke="#FF8C00" stroke-width="2" stroke-linejoin="round"/></svg>',
    iconSize: [20, 20],
    iconAnchor: [10, 20]  // Tip of triangle anchored to the coordinate
});

function makeColoredTriangleIcon(fillColor) {
    return L.divIcon({
        className: 'triangle-marker',
        html: `<svg width="20" height="20" viewBox="0 0 20 20" style="overflow:visible;"><polygon points="0,0 20,0 10,20" fill="${fillColor}" stroke="#333" stroke-width="2" stroke-linejoin="round"/></svg>`,
        iconSize: [20, 20],
        iconAnchor: [10, 20]
    });
}

function setCrossPanelMarker(mapKey, lat, lon) {
    if (crossPanelMarkers[mapKey]) {
        window.maps[mapKey].removeLayer(crossPanelMarkers[mapKey]);
    }
    crossPanelMarkers[mapKey] = L.marker([lat, lon], { icon: TRIANGLE_ICON }).addTo(window.maps[mapKey]);
}

function clearCrossPanelMarkers() {
    for (const key of Object.keys(crossPanelMarkers)) {
        if (crossPanelMarkers[key]) {
            window.maps[key].removeLayer(crossPanelMarkers[key]);
            crossPanelMarkers[key] = null;
        }
    }
}

// Unified click handler: places marker on ALL panels at the same location
function handleUnifiedClick(lat, lon) {
    console.log(`[CLICK] Unified click at ${lat.toFixed(6)}, ${lon.toFixed(6)}`);

    // Clear all existing markers
    clearCrossPanelMarkers();

    // Place marker on all geographic panels
    ['osm', 'rgb', 'embedding', 'panel5', 'embedding2'].forEach(panel => {
        setCrossPanelMarker(panel, lat, lon);
    });

    // Highlight nearest point in Panel 4 (PCA/UMAP)
    window.highlightUMAPPoint(lat, lon);

    // Highlight matching label row in panel 6
    if (window.currentPanelMode === 'labelling') {
        highlightLabelAtPixel(lat, lon);
    }
}

function highlightLabelAtPixel(lat, lon) {
    if (!window.localVectors) return;

    // Convert lat/lon to pixel coords
    const gt = window.localVectors.metadata.geotransform;
    const px = Math.trunc((lon - gt.c) / gt.a);
    const py = Math.trunc((lat - gt.f) / gt.e);

    // --- Highlight matching saved label ---
    const labelsContainer = document.getElementById('panel6-labels-list');
    if (labelsContainer) {
        let matchName = null;
        for (const label of window.savedLabels) {
            if (!label.pixel_coords) continue;
            for (let i = 0; i < label.pixel_coords.length; i += 2) {
                if (label.pixel_coords[i] === px && label.pixel_coords[i + 1] === py) {
                    matchName = label.name;
                    break;
                }
            }
            if (matchName) break;
        }

        labelsContainer.querySelectorAll('.label-item').forEach(el => {
            el.style.outline = '';
            el.style.outlineOffset = '';
        });

        if (matchName) {
            const item = labelsContainer.querySelector(`.label-item[data-label-name="${CSS.escape(matchName)}"]`);
            if (item) {
                item.style.outline = '2px solid #667eea';
                item.style.outlineOffset = '-2px';
                item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }
    }

    // --- Highlight matching seg cluster ---
    const segContainer = document.getElementById('panel6-seg-list');
    if (segContainer && window.segAssignments && window.segVectors) {
        const idx = window.segVectors.gridLookup ? window.gridLookupIndex(window.segVectors.gridLookup, px, py) : -1;
        const clusterId = idx >= 0 ? window.segAssignments[idx] : -1;

        segContainer.querySelectorAll('.seg-cluster-row').forEach(el => {
            el.style.outline = '';
            el.style.outlineOffset = '';
        });

        if (clusterId >= 0) {
            const promoteBtn = segContainer.querySelector(`[data-promote-id="${clusterId}"]`);
            if (promoteBtn) {
                const row = promoteBtn.closest('.seg-cluster-row');
                if (row) {
                    row.style.outline = '2px solid #667eea';
                    row.style.outlineOffset = '-2px';
                    row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                }
            }
        }
    }
}

// Double-click handler: triggers similarity search, shows results on Panel 2 AND Panel 4
async function handleSimilaritySearch(lat, lon) {
    console.log(`[DBLCLICK] Similarity search at ${lat.toFixed(6)}, ${lon.toFixed(6)}`);

    // Place markers at the search location on all panels
    handleUnifiedClick(lat, lon);

    // In manual label mode: drop a pin (same as Ctrl+click)
    if (window.currentPanelMode === 'labelling' && window.labelMode === 'manual') {
        handleManualPinDrop(lat, lon);
        return;
    }

    // Trigger similarity search (results shown on Panel 2)
    await window.explorerClick(lat, lon);

    // Panel 4 highlighting is handled by updateExplorerVisualization()
}

// Manual mode: similarity search with current label's color, saves as label entry
async function handleManualSimilaritySearch(lat, lon) {
    if (!window.currentManualLabel) {
        alert('Please set a label name first (top of Panel 6).');
        return;
    }
    if (!window.localVectors) {
        if (!window.viewportStatus.has_vectors) {
            alert('Please wait for vectors to be extracted.');
            return;
        }
        await window.downloadVectorData(window.currentViewportName, window.currentEmbeddingYear);
        window.viewportStatus.vectors_downloaded = true;
        window.evaluateDependencies();
    }
    if (!window.localVectors) {
        alert('Vector data not available.');
        return;
    }

    const embedding = window.localExtract(lat, lon);
    if (!embedding) {
        alert('No embedding found at this location.');
        return;
    }

    // Inherit class threshold if other labels with same name exist
    const classThreshold = window.getClassThreshold(window.currentManualLabel.name);
    const threshold = classThreshold > 0 ? classThreshold : parseInt(document.getElementById('similarity-threshold').value);

    console.log(`[MANUAL] Label "${window.currentManualLabel.name}" at ${lat.toFixed(4)},${lon.toFixed(4)}: threshold ${threshold}`);

    // Create manual label entry
    const entry = {
        name: window.currentManualLabel.name,
        color: window.currentManualLabel.color,
        code: window.currentManualLabel.code || null,
        type: 'similarity',
        lat, lon,
        embedding: Array.from(embedding),
        threshold: threshold,
        matchCount: 0
    };
    window.addManualLabel(entry);

    // Rebuild class overlay (union of all embeddings in this class)
    window.rebuildClassOverlay(window.currentManualLabel.name);
}

// Ctrl/Cmd+click: drop a colored pin at location
function handleManualPinDrop(lat, lon) {
    if (!window.currentManualLabel) {
        alert('Please set a label name first (top of Panel 6).');
        return;
    }

    console.log(`[MANUAL] Pin "${window.currentManualLabel.name}" at ${lat.toFixed(6)},${lon.toFixed(6)}`);

    // Extract embedding if vectors are available
    let embedding = null;
    if (window.localVectors) {
        const emb = window.localExtract(lat, lon);
        if (emb) embedding = Array.from(emb);
    }

    // Inherit class threshold if other labels with same name exist
    const classThreshold = window.getClassThreshold(window.currentManualLabel.name);

    const entry = {
        name: window.currentManualLabel.name,
        color: window.currentManualLabel.color,
        code: window.currentManualLabel.code || null,
        type: 'point',
        lat, lon,
        embedding: embedding,
        threshold: classThreshold,
        matchCount: 0
    };
    window.addManualLabel(entry);

    // Rebuild class overlay (includes pin marker + union similarity)
    window.rebuildClassOverlay(window.currentManualLabel.name);
}
function calculatePixelBounds(lat, lon) {
    // Each pixel is 10m x 10m
    // 1 degree latitude = ~111.32 km = 111320 m
    // 1 degree longitude = ~111.32 * cos(latitude) km at that latitude
    const latPerMeter = 1 / 111320;
    const lonPerMeter = 1 / (111320 * Math.cos(lat * Math.PI / 180));

    const pixelSizeMeters = 10;  // 10m x 10m pixel
    const latOffset = pixelSizeMeters * latPerMeter / 2;
    const lonOffset = pixelSizeMeters * lonPerMeter / 2;

    return [
        [lat - latOffset, lon - lonOffset],  // Southwest
        [lat + latOffset, lon + lonOffset]   // Northeast
    ];
}
// =====================================================================
// SIMILARITY SEARCH FUNCTIONS
// =====================================================================

// Declarative layer rules per mode
// true = add layer if it exists, false = remove if present
const PANEL5_LAYER_RULES = {
    'explore':          { satellite: false, heatmapCanvas: true,  segOverlay: true,  embedding2: false },
    'change-detection': { satellite: false, heatmapCanvas: true,  segOverlay: false, embedding2: true  },
    'labelling':        { satellite: true,  heatmapCanvas: false, segOverlay: true,  embedding2: false },
    'validation':       { satellite: false, heatmapCanvas: false, segOverlay: false, embedding2: false },
};

function applyLayerRule(layer, shouldShow, map) {
    if (!layer || !map) return;
    const onMap = map.hasLayer(layer);
    if (shouldShow && !onMap) layer.addTo(map);
    else if (!shouldShow && onMap) map.removeLayer(layer);
}

function applyHeatmapLayerRule(layer, shouldShow) {
    applyLayerRule(layer, shouldShow, window.maps.panel5);
}

// Panel layout: explore / change-detection / labelling
function setPanelLayout(mode) {
    const container = document.getElementById('map-container');
    const select = document.getElementById('panel-layout-select');
    const waitMsg = document.getElementById('heatmap-waiting-message');
    const sameMsg = document.getElementById('heatmap-same-year-message');

    // Remove old mode class, add new one (on both container and body for toolbar CSS)
    container.classList.remove('mode-explore', 'mode-change-detection', 'mode-labelling', 'mode-validation');
    container.classList.add('mode-' + mode);
    document.body.classList.remove('mode-explore', 'mode-change-detection', 'mode-labelling', 'mode-validation');
    document.body.classList.add('mode-' + mode);
    select.value = mode;
    window.currentPanelMode = mode;

    // ── Declarative panel layout table ──
    // For each mode: [panel1, panel2, panel3, panel4, panel5, panel6]
    // Each entry: { content: 'element-id' or null, title: 'Panel Title', header: true/false }
    // content=null means show the panel's default map; content='hidden' hides the entire panel
    const PANEL_LAYOUT = {
        'explore': [
            { content: null,                    title: 'OpenStreetMap' },
            { content: null,                    title: 'Satellite' },
            { content: null,                    title: 'Tessera Embeddings' },
            { content: null,                    title: 'PCA (Embedding Space)' },
            { content: null,                    title: '' },
            { content: null,                    title: '' },
        ],
        'change-detection': [
            { content: null,                    title: 'OpenStreetMap' },
            { content: null,                    title: 'Satellite' },
            { content: null,                    title: 'Tessera Embeddings' },
            { content: 'change-stats-panel',    title: 'Change Distribution' },
            { content: null,                    title: 'Change Heatmap' },
            { content: null,                    title: 'Tessera Embeddings' },
        ],
        'labelling': [
            { content: null,                    title: 'OpenStreetMap' },
            { content: null,                    title: 'Satellite' },
            { content: null,                    title: 'Tessera Embeddings' },
            { content: null,                    title: 'PCA (Embedding Space)' },
            { content: null,                    title: 'Classification results' },
            { content: 'panel6-label-view',     title: 'Auto-label' },
        ],
        'validation': [
            { content: 'hidden' },
            { content: null,                    title: 'Satellite' },
            { content: 'val-class-table-panel', title: 'Ground Truth',     header: false },
            { content: 'val-results-panel',     title: 'Progress',         header: false },
            { content: 'validation-chart-panel',title: 'Learning Curves',  header: false },
            { content: 'validation-controls',   title: '',                 header: false, order: -1,
              also: ['val-cm-panel'] },
        ],
    };

    // Apply layout
    const layout = PANEL_LAYOUT[mode] || PANEL_LAYOUT['explore'];
    const panels = document.querySelectorAll('#map-container > .panel');
    const titleIds = ['panel1-title', null, 'panel3-title', 'panel4-title', 'panel5-title', 'panel6-header-text'];

    // IDs of all switchable content (non-map overlays shown per mode)
    const SWITCHABLE = [
        'change-stats-panel', 'panel6-label-view',
        'val-class-table-panel', 'val-results-panel', 'validation-chart-panel',
        'validation-controls', 'val-cm-panel',
    ];

    // Hide all switchable content
    for (const id of SWITCHABLE) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
    }

    // Apply per-panel
    for (let i = 0; i < panels.length && i < layout.length; i++) {
        const panel = panels[i];
        const spec = layout[i];

        // Panel visibility
        if (spec.content === 'hidden') {
            panel.style.display = 'none';
            continue;
        }
        panel.style.display = '';
        panel.style.order = spec.order !== undefined ? spec.order : '';

        // Header visibility
        const hdr = panel.querySelector('.panel-header');
        if (hdr) {
            if (spec.header === false) {
                hdr.style.display = 'none';
            } else {
                hdr.style.display = '';
            }
        }

        // Title
        if (titleIds[i] && spec.title !== undefined) {
            const titleEl = document.getElementById(titleIds[i]);
            if (titleEl) titleEl.textContent = spec.title;
        }

        // Show specified content overlay
        if (spec.content && spec.content !== 'hidden') {
            const el = document.getElementById(spec.content);
            if (el) el.style.display = el.dataset.displayMode || 'block';
        }

        // Show additional content in same panel
        if (spec.also) {
            for (const id of spec.also) {
                const el = document.getElementById(id);
                if (el) el.style.display = el.dataset.displayMode || 'flex';
            }
        }
    }

    const rules = PANEL5_LAYER_RULES[mode] || PANEL5_LAYER_RULES['explore'];
    applyHeatmapLayerRule(window.panel5SatelliteLayer, rules.satellite);
    applyHeatmapLayerRule(window.heatmapCanvasLayer, rules.heatmapCanvas);
    applyHeatmapLayerRule(window.segOverlay, rules.segOverlay);

    // Panel 6: embedding2 tile layer visibility from rules
    applyLayerRule(window.embedding2Layer, rules.embedding2, window.maps.embedding2);

    // Hide classify button for all modes; labelling mode re-shows it if needed
    const classifyBtn = document.getElementById('manual-classify-btn');
    if (classifyBtn) classifyBtn.style.display = 'none';

    // Mode-specific setup beyond layers
    if (mode === 'labelling') {
        if (waitMsg) waitMsg.style.display = 'none';
        if (sameMsg) sameMsg.style.display = 'none';
        document.getElementById('seg-results-panel').style.display = 'none';
        document.getElementById('labels-details-panel').style.display = 'none';
        window.renderSegListInto(document.getElementById('panel6-seg-list'));
        window.renderLabelsInto(document.getElementById('panel6-labels-list'));
        const p6btn = document.getElementById('panel6-promote-all-btn');
        if (p6btn) p6btn.style.display = window.segLabels.length > 0 ? '' : 'none';
        // Restore label sub-mode (autolabel/manual)
        const savedLabelMode = localStorage.getItem('labelMode') || 'autolabel';
        document.getElementById('label-mode-select').value = savedLabelMode;
        window.setLabelMode(savedLabelMode);
    } else if (mode === 'change-detection') {
        window.loadHeatmap();
    } else if (mode === 'validation') {
        if (waitMsg) waitMsg.style.display = 'none';
        if (sameMsg) sameMsg.style.display = 'none';
    }

    // Leaflet maps and Three.js scene need resize after CSS transition
    setTimeout(() => {
        Object.values(window.maps).forEach(map => {
            if (map && map.invalidateSize) map.invalidateSize();
        });
        if (window.umapCanvasLayer && window.umapCanvasLayer.resize) window.umapCanvasLayer.resize();
        if (mode === 'change-detection' && window.embedding2Layer && window.maps.embedding2) {
            window.refreshEmbeddingTileLayer('embedding2', window.currentEmbeddingYear2);
        }
    }, 350);

    localStorage.setItem('panelMode', mode);
    window.evaluateDependencies();
}

// Restore panel mode preference on load
function restorePanelMode() {
    const saved = localStorage.getItem('panelMode');
    const validModes = ['explore', 'change-detection', 'labelling', 'validation'];
    const mode = validModes.includes(saved) ? saved : 'explore';
    setPanelLayout(mode);
}
// Custom tile layer that uses canvas with imageSmoothingEnabled = false
// This guarantees crisp nearest-neighbor scaling regardless of CSS
L.PixelatedTileLayer = L.GridLayer.extend({
    options: {
        tileSize: 256,
        crossOrigin: 'anonymous'
    },

    initialize: function(urlTemplate, options) {
        this._url = urlTemplate;
        L.setOptions(this, options);
    },

    createTile: function(coords, done) {
        const tile = document.createElement('canvas');
        const tileSize = this.getTileSize();
        tile.width = tileSize.x;
        tile.height = tileSize.y;

        const ctx = tile.getContext('2d');
        // CRITICAL: Disable image smoothing for crisp pixels
        ctx.imageSmoothingEnabled = false;
        ctx.webkitImageSmoothingEnabled = false;
        ctx.mozImageSmoothingEnabled = false;
        ctx.msImageSmoothingEnabled = false;

        const img = new Image();
        img.crossOrigin = this.options.crossOrigin;

        img.onload = () => {
            // Re-apply after image load (some browsers reset it)
            ctx.imageSmoothingEnabled = false;
            ctx.webkitImageSmoothingEnabled = false;
            ctx.mozImageSmoothingEnabled = false;
            ctx.msImageSmoothingEnabled = false;

            // Draw image scaled to tile size - canvas will use nearest-neighbor
            ctx.drawImage(img, 0, 0, tileSize.x, tileSize.y);
            done(null, tile);
        };

        img.onerror = () => {
            done(new Error('Tile load error'), tile);
        };

        // Build URL from template
        const url = this._url
            .replace('{z}', coords.z)
            .replace('{x}', coords.x)
            .replace('{y}', coords.y);
        img.src = url;

        return tile;
    }
});

L.pixelatedTileLayer = function(urlTemplate, options) {
    return new L.PixelatedTileLayer(urlTemplate, options);
};

// ── Expose on window for inline handlers and cross-module access ──

window.updateMapViewport = updateMapViewport;
window.createMaps = createMaps;
window.switchEmbeddingYear = switchEmbeddingYear;
window.refreshLabelsForYear = refreshLabelsForYear;
window.switchEmbeddingYear2 = switchEmbeddingYear2;
window.syncMaps = syncMaps;
window.makeColoredTriangleIcon = makeColoredTriangleIcon;
window.setCrossPanelMarker = setCrossPanelMarker;
window.clearCrossPanelMarkers = clearCrossPanelMarkers;
window.handleUnifiedClick = handleUnifiedClick;
window.highlightLabelAtPixel = highlightLabelAtPixel;
window.handleSimilaritySearch = handleSimilaritySearch;
window.handleManualSimilaritySearch = handleManualSimilaritySearch;
window.handleManualPinDrop = handleManualPinDrop;
window.calculatePixelBounds = calculatePixelBounds;
window.applyHeatmapLayerRule = applyHeatmapLayerRule;
window.setPanelLayout = setPanelLayout;
window.restorePanelMode = restorePanelMode;
