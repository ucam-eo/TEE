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
        # Evaluation (served by tee-compute, referenced in JS)
        "/api/evaluation/upload-shapefile",
        "/api/evaluation/run-large-area",
        "/api/evaluation/train-models",
        "/api/evaluation/download-model",
        # Tiles
        "/tiles/health",
        # Static
        "/schemas/ukhab-v2.json",
    ]

    @pytest.mark.parametrize("endpoint", ENDPOINTS)
    def test_endpoint_referenced(self, all_script_text, endpoint):
        # Check for literal path OR evalUrl('suffix') pattern
        if endpoint in all_script_text:
            return
        # Extract the last path segment for evalUrl('segment') check
        suffix = endpoint.rsplit('/', 1)[-1]
        assert suffix in all_script_text, (
            f"API endpoint {endpoint!r} (or evalUrl('{suffix}')) not found in JS. "
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
        "generateConfig",
        "loadResultsFile",
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
        "cm-toggle-pct",
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
        has_container = f"#map-container.mode-{mode}" in html
        has_body = f"body.mode-{mode}" in html
        assert has_container or has_body, (
            f"CSS rule for mode-{mode} missing from both #map-container and body"
        )

    def test_body_explore_label_controls(self, html):
        assert "body.mode-explore #label-controls-bar" in html

    def test_body_labelling_similarity(self, html):
        assert "body.mode-labelling #similarity-controls" in html

    def test_leaflet_draw_hidden(self, html):
        assert ".leaflet-draw-toolbar" in html

    def test_panel_layout_table(self, all_script_text):
        assert "PANEL_LAYOUT" in all_script_text, "Declarative PANEL_LAYOUT table must exist in JS"


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
            "detect_field_type",
        ]),
        # tessera_eval.* moved to its own repo (ucam-eo/tessera-eval); its API is
        # guarded there + by TestTesseraEvalSelfContained (import-based) below.
        ("lib/tile_renderer.py", [
            "render_tile_png", "tile_to_bbox", "get_pyramid_path",
        ]),
        ("api/views/share.py", [
            "submit_share", "list_shares", "download_share",
        ]),
        ("api/views/enrolment.py", [
            "create_enrolled_user", "list_enrolled_users", "disable_enrolled_user",
        ]),
    ]

    @pytest.mark.parametrize("path,functions", NEW_LIBS, ids=[p for p, _ in NEW_LIBS])
    def test_new_lib_if_exists(self, path, functions):
        lib_file = ROOT / path
        if not lib_file.is_file():
            pytest.skip(f"{path} not yet extracted")
        source = lib_file.read_text()
        for fn in functions:
            has_def = f"def {fn}(" in source
            has_import = f"import {fn}" in source or f"{fn}" in source
            assert has_def or has_import, (
                f"{path} exists but is missing function {fn}() "
                "(neither defined nor re-exported)"
            )


