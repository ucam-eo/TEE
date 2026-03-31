# Plan: Sentinel-2 Band Combination Viewer

## Overview

Display Sentinel-2 imagery in panel 1 (OSM panel) as a toggleable overlay.
Users can switch between band combinations and years to visually inspect
habitat characteristics alongside the embedding view.

## Band Combinations

| Name | Bands | Use |
|------|-------|-----|
| True Color | B04, B03, B02 | Standard RGB |
| Color Infrared | B08, B04, B03 | Vegetation health |
| Agriculture | B11, B08, B02 | Crop health |
| Short-wave IR | B12, B8A, B04 | Moisture |
| Geology | B12, B11, B02 | Geological features |

Required S2 bands: B02 (10m), B03 (10m), B04 (10m), B08 (10m), B8A (20m), B11 (20m), B12 (20m).

## Data Source

Microsoft Planetary Computer STAC catalog (`https://planetarycomputer.microsoft.com/api/stac/v1`).
S2 L2A collection, accessed via `odc-stac` or `stackstac`. Token signing via `planetary-computer` library.

## Architecture

Reuses the existing pyramid/tile system with zero changes to `tile_renderer.py`:

```
Planetary Computer → download_s2.py → raw bands .npz
                                          ↓
                                    create_s2_pyramids.py → 5 pyramid sets per year
                                          ↓
                                    pyramids/<viewport>/s2_<year>_<combo>/level_0..5.png
                                          ↓
                                    tiles.py serves at /tiles/<vp>/s2_<year>_<combo>/{z}/{x}/{y}.png
                                          ↓
                                    Leaflet tile layer on panel 1 OSM map
```

The `map_id` in the tile URL becomes `s2_2024_agri` instead of just `2024`.

## UI

Panel 1 header gains controls (visible in explore/change-detection/labelling modes):

```
OpenStreetMap  [✓ S2] [2024 ▾] [Agriculture ▾] [━━━━━○━] opacity
```

- Toggle checkbox: show/hide S2 overlay on the OSM map
- Year dropdown: which S2 year to display
- Band combination dropdown: 5 options
- Opacity slider: blend S2 with OSM tiles

The S2 layer sits on top of OSM tiles. When enabled, users see S2 imagery
overlaid on OSM. No PANEL_LAYOUT changes needed — it's a Leaflet layer, not a panel swap.

## Implementation Phases

### Phase 1: S2 Data Download (~2 days)

**New file: `download_s2.py`**

- Query PC STAC for S2 L2A items matching viewport bbox + year
- Filter by cloud cover (< 10%), build median composite for growing season
- Download bands B02, B03, B04, B08, B8A, B11, B12
- Resample 20m bands (B8A, B11, B12) to 10m grid (nearest-neighbor)
- Save as `~/data/s2/<viewport>/<year>/bands.npz` + `meta.json`

Dependencies: `pystac-client`, `planetary-computer`, `odc-stac`, `rioxarray`

### Phase 2: Pyramid Generation (~1 day)

**New file: `create_s2_pyramids.py`**

For each downloaded year, generate 5 pyramid sets:
1. Extract 3 bands per combination from the stored numpy array
2. Apply `percentile_normalize()` (reused from `process_viewport.py`)
3. Call `write_pyramid_levels()` (reused from `process_viewport.py`)
4. Output to `~/data/pyramids/<viewport>/s2_<year>_<combo>/`

### Phase 3: Tile Serving (~0.5 days)

**Modify: `api/views/tiles.py`**

Expand `_VALID_MAP_IDS` to accept `s2_<year>_<combo>` patterns.
No changes to `tile_renderer.py` — it already handles arbitrary `map_id` directories.

### Phase 4: Frontend (~2 days)

**viewer.html:** Add S2 controls in panel 1 header.

**maps.js:**
- New `s2Layer` variable (Leaflet tile layer, null by default)
- `toggleS2Layer(enabled)` — add/remove layer
- `switchS2Combo(year, combo)` — swap tile layer URL
- Wire dropdowns and opacity slider

**app.js:** Add dependency entry checking S2 pyramid availability.

### Phase 5: S2 Availability API (~0.5 days)

**viewports.py:** Add `s2_available: {year: [combos]}` to `is_ready` response.
Scans `pyramids/<viewport>/s2_*` directories.

### Phase 6: Config + Integration (~0.5 days)

**config.py:** Add `S2_DIR = DATA_DIR / 's2'`.
Optional: trigger S2 download from viewport management UI.

## Storage

| What | Size per viewport per year |
|------|---------------------------|
| Raw bands (7 bands, 10m, .npz) | ~50-100 MB |
| Pyramids per combination (6 levels PNG) | ~2-5 MB |
| All 5 combinations | ~10-25 MB |
| All years (8 years, all combos) | ~80-200 MB |

## Risks

1. **Cloud cover** — single scenes may be cloudy. Mitigation: median composite over growing season.
2. **20m vs 10m bands** — B8A/B11/B12 are 20m. Mitigation: resample to 10m during download.
3. **PC rate limiting** — free API but may throttle. Mitigation: cache aggressively, download once.
4. **Storage growth** — 5 combos × 8 years × N viewports. Mitigation: generate pyramids lazily or on demand.

## Effort

| Phase | Days |
|-------|------|
| S2 data download | 2 |
| Pyramid generation | 1 |
| Tile serving | 0.5 |
| Frontend UI | 2 |
| Availability API | 0.5 |
| Config + integration | 0.5 |
| **Total** | **~6.5 days** |
