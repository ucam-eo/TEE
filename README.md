<p align="center">
  <img src="tee-logo.jpg" alt="TEE Logo" width="500">
</p>

# TEE: Tessera Embeddings Explorer

**v1.2.1** | [User Guide](public/user_guide.md) | [User Guide (PDF)](public/user_guide.pdf) | [Docker Hub](https://hub.docker.com/r/sk818/tee)

A web-based tool for exploring and classifying land cover from Sentinel-2 satellite imagery using [Tessera](https://geotessera.org) embeddings.

**Privacy by design:** Similarity searches and labelling run entirely in your browser. ML evaluation runs on your own compute server. Ground-truth data never leaves your machine.

## What can TEE do?

- **Explore** any 5km x 5km area on Earth using 128-dimensional Tessera embeddings (2018-2025)
- **Find similar pixels** instantly — double-click anywhere to highlight similar locations
- **Label habitats** using K-means clustering, manual pins, polygon drawing, and standard schemas (UKHab, EUNIS, HOTW)
- **Evaluate classifiers** (k-NN, Random Forest, XGBoost, MLP, Spatial MLP, U-Net) on ground-truth shapefiles at any scale
- **Generate classification maps** as GeoTIFFs for use in GIS
- **Compare years** side by side to detect land-use change

![Labelling mode](public/images/labelling.png)

## Quick Start

### Hosted version

Open [tee.cl.cam.ac.uk](https://tee.cl.cam.ac.uk), create a viewport, and start exploring. No installation needed for exploration and labelling.

### Docker (self-hosted)

```bash
docker pull sk818/tee:stable
docker run -d --name tee --restart unless-stopped \
    -p 8001:8001 -v /data:/data -v /data/viewports:/app/viewports \
    sk818/tee:stable
```

Open http://localhost:8001. Manage users with:
```bash
docker cp tee:/app/scripts/manage.sh ~/manage.sh && chmod +x ~/manage.sh
sudo ./manage.sh
```

### ML Evaluation

Evaluation requires a compute server (`tee-compute`). See the [Compute Server Setup](public/user_guide.md#compute-server-setup) section of the User Guide for full instructions.

```bash
# Everything on your laptop (no GPU server needed)
./scripts/deploy-compute.sh --local

# Or offload ML to a GPU server via SSH tunnel
./scripts/deploy-compute.sh gpu-box
```

Then open http://localhost:8001 and go to Validation > Evaluate.

## Documentation

The **[User Guide](public/user_guide.md)** ([PDF](public/user_guide.pdf)) covers everything:

- Creating and managing viewports
- Similarity search and labelling workflows
- Classification schemas (UKHab, EUNIS, HOTW)
- Auto-labelling with K-means
- Compute server setup (local, GPU, all-local modes)
- Validation with learning curves and confusion matrices
- Classifier parameters and hyperparameter variants
- Spatial train/test splits
- Exporting labels and generating classification maps
- Sharing labels with other users
- CLI for headless batch evaluation

## Community

Join the TEE discussion channel at [eeg.zulipchat.com](https://eeg.zulipchat.com) for help, feedback, and announcements.

## License

MIT License — see LICENSE file for details.

## Authors

- **S. Keshav** — Primary development and design
- **Claude Opus 4.6** — AI-assisted development

## Acknowledgements

Thanks to Julia Jones (Bangor), David Coomes (Cambridge), Anil Madhavapeddy (Cambridge), and Sadiq Jaffer (Cambridge) for their insightful feedback.

## Citation

```bibtex
@software{tee2025,
  title={TEE: Tessera Embeddings Explorer},
  author={Keshav, S. and Claude Opus 4.6},
  year={2025},
  url={https://github.com/ucam-eo/TEE}
}
```
