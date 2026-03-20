# TEE — Tessera Embeddings Explorer

Web application for interactive exploration of satellite embedding vectors
produced by the [Tessera](https://github.com/ucam-eo/tessera) foundation model.

## Features

- **Six-panel viewer** with four modes: explore, change-detection, labelling, validation
- **Client-side similarity search** over 128-dim embeddings using brute-force L2
- **PCA and UMAP** dimensionality reduction as interactive 3D scatter plots
- **K-means segmentation** via Web Worker with K-means++ initialisation
- **Change-detection heatmap** comparing per-pixel embedding distances across years
- **Manual labelling** with point, polygon, and similarity-expansion label types
- **Hierarchical schema browser** (UKHab, HOTW, or custom schemas)
- **ML evaluation pipeline** with streaming learning curves (k-NN, RF, MLP, spatial MLP, U-Net)
- **Label sharing** — contribute labels to the Tessera global habitat directory (private) or share with other users (public)
- **Export** labels as JSON, GeoJSON, or ESRI Shapefile; download trained models

## Quick Start

```bash
# Local development
python3 manage.py runserver 8001

# Docker (production)
docker buildx build --platform linux/amd64 \
    --build-arg GIT_VERSION="$(git describe --tags --always)" \
    -t sk818/tee:stable --push .
```

## Documentation

See [docs/](../docs/) for comprehensive documentation:
- [Architecture](../docs/architecture.md) — system design, panel layout, module graph
- [Frontend API](../docs/frontend_api.md) — JavaScript API reference
- [Backend API](../docs/backend_api.md) — Python HTTP endpoints
- [Extension Guide](../docs/extension_guide.md) — how to add panels, classifiers, schemas