class TestBackendViewsIntact:
    """API view files must still exist and define their route handlers."""

    VIEWS = {
        "api/views/viewports.py": [
            "list_viewports", "current_viewport", "switch_viewport",
            "create_viewport", "delete_viewport", "is_ready",
        ],
        "api/views/evaluation.py": [
            "upload_shapefile", "clear_shapefiles", "run_evaluation",
            "train_models", "download_model",
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
        "api/views/enrolment.py": [
            "create_enrolled_user", "list_enrolled_users", "disable_enrolled_user",
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


# ──────────────────────────────────────────────────
# 10. tessera-eval is an external dependency
#     (https://github.com/ucam-eo/tessera-eval). Its
#     structural guarantees live in that repo's own
#     tests; here we only assert the runtime contract
#     blore relies on: it imports and is Django-free.
# ──────────────────────────────────────────────────


class TestTesseraEvalSelfContained:
    """tessera_eval must import and stay framework-independent (no Django)."""

    def test_importable(self):
        pytest.importorskip("tessera_eval")

    def test_no_django_dependency(self):
        import pkgutil
        te = pytest.importorskip("tessera_eval")
        pkg_dir = Path(te.__file__).parent
        for mod in pkgutil.iter_modules([str(pkg_dir)]):
            src = (pkg_dir / f"{mod.name}.py").read_text()
            assert "import django" not in src and "from django" not in src, (
                f"tessera_eval/{mod.name}.py imports Django"
            )

    def test_exports_core_api(self):
        te = pytest.importorskip("tessera_eval")
        for name in [
            "run_learning_curve", "run_kfold_cv", "regression_metrics",
            "detect_field_type", "make_classifier", "make_regressor",
            "rasterize_shapefile", "load_embeddings_for_shapefile",
        ]:
            assert hasattr(te, name), f"tessera_eval missing export: {name}"


# ──────────────────────────────────────────────────
# 11. NDJSON event schema conformance
#     The events streamed by evaluation endpoints must
#     match what the JS event handler expects.
# ──────────────────────────────────────────────────

class TestNDJSONEventSchema:
    """Verify JS handles all event types emitted by the backend."""

    # Events the backend can emit (from evaluation.py and evaluate.py)
    BACKEND_EVENTS = [
        "start", "progress", "confusion_matrices", "done",
        "error", "model_ready", "status",
        "download_progress", "field_start",
        "fold_result", "aggregate",
    ]

    @pytest.mark.parametrize("event_name", BACKEND_EVENTS)
    def test_js_handles_event(self, all_script_text, event_name):
        # The JS handler checks ev.event === 'name' or event["type"]
        assert f"'{event_name}'" in all_script_text or f'"{event_name}"' in all_script_text, (
            f"NDJSON event '{event_name}' emitted by backend but not handled in JS"
        )


# ──────────────────────────────────────────────────
# 12. Large-area evaluation guards
# ──────────────────────────────────────────────────

class TestLargeAreaEvaluation:
    """Guards for large-area evaluation feature (code review fixes)."""

    def test_error_bar_plugin_defined(self, all_script_text):
        assert "errorBarPlugin" in all_script_text or "errorBars" in all_script_text, (
            "Error bar plugin for bar charts must be defined in evaluation.js"
        )

    def test_classification_bar_chart_function(self, all_script_text):
        # Kept defined (an actual standalone k-fold CV title is hardcoded
        # into it) but NOT called for large-area results as of 2026-08-21 --
        # see test_bar_chart_not_called_from_aggregate_handler below for why.
        assert "renderClassificationBarChart" in all_script_text, (
            "renderClassificationBarChart must stay defined even if unused"
        )

    def test_regression_bar_chart_function(self, all_script_text):
        assert "renderRegressionBarChart" in all_script_text, (
            "renderRegressionBarChart must stay defined even if unused"
        )

    def test_bar_chart_not_called_from_aggregate_handler(self, all_script_text):
        # renderRegressionBarChart/renderClassificationBarChart destroy()
        # and replace valChart with a *bar* chart on the same canvas
        # (#val-chart) the learning-curve *line* chart was just built on
        # over the whole run -- calling either from the 'aggregate' handler
        # (fires once, when a large-area run finishes) silently clobbers
        # the learning curve with a single-bar summary the instant the run
        # completes. Confirmed live, Louis Driver, 2026-08-21: "it looks
        # fine [during the run], but when it finishes it presents a
        # strange graph" -- a box shape, which for one model is exactly
        # what a lone bar looks like.
        i = all_script_text.find("ev.event === 'aggregate'")
        assert i != -1, "expected an 'aggregate' event handler in evaluation.js"
        # Bounded to the handler's own block, not the whole file, so this
        # doesn't just ban the functions outright (they may legitimately
        # be called elsewhere, e.g. a future standalone k-fold CV UI).
        handler_block = all_script_text[i:i + 1200]
        assert "renderRegressionBarChart(" not in handler_block, (
            "renderRegressionBarChart must not be called from the 'aggregate' "
            "handler -- it destroys and replaces the learning curve line "
            "chart with a bar chart the instant a large-area run finishes"
        )
        assert "renderClassificationBarChart(" not in handler_block, (
            "renderClassificationBarChart must not be called from the "
            "'aggregate' handler -- same reason as renderRegressionBarChart above"
        )

    def test_done_handler_null_guard(self, all_script_text):
        # The done handler must guard against null lastChartData
        assert "!lastChartData" in all_script_text, (
            "done handler must guard against null lastChartData (Fix 4)"
        )

    def test_server_has_multi_shapefile(self):
        source = (TESSERA_EVAL / "server.py").read_text()
        assert "clear-shapefiles" in source, (
            "server.py must support multi-shapefile upload (clear-shapefiles endpoint)"
        )

    def test_osm_referrer_policy(self, html):
        assert 'name="referrer"' in html, (
            "viewer.html must have <meta name='referrer' content='origin'>"
        )

    def test_results_panel_in_panel3(self, html):
        assert 'id="val-results-panel"' in html, (
            "Results panel must exist in panel 3 for large-area progress table"
        )

    def test_download_triggers_training(self, all_script_text):
        assert "train-models" in all_script_text, (
            "Download Models button must call /api/evaluation/train-models"
        )

    def test_back_button_disabled_during_eval(self, all_script_text):
        assert "back-btn" in all_script_text, (
            "Back button must be disabled during evaluation"
        )


# ──────────────────────────────────────────────────
# 13. Inline handler / module scope reachability
#     <script type="module"> function declarations are module-scoped, not
#     global -- inline onclick/oninput/onchange/... attributes (including
#     ones built as JS template-literal HTML, e.g. renderXList() functions)
#     run in *global* scope and can only reach a module function if it's
#     explicitly exported via `window.fn = fn`. Missing that export lets
#     the control still visually respond (native browser behaviour, e.g. a
#     <input type="range"> drags fine on its own) while doing nothing --
#     the attribute throws a silent ReferenceError before ever reaching the
#     function body. This is exactly what shipped in the per-class
#     similarity slider (Louis Driver: slider moves but "remains
#     functionally at 0") -- onManualClassSliderInput, stepClassThreshold,
#     and editClassThresholdValue were defined but never exported to
#     window. A logic-level test of those functions (calling them directly)
#     passed and did not catch it, since that bypasses the exact seam that
#     broke -- hence this guard checks the export wiring itself, statically.
# ──────────────────────────────────────────────────

class TestInlineHandlerGlobalReachability:
    """Every function invoked from an inline on*="..." attribute that is
    defined inside a public/js/*.js module file must also be exported to
    `window` -- otherwise the browser throws a silent ReferenceError the
    moment the attribute fires."""

    # Handler-call syntax as it appears literally in source text, whether
    # that's viewer.html markup or a JS template literal building HTML.
    _ATTR_RE = re.compile(
        r"\bon(?:click|input|change|blur|focus|keydown|keyup|submit|dblclick)="
        r"""["']([a-zA-Z_$][a-zA-Z0-9_$]*)\(""",
    )
    _WINDOW_EXPORT_RE = re.compile(r"window\.([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=")
    _FUNC_DEF_RE = re.compile(r"function\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(")

    def _referenced_handlers(self, text):
        return set(self._ATTR_RE.findall(text))

    def test_module_defined_handlers_are_exported(self, html):
        if not JS_DIR.is_dir():
            pytest.skip("public/js/ not yet created (pre-extraction)")
        module_js = "\n".join(
            js_file.read_text() for js_file in sorted(JS_DIR.glob("*.js"))
        )

        # Handlers referenced directly in viewer.html markup, plus ones
        # referenced inside JS template literals that build HTML at runtime
        # (e.g. renderManualLabelsList()'s generated onclick="..." strings) --
        # those never appear in viewer.html itself.
        handlers = self._referenced_handlers(html) | self._referenced_handlers(module_js)

        # Only names actually *defined inside a module file* need an
        # export: names defined in a plain (non-module) inline <script> in
        # viewer.html are already global, and browser built-ins (alert,
        # confirm, ...) aren't defined anywhere in our own source.
        defined_in_module = set(self._FUNC_DEF_RE.findall(module_js))
        exported = set(self._WINDOW_EXPORT_RE.findall(module_js))

        missing = sorted((handlers & defined_in_module) - exported)
        assert not missing, (
            "Function(s) invoked from an inline on*=\"...\" attribute and "
            "defined inside a <script type=\"module\"> file, but never "
            "exported via `window.<name> = <name>` -- the attribute throws "
            "a silent ReferenceError when it fires, so the control looks "
            "alive but does nothing: " + ", ".join(missing)
        )


# ──────────────────────────────────────────────────
# 14. Learning-curve x-axis denominator
#     The frontend's "% of labelled pixels used for training" x-axis must be
#     computed against the real total_labelled_pixels from this run's
#     'start' event, not the rough polygon-area/100m² estimate made at
#     upload time (before real label extraction). Confirmed live (Louis
#     Driver, 2026-08-21) that using the estimate first misaligned every
#     point's x -- e.g. a point trained at the backend's nominal pct=20
#     (server.py's training_pcts = [1, 3, 5, 10, 20, 30, 50, 80]) landed at
#     ~8% instead of ~20% on the chart, because the estimate (467K in his
#     screenshot) was ~2.4x the real total_labelled_pixels (~193K, inferred
#     from his own trainPx/pct numbers). This happened on the very *first*
#     plot with no rebuild/metric-switch involved, so it was a different
#     bug from (and not fixed by) the rebuild-vs-live desync fixed
#     2026-08-20 -- that fix only made both paths *consistently* wrong.
# ──────────────────────────────────────────────────

class TestLearningCurveXAxisDenominator:
    """total_labelled_pixels (real, from 'start') must be tried before
    valEstimatedLabelledPixels (a rough upload-time guess) wherever the
    frontend computes a learning-curve point's x position."""

    def test_progress_handler_prefers_real_total_over_estimate(self, all_script_text):
        # The exact fallback-chain expression from the 'progress' handler --
        # order matters: lastChartData.total_labelled_pixels must come
        # before valEstimatedLabelledPixels, not after.
        assert (
            "lastChartData.total_labelled_pixels || valEstimatedLabelledPixels"
            in all_script_text
        ), (
            "The learning-curve x-axis denominator must try the real "
            "total_labelled_pixels (from this run's 'start' event) before "
            "falling back to valEstimatedLabelledPixels (a rough polygon-area "
            "estimate from upload time) -- the reverse order silently "
            "misaligns every point's x position whenever the two totals "
            "differ, which they generally do."
        )


# ──────────────────────────────────────────────────
# 15. Bar-chart-with-error-bars animation
#     errorBarPlugin (a custom afterDraw hook) positions its whisker caps
#     from ds.data[i], the dataset's *final* value -- but Chart.js's bar
#     charts animate their height by default (~1s grow), drawing the bar
#     itself at an *interpolated*, still-growing height every frame during
#     that window. The two draw from different sources, so anyone looking
#     at (or screenshotting) the chart during the animation sees a whisker
#     floating above a visibly too-short bar. Confirmed live (Louis Driver,
#     2026-08-21, on the R² bar chart) and reproduced headlessly with
#     Playwright: a screenshot 300ms after render showed the bar capped at
#     ~79% of its real value; the fix (animation: false) renders it correct
#     within 50ms. Every bar chart using errorBarPlugin needs this.
# ──────────────────────────────────────────────────

class TestErrorBarChartsDisableAnimation:
    """Every Chart.js bar chart that uses errorBarPlugin must set
    animation: false, or the plugin's whisker caps visibly desync from the
    still-animating bar for about a second after every render."""

    def test_every_error_bar_chart_disables_animation(self, all_script_text):
        # Anchor directly on the `plugins: [errorBarPlugin],` marker itself
        # (rather than hunting for it within a window from some other
        # anchor) so this doesn't depend on how much unrelated config
        # precedes it -- each occurrence starts exactly one chart's
        # `options: {...}` block, where animation: false should appear
        # within the first couple hundred characters (right after `options:
        # {`, give or take an explanatory comment).
        calls = all_script_text.split("plugins: [errorBarPlugin],")[1:]
        assert calls, "expected at least one Chart.js call using errorBarPlugin"
        offenders = [i for i, call in enumerate(calls) if "animation: false" not in call[:2000]]
        assert not offenders, (
            "Found a Chart.js bar chart using errorBarPlugin without "
            "animation: false -- its whisker caps (positioned from the "
            "final value) will float above the bar while Chart.js's "
            "default grow animation is still interpolating the bar's "
            "drawn height, for about a second after every render."
        )


# ──────────────────────────────────────────────────
# 16. Per-class similarity threshold distance cache
#     vectors.js::localMatchesFromDistances (re-thresholding a cached
#     distance array) must return exactly what localSearchSimilarMulti (the
#     original, always-fresh single-pass search) would have, at every
#     threshold -- see validation/test_distance_cache.mjs for the actual
#     behavioral test; this just wires it into `pytest validation/` so it
#     runs automatically rather than needing someone to remember `node
#     validation/test_distance_cache.mjs`.
# ──────────────────────────────────────────────────

class TestDistanceCacheCorrectness:
    def test_cached_rethreshold_matches_direct_search(self):
        result = subprocess.run(
            ["node", str(ROOT / "validation" / "test_distance_cache.mjs")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            "validation/test_distance_cache.mjs failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ──────────────────────────────────────────────────
# 17. Manual-label DOM sync across mirrored containers
#     renderManualLabelsList() mirrors identical markup into both
#     #manual-labels-list and #panel6-labels-list (the auto-label view's
#     copy of the same list) -- see validation/test_manual_label_dom_sync.mjs
#     for the actual behavioral test (needs linkedom, an npm devDependency,
#     to parse/query the rendered HTML -- `npm install` once from the repo
#     root); this just wires it into `pytest validation/`.
# ──────────────────────────────────────────────────

class TestManualLabelDomSync:
    def test_threshold_controls_update_every_mirrored_copy(self):
        if not (ROOT / "node_modules" / "linkedom").is_dir():
            pytest.skip("node_modules/linkedom not installed -- run `npm install` from the repo root")
        result = subprocess.run(
            ["node", str(ROOT / "validation" / "test_manual_label_dom_sync.mjs")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            "validation/test_manual_label_dom_sync.mjs failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ──────────────────────────────────────────────────
# 18. VQ loader must not build the full Float32 mosaic
#     downloadVectorDataVq used to allocate one Float32Array of
#     outH*outW*128*4 bytes (~2 GB on a national-park-scale viewport) purely
#     to re-quantise it back to uint8 -- the "Array buffer allocation failed"
#     crash. reconstructQuantisedMosaic does the same result with a dim-sized
#     scratch. validation/test_vq_quantised_mosaic.mjs proves byte-parity;
#     this locks in that the giant allocation doesn't creep back.
# ──────────────────────────────────────────────────

class TestVqQuantisedMosaic:
    def test_quantise_on_reconstruct_is_byte_identical_to_old_path(self):
        result = subprocess.run(
            ["node", str(ROOT / "validation" / "test_vq_quantised_mosaic.mjs")],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            "validation/test_vq_quantised_mosaic.mjs failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    def test_vectors_js_does_not_call_reconstruct_float_mosaic(self):
        src = (ROOT / "public" / "js" / "vectors.js").read_text()
        assert "reconstructFloatMosaic(" not in src and "reconstructFloatMosaic }" not in src, (
            "downloadVectorDataVq must not call reconstructFloatMosaic -- that "
            "builds the full Float32 mosaic (outH*outW*128*4 ~= 2 GB on a large "
            "viewport -- 'Array buffer allocation failed'). Use "
            "reconstructQuantisedMosaic. reconstructFloatMosaic stays in "
            "vq_reconstruct.js for postcard.html's small-crop path only."
        )

    def test_full_float_mosaic_alloc_is_gone_from_the_vq_loader(self):
        src = (ROOT / "public" / "js" / "vectors.js").read_text()
        vq_start = src.index("async function downloadVectorDataVq")
        vq_body = src[vq_start:src.index("\nasync function downloadVectorData(", vq_start)]
        assert "new Float32Array(numPixels" not in vq_body and "Float32Array(outH" not in vq_body, (
            "downloadVectorDataVq allocates a Float32Array sized on the pixel "
            "count again -- that is the OOM this change removed."
        )
