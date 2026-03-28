"""U-Net training and prediction on embedding tiles with sparse labels.

Provides patch extraction from labelled rasters, a self-contained TinyUNet
model, training on extracted patches, and sliding-window tile prediction.

Requires PyTorch (not in requirements.txt -- install manually on GPU hosts).
"""

import logging

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, TensorDataset
    _HAS_TORCH = True
except ImportError:
    torch = None
    _HAS_TORCH = False

logger = logging.getLogger(__name__)

_TORCH_MISSING = "U-Net requires PyTorch.  Install with: pip install torch"


def _require_torch():
    if not _HAS_TORCH:
        raise RuntimeError(_TORCH_MISSING)


# ---------------------------------------------------------------------------
# Patch extraction
# ---------------------------------------------------------------------------

def extract_labelled_patches(tile_emb, class_raster, patch_size=256,
                             min_labelled=10):
    """Extract embedding/label patch pairs centered on clusters of labelled pixels.

    Uses connected-component analysis (via scipy) to find clusters of labelled
    pixels in *class_raster*, then extracts axis-aligned patches centered on
    each cluster.  Patches that extend beyond the tile boundary are zero-padded
    for embeddings and filled with 0 (ignore) for labels.

    Args:
        tile_emb: float32 array, shape (H, W, 128) -- tile embeddings.
        class_raster: int array, shape (H, W) -- 0 = unlabelled, 1..N = class.
        patch_size: Patch side length in pixels (default 256).
        min_labelled: Minimum labelled pixels required per patch (default 10).

    Returns:
        List of (emb_patch, label_patch) tuples where
        - emb_patch is float32 (patch_size, patch_size, 128)
        - label_patch is int32 (patch_size, patch_size), 0 = ignore
    """
    from scipy import ndimage

    H, W, dim = tile_emb.shape
    class_raster = np.asarray(class_raster)
    mask = class_raster > 0

    if not mask.any():
        return []

    # Label connected components of any labelled pixel
    structure = np.ones((3, 3), dtype=int)  # 8-connectivity
    labelled_cc, n_components = ndimage.label(mask, structure=structure)

    half = patch_size // 2
    patches = []

    for cc_id in range(1, n_components + 1):
        ys, xs = np.where(labelled_cc == cc_id)
        n_labelled = len(ys)

        if n_labelled < min_labelled:
            continue

        # Centre of mass of this component
        cy = int(np.mean(ys))
        cx = int(np.mean(xs))

        # Patch bounding box in tile coordinates (may exceed tile bounds)
        r0 = cy - half
        c0 = cx - half
        r1 = r0 + patch_size
        c1 = c0 + patch_size

        # Source region clamped to tile bounds
        sr0 = max(r0, 0)
        sc0 = max(c0, 0)
        sr1 = min(r1, H)
        sc1 = min(c1, W)

        # Destination offsets inside the output patch
        dr0 = sr0 - r0
        dc0 = sc0 - c0
        dr1 = dr0 + (sr1 - sr0)
        dc1 = dc0 + (sc1 - sc0)

        emb_patch = np.zeros((patch_size, patch_size, dim), dtype=np.float32)
        label_patch = np.zeros((patch_size, patch_size), dtype=np.int32)

        emb_patch[dr0:dr1, dc0:dc1] = tile_emb[sr0:sr1, sc0:sc1]
        label_patch[dr0:dr1, dc0:dc1] = class_raster[sr0:sr1, sc0:sc1]

        # Recount labelled pixels inside the actual patch
        if (label_patch > 0).sum() < min_labelled:
            continue

        patches.append((emb_patch, label_patch))

    return patches


