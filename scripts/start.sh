#!/usr/bin/env bash
# Start ComfyUI for images/video (8188) and for music (8189), the backend (8000) and the web UI (5173).
# Ctrl+C stops everything.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMFY="$ROOT/engine/ComfyUI"
MUSIC="$ROOT/engine/music-instance"
mkdir -p "$ROOT/outputs/logs" "$MUSIC"/{output,input,temp,user}

# Login password and session-signing secret live in .env (git-ignored). Change the password there anytime.
if [ ! -f "$ROOT/.env" ]; then
  password="$(openssl rand -base64 12 | tr -dc 'A-Za-z0-9' | head -c 12)"
  printf 'HAPPICA_PASSWORD=%s\nHAPPICA_SECRET=%s\n' "$password" "$(openssl rand -hex 32)" > "$ROOT/.env"
  chmod 600 "$ROOT/.env"
  echo "Created .env — your login password is: $password (change HAPPICA_PASSWORD there)"
fi
set -a; source "$ROOT/.env"; set +a

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

# Online access (HAPPICA_ONLINE=1 in .env): a Cloudflare quick tunnel to the backend. Its address changes on every
# start, so the new one is written to the Railway web service's BACKEND_URL (Railway redeploys in about a minute).
if [ "${HAPPICA_ONLINE:-0}" = "1" ]; then
  CLOUDFLARED="$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")"
  "$CLOUDFLARED" tunnel --no-autoupdate --url http://127.0.0.1:8000 > "$ROOT/outputs/logs/tunnel.log" 2>&1 &
  (
    for _ in $(seq 1 60); do
      url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$ROOT/outputs/logs/tunnel.log" | head -1 || true)"
      [ -n "$url" ] && break
      sleep 1
    done
    if [ -z "$url" ]; then
      echo "Tunnel didn't start — see outputs/logs/tunnel.log"
    elif (cd "$ROOT" && railway variables --set "BACKEND_URL=$url" --service web --environment production > /dev/null); then
      echo "Online: tunnel $url → Railway updated (the public site works again in about a minute)"
    else
      echo "Tunnel is $url but updating Railway failed — run: railway variables --set BACKEND_URL=$url --service web"
    fi
  ) &
  caffeinate -i -w $$ &  # keep the Mac from idle-sleeping while the studio is online
fi

echo "ComfyUI  → http://127.0.0.1:8188   (log: outputs/logs/comfyui.log)"
echo "Music    → http://127.0.0.1:8189   (log: outputs/logs/comfyui-music.log)"
echo "Backend  → http://127.0.0.1:8000   (log: outputs/logs/backend.log)"
echo "Web UI   → http://127.0.0.1:5173"
wait
