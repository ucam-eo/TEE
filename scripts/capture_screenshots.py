#!/usr/bin/env python3
"""Capture UI close-up screenshots for the user guide.

Drives http://localhost:8001/viewer.html?viewport=Eddington with headless
Chromium and writes element-level PNGs to public/images/.

Run:
    venv/bin/python scripts/capture_screenshots.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8001"
VIEWPORT = "Eddington"
OUT_DIR = Path(__file__).parent.parent / "public" / "images"


def shot(page, selector, filename, description):
    """Screenshot a single DOM element."""
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=10_000)
    path = OUT_DIR / filename
    locator.screenshot(path=str(path))
    print(f"  {filename:30s}  {description}")


def wait_for_vectors_ready(page, timeout_ms=60_000):
    """Wait until vectors are downloaded so the UI is in its 'ready' state."""
    page.wait_for_function(
        "() => window.viewportStatus && window.viewportStatus.vectors_downloaded === true",
        timeout=timeout_ms,
    )


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
        )
        page = context.new_page()

        url = f"{BASE_URL}/viewer.html?viewport={VIEWPORT}"
        print(f"Loading {url}")
        page.goto(url, wait_until="networkidle")

        print("Waiting for vectors to download...")
        wait_for_vectors_ready(page)
        # Give the UI a moment to settle (panels, overlays, fonts)
        page.wait_for_timeout(1500)

        print("\nCapturing Explore-mode UI elements:")
        shot(page, "#controls", "ui_header_bar.png",
             "Full top toolbar (mode, year, similarity, buttons)")
        shot(page, "#panel-layout-select", "ui_layout_dropdown.png",
             "Mode selector dropdown")
        shot(page, "#similarity-controls", "ui_similarity_slider.png",
             "Similarity threshold slider")
        shot(page, "#satellite-source-selector", "ui_satellite_source.png",
             "Satellite source selector (Panel 2)")

        # Switch to labelling mode to access the schema/export/import/share toolbar
        print("\nSwitching to labelling mode...")
        page.evaluate("window.setPanelLayout('labelling')")
        page.wait_for_timeout(1000)

        print("\nCapturing Labelling-mode UI elements:")
        shot(page, ".labelling-toolbar", "ui_labelling_toolbar.png",
             "Labelling toolbar (Schema/Export/Import/Share)")

        # Open the schema dropdown so it's visible for the screenshot
        page.evaluate("window.toggleSchemaDropdown()")
        page.wait_for_timeout(300)
        # Capture a box around both the button and its open menu
        shot(page, "#schema-dropdown-menu", "ui_schema_menu.png",
             "Schema dropdown (UKHab, HOTW, EUNIS, Custom)")
        # Close it
        page.evaluate("window.toggleSchemaDropdown()")
        page.wait_for_timeout(200)

        shot(page, "#panel6-label-view", "ui_panel6_labelling.png",
             "Panel 6 in labelling mode (K-means controls + label list)")
        shot(page, "#seg-controls", "ui_seg_controls.png",
             "K-means segmentation controls (k slider + Segment)")

        # Open the export dropdown
        try:
            page.evaluate(
                "document.getElementById('labelling-export-btn').click()"
            )
            page.wait_for_timeout(300)
            # The export menu is a floating dropdown — find it dynamically
            dropdown = page.locator(".export-dropdown, #export-menu").first
            if dropdown.count() > 0 and dropdown.is_visible():
                dropdown.screenshot(path=str(OUT_DIR / "ui_export_menu.png"))
                print(f"  ui_export_menu.png              Export format menu")
        except Exception as e:
            print(f"  (skipped ui_export_menu.png: {e})")

        browser.close()
    print("\nDone. Screenshots written to:", OUT_DIR)


if __name__ == "__main__":
    main()
