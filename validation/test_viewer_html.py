"""
Structural validation tests for public/viewer.html

Run:  cd /Users/skeshav/blore && venv/bin/pytest validation/ -v

These tests parse the HTML statically (no browser needed) and verify that
Phase 1 (Explore rename, manual label mode, classification overlay) and
Phase 2 (schema system, polygon drawing, toolbar) haven't regressed.
"""

import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
VIEWER = ROOT / "public" / "viewer.html"
JS_DIR = ROOT / "public" / "js"


@pytest.fixture(scope="module")
def html():
    return VIEWER.read_text()


@pytest.fixture(scope="module")
def soup(html):
    return BeautifulSoup(html, "html.parser")


@pytest.fixture(scope="module")
def script_text(html):
    """All JS: inline <script> blocks + extracted ES module files."""
    parts = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    combined = "\n".join(parts)
    if JS_DIR.is_dir():
        for js_file in sorted(JS_DIR.glob("*.js")):
            combined += "\n" + js_file.read_text()
    return combined


# ────────────────────────────────────────────
# 1A  Explore rename — no residual "simple"
# ────────────────────────────────────────────

class TestExploreRename:
    def test_no_mode_simple_in_css(self, html):
        """CSS class .mode-simple must not appear anywhere."""
        assert "mode-simple" not in html

    def test_no_simple_string_in_js(self, script_text):
        """JS should not contain 'simple' as a mode string literal."""
        # Match 'simple' or "simple" but not inside comments or prose words
        literals = re.findall(r"""['"]simple['"]""", script_text)
        assert literals == [], f"Found leftover 'simple' literals: {literals}"

    def test_dropdown_has_explore(self, soup):
        sel = soup.find("select", id="panel-layout-select")
        assert sel, "panel-layout-select dropdown missing"
        opts = [o.get("value") for o in sel.find_all("option")]
        assert "explore" in opts
        assert "simple" not in opts

    def test_explore_css_rules_exist(self, html):
        assert "body.mode-explore #label-controls-bar" in html

    def test_panel5_layer_rules_has_explore(self, script_text):
        assert "'explore'" in script_text
        # Find the definition block (contains 'explore':) rather than first reference
        assert "'explore'" in script_text.split("PANEL5_LAYER_RULES = {")[1][:300]

    def test_default_mode_is_explore(self, script_text):
        m = re.search(r"let currentPanelMode\s*=\s*'(\w+)'", script_text)
        assert m and m.group(1) == "explore"

    def test_valid_modes_array_has_explore(self, script_text):
        m = re.search(r"const validModes\s*=\s*\[(.*?)\]", script_text)
        assert m
        assert "'explore'" in m.group(1)
        assert "'simple'" not in m.group(1)


# ────────────────────────────────────────────
# 1A  Clear button
# ────────────────────────────────────────────

class TestClearButton:
    def test_clear_button_exists(self, soup):
        btn = soup.find("button", id="clear-similarity-btn")
        assert btn, "Clear similarity button missing"

    def test_clear_button_in_similarity_controls(self, soup):
        ctrl = soup.find("div", id="similarity-controls")
        btn = ctrl.find("button", id="clear-similarity-btn")
        assert btn, "Clear button should be inside #similarity-controls"


# ────────────────────────────────────────────
# 1B  Panel 6 mode dropdown
# ────────────────────────────────────────────

class TestPanel6ModeDropdown:
    def test_label_mode_select_exists(self, soup):
        sel = soup.find("select", id="label-mode-select")
        assert sel, "label-mode-select missing"
        opts = {o.get("value") for o in sel.find_all("option")}
        assert opts == {"autolabel", "manual"}

    def test_autolabel_view_exists(self, soup):
        div = soup.find("div", id="panel6-autolabel-view")
        assert div, "panel6-autolabel-view wrapper missing"

    def test_manual_view_exists(self, soup):
        div = soup.find("div", id="panel6-manual-view")
        assert div, "panel6-manual-view wrapper missing"

    def test_manual_view_hidden_by_default(self, soup):
        div = soup.find("div", id="panel6-manual-view")
        style = div.get("style", "")
        assert "display: none" in style or "display:none" in style

    def test_autolabel_contains_seg_controls(self, soup):
        auto = soup.find("div", id="panel6-autolabel-view")
        seg = auto.find("div", id="seg-controls")
        assert seg, "seg-controls should be inside autolabel view"

    def test_autolabel_contains_seg_list(self, soup):
        auto = soup.find("div", id="panel6-autolabel-view")
        sl = auto.find("div", id="panel6-seg-list")
        assert sl, "panel6-seg-list should be inside autolabel view"

    def test_autolabel_contains_labels_list(self, soup):
        auto = soup.find("div", id="panel6-autolabel-view")
        ll = auto.find("div", id="panel6-labels-list")
        assert ll, "panel6-labels-list should be inside autolabel view"


