#!/usr/bin/env bash
# Build (and push) the TEE production Docker image.
#
# Always cross-builds for linux/amd64 (the deploy servers) regardless of the
# host architecture, so building on an Apple Silicon Mac no longer produces an
# arm64 image that the amd64 servers cannot pull. The git version is baked into
# /app/VERSION (surfaced by /health and the viewport-selector header).
#
# Usage:
#   scripts/build-image.sh                      # build linux/amd64, push :stable
#   scripts/build-image.sh --no-push            # build + load locally (single platform only)
#   scripts/build-image.sh -t sk818/tee:test    # different tag
#   scripts/build-image.sh --platform linux/amd64,linux/arm64   # multi-arch
#
# Requires: docker buildx (Docker Desktop) and `docker login` for pushing.

set -euo pipefail

# Repo root = parent of this script's dir, so it works from anywhere.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

IMAGE="sk818/tee:stable"
PLATFORM="linux/amd64"
OUTPUT="--push"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--tag)    IMAGE="$2";    shift 2 ;;
    --platform)  PLATFORM="$2"; shift 2 ;;
    --no-push)   OUTPUT="--load"; shift ;;
    --push)      OUTPUT="--push"; shift ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

GIT_VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo unknown)"

echo "Building $IMAGE"
echo "  platform : $PLATFORM"
echo "  version  : $GIT_VERSION"
echo "  output   : $OUTPUT"
echo

docker buildx build \
  --platform "$PLATFORM" \
  --build-arg GIT_VERSION="$GIT_VERSION" \
  -t "$IMAGE" \
  "$OUTPUT" \
  .

echo
if [[ "$OUTPUT" == "--push" ]]; then
  echo "Pushed $IMAGE ($GIT_VERSION)."
  echo "Verify platforms:  docker buildx imagetools inspect $IMAGE"
  echo "Deploy on server:  sudo bash manage.sh  ->  7) Update container"
else
  echo "Built $IMAGE locally ($GIT_VERSION)."
fi
