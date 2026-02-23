# TEE: Tessera Embeddings Explorer

**Version 2.0.2** | [Docker Hub](https://hub.docker.com/r/sk818/tee)

A system for downloading, processing, and visualizing Sentinel-2 satellite embeddings (2017-2025) with an interactive web interface.

## Overview

TEE integrates geospatial data processing with deep learning embeddings to create an interactive exploration platform. The system:

- **Downloads** Tessera embeddings from GeoTessera for multiple years
- **Processes** embeddings into RGB visualizations and pyramid tile structures
- **Builds** FAISS indices for efficient similarity search
- **Visualizes** embeddings through an interactive web-based viewer
- **Enables** temporal analysis by switching between years

## Features

### Multi-Year Support
- Download embeddings for years 2017-2025 (depending on data availability)
- Select which years to process during viewport creation
- Switch between years instantly in the viewer
- Temporal coherence in similarity search through year-specific FAISS indices

### Interactive Viewer
- Zoomable, pannable map interface using Leaflet.js
- Real-time embedding visualization with year selector
- Pixel-level extraction of embeddings
- Similarity search to find matching locations across the viewport

### Viewport Management
- Create custom geographic viewports interactively
- **Landmark/geocode search** — type a place name (e.g. "London") to jump the map and auto-fill the viewport name
- **Direct coordinate input** — enter lat/long coordinates (e.g. "51.5074, -0.1278")
- **Click-to-lock preview box** — 5km box follows the mouse, locks on click, repositionable
- Multi-year processing with progress tracking
- Automatic navigation to viewer after processing
- **Full cleanup on cancel/delete** — removes mosaics, pyramids, FAISS indices, and cached embeddings tiles; shared tiles used by other viewports are preserved

### Explorer Mode (Client-Side Search)
- Click pixels on the embedding map to extract embeddings
- **All similarity search runs locally in the browser** — no queries sent to server
- FAISS data (embeddings + coordinates) downloaded once and cached in IndexedDB
- Brute-force L2 search over ~250K vectors completes in ~100-200ms
- Real-time threshold slider for instant local filtering
- Labels and search are fully private — only tile images are fetched from the server

### Cross-Year Label Timeline
- **Track how label coverage changes over time** — click "Timeline" on any saved label to see pixel counts across all available years (2017–2025)
- Uses the label's stored embedding and threshold for consistent comparison
- Results displayed in a modal with a proportional **bar chart** (colored with the label's color) and a **percentage change summary** (e.g. "33% decrease from 2019 to 2023")
- Loads each year's FAISS data from IndexedDB cache (or downloads in background) without disrupting the current session
- All computation stays client-side — label privacy is preserved

### Advanced Viewer (6-Panel Layout)

The viewer includes a **6-panel layout** toggle for advanced analysis:

1. **OSM** — OpenStreetMap geographic reference
2. **RGB** — Satellite imagery with label painting tools
3. **Embeddings Y1** — First year embeddings with similarity search
4. **UMAP** — 2D projection of embedding space (auto-computed on load)
5. **Heatmap** — Temporal distance heatmap (Y1 vs Y2 pixel-by-pixel differences)
6. **Embeddings Y2** — Second year embeddings for temporal comparison

Key capabilities: one-click similarity search, real-time threshold control, persistent colored label overlays, cross-panel synchronized markers, UMAP visualization with satellite RGB coloring, temporal distance heatmap, year-based label updates, and cross-year label timeline analysis.

Labels are stored in browser localStorage (private, survive reloads). Labels can be exported/imported as compact JSON files for sharing — they are portable across viewports since matching uses embedding distance, not coordinates.

### Export Options

A consolidated **Export** dropdown provides three formats:

- **Labels (JSON)** — compact metadata for sharing and re-importing into TEE
- **Labels (GeoJSON)** — FeatureCollection with 10m polygons per pixel, aligned to zoom-18 Mercator projection for pixel-perfect overlay in QGIS/GIS tools. Properties include `label_name`, `label_color`, `distance`, and `threshold`.
- **Map (JPG)** — high-resolution satellite image with label overlays and legend, rendered at zoom level 18

## Quick Start

### Prerequisites

- Python 3.8+ (or Docker)
- ~5GB storage per viewport (varies by number of years)

### Option A: Docker Installation (Recommended)

1. **Install Docker Desktop:**
   - Mac: `brew install --cask docker` or download from [docker.com](https://www.docker.com/products/docker-desktop/)
   - Windows/Linux: Download from [docker.com](https://www.docker.com/products/docker-desktop/)

2. **Pull and run from Docker Hub (easiest):**
   ```bash
   docker pull sk818/tee:2.0.2
   docker run -p 8001:8001 -v ~/tee_data:/data sk818/tee:2.0.2
   ```

   **Or build from source:**
   ```bash
   git clone https://github.com/sk818/TEE.git tee
   cd tee
   docker build -t tee .
   docker run -p 8001:8001 -v ~/tee_data:/data tee
   ```

   **Or with docker-compose:**
   ```bash
   docker-compose up -d
   ```

3. **Open browser:** Navigate to http://localhost:8001

### Option B: Local Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/sk818/TEE.git tee
   cd tee
   ```

2. **Create and activate virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Start the server:**
   ```bash
   bash restart.sh
   ```
   Web server on http://localhost:8001, tile server on http://localhost:5125.

5. **Create a viewport:** Open http://localhost:8001, click "+ Create New Viewport", search for a location or click the map, select years, and click Create.

## Deployment

### Local vs Server

| | Local (single machine) | Server (VM behind Apache) |
|---|---|---|
| Setup | `bash restart.sh` | `sudo bash deploy.sh` then `sudo bash restart.sh` |
| User | Your user | `tee` system user |
| Data | `~/data/` | `/home/tee/data/` |
| Logs | `./logs/` | `/var/log/tee/` |
| Binding | `0.0.0.0` (direct access) | `127.0.0.1` (Apache proxies) |
| Tiles | Viewer talks to `:5125` directly | Apache proxies `/tiles` to `:5125` |
| HTTPS | N/A | Apache handles TLS; set `TEE_HTTPS=1` |

`restart.sh` auto-detects the environment: if a `tee` system user exists, services run as `tee` with server settings; otherwise they run as the current user in local mode. No code changes needed between server and laptop.

### Local Development

```bash
bash restart.sh
# Web server on http://localhost:8001 (waitress), tile server on http://localhost:5125
```

Data is stored in `~/data/` by default (override with `TEE_DATA_DIR`). Logs go to `./logs/`.

### Server Deployment (Behind Apache)

**First-time setup:**
```bash
cd /opt
sudo git clone https://github.com/sk818/TEE.git tee
cd /opt/tee
sudo bash deploy.sh          # Creates tee user, venv, data dirs
sudo -u tee /opt/tee/venv/bin/python3 scripts/manage_users.py add admin
sudo bash restart.sh          # Start services
curl http://localhost:8001/health   # Verify
```

**Day-to-day operations:**
```bash
cd /opt/tee
sudo git pull && sudo bash restart.sh   # Update and restart
sudo bash shutdown.sh                    # Stop services
bash status.sh                           # Check status
tail -f /var/log/tee/web_server.log      # View logs
```

The viewer uses relative URLs, so it works identically behind a local or remote server. Configure your reverse proxy to forward:
- `/` → Django/waitress (port 8001) for the web server and API
- `/tiles/` → tile server (port 5125) for map tiles

When both servers are behind the same reverse proxy, no additional configuration is needed. See `deployment_plan.md` for full Apache configuration, firewall rules, and architecture details.

## Authentication & User Management

TEE supports optional per-user authentication. When enabled, unauthenticated users can browse in read-only **demo mode** with a **Login** button in the header. Logged-in users see their username, a **Change Password** button, and a **Logout** button.

### Enabling Authentication

Authentication is controlled by the presence of a `passwd` file in the data directory (`~/data/passwd`). If no passwd file exists, auth is disabled and all users have open access with no quota limits.

### Managing Users

Use the `manage_users.py` script (run with the venv Python so bcrypt is available):

```bash
# Add a user (prompts for password with confirmation)
./venv/bin/python3 scripts/manage_users.py add admin

# Add another user
./venv/bin/python3 scripts/manage_users.py add alice

# List all users
./venv/bin/python3 scripts/manage_users.py list

# Verify a user's password
./venv/bin/python3 scripts/manage_users.py check admin

# Remove a user
./venv/bin/python3 scripts/manage_users.py remove alice
```

In Docker:
```bash
docker exec -it <container> python3 scripts/manage_users.py add admin
```

### Disabling Authentication

Remove all users or delete the passwd file:
```bash
./venv/bin/python3 scripts/manage_users.py remove admin
# or
rm ~/data/passwd
```
When the last user is removed, the script deletes the passwd file automatically, returning to open access. No server restart is needed — the passwd file is re-read on every request.

### The `admin` User

The `admin` user has special privileges:
- **No disk quota** — can create viewports without size limits
- All other users are subject to a **2 GB disk quota** per user

### Disk Quota

Each non-admin user has a **2 GB disk quota** for viewport data. When creating a viewport, the server estimates the disk usage and rejects the request if it would exceed the quota. Delete existing viewports to free up space.

### Changing Passwords

Logged-in users can change their password via the **Password** button in the header. Passwords must be at least 6 characters.

### HTTPS Session Cookies

When deploying behind HTTPS, set `TEE_HTTPS=1` to mark session cookies as secure:
```bash
export TEE_HTTPS=1
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TEE_DATA_DIR` | `~/data` | Data directory (mosaics, pyramids, FAISS indices, passwd) |
| `TEE_APP_DIR` | Project root | Application directory (auto-detected from `lib/config.py`) |
| `TEE_MODE` | `desktop` | `desktop` (DEBUG=True) or `production` (DEBUG=False, security headers) |
| `TILE_SERVER_URL` | unset | Tile server URL for local dev (set automatically by `restart.sh`) |
| `TEE_HTTPS` | unset | Set to `1` to mark session cookies as `Secure` (for HTTPS) |
| `GEOTESSERA_API_KEY` | — | GeoTessera API credentials (if required) |

### Preset Viewports

Modify `viewports/{name}.txt` to customize preset viewports:
```
name: My Viewport
description: Optional description
bounds: 77.55,13.0,77.57,13.02
```

## Data Pipeline

The system processes satellite embeddings through five main stages with **parallel multi-year processing**. All pipeline execution flows through `lib/pipeline.py::PipelineRunner`, providing consistent behavior for both web-based and CLI entry points.

### CLI One-Liner

```bash
./venv/bin/python3 setup_viewport.py --years 2023,2024,2025 --umap-year 2024
```

This runs the full pipeline: download → RGB → pyramids → FAISS → UMAP.

Or use the web interface: `bash restart.sh`, open http://localhost:8001, click "+ Create New Viewport", select years and click Create. Processing runs in the background with status tracking.

### Pipeline Stages

Each stage processes **all selected years in parallel**:

#### 1. Download Embeddings
```bash
python3 download_embeddings.py --years 2017,2021,2025
```
- Downloads Sentinel-2 embeddings from GeoTessera (all years concurrently)
- Saves as GeoTIFF files in `~/data/mosaics/`

#### 2. Create RGB Visualizations
```bash
python3 create_rgb_embeddings.py
```
- Converts 128D embeddings to RGB using the first 3 bands
- Outputs to `~/data/mosaics/rgb/`

#### 3. Build Pyramid Structure
```bash
python3 create_pyramids.py
```
- Creates multi-level zoom pyramids (0-5) with 3x nearest-neighbor upscaling
- **Viewer becomes available** once ANY year has pyramids
- Output: `~/data/pyramids/{viewport}/{year}/`

#### 4. Create FAISS Indices
```bash
python3 create_faiss_index.py
```
- Builds vector similarity search indices for all years
- **Labeling controls become available** once ANY year has FAISS
- Output: `~/data/faiss_indices/{viewport}/{year}/`

#### 5. Compute UMAP (Optional)
```bash
python3 compute_umap.py {viewport_name} {year}
```
- Computes 2D UMAP projection (~1-2 min for 264K embeddings)
- Used by the 6-panel layout (Panel 4)
- **UMAP visualization becomes available** once computed
- Output: `~/data/faiss_indices/{viewport}/{year}/umap_coords.npy`

### Incremental Feature Availability

| Stage | Feature | Available When |
|-------|---------|-----------------|
| After Stage 3 (Pyramids) | Basic viewer with maps | ANY year has pyramids |
| After Stage 4 (FAISS) | Labeling/similarity search | ANY year has FAISS index |
| After Stage 5 (UMAP) | UMAP visualization (Panel 4) | UMAP computed for any year |

### Status Tracking

Check pipeline status via:
```bash
curl http://localhost:8001/api/operations/pipeline-status/{viewport_name}
```

## API Reference

### Viewport Management

**List all viewports:**
```
GET /api/viewports/list
```

**Get current viewport:**
```
GET /api/viewports/current
```

**Switch viewport:**
```
POST /api/viewports/switch
Content-Type: application/json

{"name": "viewport_name"}
```

**Create new viewport:**
```
POST /api/viewports/create
Content-Type: application/json

{
  "bounds": "min_lon,min_lat,max_lon,max_lat",
  "name": "My Viewport",
  "years": ["2017", "2024"]  // Optional: default is [2024]
}
```

**Check viewport readiness:**
```
GET /api/viewports/{viewport_name}/is-ready
```
Returns: `{ready: bool, message: string, has_embeddings: bool, has_pyramids: bool, has_faiss: bool, years_available: [string]}`

**Get available years:**
```
GET /api/viewports/{viewport_name}/available-years
```
Returns: `{success: bool, years: [2024, 2023, ...]}`

### Authentication

**Check auth status:**
```
GET /api/auth/status
```
Returns: `{auth_enabled: bool, logged_in: bool, user: string|null}`

**Log in:**
```
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "secret"}
```

**Log out:**
```
POST /api/auth/logout
```

**Change password (requires active session):**
```
POST /api/auth/change-password
Content-Type: application/json

{"current_password": "old", "new_password": "new"}
```

## Project Structure

```
TEE/
├── README.md                          # This file
├── requirements.txt                   # Python dependencies
├── Dockerfile                         # Docker container definition
├── docker-compose.yml                 # Docker Compose configuration
│
├── deploy.sh                          # First-time VM setup (creates tee user, venv, dirs)
├── restart.sh                         # Start/restart web + tile servers
├── shutdown.sh                        # Stop all servers
├── status.sh                          # Show project status (git, data, services)
│
├── manage.py                          # Django management script
├── tee_project/                       # Django project settings
│   ├── settings/                      # Split settings (base, desktop, production)
│   ├── urls.py                        # Root URL configuration
│   └── wsgi.py                        # WSGI entry point (used by waitress)
│
├── api/                               # Django app — API endpoints
│   ├── middleware.py                   # Auth middleware (passwd file + sessions)
│   ├── auth_views.py                  # Login/logout/status/change-password
│   ├── tasks.py                       # Background task tracking
│   ├── helpers.py                     # Shared utilities
│   └── views/                         # Endpoint modules
│       ├── viewports.py               # Viewport CRUD and status
│       ├── pipeline.py                # Downloads and processing
│       ├── compute.py                 # UMAP, PCA, distance heatmap
│       ├── faiss_data.py              # FAISS index serving
│       └── config.py                  # Health, static files, client config
│
├── public/                            # Web interface
│   ├── viewer.html                    # Embedding viewer (3-panel and 6-panel layouts)
│   ├── viewport_selector.html         # Viewport creation and management
│   ├── login.html                     # Login page
│   └── README.md                      # Frontend documentation
│
├── scripts/                           # Management scripts
│   └── manage_users.py                # Add/remove/list users for authentication
│
├── lib/                               # Python utilities
│   ├── config.py                      # Centralized configuration (paths, env vars)
│   ├── pipeline.py                    # Unified pipeline orchestration
│   ├── flask_auth.py                  # Auth wrapper for tile_server.py (Flask)
│   ├── viewport_utils.py              # Viewport file operations
│   ├── viewport_writer.py             # Viewport configuration writer
│   └── progress_tracker.py            # Progress tracking utilities
│
├── viewports/                         # Viewport configurations (user-created, gitignored)
│   └── README.md                      # Viewport directory documentation
│
├── download_embeddings.py             # GeoTessera embedding downloader
├── create_rgb_embeddings.py           # Convert embeddings to RGB
├── create_pyramids.py                 # Build zoom-level pyramid structure
├── create_faiss_index.py              # Build similarity search indices
├── compute_umap.py                    # Compute UMAP projection
├── compute_pca.py                     # Compute PCA projection
├── setup_viewport.py                  # Orchestrate full workflow
└── tile_server.py                     # Tile server for map visualization
```

## Development

### Running with Custom Settings

**Download specific years only:**
```bash
python3 download_embeddings.py --years 2023,2024
```

**Process single viewport:**
Set the active viewport first, then run pipeline scripts.

## Troubleshooting

### Server fails to start
- Check if ports 8001 (web) or 5125 (tiles) are in use: `lsof -i:8001` / `lsof -i:5125`
- Check logs: `tail logs/web_server.log` (local) or `tail /var/log/tee/web_server.log` (server)

### Tile server not responding
- If map tiles fail to load, restart both servers: `bash restart.sh`

### Disk space not reclaimed after deleting a viewport
- Cancelling or deleting a viewport now automatically cleans up cached embeddings tiles in `~/data/embeddings/`
- Tiles shared with other viewports are preserved
- To manually clear all embeddings caches (when no viewports need them): `rm -rf ~/data/embeddings/global_0.1_degree_representation/`

### No data appears in viewer
- Verify pyramids exist: `ls ~/data/pyramids/{viewport}/{year}/`
- Check FAISS indices: `ls ~/data/faiss_indices/{viewport}/{year}/`
- Re-run `create_pyramids.py` or `create_faiss_index.py` as needed

### Slow similarity search
- Check FAISS index was created for the selected year
- Reduce similarity threshold for faster results
- Process fewer years per viewport

### Year doesn't appear in dropdown
- Verify embeddings were downloaded: `ls ~/data/mosaics/*_{year}.tif`
- Confirm pyramids exist for that year
- Check that FAISS index was built

## Performance

**Memory & storage:**
- ~550MB steady state, ~850MB peak during pipeline processing
- ~150-300MB per year per viewport for embeddings; ~500MB-1GB per year for pyramid tiles

**Typical processing times:**

| Stage | Time (per year) | Notes |
|-------|-----------------|-------|
| Download embeddings | 5-15 min | All years download in parallel |
| Create RGB | 2-5 min | All years process in parallel |
| Build pyramids | 5-10 min | All years process in parallel |
| Create FAISS index | 5-15 min | All years process in parallel |
| **Total** | **17-45 min** | Same time for 1 year or 8 years |

Multiple years are downloaded and processed concurrently — total time is approximately the same whether you request 1 year or 8 years. Features become available incrementally as each stage completes (see [Incremental Feature Availability](#incremental-feature-availability)).

## License

MIT License - See LICENSE file for details

## Authors

- **S. Keshav** - Primary development and design
- **Claude Opus 4.6** - AI-assisted development and feature implementation

## Related Resources

- [GeoTessera Documentation](https://geotessera.readthedocs.io/)
- [FAISS Documentation](https://faiss.ai/)
- [Leaflet.js Map Library](https://leafletjs.com/)
- [Sentinel-2 Satellite Data](https://sentinel.esa.int/web/sentinel/missions/sentinel-2)

## Support

For issues or questions:
1. Check the troubleshooting section
2. Review server logs: `/var/log/tee/web_server.log` (server) or `logs/web_server.log` (local)
3. Verify data files exist in `~/data/`
4. Check browser console for JavaScript errors

## Citation

If you use this project in research, please cite:

```bibtex
@software{tee2025,
  title={TEE: Tessera Embeddings Explorer},
  author={Keshav, S. and Claude Opus 4.6},
  year={2025},
  url={https://github.com/sk818/TEE}
}
```