# ────────────────────────────────────────────
# 1C  Manual label mode UI elements
# ────────────────────────────────────────────

class TestManualLabelUI:
    def test_label_name_input(self, soup):
        inp = soup.find("input", id="manual-label-name")
        assert inp, "manual-label-name input missing"
        assert inp.get("type") == "text"

    def test_color_picker(self, soup):
        inp = soup.find("input", id="manual-label-color")
        assert inp, "manual-label-color input missing"
        assert inp.get("type") == "color"

    def test_swatch(self, soup):
        div = soup.find("div", id="manual-label-swatch")
        assert div, "manual-label-swatch missing"

    def test_set_button(self, soup):
        btn = soup.find("button", id="manual-label-set-btn")
        assert btn, "manual-label-set-btn missing"
        assert "setCurrentManualLabel" in btn.get("onclick", "")

    def test_active_label_display(self, soup):
        div = soup.find("div", id="manual-active-label")
        assert div, "manual-active-label display missing"
        assert "display: none" in div.get("style", "")

    def test_manual_labels_list(self, soup):
        div = soup.find("div", id="manual-labels-list")
        assert div, "manual-labels-list missing"

    def test_manual_view_contains_all_ui(self, soup):
        mv = soup.find("div", id="panel6-manual-view")
        assert mv.find("div", id="manual-label-selector")
        assert mv.find("div", id="manual-active-label")
        assert mv.find("div", id="manual-labels-list")


# ────────────────────────────────────────────
# 1C/1D  Required JS functions exist
# ────────────────────────────────────────────

class TestRequiredFunctions:
    FUNCTIONS = [
        "setLabelMode",
        "setCurrentManualLabel",
        "updateManualLabelColor",
        "restoreManualLabelState",
        "saveManualLabelsToStorage",
        "addManualLabel",
        "removeManualLabel",
        "renderManualLabelsList",
        "rebuildManualOverlays",
        "handleManualSimilaritySearch",
        "handleManualPinDrop",
        "triggerManualClassification",
        "renderManualClassification",
        # Phase 3: Class-based grouping
        "getClassLabels",
        "getClassThreshold",
        "localSearchSimilarMulti",
        "rebuildClassOverlay",
        "toggleClassExpand",
        "toggleClassVisibility",
        "updateManualClassThreshold",
        "_applyClassThreshold",
        # Phase 2A: Schema system
        "loadSchema",
        "loadCustomSchema",
        "parseTabIndentedSchema",
        "renderSchemaSelector",
        "selectSchemaLabel",
        "filterSchemaTree",
        # Phase 2B: Polygon drawing
        "startPolygonDrawing",
        "cancelPolygonDrawing",
        "handlePolygonComplete",
        "pointInPolygon",
        "rasterizePolygon",
    ]

    @pytest.mark.parametrize("fname", FUNCTIONS)
    def test_function_defined(self, script_text, fname):
        pattern = rf"(?:async\s+)?function\s+{fname}\s*\("
        assert re.search(pattern, script_text), f"Function {fname} not defined"


# ────────────────────────────────────────────
# 1E  State variables
# ────────────────────────────────────────────

class TestStateVariables:
    VARS = [
        ("manualLabels", r"let manualLabels\s*=\s*\[\]"),
        ("currentManualLabel", r"let currentManualLabel\s*=\s*null"),
        ("labelMode", r"let labelMode\s*=\s*'autolabel'"),
        ("manualClassifyOverlay", r"let manualClassifyOverlay\s*=\s*null"),
        ("manualLabelIdCounter", r"let manualLabelIdCounter\s*=\s*0"),
        ("manualClassifyDebounceTimer", r"let manualClassifyDebounceTimer\s*=\s*null"),
        ("manualClassOverlays", r"let manualClassOverlays\s*=\s*\{\}"),
        ("collapsedClasses", r"let collapsedClasses\s*=\s*new Set"),
    ]

    @pytest.mark.parametrize("name,pattern", VARS, ids=[v[0] for v in VARS])
    def test_var_declared(self, script_text, name, pattern):
        assert re.search(pattern, script_text), f"State variable {name} not found"


