"""
Static validation tests for the label export/import vectorization pipeline.

Verifies that labels.js contains the correct structure for:
- d3-contour vectorization (pixel_coords → polygons)
- Import grouping (points grouped by name → single label with pixel_coords)
- Panel 6 mirror on empty state (last-label-delete bug fix)
- JSZip extraction in shapefile import
- Export -> import round trip (all shapefile layers, MultiPolygons, holes,
  ring winding, one save/render per import); the behaviour itself is checked
  in validation/test_label_import_roundtrip.mjs

Run:  cd /Users/skeshav/blore && venv/bin/pytest validation/test_label_export_import.py -v
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LABELS_JS = ROOT / "public" / "js" / "labels.js"


@pytest.fixture(scope="module")
def js():
    return LABELS_JS.read_text()


def function_source(js, name):
    """Source of a top-level function in labels.js, by brace matching."""
    start = js.index(f"function {name}(")
    depth, i = 0, js.index("{", js.index(")", start))
    while True:
        depth += {"{": 1, "}": -1}.get(js[i], 0)
        if depth == 0:
            return js[start:i + 1]
        i += 1


# ────────────────────────────────────────────
# 1. Vectorization pipeline (export)
# ────────────────────────────────────────────

class TestVectorizationExport:
    def test_d3_contour_loader_exists(self, js):
        """ensureD3Contour loads d3-array then d3-contour from CDN."""
        assert "async function ensureD3Contour()" in js
        assert "d3-array" in js
        assert "d3-contour" in js

    def test_vectorize_pixel_coords_function(self, js):
        """vectorizePixelCoords builds a binary mask and calls d3.contours."""
        assert "function vectorizePixelCoords(pixelCoords, gt)" in js
        assert "d3.contours()" in js
        assert ".smooth(false)" in js
        assert ".thresholds([0.5])" in js

    def test_vectorize_builds_bordered_mask(self, js):
        """Mask has a 1-pixel border so contours close at tile edges."""
        assert "maxX - minX + 3" in js
        assert "maxY - minY + 3" in js

    def test_vectorize_converts_to_geo_coords(self, js):
        """Local grid coords are mapped to geographic via geotransform."""
        assert "gt.c + (lx + ox) * gt.a" in js
        assert "gt.f + (ly + oy) * gt.e" in js

    def test_expand_label_to_features_function(self, js):
        """expandLabelToFeatures handles pixel_coords, polygon, and point labels."""
        assert "function expandLabelToFeatures(label)" in js
        # pixel_coords path
        assert "vectorizePixelCoords(label.pixel_coords, gt)" in js
        # polygon path
        assert 'type: \'Polygon\'' in js or "type: 'Polygon'" in js
        # point fallback
        assert 'type: \'Point\'' in js or "type: 'Point'" in js

    def test_export_paths_call_ensure_d3(self, js):
        """All three export paths load d3-contour before expanding labels."""
        # Find all calls to ensureD3Contour — should be in geojson export,
        # shapefile export, and buildShapefileZip
        calls = [m.start() for m in re.finditer(r"await ensureD3Contour\(\)", js)]
        assert len(calls) >= 3, (
            f"Expected ≥3 ensureD3Contour() calls (geojson, shapefile, share), found {len(calls)}"
        )

    def test_no_expand_label_to_points(self, js):
        """Old expandLabelToPoints function must not exist (replaced by expandLabelToFeatures)."""
        assert "expandLabelToPoints" not in js


# ────────────────────────────────────────────
# 2. Import grouping (import)
# ────────────────────────────────────────────

class TestImportGrouping:
    def test_import_groups_points_by_name(self, js):
        """importGeoJSON groups Point features by name into a Map."""
        assert "pointGroups" in js
        assert "pointGroups.set(name" in js or "pointGroups.has(name)" in js

    def test_import_each_point_is_its_own_label(self, js):
        """Every imported point becomes its own point label, like a hand-placed
        pin, so the similarity slider works for multi-point classes. (Points of
        one class used to be merged into a single frozen centroid label with a
        pixel_coords snapshot, which disabled the slider -- changed 8260689.)"""
        body = function_source(js, "importGeoJSON")
        loop = body[body.index("for (const [name, group] of pointGroups)"):]
        loop = loop[:loop.index("// Import polygon features")]
        assert "for (const { lon, lat } of group.points)" in loop
        assert "type: 'point'" in loop
        assert "pushManualLabel(entry)" in loop
        assert "pixel_coords" not in loop
        assert "centroid" not in loop

    def test_import_handles_polygons(self, js):
        """Polygon features are still imported and rasterized."""
        assert "polygonFeatures" in js
        assert "rasterizePolygon(pixVerts, polygonHolesToPixels(entry, gt))" in js


# ────────────────────────────────────────────
# 3. Shapefile import uses JSZip
# ────────────────────────────────────────────

class TestShapefileImport:
    def test_jszip_loaded(self, js):
        """importShapefile loads JSZip to extract .shp/.dbf from zip."""
        assert "JSZip" in js
        assert "jszip" in js  # CDN URL

    def test_extracts_shp_and_dbf(self, js):
        """Reads every .shp layer in the zip, each with the .dbf of the same
        name: shp-write exports one layer per geometry type, so reading only
        the first one dropped all polygons of an export that also had a pin."""
        body = function_source(js, "importShapefile")
        assert ".filter(n => n.toLowerCase().endsWith('.shp'))" in body
        assert ".find(n => n.toLowerCase().endsWith('.shp'))" not in body
        assert "for (const shpName of shpNames)" in body
        assert "+ '.dbf'" in body

    def test_passes_buffers_to_shapefile_open(self, js):
        """Passes extracted ArrayBuffers (not raw zip) to shapefile.open."""
        assert "shapefile.open(shpBuf, dbfBuf)" in js


# ────────────────────────────────────────────
# 3b. Export -> import round trip
# ────────────────────────────────────────────

class TestRoundTrip:
    def test_import_handles_multipolygons(self, js):
        """MultiPolygon features are imported (they used to be skipped), one
        label per polygon."""
        body = function_source(js, "importGeoJSON")
        assert "geom.type === 'Polygon' || geom.type === 'MultiPolygon'" in body
        assert "_polygonParts(geom)" in body

    def test_holes_are_rasterized_out(self, js):
        """rasterizePolygon takes the holes of a polygon and every caller that
        rasterises a stored label passes them."""
        assert "function rasterizePolygon(pixelVertices, holes = [])" in js
        for caller in ("rebuildClassOverlay", "computeManualClassification", "importGeoJSON"):
            assert "polygonHolesToPixels(" in function_source(js, caller), caller

    def test_holes_are_exported(self, js):
        """Polygon labels are exported with their holes (GeoJSON / Shapefile
        rings, and the JSON (full) export)."""
        assert "[label.vertices, ...(label.holes || [])]" in function_source(js, "expandLabelToFeatures")
        assert "holes: l.holes || null" in function_source(js, "doExportManualLabels")

    def test_shapefile_exports_orient_rings(self, js):
        """Both shapefile writers orient the rings (outer clockwise, holes
        counter-clockwise) before shp-write, which writes them as given."""
        assert "function orientRingsForShapefile(features)" in js
        for writer in ("exportManualLabelsShapefile", "buildShapefileZip"):
            body = function_source(js, writer)
            assert "orientRingsForShapefile(" in body, writer
            assert body.index("orientRingsForShapefile(") < body.index("shpwrite.zip("), writer

    def test_imports_save_and_render_once(self, js):
        """Imports add their labels with pushManualLabel and save / render
        once at the end; addManualLabel per label made import time quadratic."""
        assert "function pushManualLabel(entry)" in js
        for importer in ("importGeoJSON", "importLabelsJSON"):
            body = function_source(js, importer)
            assert "addManualLabel(" not in body.replace("pushManualLabel(", ""), importer
            assert "pushManualLabel(entry)" in body, importer
            assert body.count("saveManualLabelsToStorage()") == 1, importer
            assert body.count("renderManualLabelsList()") == 1, importer

    def test_round_trip_behaviour(self):
        """validation/test_label_import_roundtrip.mjs runs the import / export
        functions on polygons whose pixel counts can be checked by hand; this
        wires it into `pytest validation/`."""
        result = subprocess.run(
            ["node", str(ROOT / "validation" / "test_label_import_roundtrip.mjs")],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            "validation/test_label_import_roundtrip.mjs failed:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ────────────────────────────────────────────
# 4. Panel 6 mirror on empty state
# ────────────────────────────────────────────

class TestPanel6EmptyMirror:
    def test_panel6_updated_on_empty(self, js):
        """When manualLabels is empty, panel6-labels-list is also cleared."""
        # The early return path must update panel6
        # Find the empty-state block and verify it references panel6-labels-list
        empty_block = re.search(
            r"if\s*\(manualLabels\.length\s*===\s*0\)\s*\{(.*?)\n    \}",
            js, re.DOTALL
        )
        assert empty_block, "Empty-state guard not found in renderManualLabelsList"
        block = empty_block.group(1)
        assert "panel6-labels-list" in block, (
            "Panel 6 mirror missing from the manualLabels.length===0 early return"
        )


# ────────────────────────────────────────────
# 5. Export format consistency
# ────────────────────────────────────────────

class TestExportConsistency:
    def test_all_exports_use_expand_label_to_features(self, js):
        """Export paths vectorize labels rather than doing per-point
        expansion. The manual-labelling menu (geojson / shapefile / kml)
        now goes through manualExportFeatures(), which returns either the
        per-label features (expandLabelToFeatures) or, when "Classified
        pixels" is ticked, classifiedPixelFeatures(). The combined
        Share/buildShapefileZip path still calls expandLabelToFeatures
        directly."""
        assert "function manualExportFeatures(" in js
        assert "manualLabels.flatMap(l => expandLabelToFeatures(l))" in js, (
            "manualExportFeatures must still fall back to expandLabelToFeatures"
        )
        menu_exports = re.findall(r"const features = manualExportFeatures\(\)", js)
        assert len(menu_exports) >= 2, (
            f"Expected geojson + kml (+shapefile) to call manualExportFeatures(), "
            f"found {len(menu_exports)}"
        )

    def test_json_export_unchanged(self, js):
        """JSON (full) export still includes embeddings and metadata, not vectorized."""
        # JSON export should NOT call expandLabelToFeatures — it exports raw label data
        json_block = re.search(
            r"if\s*\(format\s*===\s*'json'\)\s*\{(.*?)}\s*else\s*if",
            js, re.DOTALL
        )
        assert json_block, "JSON export block not found"
        assert "expandLabelToFeatures" not in json_block.group(1), (
            "JSON export should not vectorize — it exports raw label metadata"
        )

    def test_json_export_keeps_cluster_pixels(self, js):
        """JSON (full) export carries the pixels of promoted auto-label clusters
        and the grid they index into; the import restores them only there."""
        json_block = re.search(
            r"if\s*\(format\s*===\s*'json'\)\s*\{(.*?)}\s*else\s*if",
            js, re.DOTALL
        ).group(1)
        assert "pixel_coords: l.pixel_coords || null" in json_block
        assert "geotransform:" in json_block
        assert "importLabelsJSON(data.labels, data.geotransform)" in js
        assert "if (sameGrid && l.pixel_coords) entry.pixel_coords = l.pixel_coords;" in function_source(js, "importLabelsJSON")
