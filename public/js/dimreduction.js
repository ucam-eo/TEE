// dimreduction.js — PCA, UMAP, heatmap, change detection, Three.js scene
// Extracted from viewer.html as an ES module.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// ── State (module-private, exposed on window via defineProperty) ──

let currentEmbeddingYear2 = '2025';
let umapData = null;
let umapCanvasLayer = null;
let currentDimReduction = 'pca';
const _dimReductionCache = {};
let heatmapCanvasLayer = null;

// Internal state (not bridged)
let umapWorker = null;
let umapWorkerURL = null;
let _heatmapCacheKey = null;
const MAX_UMAP_POINTS = 10000;
const MAX_PCA_POINTS = 4 * MAX_UMAP_POINTS;

// ── Window bridges for state shared with inline scripts ──

Object.defineProperty(window, 'currentEmbeddingYear2', {
    get: () => currentEmbeddingYear2,
    set: (v) => { currentEmbeddingYear2 = v; },
    configurable: true,
});
Object.defineProperty(window, 'umapCanvasLayer', {
    get: () => umapCanvasLayer,
    set: (v) => { umapCanvasLayer = v; },
    configurable: true,
});
Object.defineProperty(window, 'currentDimReduction', {
    get: () => currentDimReduction,
    set: (v) => { currentDimReduction = v; },
    configurable: true,
});
Object.defineProperty(window, 'heatmapCanvasLayer', {
    get: () => heatmapCanvasLayer,
    set: (v) => { heatmapCanvasLayer = v; },
    configurable: true,
});
Object.defineProperty(window, '_dimReductionCache', {
    get: () => _dimReductionCache,
    configurable: true,
});

// ── UMAPScene (Three.js 3D scatter plot — Panel 4) ──

class UMAPScene {
    /**
     * @param {string} containerId  – id of the div to render into
     * @param {Array<{x,y,z,lat,lon}>} points – UMAP coordinates from the API
     */
    constructor(containerId, points) {
        this.container = document.getElementById(containerId);
        this.points = points;           // keep reference for hit-testing
        this.disposed = false;

        // ── geometry & material ──────────────────────────────────
        const n = points.length;
        this.geometry = new THREE.BufferGeometry();

        const positions = new Float32Array(n * 3);
        this.colors     = new Float32Array(n * 3);   // default grey

        // Compute data-space bounds for normalising into [-1, 1]
        let xMin = Infinity, xMax = -Infinity,
            yMin = Infinity, yMax = -Infinity,
            zMin = Infinity, zMax = -Infinity;

        for (let i = 0; i < n; i++) {
            const p  = points[i];
            const pz = p.z !== undefined ? p.z : 0;
            if (p.x  < xMin) xMin = p.x;   if (p.x  > xMax) xMax = p.x;
            if (p.y  < yMin) yMin = p.y;   if (p.y  > yMax) yMax = p.y;
            if (pz   < zMin) zMin = pz;    if (pz   > zMax) zMax = pz;
        }

        // Centre & scale so the cloud fits in a unit cube centred at origin
        const range = Math.max(xMax - xMin, yMax - yMin, zMax - zMin, 1e-6);
        this.dataRange = range;
        this.dataMid   = [(xMin + xMax) / 2, (yMin + yMax) / 2, (zMin + zMax) / 2];

        for (let i = 0; i < n; i++) {
            const p  = points[i];
            const pz = p.z !== undefined ? p.z : 0;
            positions[i * 3]     = (p.x  - this.dataMid[0]) / range;
            positions[i * 3 + 1] = (p.y  - this.dataMid[1]) / range;
            positions[i * 3 + 2] = (pz   - this.dataMid[2]) / range;

            // Default grey
            this.colors[i * 3]     = 0.53;   // #888
            this.colors[i * 3 + 1] = 0.53;
            this.colors[i * 3 + 2] = 0.53;
        }

        this.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        this.geometry.setAttribute('color',    new THREE.BufferAttribute(this.colors, 3));

        this.material = new THREE.PointsMaterial({
            size: 0.008,
            vertexColors: true,
            transparent: true,
            opacity: 0.85,
            depthWrite: false
        });

        this.pointsMesh = new THREE.Points(this.geometry, this.material);

        // ── scene / camera / renderer ────────────────────────────
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a1a2e);
        this.scene.add(this.pointsMesh);

        // Subtle ambient light (not strictly needed for PointsMaterial, but future-proof)
        this.scene.add(new THREE.AmbientLight(0xffffff, 1.0));

        const w = this.container.clientWidth  || 400;
        const h = this.container.clientHeight || 300;

        this.camera = new THREE.PerspectiveCamera(50, w / h, 0.01, 100);

        // Fit camera distance so point cloud fills the panel
        const xExtent = (xMax - xMin) / range;
        const yExtent = (yMax - yMin) / range;
        const aspect  = w / h;
        const vFovRad = 50 * Math.PI / 180;
        const hFovRad = 2 * Math.atan(Math.tan(vFovRad / 2) * aspect);
        const padding = 1.15;  // 15% margin
        const distForY = (yExtent / 2 * padding) / Math.tan(vFovRad / 2);
        const distForX = (xExtent / 2 * padding) / Math.tan(hFovRad / 2);
        this.camera.position.set(0, 0, Math.max(distForY, distForX, 0.5));

        this.renderer = new THREE.WebGLRenderer({ antialias: true });
        this.renderer.setPixelRatio(window.devicePixelRatio);
        this.renderer.setSize(w, h);
        this.container.innerHTML = '';          // clear any "Computing…" message
        this.container.appendChild(this.renderer.domElement);

        // ── orbit controls ───────────────────────────────────────
        this.controls = new OrbitControls(this.camera, this.renderer.domElement);
        this.controls.enablePan    = true;
        this.controls.enableZoom   = true;
        this.controls.enableRotate = true;
        this.controls.minDistance   = 0.3;
        this.controls.maxDistance   = 8.0;

        // Swap mouse buttons: left-drag = pan, right-drag = rotate
        this.controls.mouseButtons = {
            LEFT: THREE.MOUSE.PAN,
            MIDDLE: THREE.MOUSE.DOLLY,
            RIGHT: THREE.MOUSE.ROTATE
        };

