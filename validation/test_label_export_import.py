"""
Static validation tests for the label export/import vectorization pipeline.

Verifies that labels.js contains the correct structure for:
- d3-contour vectorization (pixel_coords → polygons)
- Import grouping (points grouped by name → single label with pixel_coords)
- Panel 6 mirror on empty state (last-label-delete bug fix)
- JSZip extraction in shapefile import

Run:  cd /Users/skeshav/blore && venv/bin/pytest validation/test_label_export_import.py -v
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LABELS_JS = ROOT / "public" / "js" / "labels.js"


@pytest.fixture(scope="module")
def js():
    return LABELS_JS.read_text()


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

    def test_import_multi_point_creates_pixel_coords(self, js):
        """Multiple points with same name create a single label with pixel_coords."""
        # The import builds pixel_coords array from grouped points
        assert "pixel_coords.push(px, py)" in js

    def test_import_computes_centroid_embedding(self, js):
        """Import computes centroid embedding from grouped pixel embeddings."""
        # Centroid averaging
        assert "centroid[d] /= validCount" in js

    def test_import_computes_threshold(self, js):
        """Import computes max L2 distance from centroid as threshold."""
        assert "maxDistSq" in js
        assert "Math.sqrt(maxDistSq)" in js

    def test_import_single_point_stays_point(self, js):
        """A name with only one point is imported as a simple point label."""
        assert "group.points.length === 1" in js

    def test_import_handles_polygons(self, js):
        """Polygon features are still imported and rasterized."""
        assert "polygonFeatures" in js
        assert "rasterizePolygon(pixVerts)" in js


# ────────────────────────────────────────────
# 3. Shapefile import uses JSZip
# ────────────────────────────────────────────

class TestShapefileImport:
    def test_jszip_loaded(self, js):
        """importShapefile loads JSZip to extract .shp/.dbf from zip."""
        assert "JSZip" in js
        assert "jszip" in js  # CDN URL

    def test_extracts_shp_and_dbf(self, js):
        """Finds .shp and .dbf files inside the zip."""
        assert ".endsWith('.shp')" in js
        assert ".endsWith('.dbf')" in js

    def test_passes_buffers_to_shapefile_open(self, js):
        """Passes extracted ArrayBuffers (not raw zip) to shapefile.open."""
        assert "shapefile.open(shpBuf, dbfBuf)" in js


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
        """All export paths use expandLabelToFeatures (not per-point expansion)."""
        calls = re.findall(r"manualLabels\.flatMap\(l\s*=>\s*expandLabelToFeatures\(l\)\)", js)
        assert len(calls) >= 3, (
            f"Expected ≥3 expandLabelToFeatures calls (geojson, shapefile, share), found {len(calls)}"
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