# ────────────────────────────────────────────
# DirectCanvasLayer color support
# ────────────────────────────────────────────

class TestDirectCanvasLayer:
    def test_constructor_accepts_color(self, script_text):
        assert re.search(r"constructor\(matches,\s*map,\s*color\)", script_text)

    def test_color_stored(self, script_text):
        assert "this._color = color || null" in script_text

    def test_default_yellow_fallback(self, script_text):
        assert "cr = 255, cg = 255, cb = 0" in script_text


# ────────────────────────────────────────────
# Click handler wiring
# ────────────────────────────────────────────

class TestClickHandlers:
    def test_ctrl_click_detection(self, script_text):
        assert "e.originalEvent.ctrlKey || e.originalEvent.metaKey" in script_text

    def test_ctrl_click_calls_pin_drop(self, script_text):
        assert "handleManualPinDrop(lat, lon)" in script_text

    def test_dblclick_dispatches_pin_in_manual(self, script_text):
        # handleSimilaritySearch should drop a pin in manual label mode
        assert "handleManualPinDrop(lat, lon)" in script_text

    def test_dblclick_checks_label_mode(self, script_text):
        # handleSimilaritySearch should check labelMode === 'manual'
        assert "labelMode === 'manual'" in script_text


# ────────────────────────────────────────────
# Classification overlay
# ────────────────────────────────────────────

class TestClassificationOverlay:
    def test_debounce_300ms(self, script_text):
        m = re.search(r"setTimeout\(renderManualClassification,\s*(\d+)\)", script_text)
        assert m, "Debounced renderManualClassification not found"
        assert int(m.group(1)) == 300

    def test_uses_image_overlay(self, script_text):
        # renderManualClassification should create an L.imageOverlay
        fn_body = script_text.split("function renderManualClassification")[1][:10000]
        assert "L.imageOverlay" in fn_body

    def test_pixelated_rendering(self, script_text):
        fn_body = script_text.split("function renderManualClassification")[1][:10000]
        assert "imageRendering" in fn_body
        assert "pixelated" in fn_body

    def test_nearest_centroid_loop(self, script_text):
        fn_body = script_text.split("function renderManualClassification")[1][:10000]
        # Should iterate over all pixels
        assert "for (let i = 0; i < N; i++)" in fn_body
        # Should compute distance to centroids
        assert "distSq" in fn_body


# ────────────────────────────────────────────
# Existing elements still present (non-regression)
# ────────────────────────────────────────────

class TestExistingElements:
    """Verify that pre-existing functionality wasn't broken."""

    IDS = [
        "panel-layout-select",
        "similarity-threshold",
        "threshold-display",
        "label-controls-bar",
        "similarity-controls",
        "seg-run-btn",
        "seg-k-input",
        "seg-clear-btn",
        "panel6-seg-list",
        "panel6-labels-list",
        "panel6-promote-all-btn",
        "panel6-toggle-all-btn",
        "panel6-label-view",
        "panel6-header-text",
        "map-container",
        "map-embedding",
        "map-embedding2",
        "map-osm",
        "help-popup",
        # Phase 2C: Toolbar buttons
        "schema-dropdown-btn",
        "schema-dropdown-menu",
        "labelling-export-btn",
        "labelling-import-btn",
        "labelling-share-btn",
        # Panel 5 classify button
        "manual-classify-btn",
    ]

    @pytest.mark.parametrize("elem_id", IDS)
    def test_element_exists(self, soup, elem_id):
        assert soup.find(id=elem_id), f"Element #{elem_id} missing from DOM"

    EXISTING_FUNCTIONS = [
        "setPanelLayout",
        "restorePanelMode",
        "updateThresholdDisplay",
        "clearExplorerResults",
        "clearCrossPanelMarkers",
        "handleUnifiedClick",
        "handleSimilaritySearch",
        "explorerClick",
        "localExtract",
        "localSearchSimilar",
        "calculateAverageEmbedding",
        "showSegmentationOverlay",
        "runKMeans",
        "buildGridLookup",
        "gridLookupIndex",
    ]

    @pytest.mark.parametrize("fname", EXISTING_FUNCTIONS)
    def test_function_still_exists(self, script_text, fname):
        pattern = rf"(?:async\s+)?function\s+{fname}\s*\("
        assert re.search(pattern, script_text), f"Existing function {fname} was removed"