        // ── idle auto-rotation (when mouse is outside panel) ─────
        this._idleActive = true;
        this._idleAzimuthSpeed = 0.008;   // radians per frame
        this._idlePolarSpeed  = 0.003;
        this._pickNewRotation();

        this._onMouseEnterBound = () => { this._idleActive = false; };
        this._onMouseLeavePanelBound = () => { this._idleActive = true; };
        this.container.addEventListener('mouseenter', this._onMouseEnterBound);
        this.container.addEventListener('mouseleave', this._onMouseLeavePanelBound);

        this._rotationTimer = setInterval(() => this._pickNewRotation(), 3000 + Math.random() * 3000);

        // ── highlight crosshair (3D pointer, placed on click) ──
        this.highlightGroup = new THREE.Group();
        this.highlightGroup.visible = false;
        const armLen = 0.04;
        const armRadius = 0.002;
        const armMat = new THREE.MeshBasicMaterial({ color: 0xffd700 });
        // Three axis-aligned arms
        for (const axis of ['x', 'y', 'z']) {
            const cyl = new THREE.CylinderGeometry(armRadius, armRadius, armLen * 2, 6);
            const mesh = new THREE.Mesh(cyl, armMat);
            if (axis === 'x') mesh.rotation.z = Math.PI / 2;
            if (axis === 'z') mesh.rotation.x = Math.PI / 2;
            this.highlightGroup.add(mesh);
        }
        // Small wireframe sphere at center
        const ringGeo = new THREE.SphereGeometry(0.012, 12, 8);
        const ringMat = new THREE.MeshBasicMaterial({ color: 0xffd700, wireframe: true });
        this.highlightGroup.add(new THREE.Mesh(ringGeo, ringMat));
        this.scene.add(this.highlightGroup);

        // ── raycaster for click detection ────────────────────────
        this.raycaster = new THREE.Raycaster();
        this.raycaster.params.Points.threshold = 0.015;
        this._mouse = new THREE.Vector2();

        this._onClickBound    = this._onClick.bind(this);
        this._onDblClickBound = this._onDblClick.bind(this);
        this._onResizeBound   = this._onResize.bind(this);
        this._dragStartPos = null;
        this._onMouseDownBound = (e) => {
            this.renderer.domElement.style.cursor = 'grabbing';
            this._dragStartPos = { x: e.clientX, y: e.clientY };
        };
        this._onMouseUpBound = () => { this.renderer.domElement.style.cursor = 'crosshair'; };

        this.renderer.domElement.addEventListener('click', this._onClickBound);
        this.renderer.domElement.addEventListener('dblclick', this._onDblClickBound);
        this.renderer.domElement.addEventListener('mousedown', this._onMouseDownBound);
        this.renderer.domElement.addEventListener('mouseup', this._onMouseUpBound);
        this.renderer.domElement.addEventListener('mouseleave', this._onMouseUpBound);
        window.addEventListener('resize', this._onResizeBound);

        // ── animation loop ───────────────────────────────────────
        this._animId = null;
        this._animate();

