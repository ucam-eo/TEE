#!/usr/bin/env bash
# Deploy tee-compute to a remote server and (re)start the SSH tunnel.
#
# Usage:
#   ./scripts/deploy-compute.sh <server>                    # deploy + start tunnel
#   ./scripts/deploy-compute.sh <server> --install-torch    # also install PyTorch
#   ./scripts/deploy-compute.sh <server> --no-tunnel        # deploy only
#
# Prerequisites:
#   - SSH access to <server> (configure in ~/.ssh/config)
#   - TEE repo cloned on server: git clone https://github.com/ucam-eo/TEE.git ~/TEE
#   - Python venv created: python3 -m venv ~/TEE/venv
#
# See the User Guide for full setup instructions.

set -u

REMOTE="${1:-}"
if [[ -z "$REMOTE" ]]; then
    echo "Usage: $0 <server> [--install-torch] [--no-tunnel]"
    echo ""
    echo "Examples:"
    echo "  $0 gpu-box                    # deploy + start tunnel"
    echo "  $0 gpu-box --install-torch    # also install PyTorch for U-Net"
    echo "  $0 gpu-box --no-tunnel        # deploy only, no tunnel"
    exit 1
fi
shift  # consume server name, remaining args are flags

REMOTE_PORT=5050
LOCAL_PORT=8001
REMOTE_DIR=TEE
VENV="\$HOME/$REMOTE_DIR/venv"

echo "=== Deploying tee-compute to $REMOTE ==="

echo "--- Killing local processes on port $LOCAL_PORT (if any) ---"
pkill -f "tee-compute.*$LOCAL_PORT" 2>/dev/null && echo "  killed tee-compute" || true
pkill -f "waitress.*$LOCAL_PORT\|waitress.*tee_project" 2>/dev/null && echo "  killed Django" || true
pkill -f "ssh.*-L $LOCAL_PORT.*$REMOTE" 2>/dev/null && echo "  killed tunnel" || true

echo "--- Pulling latest code and installing on $REMOTE ---"
ssh "$REMOTE" "cd ~/$REMOTE_DIR && git pull && $VENV/bin/pip install -q --upgrade geotessera && $VENV/bin/pip install -q -e 'packages/tessera-eval[server]' 2>&1 || true"

# Install PyTorch if requested (needed for U-Net)
if [[ "$*" == *"--install-torch"* ]]; then
    if ssh "$REMOTE" "nvidia-smi" &>/dev/null; then
        echo "--- Installing PyTorch with CUDA on $REMOTE ---"
        # Default to cu121; override with CUDA_INDEX env var if needed
        CUDA_INDEX="${CUDA_INDEX:-https://download.pytorch.org/whl/cu121}"
        ssh "$REMOTE" "$VENV/bin/pip install -q torch --index-url $CUDA_INDEX 2>&1 || true"
    else
        echo "--- Installing PyTorch (CPU only) on $REMOTE ---"
        ssh "$REMOTE" "$VENV/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu 2>&1 || true"
    fi
fi

echo "--- Killing old tee-compute on $REMOTE ---"
ssh "$REMOTE" "kill -9 \$(pgrep -f 'python.*tee-compute') 2>/dev/null || true; kill -9 \$(lsof -t -i :$REMOTE_PORT) 2>/dev/null || true; sleep 1"
sleep 2

if [[ "$*" == *"--no-tunnel"* ]]; then
    echo "=== Done (no tunnel requested) ==="
    exit 0
fi

echo "=== Starting SSH tunnel: localhost:$LOCAL_PORT -> $REMOTE:$REMOTE_PORT ==="
echo "    Ctrl+C to stop"
echo ""
echo "    Open http://localhost:$LOCAL_PORT"
echo ""
ssh -L "$LOCAL_PORT:localhost:$REMOTE_PORT" "$REMOTE" "OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 $VENV/bin/tee-compute --port $REMOTE_PORT"