# ---------------------------------------------------------------------------
# TinyUNet model -- only defined when torch is available
# ---------------------------------------------------------------------------
if _HAS_TORCH:

    class _ConvBlock(nn.Module):
        """Two Conv3x3 + BN + ReLU layers."""

        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.block = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 3, padding=1),
                nn.BatchNorm2d(out_ch),
                nn.ReLU(inplace=True),
                nn.Conv2d(out_ch, out_ch, 3, padding=1),
                nn.BatchNorm2d(out_ch),
                nn.ReLU(inplace=True),
            )

        def forward(self, x):
            return self.block(x)

    class TinyUNet(nn.Module):
        """Lightweight U-Net for pixel classification on embedding grids.

        Architecture: *depth* encoder stages (ConvBlock + MaxPool), a bottleneck
        ConvBlock, then *depth* decoder stages (ConvTranspose2d + skip-cat +
        ConvBlock), followed by a 1x1 output convolution.

        Inputs are automatically padded to multiples of 2^depth and cropped back
        before output.

        Args:
            in_channels: Number of input channels (default 128 for embeddings).
            n_classes: Number of output classes.
            depth: Number of encoder/decoder stages (default 3).
            base_filters: Filters in the first encoder stage (default 64).
        """

        def __init__(self, in_channels=128, n_classes=2, depth=3,
                     base_filters=64):
            super().__init__()
            self.depth = depth

            # Encoder
            self.encoders = nn.ModuleList()
            self.pools = nn.ModuleList()
            ch = in_channels
            for i in range(depth):
                out_ch = base_filters * (2 ** i)
                self.encoders.append(_ConvBlock(ch, out_ch))
                self.pools.append(nn.MaxPool2d(2))
                ch = out_ch

            # Bottleneck
            self.bottleneck = _ConvBlock(ch, ch * 2)
            ch = ch * 2

            # Decoder
            self.upconvs = nn.ModuleList()
            self.decoders = nn.ModuleList()
            for i in range(depth - 1, -1, -1):
                skip_ch = base_filters * (2 ** i)
                self.upconvs.append(
                    nn.ConvTranspose2d(ch, skip_ch, 2, stride=2))
                self.decoders.append(_ConvBlock(skip_ch * 2, skip_ch))
                ch = skip_ch

            # Output head
            self.out_conv = nn.Conv2d(ch, n_classes, 1)

        def forward(self, x):
            factor = 2 ** self.depth
            _, _, h, w = x.shape
            pad_h = (factor - h % factor) % factor
            pad_w = (factor - w % factor) % factor
            if pad_h or pad_w:
                x = F.pad(x, [0, pad_w, 0, pad_h])

            # Encoder
            skips = []
            for enc, pool in zip(self.encoders, self.pools):
                x = enc(x)
                skips.append(x)
                x = pool(x)

            x = self.bottleneck(x)

            # Decoder
            for upconv, dec, skip in zip(self.upconvs, self.decoders,
                                         reversed(skips)):
                x = upconv(x)
                if x.shape != skip.shape:
                    x = F.pad(x, [0, skip.shape[3] - x.shape[3],
                                   0, skip.shape[2] - x.shape[2]])
                x = torch.cat([x, skip], dim=1)
                x = dec(x)

            x = self.out_conv(x)
            return x[:, :, :h, :w]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_unet_on_patches(patches, n_classes, params=None):
    """Train a TinyUNet on extracted embedding/label patches.

    Args:
        patches: List of (emb_patch, label_patch) tuples from
            :func:`extract_labelled_patches`.
        n_classes: Total number of classes (labels 1..n_classes).
        params: Optional dict with keys:
            - epochs (int, default 50)
            - lr (float, default 0.001)
            - depth (int, default 3)
            - base_filters (int, default 64)
            - batch_size (int, default 4)

    Returns:
        Trained TinyUNet model (on CPU).
    """
    _require_torch()

    if not patches:
        raise ValueError("No patches to train on")

    p = params or {}
    epochs = int(p.get("epochs", 50))
    lr = float(p.get("lr", 0.001))
    depth = int(p.get("depth", 3))
    base_filters = int(p.get("base_filters", 64))
    batch_size = int(p.get("batch_size", 4))

    # Stack patches into tensors
    # emb_patch: (patch_size, patch_size, dim) -> (dim, patch_size, patch_size)
    emb_list = [patch[0].transpose(2, 0, 1) for patch in patches]
    lbl_list = [patch[1].astype(np.int64) for patch in patches]

    X = torch.from_numpy(np.stack(emb_list))      # (N, dim, H, W)
    Y = torch.from_numpy(np.stack(lbl_list))       # (N, H, W)

    dataset = TensorDataset(X, Y)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    in_channels = X.shape[1]
    # n_classes+1 outputs: index 0 is the ignore/background class
    model = TinyUNet(in_channels=in_channels, n_classes=n_classes + 1,
                     depth=depth, base_filters=base_filters)

    device = torch.device("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available()
                          else "cpu")
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss(ignore_index=0)

    model.train()
    for epoch in range(epochs):
        total_loss = 0.0
        for xb, yb in loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad()
            out = model(xb)          # (B, n_classes+1, H, W)
            loss = criterion(out, yb)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * xb.size(0)
        if (epoch + 1) % 10 == 0 or epoch == 0:
            avg = total_loss / len(dataset)
            logger.info("Epoch %d/%d  loss=%.4f", epoch + 1, epochs, avg)

    model.cpu()
    return model


