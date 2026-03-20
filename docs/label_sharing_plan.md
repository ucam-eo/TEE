# Label Sharing — Implementation Plan

## Overview

Users can share their labels (manual + saved) to a central directory on the
server. Two modes:

- **Private**: shares only `{embedding, label}` pairs (no locations) for the
  Tessera team's global habitat directory. Not visible to other users.
- **Public**: shares geolocated labels as ESRI Shapefile so other users of the
  same viewport can import them.

---

## 1. Data Flow

```
SHARE (outbound):
  User clicks Share → dropdown (privacy toggle, name/email/org)
    │
    ├─ Private mode:
    │   collectPrivateShareData() gathers manualLabels + savedLabels
    │   strips lat/lon/vertices/pixel_coords
    │   keeps: name, code, embedding, type
    │   POST /api/share/submit (JSON body)
    │   → server writes /data/share/<email>/<viewport>/metadata.json + labels.json
    │
    └─ Public mode:
        buildShapefileZip() builds GeoJSON → ESRI Shapefile ZIP
        POST /api/share/submit (multipart: metadata JSON + ZIP)
        → server writes /data/share/<email>/<viewport>/metadata.json + labels.zip

  If "Download copy" checked → also triggers browser download


IMPORT (inbound, public shares only):
  User clicks Import → dropdown
    │
    ├─ "From file..." → existing file picker (unchanged)
    │
    └─ "From shared labels..."
        GET /api/share/list/<viewport>
        ← JSON array of public share summaries
        User clicks one
        GET /api/share/download/<email>/<viewport>
        ← shapefile ZIP
        → imported as manual labels via existing importShapefile()
```

---

## 2. Storage Structure

```
/data/share/
  alice_at_cam_ac_uk/
    cambridge/
      metadata.json     user info, viewport, schema, format, shared_at
      labels.json       private mode: [{name, code, embedding, type}, ...]
      labels.zip        public mode: ESRI shapefile
  bob_at_example_com/
    cambridge/
      metadata.json
      labels.zip
```

- Email sanitized: `@` → `_at_`, `.` → `_`
- Overwrite on re-submit (no versioning)
- Write-only API (no delete/update endpoints)
- Private shares: `labels.json` only, not listed or downloadable by other users
- Public shares: `labels.zip`, listed and downloadable

---

## 3. Data Formats

### Private mode (labels.json)

```json
{
  "format": "private",
  "user": { "name": "Alice", "email": "alice@cam.ac.uk", "organization": "Cambridge" },
  "viewport": "cambridge",
  "schema": { "mode": "ukhab", "data": null },
  "labels": [
    { "name": "Woodland", "code": "w1a", "embedding": [0.12, -0.34, ...], "type": "point" },
    { "name": "Grassland", "code": "g1", "embedding": [0.56, 0.78, ...], "type": "polygon" }
  ],
  "shared_at": "2026-03-20T12:00:00Z"
}
```

Schema field: `{"mode": "none", "data": null}`, `{"mode": "ukhab", "data": null}`,
or `{"mode": "custom", "data": {name, tree}}`.

Labels come from BOTH `manualLabels` and `savedLabels`. No lat/lon, no vertices,
no pixel_coords, no viewport bounds.

### Public mode (labels.zip)

ESRI Shapefile ZIP (same format as existing Export → Shapefile), containing
points and polygons with attributes: `name`, `color`, `code`, `type`.

Plus `metadata.json` inside the zip:
```json
{
  "format": "public",
  "user": { "name": "Alice", "email": "alice@cam.ac.uk", "organization": "Cambridge" },
  "viewport": "cambridge",
  "viewport_bounds": { "minLon": 0.08, "minLat": 52.18, "maxLon": 0.16, "maxLat": 52.22 },
  "schema": { "mode": "ukhab", "data": null },
  "shared_at": "2026-03-20T12:00:00Z"
}
```

### metadata.json (always written alongside labels)

