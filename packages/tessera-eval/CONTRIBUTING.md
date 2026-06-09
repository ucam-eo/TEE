# Contributing to tessera-eval

Thanks for your interest in improving `tessera-eval`.

## Development setup

```bash
git clone https://github.com/ucam-eo/tessera-eval
cd tessera-eval
python -m venv .venv && source .venv/bin/activate
pip install -e ".[server,dev]"      # core + Flask compute server + dev tools
```

Optional extras: `geotessera` (tile access), `xgboost` (gradient-boosted models),
`torch` (the U-Net), `plot` (matplotlib).

## Before opening a PR

```bash
ruff check .          # lint
ruff format --check . # formatting
pytest                # tests
```

- Keep the library framework-independent: `tessera_eval/` must not import any
  web framework (Flask is confined to `server.py`) or depend on a hosting
  application. New numeric code belongs in `data`, `rasterize`, `classify`,
  `evaluate`, `zarr_utils`, or `unet`.
- Add or update tests under `tests/` for behaviour changes. Tests must use
  synthetic fixtures and must not require network access or real Tessera data.
- Public functions need a docstring with `Args:` / `Returns:` and array
  shapes + dtypes (see existing modules for the house style).
- Heavy / optional dependencies (`xgboost`, `torch`) must stay import-guarded so
  the core install works without them.

## Determinism

Estimators are constructed with `random_state=42`; evaluation accepts an explicit
`seed`. Don't introduce uncontrolled randomness into the library path.

## Releasing

Bump `version` in `pyproject.toml`, update `CHANGELOG.md`, tag `vX.Y.Z`.
