#!/usr/bin/env bash
# Start ComfyUI for images/video (8188) and for music (8189), the backend (8000) and the web UI (5173).
# Ctrl+C stops everything.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMFY="$ROOT/engine/ComfyUI"
MUSIC="$ROOT/engine/music-instance"
mkdir -p "$ROOT/outputs/logs" "$MUSIC"/{output,input,temp,user}

trap 'kill 0' EXIT INT TERM

# PYTORCH_ENABLE_MPS_FALLBACK lets ops that MPS lacks run on the CPU instead of crashing.
# Both instances use --cache-none (models are released as soon as a run ends) and --fp32-vae
# (bf16 VAEs are very slow on Apple GPUs; fp16 overflows).
COMMON=(--listen 127.0.0.1 --cache-none --fp32-vae --extra-model-paths-config "$ROOT/models/extra_model_paths.yaml")

(cd "$COMFY" && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py --port 8188 "${COMMON[@]}" \
  > "$ROOT/outputs/logs/comfyui.log" 2>&1) &

# Music runs ACE-Step's song planner (a 1.7B LM) on the GPU, which needs --gpu-only: on the CPU it is
# ~8x slower. --gpu-only would slow LTX-Video by ~20%, so music gets its own instance with its own folders.
(cd "$COMFY" && PYTORCH_ENABLE_MPS_FALLBACK=1 .venv/bin/python main.py --port 8189 --gpu-only "${COMMON[@]}" \
  --output-directory "$MUSIC/output" --input-directory "$MUSIC/input" \
  --temp-directory "$MUSIC/temp" --user-directory "$MUSIC/user" \
  > "$ROOT/outputs/logs/comfyui-music.log" 2>&1) &

(cd "$ROOT/backend" && .venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --reload \
  > "$ROOT/outputs/logs/backend.log" 2>&1) &

(cd "$ROOT/frontend" && npm run dev -- --host 127.0.0.1 \
  > "$ROOT/outputs/logs/frontend.log" 2>&1) &

echo "ComfyUI  → http://127.0.0.1:8188   (log: outputs/logs/comfyui.log)"
echo "Music    → http://127.0.0.1:8189   (log: outputs/logs/comfyui-music.log)"
echo "Backend  → http://127.0.0.1:8000   (log: outputs/logs/backend.log)"
echo "Web UI   → http://127.0.0.1:5173"
wait
