# Happica

A local text → image → video generator. ComfyUI does the generation, a FastAPI backend runs the pipeline and job queue, and a React web UI runs at `localhost:5173`.

```
Web UI (React/Vite :5173) ──► Backend (FastAPI :8000) ──► ComfyUI (:8188, PyTorch on MPS)
                                   │
                                   └─ ffmpeg (clip assembly)
```

## Setup (once)

Requires macOS on Apple Silicon, git, ffmpeg, Node 18+, and [uv](https://docs.astral.sh/uv/).

```bash
scripts/setup.sh
```

Then download the image model (6.9 GB) into `models/checkpoints/`:

```bash
curl -L -o models/checkpoints/sd_xl_turbo_1.0_fp16.safetensors https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors
```

SDXL-Turbo is under Stability AI's community license; check its terms before any commercial use.

## Run

```bash
scripts/start.sh
```

Then open http://127.0.0.1:5173. Logs go to `outputs/logs/`.

## Layout

| Path | What it is |
|---|---|
| `backend/app.py` | REST + WebSocket API |
| `backend/jobs.py` | SQLite job store and a single sequential worker |
| `backend/comfy_client.py` | ComfyUI API client (queue, progress, download) |
| `backend/pipeline/` | Stages: `scenes` → `image` → `video` → `assemble` |
| `backend/workflows/` | ComfyUI workflow JSONs (API format) |
| `frontend/` | Web UI |
| `engine/ComfyUI/` | ComfyUI install (own venv; not committed) |
| `models/` | Model weights shared with ComfyUI via `extra_model_paths.yaml` |
| `outputs/<job_id>/` | Images, clips and `final.mp4` per job |

## Roadmap

1. ✅ Environment + skeleton
2. ✅ Text → image (SDXL-Turbo workflow)
3. ✅ Ken Burns motion + crossfaded ffmpeg assembly → first end-to-end video
4. Job queue polish: cancel, history gallery, re-run
5. AI motion with LTX-Video image-to-video
6. Story mode: LLM scene splitting (Ollama), TTS narration, subtitles
7. Presets and memory tuning for 16 GB