# ────────────────────────────────────────────
# Panel mode CSS class consistency
# ────────────────────────────────────────────

class TestModeClasses:
    MODES = ["explore", "change-detection", "labelling", "validation"]

    def test_classlist_remove_has_all_modes(self, script_text):
        """container.classList.remove() must list all four modes."""
        pattern = r"classList\.remove\((.*?)\)"
        matches = re.findall(pattern, script_text)
        for m in matches:
            if "mode-explore" in m:
                for mode in self.MODES:
                    assert f"mode-{mode}" in m, f"mode-{mode} missing from classList.remove"

    def test_panel5_rules_has_all_modes(self, script_text):
        block = script_text.split("PANEL5_LAYER_RULES = {")[1][:500]
        for mode in self.MODES:
            assert f"'{mode}'" in block

    def test_panel_layout_has_all_modes(self):
        # PANEL_LAYOUT declarative table must have all modes (in maps.js)
        from pathlib import Path
        maps_js = (Path(__file__).parent.parent / "public" / "js" / "maps.js").read_text()
        idx = maps_js.find("PANEL_LAYOUT")
        assert idx >= 0, "PANEL_LAYOUT table not found in maps.js"
        layout_block = maps_js[idx:idx+2000]
        for mode in self.MODES:
            assert f"'{mode}'" in layout_block, f"PANEL_LAYOUT missing mode '{mode}'"


# ────────────────────────────────────────────
# 2C  Toolbar buttons
# ────────────────────────────────────────────

class TestToolbarButtons:
    def test_labelling_toolbar_exists(self, soup):
        div = soup.find("div", class_="labelling-toolbar")
        assert div, "div.labelling-toolbar not found"

    def test_schema_dropdown_btn(self, soup):
        btn = soup.find("button", id="schema-dropdown-btn")
        assert btn, "#schema-dropdown-btn missing"

    def test_labelling_export_btn(self, soup):
        btn = soup.find("button", id="labelling-export-btn")
        assert btn, "#labelling-export-btn missing"

    def test_labelling_import_btn(self, soup):
        btn = soup.find("button", id="labelling-import-btn")
        assert btn, "#labelling-import-btn missing"

    def test_labelling_share_btn_disabled(self, soup):
        btn = soup.find("button", id="labelling-share-btn")
        assert btn, "#labelling-share-btn missing"
        assert "toggleShareDropdown" in str(btn), "Share button should call toggleShareDropdown"

    def test_toolbar_hidden_css(self, html):
        assert "body:not(.mode-labelling) .labelling-toolbar" in html


# ────────────────────────────────────────────
# 2A  Schema system
# ────────────────────────────────────────────

class TestSchemaSystem:
    def test_schema_state_vars(self, script_text):
        assert re.search(r"let activeSchema\s*=\s*null", script_text)
        assert re.search(r"let activeSchemaMode\s*=\s*'none'", script_text)

    def test_load_schema_function(self, script_text):
        assert re.search(r"(?:async\s+)?function\s+loadSchema\s*\(", script_text)

    def test_load_custom_schema_function(self, script_text):
        assert re.search(r"function\s+loadCustomSchema\s*\(", script_text)

    def test_parse_tab_indented_function(self, script_text):
        assert re.search(r"function\s+parseTabIndentedSchema\s*\(", script_text)

    def test_render_schema_selector_function(self, script_text):
        assert re.search(r"function\s+renderSchemaSelector\s*\(", script_text)

    def test_select_schema_label_function(self, script_text):
        assert re.search(r"function\s+selectSchemaLabel\s*\(", script_text)

    def test_filter_schema_tree_function(self, script_text):
        assert re.search(r"function\s+filterSchemaTree\s*\(", script_text)

    def test_schema_dropdown_menu(self, soup):
        menu = soup.find("div", id="schema-dropdown-menu")
        assert menu, "#schema-dropdown-menu missing"


# ────────────────────────────────────────────
# 2B  Polygon drawing
# ────────────────────────────────────────────