        console.log(`[UMAPScene] Initialised: ${n} points`);
    }

    // ── public API called by the main script ──────────────────────

    /** Move highlight crosshair to the given point (or hide if null). */
    setHighlight(point) {
        if (!point) {
            this.highlightGroup.visible = false;
            return;
        }
        const range = this.dataRange;
        const mid   = this.dataMid;
        const pz    = point.z !== undefined ? point.z : 0;
        this.highlightGroup.position.set(
            (point.x - mid[0]) / range,
            (point.y - mid[1]) / range,
            (pz      - mid[2]) / range
        );
        this.highlightGroup.visible = true;
    }

    /**
     * Recolour points according to saved labels.
     * Each label has .pixels (array of {lat,lon}) and .color (CSS colour string).
     * Unlabelled points stay grey.
     */
    updateLabelColors(savedLabels) {
        // Build a lat/lon → colour map (keyed to 6 dp for speed)
        const colourMap = new Map();
        for (const label of savedLabels) {
            if (!label.visible) continue;
            const rgb = this._cssColorToRGB(label.color);
            if (!rgb) continue;
            for (const px of label.pixels) {
                const key = px.lat.toFixed(6) + ',' + px.lon.toFixed(6);
                colourMap.set(key, rgb);
            }
        }

        const c = this.colors;
        const n = this.points.length;
        for (let i = 0; i < n; i++) {
            const key = this.points[i].lat.toFixed(6) + ',' + this.points[i].lon.toFixed(6);
            const rgb = colourMap.get(key);
            if (rgb) {
                c[i * 3]     = rgb[0];
                c[i * 3 + 1] = rgb[1];
                c[i * 3 + 2] = rgb[2];
            } else {
                c[i * 3]     = 0.53;
                c[i * 3 + 1] = 0.53;
                c[i * 3 + 2] = 0.53;
            }
        }

        this.geometry.attributes.color.needsUpdate = true;
    }

    /**
     * Highlight points matching similarity search results.
     * Matches are colored yellow (same as Panel 2), others stay gray.
     * @param {Array<{lat, lon, distance}>} matches - Similarity search results
     */
    highlightSimilarPoints(matches) {
        // Build a lat/lon set of matching points (keyed to 6 dp for speed)
        const matchSet = new Set();
        for (const m of matches) {
            const key = m.lat.toFixed(6) + ',' + m.lon.toFixed(6);
            matchSet.add(key);
        }

        // Yellow color for matches (same as Panel 2: #FFFF00)
        const highlightRGB = [1.0, 1.0, 0.0];  // Yellow
        const grayRGB = [0.53, 0.53, 0.53];    // Gray for non-matches

        const c = this.colors;
        const n = this.points.length;
        for (let i = 0; i < n; i++) {
            const key = this.points[i].lat.toFixed(6) + ',' + this.points[i].lon.toFixed(6);
            if (matchSet.has(key)) {
                c[i * 3]     = highlightRGB[0];
                c[i * 3 + 1] = highlightRGB[1];
                c[i * 3 + 2] = highlightRGB[2];
            } else {
                c[i * 3]     = grayRGB[0];
                c[i * 3 + 1] = grayRGB[1];
                c[i * 3 + 2] = grayRGB[2];
            }
        }

        this.geometry.attributes.color.needsUpdate = true;
        console.log(`[UMAPScene] Highlighted ${matchSet.size} similar points`);
    }

    /**
     * Colour points by manual label classes.
     * @param {Map<string, [number,number,number]>} colourMap - lat,lon key → [r,g,b] (0-1)
     */
    colorByManualLabels(colourMap) {
        const c = this.colors;
        const n = this.points.length;
        for (let i = 0; i < n; i++) {
            const key = this.points[i].lat.toFixed(6) + ',' + this.points[i].lon.toFixed(6);
            const rgb = colourMap.get(key);
            if (rgb) {
                c[i * 3]     = rgb[0];
                c[i * 3 + 1] = rgb[1];
                c[i * 3 + 2] = rgb[2];
            } else {
                c[i * 3]     = 0.53;
                c[i * 3 + 1] = 0.53;
                c[i * 3 + 2] = 0.53;
            }
        }
        this.geometry.attributes.color.needsUpdate = true;
    }

    /**
     * Clear similarity highlighting and restore to gray.
     */
    clearSimilarityHighlight() {
        const c = this.colors;
        const n = this.points.length;
        for (let i = 0; i < n; i++) {
            c[i * 3]     = 0.53;
            c[i * 3 + 1] = 0.53;
            c[i * 3 + 2] = 0.53;
        }
        this.geometry.attributes.color.needsUpdate = true;
    }

    /** Release GPU & DOM resources. */
    dispose() {
        this.disposed = true;
        if (this._animId !== null) cancelAnimationFrame(this._animId);
        if (this._rotationTimer) clearInterval(this._rotationTimer);
        this.container.removeEventListener('mouseenter', this._onMouseEnterBound);
        this.container.removeEventListener('mouseleave', this._onMouseLeavePanelBound);
        this.renderer.domElement.removeEventListener('click', this._onClickBound);
        this.renderer.domElement.removeEventListener('dblclick', this._onDblClickBound);
        this.renderer.domElement.removeEventListener('mousedown', this._onMouseDownBound);
        this.renderer.domElement.removeEventListener('mouseup', this._onMouseUpBound);
        this.renderer.domElement.removeEventListener('mouseleave', this._onMouseUpBound);
        window.removeEventListener('resize', this._onResizeBound);
        this.geometry.dispose();
        this.material.dispose();
        this.renderer.dispose();
        if (this.renderer.domElement.parentNode) {
            this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
        }
        console.log('[UMAPScene] Disposed');
    }

    // ── internal helpers ─────────────────────────────────────────

    _pickNewRotation() {
        // Random azimuth speed (horizontal), random direction
        this._idleAzimuthSpeed = (0.004 + Math.random() * 0.008) * (Math.random() < 0.5 ? 1 : -1);
        // Random polar speed (vertical), random direction
        this._idlePolarSpeed  = (0.001 + Math.random() * 0.004) * (Math.random() < 0.5 ? 1 : -1);
    }

    _animate() {
        if (this.disposed) return;
        this._animId = requestAnimationFrame(() => this._animate());
        if (this._idleActive) {
            // Spherical coordinates around the target
            const offset = this.camera.position.clone().sub(this.controls.target);
            const spherical = new THREE.Spherical().setFromVector3(offset);
            spherical.theta += this._idleAzimuthSpeed;
            // Clamp polar angle to avoid flipping (keep between 15° and 165°)
            spherical.phi = Math.max(0.26, Math.min(2.88, spherical.phi + this._idlePolarSpeed));
            offset.setFromSpherical(spherical);
            this.camera.position.copy(this.controls.target).add(offset);
            this.camera.lookAt(this.controls.target);
        }
        this.controls.update();
        this.renderer.render(this.scene, this.camera);
    }

    _wasDrag(e) {
        if (!this._dragStartPos) return false;
        const dx = e.clientX - this._dragStartPos.x;
        const dy = e.clientY - this._dragStartPos.y;
        return (dx * dx + dy * dy) > 9; // 3px threshold
    }

    _onClick(e) {
        if (this._wasDrag(e)) return; // Ignore drags (pan/rotate)

        const rect = this.renderer.domElement.getBoundingClientRect();
        this._mouse.x =  ((e.clientX - rect.left)  / rect.width)  * 2 - 1;
        this._mouse.y = -((e.clientY - rect.top)   / rect.height) * 2 + 1;

        this.raycaster.setFromCamera(this._mouse, this.camera);
        const intersects = this.raycaster.intersectObject(this.pointsMesh);

        if (intersects.length > 0) {
            const idx = intersects[0].index;
            const hitPoint = this.points[idx];

            // Move highlight crosshair
            this.setHighlight(hitPoint);

            // Use unified click handler to place markers on all panels
            if (typeof window.handleUnifiedClick === 'function') {
                window.handleUnifiedClick(hitPoint.lat, hitPoint.lon);
            }
            // Pan map panels to the clicked location so markers are visible
            if (window.maps && window.maps.osm) {
                window.maps.osm.panTo([hitPoint.lat, hitPoint.lon]);
            }
            console.log(`[UMAPScene] Click → idx=${idx}/${this.points.length} lat=${hitPoint.lat.toFixed(6)} lon=${hitPoint.lon.toFixed(6)}`);
        }
    }

    _onDblClick(e) {
        if (this._wasDrag(e)) return; // Ignore drags

        const rect = this.renderer.domElement.getBoundingClientRect();
        this._mouse.x =  ((e.clientX - rect.left)  / rect.width)  * 2 - 1;
        this._mouse.y = -((e.clientY - rect.top)   / rect.height) * 2 + 1;

        this.raycaster.setFromCamera(this._mouse, this.camera);
        const intersects = this.raycaster.intersectObject(this.pointsMesh);

        if (intersects.length > 0) {
            const idx = intersects[0].index;
            const hitPoint = this.points[idx];

            // Use similarity search handler
            if (typeof window.handleSimilaritySearch === 'function') {
                window.handleSimilaritySearch(hitPoint.lat, hitPoint.lon);
            }
            // Pan map panels to the clicked location so markers are visible
            if (window.maps && window.maps.osm) {
                window.maps.osm.panTo([hitPoint.lat, hitPoint.lon]);
            }
            console.log(`[UMAPScene] DblClick → similarity search at lat=${hitPoint.lat.toFixed(6)} lon=${hitPoint.lon.toFixed(6)}`);
        }
    }

    /** Public method to trigger resize (call after layout changes) */
    resize() {
        this._onResize();
    }

    _onResize() {
        if (this.disposed) return;
        const w = this.container.clientWidth  || 400;
        const h = this.container.clientHeight || 300;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.renderer.setSize(w, h);
    }

    /** Parse a CSS colour string like "#a1b2c3" or "rgb(161,178,195)" → [r,g,b] in 0-1. */
    _cssColorToRGB(css) {
        if (!css) return null;

        // Hex
        const hexMatch = css.match(/^#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})$/);
        if (hexMatch) {
            return [
                parseInt(hexMatch[1], 16) / 255,
                parseInt(hexMatch[2], 16) / 255,
                parseInt(hexMatch[3], 16) / 255
            ];
        }

        // rgb(r,g,b)
        const rgbMatch = css.match(/rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/);
        if (rgbMatch) {
            return [
                parseInt(rgbMatch[1]) / 255,
                parseInt(rgbMatch[2]) / 255,
                parseInt(rgbMatch[3]) / 255
            ];
        }

        // Short hex #rgb
        const shortHex = css.match(/^#([0-9a-fA-F])([0-9a-fA-F])([0-9a-fA-F])$/);
        if (shortHex) {
            return [
                parseInt(shortHex[1] + shortHex[1], 16) / 255,
                parseInt(shortHex[2] + shortHex[2], 16) / 255,
                parseInt(shortHex[3] + shortHex[3], 16) / 255
            ];
        }

        return null;
    }
}

// ── HeatmapCanvasLayer (Leaflet canvas overlay for distance heatmap) ──

class HeatmapCanvasLayer extends L.Layer {
    constructor(distances, stats = null) {
        super();
        this.distances = distances;  // [{lat, lon, distance}, ...]
        this._canvas = null;
        this._ctx = null;

        // Use percentile-based normalization for better color distribution
        if (stats && stats.max_distance > 0) {
            const median = stats.median_distance || stats.mean_distance;
            this.maxDistance = median * 1.5;

            console.log('Heatmap distance scale:', {
                actual_max: stats.max_distance.toFixed(3),
                median: median.toFixed(3),
                scale_to: this.maxDistance.toFixed(3)
            });
        } else {
            // Fallback: calculate maxDistance locally
            let maxDist = 0;
            for (let i = 0; i < distances.length; i++) {
                if (distances[i].distance > maxDist) {
                    maxDist = distances[i].distance;
                }
            }
            this.maxDistance = maxDist > 0 ? maxDist : 1;
        }

        // Sample and log distance values for debugging
        if (distances && distances.length > 0) {
            const sampleDistances = distances.slice(0, 5).map(d => d.distance.toFixed(3));
        }
    }

    onAdd(map) {
        this._map = map;

        // Create canvas
        this._canvas = document.createElement('canvas');
        this._canvas.style.position = 'absolute';
        this._canvas.style.top = '0';
        this._canvas.style.left = '0';
        this._canvas.style.opacity = '1.0';
        this._canvas.style.pointerEvents = 'none';

        // Insert at START of container so Leaflet panes render on top
        const container = map.getContainer();
        container.insertBefore(this._canvas, container.firstChild);

        this._ctx = this._canvas.getContext('2d');

        map.on('move zoom resize', () => this._redraw());
        this._redraw();
    }

    onRemove(map) {
        map.off('move zoom resize', () => this._redraw());
        if (this._canvas && this._canvas.parentNode) {
            this._canvas.parentNode.removeChild(this._canvas);
        }
    }

    _redraw() {
        if (!this._map || !this._canvas || !this._ctx) return;

        const size = this._map.getSize();

        this._canvas.width = size.x;
        this._canvas.height = size.y;
        this._canvas.style.width = size.x + 'px';
        this._canvas.style.height = size.y + 'px';

        this._ctx.imageSmoothingEnabled = true;
        this._ctx.clearRect(0, 0, size.x, size.y);

        let pixelsDrawn = 0;
        const colors = new Set();

        for (const d of this.distances) {
            const bounds = window.calculatePixelBounds(d.lat, d.lon);
            const sw = this._map.latLngToContainerPoint(bounds[0]);
            const ne = this._map.latLngToContainerPoint(bounds[1]);

            const x = Math.floor(sw.x);
            const width = Math.ceil(ne.x - sw.x) + 1;
            const y = Math.floor(ne.y);
            const height = Math.ceil(sw.y - ne.y) + 1;

            if (x + width < 0 || x > size.x || y + height < 0 || y > size.y) {
                continue;
            }

            const normalized = d.distance / this.maxDistance;
            const color = this.getHeatmapColor(normalized);
            colors.add(color);

            this._ctx.fillStyle = color;
            this._ctx.fillRect(x, y, width, height);
            pixelsDrawn++;
        }

        if (pixelsDrawn > 0) {
            if (colors.size > 0) {
                const colorArray = Array.from(colors).slice(0, 5);
            }
        }
    }

    getHeatmapColor(value) {
        // Viridis colormap - perceptually uniform
        const viridis = [
            [68, 1, 84],      // 0.00: dark purple
            [72, 40, 120],    // 0.17
            [62, 74, 137],    // 0.33: blue
            [47, 105, 141],   // 0.50: teal
            [39, 133, 133],   // 0.67: cyan-green
            [92, 156, 89],    // 0.83: green
            [181, 172, 39],   // 1.00: yellow
        ];

        const clampedValue = Math.max(0, Math.min(1, value));
        const idx = clampedValue * (viridis.length - 1);
        const lowerIdx = Math.floor(idx);
        const upperIdx = Math.ceil(idx);
        const fraction = idx - lowerIdx;

        const lower = viridis[Math.min(lowerIdx, viridis.length - 1)];
        const upper = viridis[Math.min(upperIdx, viridis.length - 1)];

        const r = Math.floor(lower[0] * (1 - fraction) + upper[0] * fraction);
        const g = Math.floor(lower[1] * (1 - fraction) + upper[1] * fraction);
        const b = Math.floor(lower[2] * (1 - fraction) + upper[2] * fraction);

        return `rgb(${r}, ${g}, ${b})`;
    }

    updateDistances(newDistances) {
        this.distances = newDistances;

        let maxDist = 0;
        for (let i = 0; i < newDistances.length; i++) {
            if (newDistances[i].distance > maxDist) {
                maxDist = newDistances[i].distance;
            }
        }
        this.maxDistance = maxDist > 0 ? maxDist : 1;
        this._redraw();
    }
}

// ── UMAP Web Worker ──

async function getUmapWorkerURL() {
    if (umapWorkerURL) return umapWorkerURL;
    // Fetch the umap-js library source and prepend it to worker code
    const resp = await fetch('/lib/umap-js.min.js');
    const umapLib = await resp.text();
    const workerCode = umapLib + `\n;
    self.onmessage = function(e) {
        const {n, dim, nComponents} = e.data;
        const embeddings = new Float32Array(e.data.vectors);

        // Reshape flat array into 2D array for UMAP
        const data = [];
        for (let i = 0; i < n; i++) {
            data.push(Array.from(embeddings.subarray(i * dim, (i + 1) * dim)));
        }

        try {
            const umap = new UMAP.UMAP({
                nNeighbors: 15,
                minDist: 0.1,
                nComponents: nComponents || 3,
                nEpochs: 200
            });
            const result = umap.fit(data);

            // Flatten result back to Float32Array
            const nc = nComponents || 3;
            const flat = new Float32Array(n * nc);
            for (let i = 0; i < n; i++) {
                for (let j = 0; j < nc; j++) {
                    flat[i * nc + j] = result[i][j];
                }
            }
            self.postMessage({coords: flat.buffer}, [flat.buffer]);
        } catch (err) {
            self.postMessage({error: err.message || String(err)});
        }
    };`;
    const blob = new Blob([workerCode], {type: 'application/javascript'});
    umapWorkerURL = URL.createObjectURL(blob);
    return umapWorkerURL;
}

// ── PCA (client-side, no server round-trip) ──

// Compute PCA from locally-downloaded vectors (no server round-trip)
function computePCAFromLocal(lv) {
    const N = lv.numVectors;
    const dim = lv.dim; // 128
    const emb = lv.values; // Float32Array, N * dim
    const allCoords = lv.coords;  // Int32Array, N * 2
    const gt = lv.metadata.geotransform;

    // Subsample for scatter plot (PCA is fast, so allow 4x more points than UMAP)
    let n, subEmb, subCoords;
    if (N > MAX_PCA_POINTS) {
        const stride = Math.ceil(N / MAX_PCA_POINTS);
        n = Math.floor(N / stride);
        subEmb = new Float32Array(n * dim);
        subCoords = new Int32Array(n * 2);
        for (let i = 0, si = 0; si < n; i += stride, si++) {
            subEmb.set(emb.subarray(i * dim, (i + 1) * dim), si * dim);
            subCoords[si * 2] = allCoords[i * 2];
            subCoords[si * 2 + 1] = allCoords[i * 2 + 1];
        }
        console.log(`[PCA] Subsampled ${N} -> ${n} points (stride ${stride})`);
    } else {
        n = N;
        subEmb = emb;
        subCoords = allCoords;
    }

    // 1. Compute per-dimension mean
    const mean = new Float64Array(dim);
    for (let i = 0; i < n; i++) {
        const off = i * dim;
        for (let d = 0; d < dim; d++) mean[d] += subEmb[off + d];
    }
    for (let d = 0; d < dim; d++) mean[d] /= n;

    // 2. Center data
    const centered = new Float32Array(n * dim);
    for (let i = 0; i < n; i++) {
        const off = i * dim;
        for (let d = 0; d < dim; d++) centered[off + d] = subEmb[off + d] - mean[d];
    }

    // 3. Compute 128x128 covariance matrix (upper triangle, then mirror)
    const cov = new Float64Array(dim * dim);
    for (let i = 0; i < n; i++) {
        const off = i * dim;
        for (let d1 = 0; d1 < dim; d1++) {
            const v1 = centered[off + d1];
            for (let d2 = d1; d2 < dim; d2++) {
                cov[d1 * dim + d2] += v1 * centered[off + d2];
            }
        }
    }
    const denom = n > 1 ? n - 1 : 1;
    for (let d1 = 0; d1 < dim; d1++) {
        for (let d2 = d1; d2 < dim; d2++) {
            cov[d1 * dim + d2] /= denom;
            if (d2 !== d1) cov[d2 * dim + d1] = cov[d1 * dim + d2];
        }
    }

    // 4. Power iteration with deflation to find top 3 eigenvectors
    const numComponents = 3;
    const eigenvectors = [];
    const covWork = new Float64Array(cov); // working copy for deflation

    for (let comp = 0; comp < numComponents; comp++) {
        // Initialize random vector
        let vec = new Float64Array(dim);
        for (let d = 0; d < dim; d++) vec[d] = Math.random() - 0.5;

        // Normalize
        let norm = Math.sqrt(vec.reduce((s, v) => s + v * v, 0));
        for (let d = 0; d < dim; d++) vec[d] /= norm;

        // Power iteration (100 iterations is plenty for 128-dim)
        for (let iter = 0; iter < 100; iter++) {
            const newVec = new Float64Array(dim);
            for (let d1 = 0; d1 < dim; d1++) {
                let s = 0;
                for (let d2 = 0; d2 < dim; d2++) s += covWork[d1 * dim + d2] * vec[d2];
                newVec[d1] = s;
            }
            norm = Math.sqrt(newVec.reduce((s, v) => s + v * v, 0));
            if (norm < 1e-10) break;
            for (let d = 0; d < dim; d++) vec[d] = newVec[d] / norm;
        }

        eigenvectors.push(vec);

        // Deflate: covWork -= eigenvalue * vec * vec^T
        const eigenvalue = norm;
        for (let d1 = 0; d1 < dim; d1++) {
            for (let d2 = 0; d2 < dim; d2++) {
                covWork[d1 * dim + d2] -= eigenvalue * vec[d1] * vec[d2];
            }
        }
    }

    // 5. Project centered data onto eigenvectors
    const points = new Array(n);
    for (let i = 0; i < n; i++) {
        const off = i * dim;
        const px = subCoords[i * 2];
        const py = subCoords[i * 2 + 1];
        const lon = gt.c + gt.a * px + gt.b * py;
        const lat = gt.f + gt.d * px + gt.e * py;

        let x = 0, y = 0, z = 0;
        for (let d = 0; d < dim; d++) {
            const v = centered[off + d];
            x += v * eigenvectors[0][d];
            y += v * eigenvectors[1][d];
            z += v * eigenvectors[2][d];
        }
        points[i] = { lat, lon, x, y, z };
    }

    return points;
}

// ── Dimensionality Reduction Loading ──

// Load dimensionality reduction (PCA or UMAP) with background computation if needed
async function loadDimReduction(method = null) {
    // Use specified method or current selection
    const dimMethod = method || currentDimReduction;
    const methodUpper = dimMethod.toUpperCase();

    try {
        console.log(`[${methodUpper}] Loading for ${window.currentViewportName}/${window.currentEmbeddingYear}...`);

        // Update panel title (validation mode has its own static title)
        if (window.currentPanelMode !== 'validation') {
            document.getElementById('panel4-title').textContent = `${methodUpper} (Embedding Space)`;
        }

        // Check cache for previously computed results
        const cacheKey = `${window.currentViewportName}/${window.currentEmbeddingYear}/${dimMethod}`;
        if (_dimReductionCache[cacheKey]) {
            console.log(`[${methodUpper}] Cache hit for ${cacheKey}`);
            const points = _dimReductionCache[cacheKey];
            umapData = points;
            if (umapCanvasLayer) { umapCanvasLayer.dispose(); umapCanvasLayer = null; }
            umapCanvasLayer = new UMAPScene('map-umap', umapData);
            setTimeout(() => { if (umapCanvasLayer && umapCanvasLayer.resize) umapCanvasLayer.resize(); }, 50);
            if (window.savedLabels && window.savedLabels.length > 0) updateUMAPColorsFromLabels();
            updatePanel4ManualLabels();
            console.log(`✓ ${methodUpper} restored from cache: ${points.length} points`);
            return;
        }

        let points = null;

        if (dimMethod === 'pca') {
            // PCA: compute client-side from downloaded vectors
            const umapContainer = document.getElementById('map-umap');

            // Ensure vectors are downloaded first
            if (!window.localVectors || window.localVectors.viewport !== window.currentViewportName || window.localVectors.year !== String(window.currentEmbeddingYear)) {
                umapContainer.innerHTML = `<div id="umap-progress" style="display:flex;align-items:center;justify-content:center;height:100%;font-size:18px;color:#666;">⏳ Downloading vectors...</div>`;
                await window.downloadVectorData(window.currentViewportName, window.currentEmbeddingYear);
                window.viewportStatus.vectors_downloaded = true;
            }

            umapContainer.innerHTML = `<div id="umap-progress" style="display:flex;align-items:center;justify-content:center;height:100%;font-size:18px;color:#666;">⏳ Computing PCA...</div>`;

            // Yield to UI before heavy computation
            await new Promise(resolve => setTimeout(resolve, 50));

            console.log(`[PCA] Computing client-side PCA for ${window.localVectors.numVectors} vectors...`);
            const t0 = performance.now();
            points = computePCAFromLocal(window.localVectors);
            console.log(`[PCA] Computed in ${((performance.now() - t0) / 1000).toFixed(2)}s`);
        } else {
            // UMAP: compute client-side in Web Worker
            const umapContainer = document.getElementById('map-umap');

            // Ensure vectors are downloaded first
            if (!window.localVectors || window.localVectors.viewport !== window.currentViewportName || window.localVectors.year !== String(window.currentEmbeddingYear)) {
                umapContainer.innerHTML = `<div id="umap-progress" style="display:flex;align-items:center;justify-content:center;height:100%;font-size:18px;color:#666;">Downloading vectors...</div>`;
                await window.downloadVectorData(window.currentViewportName, window.currentEmbeddingYear);
                window.viewportStatus.vectors_downloaded = true;
            }

            umapContainer.innerHTML = `<div id="umap-progress" style="display:flex;align-items:center;justify-content:center;height:100%;font-size:18px;color:#666;">Computing UMAP...</div>`;
            await new Promise(resolve => setTimeout(resolve, 50));

            const N = window.localVectors.numVectors;
            const dim = window.localVectors.dim;
            const gt = window.localVectors.metadata.geotransform;
            const emb = window.localVectors.values;
            const coords = window.localVectors.coords;
            const nComponents = 3;

            // Subsample if too many points
            let subN, subEmb, subCoords;
            if (N > MAX_UMAP_POINTS) {
                const stride = Math.ceil(N / MAX_UMAP_POINTS);
                subN = Math.floor(N / stride);
                subEmb = new Float32Array(subN * dim);
                subCoords = new Int32Array(subN * 2);
                for (let i = 0, si = 0; si < subN; i += stride, si++) {
                    subEmb.set(emb.subarray(i * dim, (i + 1) * dim), si * dim);
                    subCoords[si * 2] = coords[i * 2];
                    subCoords[si * 2 + 1] = coords[i * 2 + 1];
                }
                console.log(`[UMAP] Subsampled ${N} -> ${subN} points (stride ${stride})`);
            } else {
                subN = N;
                subEmb = emb.slice(0);
                subCoords = coords;
            }

            try {
                const workerURL = await getUmapWorkerURL();
                if (umapWorker) umapWorker.terminate();
                umapWorker = new Worker(workerURL);

                const t0 = performance.now();
                points = await new Promise((resolve, reject) => {
                    umapWorker.onmessage = (e) => {
                        if (e.data.error) {
                            reject(new Error(e.data.error));
                            return;
                        }
                        const flat = new Float32Array(e.data.coords);
                        const pts = new Array(subN);
                        for (let i = 0; i < subN; i++) {
                            const px = subCoords[i * 2];
                            const py = subCoords[i * 2 + 1];
                            pts[i] = {
                                lat: gt.f + gt.d * px + gt.e * py,
                                lon: gt.c + gt.a * px + gt.b * py,
                                x: flat[i * nComponents],
                                y: flat[i * nComponents + 1],
                                z: flat[i * nComponents + 2]
                            };
                        }
                        resolve(pts);
                    };
                    umapWorker.onerror = (err) => {
                        reject(new Error(err.message || 'UMAP worker error'));
                    };
                    umapWorker.postMessage({
                        vectors: subEmb.buffer,
                        n: subN,
                        dim: dim,
                        nComponents: nComponents
                    }, [subEmb.buffer]);
                });
                console.log(`[UMAP] Computed ${subN} points in ${((performance.now() - t0) / 1000).toFixed(1)}s`);
            } catch (err) {
                console.error('[UMAP] Worker error:', err);
                umapContainer.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;font-size:16px;color:#e74c3c;">Error: ${err.message}</div>`;
                return;
            }
        }

        // Filter out points outside viewport bounds
        const viewportBounds = window.getViewportBounds();
        if (points && viewportBounds) {
            const [[latMin, lonMin], [latMax, lonMax]] = viewportBounds;
            const before = points.length;
            points = points.filter(p =>
                p.lat >= latMin && p.lat <= latMax && p.lon >= lonMin && p.lon <= lonMax);
            if (points.length < before) {
                console.log(`[${methodUpper}] Filtered ${before - points.length} out-of-bounds points (${before} → ${points.length})`);
            }
        }

        if (points) {
            // Cache the computed points for instant restore on re-selection
            _dimReductionCache[cacheKey] = points;
            console.log(`[${methodUpper}] Got ${points.length} points, initializing Three.js scene...`);
            umapData = points;

            // Destroy previous scene if reloading
            if (umapCanvasLayer) {
                umapCanvasLayer.dispose();
                umapCanvasLayer = null;
            }

            umapCanvasLayer = new UMAPScene('map-umap', umapData);

            // Ensure correct size after layout
            setTimeout(() => {
                if (umapCanvasLayer && umapCanvasLayer.resize) {
                    umapCanvasLayer.resize();
                }
            }, 50);

            // Apply labels if they were loaded before
            if (window.savedLabels && window.savedLabels.length > 0) {
                console.log(`[${methodUpper}] Applying ${window.savedLabels.length} labels to colors...`);
                updateUMAPColorsFromLabels();
                console.log(`✓ ${methodUpper} rendered: ${points.length} points (colored by labels)`);
            } else {
                console.log(`✓ ${methodUpper} rendered: ${points.length} points (all grey)`);
            }
            updatePanel4ManualLabels();
        }
    } catch (error) {
        console.error(`[${methodUpper}] Fatal error:`, error);
    }
}

// Backward-compatible alias
async function loadUMAP() {
    await loadDimReduction(currentDimReduction);
}

// ── Heatmap (change detection) ──

// Load distance heatmap for Panel 5 (client-side computation)
async function loadHeatmap() {
    if (window.currentPanelMode !== 'change-detection') return;

    const waitMsg = document.getElementById('heatmap-waiting-message');
    const sameMsg = document.getElementById('heatmap-same-year-message');

    // Sync year variables from actual dropdown values
    const sel1 = document.getElementById('embedding-year-selector');
    const sel2 = document.getElementById('embedding-year-selector-2');
    if (sel1 && sel1.value) window.currentEmbeddingYear = sel1.value;
    if (sel2 && sel2.value) currentEmbeddingYear2 = sel2.value;

    // Guard: need vector data for both years
    if (!window.viewportStatus.has_vectors) {
        waitMsg.style.display = 'block';
        sameMsg.style.display = 'none';
        return;
    }
    waitMsg.style.display = 'none';

    // Guard: need different years
    if (window.currentEmbeddingYear === currentEmbeddingYear2) {
        sameMsg.style.display = 'block';
        // Remove stale heatmap (e.g. PCA canvas from explore mode)
        if (heatmapCanvasLayer && window.maps.panel5 && window.maps.panel5.hasLayer(heatmapCanvasLayer)) {
            window.maps.panel5.removeLayer(heatmapCanvasLayer);
        }
        return;
    }
    sameMsg.style.display = 'none';

    // Cache check: skip full recomputation if inputs unchanged
    const cacheKey = `${window.currentViewportName}|${window.currentEmbeddingYear}|${currentEmbeddingYear2}`;
    if (cacheKey === _heatmapCacheKey && heatmapCanvasLayer) {
        // Layer already exists with correct data; ensure it's on the map
        if (window.maps.panel5 && !window.maps.panel5.hasLayer(heatmapCanvasLayer)) {
            heatmapCanvasLayer.addTo(window.maps.panel5);
        }
        return;
    }

    try {
        // Save current localVectors so downloadVectorData side-effect doesn't clobber it
        const savedLocalVectors = window.localVectors;

        // Download vectors for both years (uses IndexedDB cache)
        const [data1, data2] = await Promise.all([
            window.downloadVectorData(window.currentViewportName, window.currentEmbeddingYear),
            window.downloadVectorData(window.currentViewportName, currentEmbeddingYear2)
        ]);

        // Restore localVectors
        window.localVectors = savedLocalVectors;

        const numVectors = Math.min(data1.numVectors, data2.numVectors);
        const dim = 128;
        const gt = data1.metadata.geotransform;
        const emb1 = data1.values;
        const emb2 = data2.values;
        const coords = data1.coords;

        // Compute Euclidean distances element-wise and build result array
        const distances = new Array(numVectors);
        let minDist = Infinity, maxDist = 0, sumDist = 0;
        const rawDists = new Float32Array(numVectors);

        for (let i = 0; i < numVectors; i++) {
            let sum = 0;
            const base = i * dim;
            for (let d = 0; d < dim; d++) {
                const diff = emb1[base + d] - emb2[base + d];
                sum += diff * diff;
            }
            const dist = Math.sqrt(sum);
            rawDists[i] = dist;

            const px = coords[i * 2];
            const py = coords[i * 2 + 1];
            distances[i] = {
                lat: gt.f + py * gt.e,
                lon: gt.c + px * gt.a,
                distance: dist
            };

            if (dist < minDist) minDist = dist;
            if (dist > maxDist) maxDist = dist;
            sumDist += dist;
        }

        // Compute median
        const sorted = Float32Array.from(rawDists).sort();
        const median = numVectors % 2 === 0
            ? (sorted[numVectors / 2 - 1] + sorted[numVectors / 2]) / 2
            : sorted[Math.floor(numVectors / 2)];

        const stats = {
            matched: numVectors,
            min_distance: minDist,
            max_distance: maxDist,
            mean_distance: sumDist / numVectors,
            median_distance: median
        };

        // Remove old heatmap layer
        if (heatmapCanvasLayer && window.maps.panel5.hasLayer(heatmapCanvasLayer)) {
            window.maps.panel5.removeLayer(heatmapCanvasLayer);
        }

        // Create new heatmap layer
        heatmapCanvasLayer = new HeatmapCanvasLayer(distances, stats);
        heatmapCanvasLayer.addTo(window.maps.panel5);

        // Update cache key
        _heatmapCacheKey = cacheKey;

        console.log(`✓ Heatmap loaded (client-side): ${numVectors} distances (${stats.matched} matched)`);
        console.log(`  Distance range: ${stats.min_distance.toFixed(3)} to ${stats.max_distance.toFixed(3)} (mean: ${stats.mean_distance.toFixed(3)}, median: ${stats.median_distance.toFixed(3)})`);

        // Populate change-stats table
        populateChangeStats(sorted, numVectors, stats);
    } catch (error) {
        console.error('Heatmap computation failed:', error);
    }
}

function populateChangeStats(sorted, n, stats) {
    const panel = document.getElementById('change-stats-panel');
    const table = document.getElementById('change-stats-table');
    const placeholder = panel.querySelector('.change-stats-placeholder');
    if (!panel || !table || n === 0) return;

    // Use median-relative thresholds so bins reveal actual distribution shape
    const median = stats.median_distance;
    const t1 = median * 0.5;   // half-median
    const t2 = median;          // median
    const t3 = median * 1.5;    // 1.5x median
    const maxD = sorted[n - 1];

    const bins = [
        { label: 'Stable',          hi: t1,   count: 0 },
        { label: 'Minor change',    hi: t2,   count: 0 },
        { label: 'Moderate change', hi: t3,   count: 0 },
        { label: 'Major change',    hi: maxD, count: 0 },
    ];

    for (let i = 0; i < n; i++) {
        const d = sorted[i];
        if (d < t1) bins[0].count++;
        else if (d < t2) bins[1].count++;
        else if (d < t3) bins[2].count++;
        else bins[3].count++;
    }

    const tbody = table.querySelector('tbody');
    tbody.innerHTML = '';
    let lo = 0;
    for (const b of bins) {
        const pct = ((b.count / n) * 100).toFixed(1);
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${b.label} <span style="color:#777;font-size:11px;">(${lo.toFixed(1)}\u2013${b.hi.toFixed(1)})</span></td><td>${b.count.toLocaleString()}</td><td>${pct}%</td>`;
        tbody.appendChild(tr);
        lo = b.hi;
    }

    placeholder.style.display = 'none';
    table.style.display = '';
    const note = document.getElementById('change-stats-note');
    if (note) note.style.display = '';
}

// ── Point interaction helpers ──

// Highlight a point in the UMAP scene by geographic lat/lon
function highlightUMAPPoint(lat, lon) {
    if (!umapData || !umapCanvasLayer) return;

    // Find the nearest UMAP point to clicked geographic location
    let nearest = null;
    let minDist = Infinity;

    for (const point of umapData) {
        const dist = Math.pow(point.lat - lat, 2) + Math.pow(point.lon - lon, 2);
        if (dist < minDist) {
            minDist = dist;
            nearest = point;
        }
    }

    if (!nearest) return;
    umapCanvasLayer.setHighlight(nearest);
}

// Show distance at a point in heatmap
function showDistanceAtPoint(lat, lon) {
    if (!heatmapCanvasLayer || !heatmapCanvasLayer.distances) return;

    // Find closest distance point
    let closest = null;
    let minDist = Infinity;

    for (const d of heatmapCanvasLayer.distances) {
        const dist = Math.sqrt((d.lat - lat) ** 2 + (d.lon - lon) ** 2);
        if (dist < minDist) {
            minDist = dist;
            closest = d;
        }
    }

    if (closest && minDist < 0.0001) {  // Within ~10m
        console.log(`Distance at (${lat.toFixed(6)}, ${lon.toFixed(6)}): ${closest.distance.toFixed(3)}`);
        alert(`Embedding distance: ${closest.distance.toFixed(3)}\nLocation: (${lat.toFixed(6)}, ${lon.toFixed(6)})`);
    }
}

// Update UMAP colors when labels are saved
function updateUMAPColorsFromLabels() {
    if (umapCanvasLayer && window.savedLabels) {
        umapCanvasLayer.updateLabelColors(window.savedLabels);
    }
}

// Update Panel 4 UMAP colors from manual label classes
function updatePanel4ManualLabels() {
    if (!umapCanvasLayer) return;
    const classNames = [...new Set(window.manualLabels.filter(l => l.visible).map(l => l.name))];
    // No visible manual labels at all → clear to gray
    if (classNames.length === 0) {
        umapCanvasLayer.clearSimilarityHighlight();
        return;
    }
    // Cache not yet populated (vectors not loaded) → skip, don't clear
    const hasAny = classNames.some(cn => window._classMatchCache[cn] && window._classMatchCache[cn].length > 0);
    if (!hasAny) return;
    // Build colourMap across all visible classes
    const colourMap = new Map();
    for (const cn of classNames) {
        const classLabels = window.getClassLabels(cn);
        const first = classLabels[0];
        if (!first) continue;
        const rgb = umapCanvasLayer._cssColorToRGB(first.color);
        if (!rgb) continue;
        const coords = window._classMatchCache[cn];
        if (!coords) continue;
        for (const c of coords) {
            const key = c.lat.toFixed(6) + ',' + c.lon.toFixed(6);
            colourMap.set(key, rgb);
        }
    }
    umapCanvasLayer.colorByManualLabels(colourMap);
}

// ── Expose on window for inline script and cross-module access ──

window.UMAPScene = UMAPScene;
window.HeatmapCanvasLayer = HeatmapCanvasLayer;
window.loadDimReduction = loadDimReduction;
window.loadUMAP = loadUMAP;
window.loadHeatmap = loadHeatmap;
window.highlightUMAPPoint = highlightUMAPPoint;
window.showDistanceAtPoint = showDistanceAtPoint;
window.updateUMAPColorsFromLabels = updateUMAPColorsFromLabels;
window.updatePanel4ManualLabels = updatePanel4ManualLabels;
window.computePCAFromLocal = computePCAFromLocal;
window.populateChangeStats = populateChangeStats;
