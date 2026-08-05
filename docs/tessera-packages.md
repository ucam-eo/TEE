# The Tessera-Family Packages

TEE doesn't do embedding fetching, quantisation, or ML evaluation itself —
it delegates to four separate, independently-versioned packages, each
its own GitHub repository. This document explains what each one
provides, how they depend on each other, and how their requirements
actually resolve when TEE is installed — including a real bug this
project hit, as a worked example of the kind of thing that goes wrong
when this isn't understood.

If you just want a quick one-line-per-package summary, see
[features.md §14](features.md#14-backend-architecture-for-developers)
("External Tessera-family packages"). This document goes deeper.

---

## 1. The four packages

| Package | Repo | Provides |
|---------|------|----------|
| **`geotessera`** | `ucam-eo/geotessera` | The foundational client. Downloads and serves raw 128-dim per-pixel Tessera embeddings, full precision, no compression. Everything else in this family either wraps it or replaces it. |
| **`tessera-zarr-utils`** | `ucam-eo/tessera-zarr-utils` | A faster region-reader built on top of `geotessera`'s zarr store, with cross-UTM-zone EPSG:4326 merging. Originally lived inside `tessera-eval`; extracted into its own package (v0.1.0) so other consumers could use it without pulling in all of `tessera-eval`. |
| **`tessera-vq`** | `sk818/tessera-vq` | A compression layer: replaces raw 128-dim floats with small per-tile codebooks + index maps (vector quantisation). Ships both a lightweight client (`VQTessera`, plug-compatible with `GeoTessera`'s interface) and a server ("the bolt-on") that does the actual quantisation, running on `tee.cl.cam.ac.uk`. |
| **`tessera-eval`** | `ucam-eo/tessera-eval` | The ML evaluation library: classifier/regressor factories, learning-curve machinery, spatial train/test splitting — plus the `tee-compute` Flask server that TEE's Validation feature talks to. |

None of these are vendored in this repo. All four are installed as
ordinary pip/git dependencies in `requirements.txt`, each pinned to a
specific tag.

---

## 2. How they relate to each other

```
                    ┌──────────────┐
                    │  geotessera  │  full-precision embeddings, raw client
                    └──────┬───────┘
                           │ wraps for faster region reads
                           ▼
                 ┌───────────────────┐
                 │ tessera-zarr-utils │  fast zarr region reader
                 └─────────┬─────────┘
                           │ used by (via [server] extra)
                           ▼
  ┌────────────┐   ┌───────────────┐        ┌──────────────┐
  │ tessera-vq │   │ tessera-eval  │        │  geotessera  │ (also used
  │ (separate, │   │ (ML library + │◄───────┤ directly, for│
  │ own server)│   │ tee-compute)  │        │ full precision│
  └─────┬──────┘   └───────┬───────┘        └──────────────┘
        │                  │
        │ HTTP             │ imported directly
        ▼                  ▼
┌──────────────────────────────────────┐
│                  TEE                 │
│  process_viewport.py, embeddings_    │
│  provider.py, postcard.py  --------->│ uses tessera-vq (VQTessera)
│  api/views/viewports.py (coverage) ->│ uses geotessera directly
│  scripts/tee_evaluate.py  --------->│ uses tessera-eval + geotessera
│  Validation feature (browser) ------>│ talks to tee-compute over HTTP
└──────────────────────────────────────┘
```

`tessera-vq` is deliberately **not** built on `geotessera` at the client
level — `VQTessera` only needs `numpy` + `affine`, so that TEE's own
pipeline (which uses it for every viewport) doesn't have to drag in
`geopandas`/`rasterio`/etc. just to fetch embeddings. The bolt-on
*server* half (not something TEE installs) does depend on `geotessera`
and `tessera-zarr-utils` to actually do the quantisation.

---

## 3. What TEE uses each one for

| Package | Used by | For what |
|---------|---------|----------|
| `geotessera` | `api/views/viewports.py` (`embedding_coverage`) | Lightweight coverage/registry lookup — no embedding fetch |
| `geotessera` | `scripts/tee_evaluate.py` | Standalone large-area CLI; delegates to `tessera-eval`, doesn't reimplement anything |
| `geotessera` | `tessera-eval`'s `tee-compute` server | Full-precision embedding fetch for ML evaluation — deliberate, not a fallback (see §4 below) |
| `tessera-zarr-utils` | `tessera-eval`'s `tee-compute` server only | Fast region reads during evaluation. **Not used by TEE's own code directly** — `process_viewport.py` used to import it for a "zarr fast path", but that whole branch is now dead code (removed) since `tessera-vq` became the unconditional embeddings client. Still needed in `requirements.txt` because `tessera-eval` doesn't declare it as its own dependency outside the `[server]` extra (see §4). |
| `tessera-vq` | `process_viewport.py`, `api/embeddings_provider.py`, `api/views/postcard.py` | The standard embeddings client for every viewport and for Postcard — not an opt-in "fast path" any more, see [architecture.md](architecture.md) |
| `tessera-eval` | `scripts/tee_evaluate.py`, the whole Validation feature (via `tee-compute`) | Classifier/regressor factories, learning curves, spatial splits, the compute server itself |

**Why evaluation stays on plain `geotessera` instead of `tessera-vq`:**
this was a deliberate choice, not an oversight. The backend needs full
embeddings to maximise evaluation quality, and there's no responsiveness
requirement for a batch training run the way there is for interactive
map browsing — so the lossy compression tradeoff that makes sense for
the viewer doesn't make sense for evaluation.

---

## 4. A real bug, as a worked example: the missing `[server]` extra

This actually happened in this repo and is worth understanding, because
it's the kind of failure that's invisible in a long-lived dev environment
and only shows up on a genuinely fresh install.

`tessera-eval`'s own `pyproject.toml` correctly declares its dependencies:

```toml
dependencies = ["numpy>=1.24", "geopandas>=0.14", "rasterio>=1.3", "scikit-learn>=1.3", "affine>=2.4"]

[project.optional-dependencies]
server = ["flask>=3.0", "waitress>=2.1", "requests>=2.28", "geotessera>=0.9.0",
          "tessera-zarr-utils[geotessera] @ git+https://github.com/ucam-eo/tessera-zarr-utils.git@v0.1.0"]
```

`tee-compute` is a Flask app — it needs everything in `[server]` to run
at all. But TEE's `requirements.txt` used to install plain `tessera-eval`
(no extra). `pip show tessera-eval` on that install shows:

```
Requires: affine, geopandas, numpy, rasterio, scikit-learn
```

No Flask. TEE's `requirements.txt` happened to *also* list `geotessera`
and `tessera-zarr-utils` directly (for unrelated, now largely obsolete
reasons), which accidentally covered two of the four missing pieces —
but nothing supplied Flask. In this dev environment it went unnoticed for
a long time because Flask was already installed from some earlier,
unrelated state — `pip show flask` showed `Required-by:` empty, meaning
nothing currently declared actually needed it. A genuinely fresh clone +
fresh venv + `pip install -r requirements.txt` would have hit
`ModuleNotFoundError: No module named 'flask'` the moment `tee-compute`
tried to start.

**The fix**, applied in `requirements.txt`:

```diff
- tessera-eval @ git+https://github.com/ucam-eo/tessera-eval.git@v1.2.0
+ tessera-eval[server] @ git+https://github.com/ucam-eo/tessera-eval.git@v1.2.0
```

...and the now-redundant standalone `tessera-zarr-utils` line was
removed, since it comes in properly via the extra.

**Verified for real**, not just reasoned about: `flask`, `tessera-eval`,
and `tessera-zarr-utils` were uninstalled, `pip install -r
requirements.txt` was re-run, and all three came back automatically as
part of resolving `tessera-eval[server]` — confirmed by the install log,
and by `tee-compute` starting and answering its own `/health` endpoint
afterward.

**The general lesson:** when a package declares dependencies under an
optional extra, installing it without that extra will *look* fine in any
environment that happens to already have the extra's packages installed
for some other reason — the failure only appears on a clean install. If
something works locally but you're not sure why, check `pip show
<package> | grep Required-by` — an empty result means nothing currently
declared actually needs it, and you may be relying on accumulated venv
state rather than a correct dependency chain.

---

## 5. Version pins and where they live

All four packages are pinned by git tag in `requirements.txt`:

```
geotessera>=0.9.0
tessera-vq @ git+https://github.com/sk818/tessera-vq.git@v0.5.5
tessera-eval[server] @ git+https://github.com/ucam-eo/tessera-eval.git@v1.2.0
```

(`tessera-zarr-utils` is no longer pinned directly — it arrives via
`tessera-eval[server]`.)

Local, push-access checkouts of the two `ucam-eo`/`sk818`-controlled
repos used to make and test upstream fixes exist at:

- `~/code/tessera-vq` — `sk818/tessera-vq`
- `~/code/tessera-eval` — `ucam-eo/tessera-eval`

(`geotessera` and `tessera-zarr-utils` don't currently have local
checkouts in this environment; only pip-installed copies.)

### Bumping a pin after an upstream fix

1. Make the change in the package's own local checkout, update its
   version in `pyproject.toml` (and `__init__.py`'s `__version__` if it
   has one — check it isn't already stale relative to `pyproject.toml`,
   that drift has happened before)
2. Commit, push, tag (`git tag vX.Y.Z && git push origin vX.Y.Z` —
   lightweight tags, matching each repo's existing convention; TEE's own
   convention of annotated tags does *not* apply to these separate repos)
3. Bump the tag in TEE's `requirements.txt`
4. `pip install <the changed line>` in TEE's venv to pick it up locally
5. Restart TEE's services and verify the actual behaviour changed, not
   just that the version number did — pin bumps are easy to get "half
   done" (tag pushed, but the actual fix not exercised) if you don't
   re-test end to end