```json
{
  "format": "private" | "public",
  "user": { "name": "...", "email": "...", "organization": "..." },
  "viewport": "cambridge",
  "viewport_bounds": null | { "minLon": ..., ... },
  "schema": { "mode": "...", "data": ... },
  "shared_at": "2026-03-20T12:00:00Z"
}
```

---

## 4. Backend Changes

### 4.1 New file: `api/views/share.py` (~120 lines)

```python
def submit_share(request):
    """Accept shared label data (private JSON or public multipart)."""
    # Validate: user.name, user.email, user.organization, format, viewport
    # Sanitize email: replace @ with _at_, . with _
    # Validate no path traversal in email or viewport
    # Create /data/share/<email>/<viewport>/
    # Write metadata.json
    # Private: write labels.json from request body
    # Public: write labels.zip from uploaded file
    # Return {"status": "ok"}

def list_shares(request, viewport_name):
    """List available PUBLIC shares for a viewport."""
    # Scan /data/share/*/
    # For each: check <viewport>/metadata.json exists and format == "public"
    # Return [{name, email, organization, shared_at}]

def download_share(request, sanitized_email, viewport_name):
    """Download a public share as shapefile ZIP."""
    # Read metadata.json, verify format == "public"
    # Return labels.zip as FileResponse
    # Return 404 for private shares
```

### 4.2 `api/urls.py` (+6 lines)

```python
from .views.share import submit_share, list_shares, download_share

path('share/submit', submit_share),
path('share/list/<str:viewport_name>', list_shares),
path('share/download/<str:sanitized_email>/<str:viewport_name>', download_share),
```

### 4.3 `api/middleware.py` (+1 line)

Add to `WRITE_ENDPOINTS`:
```python
'/api/share/submit',
```

### 4.4 `lib/config.py` (+2 lines)

```python
SHARE_DIR = DATA_DIR / 'share'
```

Add `SHARE_DIR` to `ensure_dirs()`.

---

## 5. Frontend Changes

All in `labels.js` (~250 lines added) and `viewer.html` (~55 lines added).
No new JS module.

### 5.1 Share Button Wiring

**viewer.html**: Enable share button (remove disabled state), add
`onclick="toggleShareDropdown()"`. Add dropdown HTML after the button.

**labels.js** new functions:

| Function | Lines | Description |
|---|---|---|
| `toggleShareDropdown()` | ~15 | Show/hide the share dropdown |
| `submitShare()` | ~90 | Validate form, collect data, POST, handle download copy |
| `collectPrivateShareData()` | ~30 | Gather manualLabels + savedLabels, strip locations |
| `collectPublicShareData()` | ~25 | Build shapefile ZIP using `buildShapefileZip()` |
| `buildShapefileZip()` | ~20 | Refactored from `exportManualLabelsShapefile()` — returns blob |

### 5.2 Import Extension

**viewer.html**: Change import button to show dropdown instead of direct file
picker. Add "shared labels" floating panel HTML.

**labels.js** new functions:

| Function | Lines | Description |
|---|---|---|
| `toggleImportDropdown()` | ~20 | Show "From file" / "From shared labels" options |
| `showSharedLabelsPanel()` | ~30 | Fetch list, render floating panel with available shares |
| `importSharedLabels(email, viewport)` | ~20 | Download ZIP, import via existing `importShapefile()` |

### 5.3 Badge on Import Button

On viewport load (or when entering labelling mode), fetch
`GET /api/share/list/<viewport>` and show count badge on the import button
if shares are available.

### 5.4 Window Exports

```javascript
window.toggleShareDropdown = toggleShareDropdown;
window.submitShare = submitShare;
window.toggleImportDropdown = toggleImportDropdown;
window.showSharedLabelsPanel = showSharedLabelsPanel;
window.importSharedLabels = importSharedLabels;
window.buildShapefileZip = buildShapefileZip;
```

---

## 6. Validation Tests

### 6.1 `validation/test_refactoring_guards.py`

Add to `TestBackendViewsIntact.VIEWS`:
```python
("api/views/share.py", ["submit_share", "list_shares", "download_share"]),
```

Add to `TestCriticalState.STATE_VARS`:
```python
(r"SHARE_DIR", "SHARE_DIR"),
```

