"""
Refactoring guard tests for TEE viewer.html modularization.

These tests lock down the current contract so that extracting JS modules
and backend libraries cannot silently break functionality. Run after every
extraction step:

    cd /Users/skeshav/blore && venv/bin/pytest validation/ -v

The tests are static — they parse HTML and Python files without running
the app or needing a browser.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "public" / "viewer.html"
JS_DIR = ROOT / "public" / "js"


# ──────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def html():
    return VIEWER.read_text()


@pytest.fixture(scope="module")
def all_script_text(html):
    """All JS: inline <script> blocks + any <script type='module'> src files."""
    parts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.DOTALL)
    combined = "\n".join(parts)
    # Also read any JS module files in public/js/ (post-extraction)
    if JS_DIR.is_dir():
        for js_file in sorted(JS_DIR.glob("*.js")):
            combined += "\n" + js_file.read_text()
    return combined


# ──────────────────────────────────────────────────
# 1. API endpoint coverage
#    Every fetch() call in the frontend must survive extraction.
# ──────────────────────────────────────────────────

class TestAPIEndpointCoverage:
    """Verify all backend API calls are present in the JS."""

    ENDPOINTS = [
        # Auth
        "/api/auth/status",
        "/api/auth/logout",
        "/api/auth/change-password",
        # Viewports
        "/api/viewports/current",
        "/api/viewports/",              # covers is-ready, add-years via template
        # Vector data
        "/api/vector-data/",            # covers all vector file fetches
        # Operations
        "/api/operations/progress/",
        # Config
        "/api/config",
        # Evaluation
        "/api/evaluation/upload-shapefile",
        "/api/evaluation/class-counts",
        "/api/evaluation/run",
        "/api/evaluation/finish-classifier",
        # Tiles
        "/tiles/health",
        # Static
        "/schemas/ukhab-v2.json",
    ]

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_endpoint_referenced(self, all_script_text, endpoint):
        assert endpoint in all_script_text, (
            f"API endpoint {endpoint!r} not found in JS. "
            "Was it lost during module extraction?"
        )


# ──────────────────────────────────────────────────
# 2. Critical JS function coverage
#    All public functions must still be defined somewhere.
# ──────────────────────────────────────────────────

class TestCriticalFunctions:
    """Every function critical to the app must exist in the combined JS."""

    # Grouped by module they'll live in after extraction
    FUNCTIONS = [
        # app.js
        "setPanelLayout",
        "restorePanelMode",
        "evaluateDependencies",
        "pollViewportStatus",
        "startPoller",
        "showProgressModal",
        "hideProgressModal",
        "updateProgressUI",
        "pollOperationProgress",
        # maps.js
        "createMaps",
        "syncMaps",
        "handleUnifiedClick",
        "setCrossPanelMarker",
        "clearCrossPanelMarkers",
        "refreshEmbeddingTileLayer",
        "switchEmbeddingYear",
        # vectors.js (vectors + similarity)
        "downloadVectorData",
        "parseNpy",
        "decompressGzip",
        "buildGridLookup",
        "gridLookupIndex",
        "localExtract",
        "localSearchSimilar",
        "localSearchSimilarMulti",
        "clearExplorerResults",
        "explorerClick",
        "calculateAverageEmbedding",
        # labels.js (labels + fileio + polygon)
        "setLabelMode",
        "setCurrentManualLabel",
        "addManualLabel",
        "removeManualLabel",
        "renderManualLabelsList",
        "rebuildManualOverlays",
        "rebuildClassOverlay",
        "saveManualLabelsToStorage",
        "restoreManualLabelState",
        "handleManualSimilaritySearch",
        "handleManualPinDrop",
        "toggleAllManualLabels",
        "triggerManualClassification",
        "renderManualClassification",
        "exportManualLabels",
        "importManualLabels",
        "startPolygonDrawing",
        "cancelPolygonDrawing",
        "handlePolygonComplete",
        "pointInPolygon",
        "rasterizePolygon",
        "showLabelTimeline",
        # segmentation.js
        "runKMeans",
        "showSegmentationOverlay",
        "clearSegmentation",
        "saveClusterAsLabel",
        "saveAllClustersAsLabels",
        # dimreduction.js
        "computePCAFromLocal",
        "loadHeatmap",
        # evaluation.js
        "uploadShapefile",
        "runEvaluation",
        "renderConfusionMatrix",
        "exportEvalResults",
        # schema.js
        "loadSchema",
        "loadCustomSchema",
        "parseTabIndentedSchema",
        "renderSchemaSelector",
        "selectSchemaLabel",
        "filterSchemaTree",
        "toggleSchemaDropdown",
    ]

    @pytest.mark.parametrize("fname", FUNCTIONS)
    def test_function_exists(self, all_script_text, fname):
        pattern = rf"(?:async\s+)?function\s+{fname}\s*\("
        # Also match ES module export: export function foo(
        pattern_export = rf"export\s+(?:async\s+)?function\s+{fname}\s*\("
        found = re.search(pattern, all_script_text) or re.search(pattern_export, all_script_text)
        assert found, (
            f"Function {fname}() not found in any JS. "
            "Was it lost during module extraction?"
        )


# ──────────────────────────────────────────────────
# 3. Critical state variables
# ──────────────────────────────────────────────────

class TestCriticalState:
    """State variables that must be initialized somewhere in the JS."""

    VARS = [
        (r"(?:let|const|var)\s+maps\s*=\s*\{", "maps"),
        (r"(?:let|const|var)\s+localVectors\s*=", "localVectors"),
        (r"(?:let|const|var)\s+manualLabels\s*=\s*\[", "manualLabels"),
        (r"(?:let|const|var)\s+currentPanelMode\s*=", "currentPanelMode"),
        (r"(?:let|const|var)\s+viewportStatus\s*=", "viewportStatus"),
        (r"(?:let|const|var)\s+segLabels\s*=", "segLabels"),
        (r"(?:let|const|var)\s+currentManualLabel\s*=", "currentManualLabel"),
        (r"(?:let|const|var)\s+labelMode\s*=", "labelMode"),
        (r"(?:let|const|var)\s+activeSchema\s*=", "activeSchema"),
        (r"(?:let|const|var)\s+activeSchemaMode\s*=", "activeSchemaMode"),
        (r"(?:let|const|var)\s+polygonDrawHandler\s*=", "polygonDrawHandler"),
        (r"(?:let|const|var)\s+segAssignments\s*=", "segAssignments"),
        (r"(?:let|const|var)\s+currentDimReduction\s*=", "currentDimReduction"),
        (r"PANEL5_LAYER_RULES", "PANEL5_LAYER_RULES"),
    ]

    @pytest.mark.parametrize("pattern,name", VARS, ids=[v[1] for v in VARS])
    def test_state_initialized(self, all_script_text, pattern, name):
        assert re.search(pattern, all_script_text), (
            f"State variable {name!r} not found. "
            "Was it lost during module extraction?"
        )


# ──────────────────────────────────────────────────
# 4. DOM element completeness
#    Every critical element must be in viewer.html.
# ──────────────────────────────────────────────────

class TestDOMCompleteness:
    """Critical DOM element IDs that must exist in viewer.html."""

    IDS = [
        # Header controls
        "panel-layout-select", "similarity-threshold", "similarity-controls",
        "clear-similarity-btn", "label-controls-bar", "schema-dropdown-btn",
        "labelling-export-btn", "labelling-import-btn",
        # Panels
        "map-container", "map-osm", "map-embedding", "map-embedding2",
        "map-rgb", "map-umap", "map-panel5",
        # Panel 6
        "panel6-label-view", "panel6-autolabel-view", "panel6-manual-view",
        "panel6-seg-list", "panel6-labels-list", "panel6-promote-all-btn",
        "panel6-toggle-all-btn", "label-mode-select",
        # Segmentation
        "seg-run-btn", "seg-k-input", "seg-k-minus", "seg-k-plus",
        "seg-clear-btn", "seg-export-btn", "seg-panel-close-btn",
        # Manual labels
        "manual-label-set-btn", "manual-label-name", "manual-label-color",
        "manual-label-swatch", "manual-active-label", "manual-labels-list",
        "manual-hide-all-btn", "manual-classify-btn",
        # Schema
        "schema-dropdown-menu", "schema-float",
        # Modals
        "progress-overlay", "timeline-modal-overlay",
        "save-label-modal-overlay", "label-save-confirm", "label-save-cancel",
        # Help & status
        "help-popup", "help-btn", "help-close-btn",
        "status-btn", "status-close-btn",
        # Validation
        "val-run-btn", "val-cancel-btn", "val-export-btn",
        "cm-export-btn", "cm-toggle-pct",
        "val-cm-panel", "validation-controls",
        # Auth
        "loginBtn",
    ]

    @pytest.mark.parametrize("elem_id", IDS)
    def test_element_in_html(self, html, elem_id):
        assert f'id="{elem_id}"' in html or f"id='{elem_id}'" in html, (
            f"DOM element #{elem_id} not found in viewer.html"
        )


# ──────────────────────────────────────────────────
# 5. CSS mode rules intact
# ──────────────────────────────────────────────────

class TestCSSModeRules:
    """CSS rules needed for mode switching must be in viewer.html."""

    MODES = ["explore", "change-detection", "labelling", "validation"]

    @pytest.mark.parametrize("mode", MODES)
    def test_container_mode_css(self, html, mode):
        assert f"#map-container.mode-{mode}" in html, (
            f"CSS rule for #map-container.mode-{mode} missing"
        )

    def test_body_explore_label_controls(self, html):
        assert "body.mode-explore #label-controls-bar" in html

    def test_body_labelling_similarity(self, html):
        assert "body.mode-labelling #similarity-controls" in html

    def test_leaflet_draw_hidden(self, html):
        assert ".leaflet-draw-toolbar" in html

    def test_panel6_label_view_css(self, html):
        assert "#map-container.mode-labelling #panel6-label-view" in html


# ──────────────────────────────────────────────────
# 6. JS module integrity (post-extraction)
#    If public/js/ exists, all .js files must be imported.
# ──────────────────────────────────────────────────

class TestModuleIntegrity:
    """After extraction, every JS file in public/js/ must be referenced."""

    def test_all_js_files_imported(self, html):
        if not JS_DIR.is_dir():
            pytest.skip("public/js/ not yet created (pre-extraction)")
        js_files = sorted(JS_DIR.glob("*.js"))
        assert js_files, "public/js/ exists but is empty"
        for js_file in js_files:
            ref = f"js/{js_file.name}"
            assert ref in html, (
                f"{js_file.name} exists in public/js/ but is not referenced in viewer.html"
            )

    def test_module_script_tag(self, html):
        if not JS_DIR.is_dir():
            pytest.skip("public/js/ not yet created (pre-extraction)")
        assert 'type="module"' in html or "type='module'" in html, (
            "viewer.html has public/js/ files but no <script type='module'> tag"
        )

    def test_js_files_parse(self):
        """Every .js file in public/js/ must be valid JavaScript."""
        if not JS_DIR.is_dir():
            pytest.skip("public/js/ not yet created (pre-extraction)")
        for js_file in sorted(JS_DIR.glob("*.js")):
            code = js_file.read_text()
            result = subprocess.run(
                ["node", "--input-type=module", "-e", code],
                capture_output=True, text=True, timeout=10,
            )
            # We only check parse errors, not runtime errors.
            # Node exits 1 on SyntaxError but also on ReferenceError at top level.
            # Filter to only fail on SyntaxError.
            if result.returncode != 0 and "SyntaxError" in result.stderr:
                pytest.fail(
                    f"{js_file.name} has a JS syntax error:\n{result.stderr[:500]}"
                )


# ──────────────────────────────────────────────────
# 7. Backend library extraction guards
# ──────────────────────────────────────────────────

class TestBackendLibraries:
    """After backend extraction, new lib files must exist and be importable."""

    EXISTING_LIBS = [
        "lib/config.py",
        "lib/progress_tracker.py",
        "lib/viewport_utils.py",
        "lib/viewport_writer.py",
        "lib/pipeline.py",
    ]

    @pytest.mark.parametrize("path", EXISTING_LIBS)
    def test_existing_lib_present(self, path):
        assert (ROOT / path).is_file(), f"{path} missing"

    NEW_LIBS = [
        ("lib/viewport_ops.py", [
            "check_readiness", "delete_viewport_data", "compute_data_size",
        ]),
        ("lib/evaluation_engine.py", [
            "load_vectors", "dequantize", "rasterize_shapefile",
            "make_classifier", "run_learning_curve",
        ]),
        ("lib/tile_renderer.py", [
            "render_tile_png", "tile_to_bbox", "get_pyramid_path",
        ]),
        ("api/views/share.py", [
            "submit_share", "list_shares", "download_share",
        ]),
    ]

    @pytest.mark.parametrize("path,functions", NEW_LIBS, ids=[p for p, _ in NEW_LIBS])
    def test_new_lib_if_exists(self, path, functions):
        lib_file = ROOT / path
        if not lib_file.is_file():
            pytest.skip(f"{path} not yet extracted")
        source = lib_file.read_text()
        for fn in functions:
            assert f"def {fn}(" in source, (
                f"{path} exists but is missing function {fn}()"
            )


class TestBackendViewsIntact:
    """API view files must still exist and define their route handlers."""

    VIEWS = {
        "api/views/viewports.py": [
            "list_viewports", "current_viewport", "switch_viewport",
            "create_viewport", "delete_viewport", "is_ready",
        ],
        "api/views/evaluation.py": [
            "upload_shapefile", "class_pixel_counts", "run_evaluation",
            "finish_classifier", "download_model",
        ],
        "api/views/tiles.py": [
            "get_tile", "get_bounds", "tile_health",
        ],
        "api/views/pipeline.py": [
            "operations_progress",
        ],
        "api/views/vector_data.py": [
            "serve_vector_data",
        ],
        "api/views/config.py": [
            "health", "get_config",
        ],
        "api/auth_views.py": [
            "auth_login", "auth_logout", "auth_status", "auth_change_password",
        ],
    }

    @pytest.mark.parametrize("path", VIEWS.keys())
    def test_view_file_exists(self, path):
        assert (ROOT / path).is_file(), f"{path} missing"

    @pytest.mark.parametrize(
        "path,handlers",
        VIEWS.items(),
        ids=VIEWS.keys(),
    )
    def test_view_handlers_defined(self, path, handlers):
        source = (ROOT / path).read_text()
        for fn in handlers:
            assert f"def {fn}(" in source, (
                f"{path} missing handler {fn}(). "
                "Was it accidentally deleted during extraction?"
            )


# ──────────────────────────────────────────────────
# 8. Event listener wiring
#    Key DOM elements must have their listeners attached in JS.
# ──────────────────────────────────────────────────

class TestEventListenerWiring:
    """Critical event listeners that must be wired up somewhere in the JS."""

    WIRING = [
        ("help-btn", "addEventListener"),
        ("help-close-btn", "addEventListener"),
        ("status-btn", "addEventListener"),
        ("status-close-btn", "addEventListener"),
        ("seg-run-btn", "addEventListener"),
        ("seg-clear-btn", "addEventListener"),
        ("seg-export-btn", "addEventListener"),
        ("label-save-confirm", "addEventListener"),
        ("label-save-cancel", "addEventListener"),
        ("timeline-close-btn", "addEventListener"),
        ("val-run-btn", "addEventListener"),
        ("cm-export-btn", "addEventListener"),
        ("cm-toggle-pct", "addEventListener"),
    ]

    @pytest.mark.parametrize(
        "elem_id,method",
        WIRING,
        ids=[w[0] for w in WIRING],
    )
    def test_listener_attached(self, all_script_text, elem_id, method):
        # Match: getElementById('elem-id').addEventListener
        # or: document.getElementById('elem-id').addEventListener
        pattern = rf"""['"]{elem_id}['"].*?{method}"""
        assert re.search(pattern, all_script_text, re.DOTALL), (
            f"No {method}() found for #{elem_id}. "
            "Was the event listener lost during extraction?"
        )


# ──────────────────────────────────────────────────
# 9. External library dependencies
# ──────────────────────────────────────────────────

class TestExternalDeps:
    """Third-party libraries that must be loaded."""

    def test_leaflet_css(self, html):
        assert "leaflet.css" in html

    def test_leaflet_js(self, html):
        assert "leaflet.js" in html or "leaflet.min.js" in html

    def test_leaflet_draw(self, html):
        assert "leaflet.draw.js" in html or "leaflet-draw" in html

    def test_threejs(self, html):
        assert "three" in html.lower()

    def test_importmap_exists(self, html):
        assert "importmap" in html
