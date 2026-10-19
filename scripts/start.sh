#!/usr/bin/env bash
# Start ComfyUI (8188), the backend (8000) and the web UI (5173). Ctrl+C stops all three.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/outputs/logs"

trap 'kill 0' EXIT INT TERM

# PYTORCH_ENABLE_MPS_FALLBACK lets ops that MPS lacks run on the CPU instead of crashing
(cd "$ROOT/engine/ComfyUI" && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py \
  --listen 127.0.0.1 --port 8188 \
  --extra-model-paths-config "$ROOT/models/extra_model_paths.yaml" \
  > "$ROOT/outputs/logs/comfyui.log" 2>&1) &

(cd "$ROOT/backend" && .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload \
  > "$ROOT/outputs/logs/backend.log" 2>&1) &

(cd "$ROOT/frontend" && npm run dev -- --host 127.0.0.1 \
  > "$ROOT/outputs/logs/frontend.log" 2>&1) &

echo "ComfyUI  → http://127.0.0.1:8188   (log: outputs/logs/comfyui.log)"
echo "Backend  → http://127.0.0.1:8000   (log: outputs/logs/backend.log)"
echo "Web UI   → http://127.0.0.1:5173"
wait
