# TEE User Guide

A guide to using the Tessera Embeddings Explorer (TEE) web interface.

## Creating a Viewport

A **viewport** is a 5km x 5km geographic area for which TEE downloads and processes Sentinel-2 embeddings.

1. Open the **Viewport Manager** (the home page)
2. Click **+ Create New Viewport**
3. Choose a location using one of three methods:
   - **Search** — type a place name (e.g. "Cambridge") or coordinates (e.g. "52.2, 0.12") in the search bar
   - **Click the map** — a 5km preview box follows the cursor; click to lock it in place
   - **Reposition** — after clicking, click again to move the box
4. Enter a **viewport name** and select which **years** to process (2017-2025)
5. Click **Create** — processing runs in the background with a progress bar
6. The viewer opens automatically when processing completes

**Deleting a viewport:** Click the trash icon next to any viewport in the list. All associated data (mosaics, pyramids, vectors) is cleaned up automatically.

## The Viewer

### 3-Panel Layout (Default)

| Panel | Content |
|-------|---------|
| **OSM** | OpenStreetMap geographic reference |
| **Satellite** | Esri or Google satellite imagery (toggle in header) |
| **Embeddings** | Tessera embedding visualization with year selector |

All three panels are synchronized — panning or zooming one pans/zooms all.

### 6-Panel Layout (Advanced)

Click the **3/6 Panel** toggle in the header to switch to the advanced layout:

| Panel | Content |
|-------|---------|
| 1. **OSM** | OpenStreetMap reference |
| 2. **Satellite** | Satellite imagery with label painting |
| 3. **Embeddings Y1** | First year's embeddings with similarity search |
| 4. **UMAP / PCA** | Dimensionality reduction visualization |
| 5. **Heatmap** | Temporal distance heatmap (Y1 vs Y2) |
| 6. **Embeddings Y2** | Second year's embeddings for comparison |

### Switching Years

Use the **year dropdown** above the embedding panels to switch between processed years. In the 6-panel layout, Y1 and Y2 can be set to different years for temporal comparison.

## Similarity Search

**Double-click** anywhere on the map to trigger a similarity search. TEE extracts the 128-dimensional embedding at that pixel and finds all similar locations across the viewport. All computation runs **locally in your browser** — no data is sent to the server.

### How to Use

1. **Double-click any pixel** on any map panel — similar locations are highlighted across all panels as colored dots
2. Adjust the **similarity slider** to control how similar a match must be (lower = more strict)
3. Click **Save as Label** to name and color-code the current search results

The first time you run a search for a viewport+year, vector data (~20-50MB) is downloaded and cached in your browser's IndexedDB. Subsequent searches are instant.

### Single-Click

A **single click** on any panel places a synchronized marker across all panels, useful for cross-referencing a location between OSM, satellite, and embedding views without triggering a search.

## Labels

Labels are named, colored collections of similar pixels found through similarity search.

### Managing Labels

- **Save** — after a similarity search, click "Save as Label", choose a name and color
- **Toggle visibility** — click a label name to show/hide it
- **Delete** — click the X next to a label
- **Labels persist** across page reloads (stored in browser localStorage)

### Cross-Year Timeline

Click **Timeline** on any saved label to see how its coverage changes across all available years:

- A bar chart shows pixel counts per year
- A percentage change summary compares the first and last years
- Each year's vector data is loaded automatically from cache (or downloaded in background)

### Importing and Exporting Labels

Labels are portable — they use embedding distance rather than coordinates, so they work across different viewports.

- **Import** — use the Import button to load a previously exported JSON file
- **Export** — see Export Options below

## Segmentation (K-Means Clustering)

TEE can automatically segment the viewport into distinct clusters using K-means clustering on the embedding space. Segmentation clusters are **temporary previews** — they appear in a floating panel but are not saved until you promote them.

### Running Segmentation

1. Set the number of clusters using the **k slider** in the toolbar (2–20)
2. Click **Segment** — clustering runs in a Web Worker and results appear as a colored overlay on the heatmap panel
3. The **Segmentation (temporary preview)** panel lists each cluster with its color, pixel count, and percentage

### Promoting Clusters to Labels

Segmentation clusters are temporary — they disappear when you re-segment or clear. To make them permanent:

- **Promote one cluster** — click the **↗** button on a cluster row. It moves from the seg panel to your saved labels.
- **Promote all** — click **Promote All to Labels** to save every cluster at once.
- **Name before promoting** — type a label name in the text field next to a cluster before promoting; otherwise it defaults to "Cluster N".

Promoted labels are fully functional saved labels:
- They have an embedding and source pixel, so they support **Timeline** analysis across years
- They can be **re-matched** on other viewports via import/export
- They appear in the **Labels** panel and persist across page reloads

### Clearing Segmentation

Click **Clear** to remove the seg overlay and panel. This does not affect any already-promoted labels.

## Export Options

The **Export** dropdown in the header provides three formats:

| Format | Description |
|--------|-------------|
| **Labels (JSON)** | Compact metadata for re-importing into TEE |
| **Labels (GeoJSON)** | FeatureCollection with 10m polygons per pixel, compatible with QGIS and other GIS tools. Properties include label name, color, distance, and threshold. |
| **Map (JPG)** | High-resolution satellite image with label overlays and legend, rendered at zoom level 18 |

## PCA / UMAP Visualization (Panel 4)

In the 6-panel layout, Panel 4 shows a 3D projection of the embedding space:

- **PCA** — computed instantly in your browser from the downloaded vectors (no server round-trip). Available as soon as vectors are downloaded.
- **UMAP** — richer structure, computed server-side on first load (~1-2 min)
- Points are colored using satellite RGB values
- Single-click a point to place a marker on all map panels; double-click to trigger a similarity search from that point
- Toggle between PCA and UMAP using the dropdown
- Right-click drag to rotate the 3D view

## Heatmap (Panel 5)

The temporal distance heatmap shows pixel-by-pixel embedding differences between Y1 and Y2:

- Bright areas indicate large changes between the two years
- Dark areas indicate stability
- Useful for detecting land use change, deforestation, construction, etc.

## Mouse Controls

| Action | Control |
|--------|---------|
| Pan | Click and drag |
| Zoom | Scroll wheel, or +/- buttons |
| Place marker | Single-click on any panel |
| Similarity search | Double-click on any panel |
| Adjust similarity | Drag the similarity slider |
| Rotate (PCA/UMAP) | Right-click drag |

## Tips

- **Processing time** is roughly the same whether you select 1 year or 8 years — all years download and process in parallel
- **Features appear incrementally** — the viewer becomes usable as soon as pyramids are built, even before vector extraction completes
- **Privacy** — all similarity search and labeling runs locally in your browser. Only tile images are fetched from the server
- **Storage** — each viewport uses ~5GB depending on the number of years processed