class TestPolygonDrawing:
    def test_leaflet_draw_cdn(self, html):
        assert "leaflet-draw" in html
        assert "leaflet.draw.css" in html or "leaflet-draw" in html
        assert "leaflet.draw.js" in html

    def test_polygon_state_vars(self, script_text):
        assert re.search(r"let polygonDrawHandler\s*=\s*null", script_text)
        assert re.search(r"let isPolygonDrawing\s*=\s*false", script_text)

    def test_start_polygon_function(self, script_text):
        assert re.search(r"function\s+startPolygonDrawing\s*\(", script_text)

    def test_cancel_polygon_function(self, script_text):
        assert re.search(r"function\s+cancelPolygonDrawing\s*\(", script_text)

    def test_handle_polygon_complete(self, script_text):
        assert re.search(r"function\s+handlePolygonComplete\s*\(", script_text)

    def test_point_in_polygon_function(self, script_text):
        assert re.search(r"function\s+pointInPolygon\s*\(", script_text)

    def test_rasterize_polygon_function(self, script_text):
        assert re.search(r"function\s+rasterizePolygon\s*\(", script_text)

    def test_ctrl_dblclick_polygon(self, script_text):
        assert re.search(r"startPolygonDrawing\(", script_text)

    def test_escape_key_cancel(self, script_text):
        assert "'Escape'" in script_text or '"Escape"' in script_text

    def test_draw_toolbar_hidden(self, html):
        assert ".leaflet-draw-toolbar" in html
        assert "display: none" in html


# ────────────────────────────────────────────
# JS syntax check (via Node.js)
# ────────────────────────────────────────────

class TestJSSyntax:
    def test_all_script_blocks_parse(self, html):
        """Extract each <script> block and verify it parses with Node."""
        import subprocess

        blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
        for i, block in enumerate(blocks):
            result = subprocess.run(
                ["node", "-e", f"new Function({repr(block)})"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, (
                f"Script block {i} has JS syntax error:\n{result.stderr[:500]}"
            )


# ────────────────────────────────────────────
# Label sharing
# ────────────────────────────────────────────

class TestLabelSharing:
    def test_share_dropdown_exists(self, html):
        assert 'share-dropdown' in html

    def test_share_privacy_toggle(self, html):
        assert 'share-privacy' in html

    def test_import_dropdown_exists(self, html):
        assert 'import-dropdown' in html

    def test_import_share_badge(self, html):
        assert 'import-share-badge' in html

    def test_submit_share_function(self, script_text):
        assert 'function submitShare' in script_text

    def test_build_shapefile_zip_function(self, script_text):
        assert 'function buildShapefileZip' in script_text

    def test_load_shared_labels_list(self, script_text):
        assert 'function loadSharedLabelsList' in script_text

    def test_import_shared_labels(self, script_text):
        assert 'function importSharedLabels' in script_text


# ────────────────────────────────────────────
# HTML well-formedness
# ────────────────────────────────────────────

# ────────────────────────────────────────────
# Large-area evaluation
# ────────────────────────────────────────────

class TestLargeAreaValidation:
    def test_year_select_exists(self, html):
        assert 'val-year-select' in html

    def test_upload_config_button_exists(self, html):
        assert 'val-upload-config' in html

    def test_generate_config_button_exists(self, html):
        assert 'val-generate-config' in html

    def test_regression_panel_exists(self, html):
        assert 'val-regression-panel' in html

    def test_regression_table_exists(self, html):
        assert 'val-regression-table' in html

    def test_generate_config_function(self, script_text):
        assert 'function generateConfig' in script_text

    def test_run_large_area_function(self, script_text):
        assert 'runLargeAreaEvaluation' in script_text

    def test_results_panel_exists(self, html):
        assert 'val-results-panel' in html

    def test_load_results_file_function(self, script_text):
        assert 'function loadResultsFile' in script_text

    def test_year_select_exists(self, html):
        assert 'val-year-select' in html


class TestHTMLStructure:
    def test_starts_with_doctype(self, html):
        assert html.strip().startswith("<!DOCTYPE html>")

    def test_has_closing_html(self, html):
        assert "</html>" in html

    def test_no_duplicate_ids(self, soup):
        """Every id attribute should be unique."""
        all_ids = [tag["id"] for tag in soup.find_all(id=True)]
        dupes = [x for x in all_ids if all_ids.count(x) > 1]
        assert not dupes, f"Duplicate IDs found: {set(dupes)}"

    def test_panel6_label_view_inside_panel(self, soup):
        """panel6-label-view should be inside a .panel div."""
        pv = soup.find("div", id="panel6-label-view")
        assert pv
        parent = pv.find_parent("div", class_="panel")
        assert parent, "panel6-label-view not inside a .panel container"
