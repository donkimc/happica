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

Then download the models (about 18 GB in total):

```bash
curl -L -o models/checkpoints/sd_xl_turbo_1.0_fp16.safetensors https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/sd_xl_turbo_1.0_fp16.safetensors
```

```bash
curl -L -o models/checkpoints/ltxv-2b-0.9.8-distilled.safetensors https://huggingface.co/Lightricks/LTX-Video/resolve/main/ltxv-2b-0.9.8-distilled.safetensors
```

```bash
curl -L -o models/text_encoders/t5xxl_fp8_e4m3fn.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors
```

| Model | Used for | Size |
|---|---|---|
| SDXL-Turbo | Text → image | 6.9 GB |
| LTX-Video 2B 0.9.8 distilled | Image → video ("AI motion") | 6.3 GB |
| T5-XXL fp8 | LTX-Video's text encoder (runs on CPU) | 4.9 GB |

Narration voices (Kokoro TTS, 354 MB):

```bash
mkdir -p models/tts && for f in kokoro-v1.0.onnx voices-v1.0.bin; do curl -L -o models/tts/$f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/$f; done
```

Story mode needs [Ollama](https://ollama.com) running with a small model:

```bash
ollama pull qwen2.5:3b
```

Set `OLLAMA_MODEL=qwen2.5:7b` before `scripts/start.sh` for better storyboards (characters stay more consistent across scenes).

Check each model's license before any commercial use.

## Run

```bash
scripts/start.sh
```

Then open http://127.0.0.1:5173. Logs go to `outputs/logs/`.

## Speed on an M2 with 16 GB

| Step | Time |
|---|---|
| Images (SDXL-Turbo, 768×512) | ~25 s to load the model once per job, then ~5 s per image |
| Story → scenes (qwen2.5:3b) | ~15 s |
| Narration (Kokoro) | ~5–10 s per scene |
| Pan & zoom clip | < 1 s |
| AI motion clip (LTX-Video, 3 s at 768×512) | ~6.5 min |

ComfyUI starts with two flags that matter on a 16 GB Mac:

- `--cache-none` releases each model once the workflow no longer needs it, so the 4.5 GB text encoder is gone before LTX-Video samples. Without it the machine swaps heavily.
- `--fp32-vae` decodes video in fp32. The default bf16 is ~11× slower for 3D convolutions on Apple GPUs, and fp16 overflows into garbage frames.

## Layout

| Path | What it is |
|---|---|
| `backend/app.py` | REST + WebSocket API |
| `backend/jobs.py` | SQLite job store and a single sequential worker |
| `backend/comfy_client.py` | ComfyUI API client (queue, progress, download) |
| `backend/pipeline/` | Stages: `scenes` → `tts` → `image` → `video` → `assemble` (+ `captions`) |
| `backend/workflows/` | ComfyUI workflow JSONs (API format) |
| `frontend/` | Web UI |
| `engine/ComfyUI/` | ComfyUI install (own venv; not committed) |
| `models/` | Model weights shared with ComfyUI via `extra_model_paths.yaml` |
| `outputs/<job_id>/` | Images, clips and `final.mp4` per job |

## Roadmap

1. ✅ Environment + skeleton
2. ✅ Text → image (SDXL-Turbo workflow)
3. ✅ Ken Burns motion + crossfaded ffmpeg assembly → first end-to-end video
4. ✅ History gallery, re-run (new or same seed), edit & re-run, regenerate a single scene, delete
5. ✅ AI motion with LTX-Video image-to-video
6. ✅ Story mode: LLM storyboard (Ollama), Kokoro narration, timed subtitles (burned in, soft track, .srt/.vtt)
7. ✅ Presets (Quick preview, Narrated story, Vertical short, Cinematic) with time estimates; all of a job's images in one ComfyUI run; captions sized to the frame

Typical jobs: a 4-scene narrated story with pan & zoom takes about 1.5–2 minutes; with AI motion, about 6–7 minutes per scene.
