"""U-Net classifier for 2D embedding grids.

Requires PyTorch (not in requirements.txt — install manually on GPU hosts).
"""

import numpy as np

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError:
    torch = None
    nn = None
    F = None

TORCH_MISSING_MSG = "U-Net requires PyTorch. Install with: pip install torch"


def _check_torch():
    if torch is None:
        raise ImportError(TORCH_MISSING_MSG)


class _ConvBlock(nn.Module):
    """Two Conv3x3 → BN → ReLU layers."""

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


class UNet(nn.Module):
    """Lightweight U-Net for pixel classification on embedding grids."""

    def __init__(self, in_channels=128, n_classes=2, depth=3, base_filters=64):
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
            self.upconvs.append(nn.ConvTranspose2d(ch, skip_ch, 2, stride=2))
            self.decoders.append(_ConvBlock(skip_ch * 2, skip_ch))
            ch = skip_ch

        # Output
        self.out_conv = nn.Conv2d(ch, n_classes, 1)

    def forward(self, x):
        # Pad to multiple of 2^depth
        factor = 2 ** self.depth
        _, _, h, w = x.shape
        pad_h = (factor - h % factor) % factor
        pad_w = (factor - w % factor) % factor
        if pad_h > 0 or pad_w > 0:
            x = F.pad(x, [0, pad_w, 0, pad_h])

        # Encoder
        skips = []
        for enc, pool in zip(self.encoders, self.pools):
            x = enc(x)
            skips.append(x)
            x = pool(x)

        x = self.bottleneck(x)

        # Decoder
        for upconv, dec, skip in zip(self.upconvs, self.decoders, reversed(skips)):
            x = upconv(x)
            # Handle size mismatch from rounding
            if x.shape != skip.shape:
                x = F.pad(x, [0, skip.shape[3] - x.shape[3],
                               0, skip.shape[2] - x.shape[2]])
            x = torch.cat([x, skip], dim=1)
            x = dec(x)

        x = self.out_conv(x)

        # Crop back to original size
        return x[:, :, :h, :w]


def build_embedding_grid(embeddings, coords, width, height):
    """Build a dense (H, W, dim) grid from sparse (N, dim) embeddings.

    Pixels with no data are zero-filled.
    """
    dim = embeddings.shape[1]
    grid = np.zeros((height, width, dim), dtype=np.float32)
    grid[coords[:, 1], coords[:, 0]] = embeddings
    return grid


def train_unet(embedding_grid, labelled_coords, labelled_labels,
               train_idx, n_classes, params=None):
    """Train U-Net on the 2D embedding grid using masked cross-entropy.

    Args:
        embedding_grid: (H, W, dim) float32 array
        labelled_coords: (M, 2) array of [col, row] for labelled pixels
        labelled_labels: (M,) int array of class labels
        train_idx: indices into labelled_coords/labels for training
        n_classes: number of classes
        params: dict with optional keys: epochs, lr, depth, base_filters

    Returns:
        Trained UNet model on CPU.
    """
    _check_torch()
    p = params or {}
    epochs = int(p.get("epochs", 50))
    lr = float(p.get("lr", 0.001))
    depth = int(p.get("depth", 3))
    base_filters = int(p.get("base_filters", 64))

    H, W, dim = embedding_grid.shape
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Build label grid: -1 means ignore
    label_grid = np.full((H, W), -1, dtype=np.int64)
    train_coords = labelled_coords[train_idx]
    train_labels = labelled_labels[train_idx]
    label_grid[train_coords[:, 1], train_coords[:, 0]] = train_labels

    # Tensors: (1, dim, H, W) input, (1, H, W) target
    x = torch.from_numpy(embedding_grid.transpose(2, 0, 1)[np.newaxis]).to(device)
    y = torch.from_numpy(label_grid[np.newaxis]).to(device)

    model = UNet(in_channels=dim, n_classes=n_classes,
                 depth=depth, base_filters=base_filters).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.CrossEntropyLoss(ignore_index=-1)

    model.train()
    for epoch in range(epochs):
        optimizer.zero_grad()
        out = model(x)           # (1, n_classes, H, W)
        loss = criterion(out, y)
        loss.backward()
        optimizer.step()

    model.cpu()
    return model


def predict_unet(model, embedding_grid):
    """Run inference and return (H, W) argmax predictions.

    Args:
        model: trained UNet model (on CPU)
        embedding_grid: (H, W, dim) float32 array

    Returns:
        (H, W) int array of predicted class indices.
    """
    _check_torch()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()

    x = torch.from_numpy(
        embedding_grid.transpose(2, 0, 1)[np.newaxis]
    ).to(device)

    with torch.no_grad():
        out = model(x)  # (1, n_classes, H, W)

    preds = out.argmax(dim=1).squeeze(0).cpu().numpy()
    model.cpu()
    return preds
