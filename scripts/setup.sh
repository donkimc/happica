#!/usr/bin/env bash
# One-time setup: Python 3.12 venvs for ComfyUI and the backend, frontend packages, model folders.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"

if [ ! -x "$UV" ]; then
  echo "uv not found. Install it with: python3 -m venv ~/.local/tools/uv-venv && ~/.local/tools/uv-venv/bin/pip install uv"
  exit 1
fi
"$UV" python install 3.12

# ComfyUI engine
if [ ! -d "$ROOT/engine/ComfyUI" ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$ROOT/engine/ComfyUI"
fi
cd "$ROOT/engine/ComfyUI"
[ -d .venv ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python torch torchvision torchaudio
"$UV" pip install --python .venv/bin/python -r requirements.txt

# Models live outside the engine so ComfyUI can be reinstalled without re-downloading them
mkdir -p "$ROOT"/models/{checkpoints,diffusion_models,text_encoders,vae,loras,upscale_models,photomaker,tts,ipadapter,clip_vision}
cat > "$ROOT/models/extra_model_paths.yaml" <<EOF
happica:
  base_path: $ROOT/models
  checkpoints: checkpoints
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  clip: text_encoders
  vae: vae
  loras: loras
  upscale_models: upscale_models
  photomaker: photomaker
  ipadapter: ipadapter
  clip_vision: clip_vision
EOF

# IP-Adapter nodes (cartoon characters from reference art)
[ -d custom_nodes/ComfyUI_IPAdapter_plus ] || git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus custom_nodes/ComfyUI_IPAdapter_plus

# Song tools (stems + lyric timing) in their own venv, so ComfyUI's packages stay untouched
[ -d "$ROOT/engine/audio-tools/.venv" ] || "$UV" venv --python 3.12 "$ROOT/engine/audio-tools/.venv"
"$UV" pip install --python "$ROOT/engine/audio-tools/.venv/bin/python" "torch==2.8.0" "torchaudio==2.8.0" demucs mlx-whisper soundfile numpy

# Backend
cd "$ROOT/backend"
[ -d .venv ] || "$UV" venv --python 3.12 .venv
"$UV" pip install --python .venv/bin/python -r requirements.txt

# Frontend
cd "$ROOT/frontend" && npm install

echo "Setup complete. Run scripts/start.sh"
