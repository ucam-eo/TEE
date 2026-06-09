# The `tee-compute` server

`tessera-eval` ships an optional local compute server that lets you **run the ML on
your own machine** (where you have CPU/RAM/GPU) while a **hosted TEE server**
supplies the map UI, tiles, and label sharing. It's the "bring your own compute"
companion to the hosted [TEE](https://tee.cl.cam.ac.uk) viewer.

```bash
pip install "tessera-eval[server]"
tee-compute --hosted https://tee.cl.cam.ac.uk
# open http://localhost:8001 in your browser
```

## Why

The interactive evaluation — uploading a shapefile, running learning curves over
millions of pixels, training models, rendering large-area prediction maps — is
compute-heavy and data-heavy. Running it locally means:

- your labelled shapefiles and embeddings stay on your machine;
- you use your own cores/RAM (and GPU for the U-Net) instead of a shared server;
- you still get the hosted UI, basemaps, and tile delivery for free.

## How it works

```
            browser ──▶ tee-compute (localhost:8001)
                          │
          ML requests ────┤ handled locally (this package)
          everything else ┴──▶ proxied to --hosted (UI, tiles, labels)
```

`tee-compute` is a small Flask app. Requests under `/api/evaluation/*` are served
locally by `tessera_eval` (loading embeddings via GeoTessera, running
`run_learning_curve` / `run_kfold_cv`, training and applying models). Every other
request is transparently proxied to the `--hosted` server, so the browser sees a
single origin.

Local endpoints (consumed by the hosted UI; not a stable public API):

| Endpoint | Purpose |
|---|---|
| `POST /api/evaluation/upload-shapefile` | accept a labelled shapefile/GeoJSON |
| `POST /api/evaluation/run-large-area` | learning-curve / CV over the labelled area (streams progress) |
| `POST /api/evaluation/train-models` | fit final models on all labels |
| `GET  /api/evaluation/download-model/<name>` | download a trained model |
| `POST /api/evaluation/create-map` | render a prediction map for a region |
| `GET  /api/evaluation/download-map/<name>` | download a rendered map |
| `POST /api/evaluation/cancel` · `clear-shapefiles` · `finish-classifier` | session control |
| `GET  /health` | liveness |
| `* /<path>` | proxied to `--hosted` |

## Configuration

| Flag | Default | Meaning |
|---|---|---|
| `--hosted` | `https://tee.cl.cam.ac.uk` | hosted TEE server for UI/data/proxy |
| `--port` | `8001` | local port to serve on |
| `--host` | `127.0.0.1` | bind address (keep loopback unless you know you want LAN access) |
| `--debug` | off | Flask debug mode (auto-reload, verbose errors) |

In production mode it serves via `waitress` (4 threads, long channel timeout for
big jobs); `--debug` uses the Flask dev server.

## Requirements

The `[server]` extra pulls Flask, waitress, requests, and geotessera. Tile access
(GeoTessera) requires network access to fetch embeddings unless they're cached
locally. For the U-Net path, install `torch` as well (`pip install torch`).

## Programmatic alternative

If you don't need the UI, skip the server entirely and call the library directly —
see the [tutorial](tutorial.md). The server is purely a convenience layer over the
same `tessera_eval` functions.