Add to `TestCriticalFunctions.FUNCTIONS` (window exports in JS):
```python
"toggleShareDropdown",
"submitShare",
"buildShapefileZip",
```

### 6.2 `validation/test_viewer_html.py`

Add test class `TestLabelSharing`:
```python
class TestLabelSharing:
    def test_share_button_exists(self, html):
        assert 'share' in html.lower()

    def test_share_dropdown_exists(self, html):
        assert 'share-dropdown' in html

    def test_share_privacy_toggle(self, html):
        assert 'share-privacy' in html

    def test_share_submit_function(self, script_text):
        assert 'submitShare' in script_text

    def test_share_import_from_shared(self, script_text):
        assert 'showSharedLabelsPanel' in script_text
```

---

## 7. Documentation Updates

### 7.1 `docs/backend_api.md`

Add new section **1.8 Label Sharing — `api/views/share.py`** with:
- Overview of the sharing system
- `POST /api/share/submit` — request/response for both formats
- `GET /api/share/list/<viewport>` — response format
- `GET /api/share/download/<email>/<viewport>` — response format
- Validation rules and error responses

### 7.2 `docs/frontend_api.md`

Add to §4 (labels.js) a new subsection **Functions — Label Sharing** with
all 7 new functions documented.

### 7.3 `docs/extension_guide.md`

No changes needed — the share feature is a built-in feature, not an
extension point.

### 7.4 `docs/architecture.md`

Update §4 Data Flow diagram to include the share/import path.
Add mention of `/data/share/` in §6 File/Directory Structure.

### 7.5 `public/user_guide.md` and `public/user_guide.html`

Add new section **Sharing Labels** covering:
- How to share (share button, privacy toggle, user info)
- Private vs public mode — what data is shared
- Who can see shared labels (private: Tessera team only; public: all users)
- How to import shared labels from other users
- Warning about irrevocability
- Download copy option for verification

### 7.6 `public/README.md`

Add bullet point to features list:
- **Label sharing** — contribute labels to the Tessera global habitat
  directory (private) or share with other users on the same server (public)

---

## 8. Implementation Sequence

| Phase | Steps | Can test independently? |
|---|---|---|
| **1. Backend** | config.py, share.py, urls.py, middleware.py | Yes — curl / Django test |
| **2. Share UI** | viewer.html share dropdown, labels.js share functions | Yes — needs backend |
| **3. Import UI** | viewer.html import dropdown, labels.js import functions | Yes — needs shares to exist |
| **4. Tests** | test_refactoring_guards.py, test_viewer_html.py | Yes — pytest |
| **5. Docs** | backend_api.md, frontend_api.md, architecture.md, user_guide, README | N/A |

---

## 9. Security Considerations

- **Path traversal**: Validate sanitized email and viewport name — reject `/`, `..`, `\`
- **File size**: Django default 2.5MB upload limit. Sufficient for most label sets.
- **PII in private mode**: Verify no lat/lon, vertices, pixel_coords, or viewport bounds leak
- **Write-only**: No delete/update API. Admin cleans up via filesystem on michael.
- **Email sanitization**: `@` → `_at_`, `.` → `_`. Reject empty or excessively long values.

---

## 10. Estimated Totals

| File | Type | Lines |
|---|---|---|
| `api/views/share.py` | New | ~120 |
| `api/urls.py` | Modify | +6 |
| `api/middleware.py` | Modify | +1 |
| `lib/config.py` | Modify | +2 |
| `public/viewer.html` | Modify | +55 |
| `public/js/labels.js` | Modify | +250 |
| `validation/test_refactoring_guards.py` | Modify | +10 |
| `validation/test_viewer_html.py` | Modify | +15 |
| `docs/backend_api.md` | Modify | +40 |
| `docs/frontend_api.md` | Modify | +30 |
| `docs/architecture.md` | Modify | +10 |
| `public/user_guide.md` | Modify | +30 |
| `public/user_guide.html` | Modify | +30 |
| `public/README.md` | Modify | +3 |
| **Total** | | **~600** |