# ---------------------------------------------------------------------------
# Tile-level prediction
# ---------------------------------------------------------------------------

def predict_unet_tile(model, tile_emb, patch_size=256, overlap=32):
    """Sliding-window U-Net prediction over a full embedding tile.

    Runs the model on overlapping patches and averages the softmax
    probabilities in overlap regions before taking argmax.

    Args:
        model: Trained TinyUNet (on CPU; moved to device internally).
        tile_emb: float32 array, shape (H, W, dim) -- tile embeddings.
        patch_size: Patch side length in pixels (default 256).
        overlap: Overlap between adjacent patches in pixels (default 32).

    Returns:
        int array, shape (H, W) -- predicted class indices (1..N).
    """
    _require_torch()

    H, W, dim = tile_emb.shape
    stride = patch_size - overlap

    device = torch.device("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available()
                          else "cpu")
    model.to(device).eval()

    # Determine n_classes from the model output layer
    n_out = model.out_conv.out_channels

    # Accumulators for soft predictions and counts
    sum_probs = np.zeros((n_out, H, W), dtype=np.float32)
    counts = np.zeros((H, W), dtype=np.float32)

    # Generate patch start positions
    row_starts = list(range(0, H, stride))
    col_starts = list(range(0, W, stride))

    with torch.no_grad():
        for r0 in row_starts:
            for c0 in col_starts:
                r1 = min(r0 + patch_size, H)
                c1 = min(c0 + patch_size, W)

                # Extract patch, zero-pad if smaller than patch_size
                patch = tile_emb[r0:r1, c0:c1]
                ph, pw = patch.shape[:2]

                if ph < patch_size or pw < patch_size:
                    padded = np.zeros((patch_size, patch_size, dim),
                                     dtype=np.float32)
                    padded[:ph, :pw] = patch
                    patch = padded

                # (1, dim, ps, ps)
                x = torch.from_numpy(
                    patch.transpose(2, 0, 1)[np.newaxis]
                ).to(device)

                out = model(x)  # (1, n_out, ps, ps)
                probs = F.softmax(out, dim=1).squeeze(0).cpu().numpy()

                # Accumulate only the valid (non-padded) region
                sum_probs[:, r0:r1, c0:c1] += probs[:, :ph, :pw]
                counts[r0:r1, c0:c1] += 1.0

    model.cpu()

    # Avoid division by zero for any uncovered pixels
    counts[counts == 0] = 1.0
    avg_probs = sum_probs / counts[np.newaxis, :, :]

    # Argmax across class dimension
    preds = avg_probs.argmax(axis=0).astype(np.int32)
    return preds
