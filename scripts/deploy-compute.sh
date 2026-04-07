#!/usr/bin/env bash
# Deploy tee-compute and start TEE.
#
# Usage:
#   ./scripts/deploy-compute.sh <server>                    # GPU tunnel on :8001
#   ./scripts/deploy-compute.sh <server> --install-torch    # also install PyTorch
#   ./scripts/deploy-compute.sh <server> --no-tunnel        # deploy only
#   ./scripts/deploy-compute.sh --local                     # local Django + tee-compute
#   ./scripts/deploy-compute.sh --local <server>            # local Django + GPU tunnel
#
# --local: runs Django on :8001 for local viewports/labelling.
#          Without <server>: local tee-compute on :8002.
#          With <server>: GPU tunnel on :8002.
#
# Without --local: tunnel on :8001, UI proxied from tee.cl.cam.ac.uk.
#
# Prerequisites (remote):
#   - SSH access to <server> (configure in ~/.ssh/config)
#   - TEE repo cloned: git clone https://github.com/ucam-eo/TEE.git ~/TEE
#   - Python venv: python3 -m venv ~/TEE/venv

set -u

# ── Parse arguments ──

LOCAL_MODE=false
REMOTE=""
EXTRA_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --local) LOCAL_MODE=true ;;
        --install-torch|--no-tunnel) EXTRA_ARGS="$EXTRA_ARGS $arg" ;;
        *) if [[ -z "$REMOTE" ]]; then REMOTE="$arg"; fi ;;
    esac
done

if [[ "$LOCAL_MODE" == false && -z "$REMOTE" ]]; then
    echo "Usage: $0 <server> [--install-torch] [--no-tunnel]"
    echo "       $0 --local                     # all local"
    echo "       $0 --local <server>             # local Django + GPU tunnel"
    echo ""
    echo "Examples:"
    echo "  $0 gpu-box                    # GPU tunnel on :8001 (recommended)"
    echo "  $0 gpu-box --install-torch    # also install PyTorch for U-Net"
    echo "  $0 --local                    # everything on your laptop"
    echo "  $0 --local gpu-box            # local UI + GPU evaluation"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_PORT=5050
REMOTE_DIR=TEE
VENV="\$HOME/$REMOTE_DIR/venv"
LOG_DIR="$SCRIPT_DIR/logs"
mkdir -p "$LOG_DIR"

# ── Kill existing processes ──

echo "--- Killing existing TEE processes ---"
pkill -f "waitress.*tee_project" 2>/dev/null && echo "  killed Django" || true
pkill -f "tee-compute" 2>/dev/null && echo "  killed tee-compute" || true
lsof -ti:8001 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:8002 2>/dev/null | xargs kill -9 2>/dev/null || true
if [[ -n "$REMOTE" ]]; then
    pkill -f "ssh.*-L.*$REMOTE" 2>/dev/null && echo "  killed tunnel" || true
fi
sleep 1

# ── Deploy to remote server (if specified) ──

if [[ -n "$REMOTE" ]]; then
    echo "=== Deploying to $REMOTE ==="
    ssh "$REMOTE" "cd ~/$REMOTE_DIR && git pull && $VENV/bin/pip install -q --upgrade geotessera && $VENV/bin/pip install -q -e 'packages/tessera-eval[server]' 2>&1 || true"

    if [[ "$EXTRA_ARGS" == *"--install-torch"* ]]; then
        if ssh "$REMOTE" "nvidia-smi" &>/dev/null; then
            echo "--- Installing PyTorch with CUDA on $REMOTE ---"
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

    if [[ "$EXTRA_ARGS" == *"--no-tunnel"* ]]; then
        echo "=== Done (no tunnel requested) ==="
        exit 0
    fi
fi

# ── Start services ──

if [[ "$LOCAL_MODE" == true ]]; then
    # Local mode: Django on :8001
    PYTHON="$SCRIPT_DIR/venv/bin/python3"
    TEE_COMPUTE="$SCRIPT_DIR/venv/bin/tee-compute"

    echo "=== Starting Django on localhost:8001 ==="
    $PYTHON -m waitress --host=0.0.0.0 --port=8001 --threads=16 --channel-timeout=7200 tee_project.wsgi:application \
        >> "$LOG_DIR/web_server.log" 2>&1 &
    DJANGO_PID=$!
    sleep 2

    if ! ps -p $DJANGO_PID > /dev/null 2>&1; then
        echo "  Django FAILED — check $LOG_DIR/web_server.log"
        exit 1
    fi
    echo "  Django OK (PID: $DJANGO_PID)"

    if [[ -n "$REMOTE" ]]; then
        # Local Django + GPU tunnel on :8002
        echo "=== Starting SSH tunnel: localhost:8002 -> $REMOTE:$REMOTE_PORT ==="
        echo ""
        echo "    Open http://localhost:8001"
        echo "    Ctrl+C to stop tunnel (Django stays running)"
        echo ""
        ssh -L "8002:localhost:$REMOTE_PORT" "$REMOTE" "OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 $VENV/bin/tee-compute --port $REMOTE_PORT"
    else
        # All local: tee-compute on :8002
        echo "  Starting tee-compute on localhost:8002"
        $TEE_COMPUTE --hosted "http://localhost:8001" --host 0.0.0.0 --port 8002 \
            >> "$LOG_DIR/compute_server.log" 2>&1 &
        COMPUTE_PID=$!
        sleep 2

        if ! ps -p $COMPUTE_PID > /dev/null 2>&1; then
            echo "  tee-compute FAILED — check $LOG_DIR/compute_server.log"
            exit 1
        fi
        echo "  tee-compute OK (PID: $COMPUTE_PID)"
        echo ""
        echo "    Open http://localhost:8001"
        echo "    Logs: $LOG_DIR/"
    fi
else
    # Remote only: tunnel on :8001, UI proxied from tee.cl
    echo "=== Starting SSH tunnel: localhost:8001 -> $REMOTE:$REMOTE_PORT ==="
    echo "    Ctrl+C to stop"
    echo ""
    echo "    Open http://localhost:8001"
    echo ""
    ssh -L "8001:localhost:$REMOTE_PORT" "$REMOTE" "OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 $VENV/bin/tee-compute --port $REMOTE_PORT"
fi
