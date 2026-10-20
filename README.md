# Happica

A local AI media studio. ComfyUI does the generation, a FastAPI backend runs the pipeline and job queue, and a React web UI runs at `localhost:5173`.

Tools: **Images** · **Story Video** (from text, a story, or your own pictures, with AI narration written from the pictures) · **Music** (songs with lyrics or instrumentals) · **Music Video** · **Sound FX** · **Voiceover** · **Characters** (a reusable face via PhotoMaker). The in-app guide is at `/guide.html`.

```
Web UI (React/Vite :5173) ──► Backend (FastAPI :8000) ──► ComfyUI (:8188)        images, video
                                   │                  └─► ComfyUI music (:8189)  songs (--gpu-only)
                                   ├─ Ollama (:11434)  storyboards
                                   ├─ Kokoro TTS       narration
                                   └─ ffmpeg           assembly, mastering
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

Music (ACE-Step 1.5 turbo with its 1.7B song planner, 10 GB):

```bash
B=https://huggingface.co/Comfy-Org/ace_step_1.5_ComfyUI_files/resolve/main/split_files
curl -L -o models/diffusion_models/acestep_v1.5_turbo.safetensors $B/diffusion_models/acestep_v1.5_turbo.safetensors
curl -L -o models/text_encoders/qwen_1.7b_ace15.safetensors $B/text_encoders/qwen_1.7b_ace15.safetensors
curl -L -o models/text_encoders/qwen_0.6b_ace15.safetensors $B/text_encoders/qwen_0.6b_ace15.safetensors
curl -L -o models/vae/ace_1.5_vae.safetensors $B/vae/ace_1.5_vae.safetensors
```

Sound effects (Stable Audio 3 small SFX, 3.5 GB):

```bash
B=https://huggingface.co/Comfy-Org/stable-audio-3/resolve/main
curl -L -o models/checkpoints/stable_audio_3_small_sfx.safetensors $B/checkpoints/stable_audio_3_small_sfx.safetensors
curl -L -o models/text_encoders/t5gemma_b_b_ul2.safetensors $B/text_encoders/t5gemma_b_b_ul2.safetensors
```

Better pictures (optional): FLUX.1-schnell as a Q5 GGUF, picked per job with `image_model: "flux"` (the **Picture model** menu). It follows long descriptions closely and draws cleaner faces and eyes than SDXL-Turbo, at ~4–5 min per picture at 576×1024; it can't use characters (PhotoMaker and IP-Adapter are SDXL-only). Apache 2.0 license. Needs the [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) custom nodes (`git clone` into `engine/ComfyUI/custom_nodes`, then `uv pip install --python engine/ComfyUI/.venv/bin/python gguf sentencepiece protobuf`) and reuses the T5-XXL above (8.8 GB):

```bash
curl -L -o models/diffusion_models/flux1-schnell-Q5_K_S.gguf https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q5_K_S.gguf
curl -L -o models/text_encoders/clip_l.safetensors https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors
curl -L -o models/vae/flux_ae.safetensors https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged/resolve/main/split_files/vae/ae.safetensors
```

Characters (PhotoMaker v1, 0.93 GB):

```bash
curl -L -o models/photomaker/photomaker-v1.bin https://huggingface.co/TencentARC/PhotoMaker/resolve/main/photomaker-v1.bin
```

Extra voices for Korean and 23 more languages (Supertonic 3, 385 MB):

```bash
backend/.venv/bin/python -c "from supertonic import TTS; TTS(model_dir='models/tts/supertonic-3')"
```

Narration voices (Kokoro TTS, 354 MB):

```bash
mkdir -p models/tts && for f in kokoro-v1.0.onnx voices-v1.0.bin; do curl -L -o models/tts/$f https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/$f; done
```

Upscaling (API only) uses two Real-ESRGAN models — 64 MB for pictures, 5 MB for video — run by `backend/tools/upscale_worker.py` in ComfyUI's Python:

```bash
curl -L -o models/upscale_models/RealESRGAN_x4plus.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth
curl -L -o models/upscale_models/realesr-general-x4v3.pth https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth
```

Cartoon characters from reference art (IP-Adapter Plus for SDXL + its CLIP-ViT-H image encoder, 3.4 GB; `scripts/setup.sh` installs the `ComfyUI_IPAdapter_plus` nodes):

```bash
curl -L -o models/ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors https://huggingface.co/h94/IP-Adapter/resolve/main/sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors
curl -L -o models/clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors https://huggingface.co/h94/IP-Adapter/resolve/main/models/image_encoder/model.safetensors
```

Sung songs in videos — vocal/instrumental separation (Demucs `htdemucs`) and lyric timing (Whisper large-v3-turbo on MLX) — run in their own venv, `engine/audio-tools/.venv`, which `scripts/setup.sh` creates. The models download on first use (~80 MB + 1.6 GB), or ahead of time:

```bash
engine/audio-tools/.venv/bin/python -c "from demucs.pretrained import get_model; get_model('htdemucs')"
engine/audio-tools/.venv/bin/python -c "from huggingface_hub import snapshot_download as s; s('mlx-community/whisper-large-v3-turbo')"
```

Rounded Korean caption fonts (Jua, Gamja Flower) are in `fonts/` under the SIL Open Font License.

Story mode needs [Ollama](https://ollama.com) running with a small model:

```bash
ollama pull qwen2.5:3b
```

The Music tab's songwriter uses `qwen2.5:7b` (`ollama pull qwen2.5:7b`; override with `SONGWRITER_MODEL`), falling back to `OLLAMA_MODEL`.

Set `OLLAMA_MODEL=qwen2.5:7b` before `scripts/start.sh` for better storyboards (characters stay more consistent across scenes).

Check each model's license before any commercial use.

## Run

```bash
scripts/start.sh
```

Then open http://127.0.0.1:5173. Logs go to `outputs/logs/`.

## REST API (v1)

Other apps automate Happica through `/api/v1` with an API key (create and revoke keys in the web app's ☰ menu → **API keys**; only a SHA-256 hash is stored). Send `Authorization: Bearer hap_…` or `X-API-Key: hap_…`. Interactive docs: `/api/v1/docs` (OpenAPI at `/api/v1/openapi.json`).

| Call | Purpose |
|---|---|
| `POST /videos` · `/images` · `/music` · `/music/draft` · `/sfx` · `/voiceovers` · `/characters` | Start a job (202 + job); optional `webhook_url` |
| `POST /videos` with `scene_mode: "script"` | Scenes written outside Happica: `script: [{image_prompt, narration, motion, motion_mode, sfx: [{prompt, at, duration, volume}]}]` (≤16), `characters: {name: look}`. No story AI; narration spoken as written; per-scene motion (kenburns/ltx/none) |
| `POST /jobs/{id}/scenes/{i}/regenerate` · `/scenes/{i}/sfx/{k}/regenerate` | Redo one scene's picture, or one sound effect, and rebuild the video |
| `song` on `POST /videos` | A sung song: `{track \| spec, placement: bed \| intro_outro \| sung_story, volume, lyrics_subtitles}`. `spec` makes a new song (title, tags, lyrics, bpm, keyscale, language, duration). Lyrics are timed with Demucs + Whisper and burned in yellow; `lyrics_timing` is saved on the music job |
| `caption_style`, `output_height`, `upscale` on `POST /videos` | Caption font (`rounded` Jua, `handwritten` Gamja Flower, `system`), size, colour, `bottom`/`lower_third`, box; export e.g. 576×1024 → `output_height: 1920` with `fast` or `ai` upscaling |
| `art_characters` on `POST /videos`, `kind: "art"` on `POST /characters` | Cartoon characters from 1–3 reference pictures (`refs`) via IP-Adapter; scenes that name one are drawn from its pictures |
| `POST /stories` | **A whole story video in one call** — the story-file format (`title`, `style`, `characters`, `voice`, `motion`, `scenes: [{image, narration, sfx}]`, `song`, `hashtags`, `caption`). Song, pictures, narration, SFX, video and the 1080×1920 export run as one job; files include `final`, `cover` (cover.png, first scene + title) and `caption` (caption.txt) |
| `POST /stories/batch` | Up to 20 stories, queued in order; `song_reuse: true` makes the first story's song once and uses it in all |
| `POST /combine` | **API only.** Join finished videos, or finished audio (music, sfx, voiceovers), into one long file: `items: [{job, take}]`, `target_seconds` (repeat up to 4 h, e.g. 3600), `order` sequence/shuffle, `fade_in`/`fade_out`, `width`/`height` for video |
| `POST /upscale` | **API only.** Make a finished video/image/character job (`job`) or an upload (`path`) bigger: `target_height` (e.g. 1080, 2160) or `scale` (1.5–4), `method` `ai` (Real-ESRGAN) or `fast` (resize) |
| `POST /uploads` | Upload a picture (multipart `file`) for slides, characters or upscaling, or a video (MP4/MOV/WebM/MKV, up to 1 GB) for upscaling |
| `GET /jobs/{id}` · `/jobs/{id}/wait?timeout=` · `/jobs?kind=&state=&limit=` | Status, block until finished, list |
| `POST /jobs/{id}/cancel` · `/jobs/{id}/rerun` · `DELETE /jobs/{id}` | Manage jobs |
| `GET /health` · `/voices` · `/music/options` | Info |

Jobs return `files` with a `role` (video, audio, audio_lossless, image, scene_image, subtitles, music, ambience…) and a download `url` (send the same key). Webhooks POST `{"event": "job.done|job.failed|job.cancelled", "job": …}` with up to 3 tries. Key management endpoints (`/api/keys`) accept only a logged-in session, never an API key.

## Online access

```
Browser ──https──► Railway "web" (frontend/Dockerfile: Caddy serves the app, proxies /api /ws /outputs)
                       ──► Cloudflare quick tunnel ──► this Mac: backend :8000 (checks the login)
```

- First run of `start.sh` creates `.env` (git-ignored) with `HAPPICA_PASSWORD` (random; change it) and `HAPPICA_SECRET`.
- Every request needs a login session (signed cookie, 30 days); failed logins are rate-limited.
- With `HAPPICA_ONLINE=1` in `.env`, `start.sh` also starts `cloudflared`, and because a quick tunnel's address changes on every start, writes the new one to the Railway service's `BACKEND_URL` with the Railway CLI (folder linked once with `railway link`). It also runs `caffeinate` so the Mac doesn't idle-sleep.
- Railway service: root directory `/frontend`, Dockerfile build, `PORT=8080`, watch pattern `/frontend/**`.
- For a fixed tunnel address, a named Cloudflare tunnel (free account + a domain) or Tailscale Funnel can replace the quick tunnel.

## Speed on an M2 with 16 GB

| Step | Time |
|---|---|
| Images (SDXL-Turbo, 768×512) | ~25 s to load the model once per job, then ~5 s per image |
| Images (FLUX.1-schnell Q5, 576×1024) | ~40 s to load, then ~4–5 min per image (4 steps at ~60 s); 512×896 ≈ 3.5 min |
| Story → scenes (qwen2.5:3b) | ~15 s |
| Narration (Kokoro) | ~5–10 s per scene |
| Pan & zoom clip | < 1 s |
| AI motion clip (LTX-Video, 3 s at 768×512) | ~6.5 min |
| Song, 2 takes (ACE-Step 1.5) | ~2 min for 30 s, ~3 min for 1 min |
| Sound effect, 2 takes (Stable Audio 3 small SFX) | ~5 s for 10 s, ~15 s for 45 s (+~8 s with AI detail) |
| Scene sound effects (all of a video's cues in one run) | 9 cues in ~22 s; cached, so re-mixing is free |
| Sung song prep (once per song, then cached) | Demucs stems ~43 s (CPU — htdemucs exceeds an MPS limit) + Whisper large-v3-turbo ~20 s for a 70 s song; alignment < 0.1 s |
| Cartoon character (IP-Adapter Plus) | ~+40 s per job to load CLIP-ViT-H + adapter, images ~2× slower (3 scenes: ~140 s vs 71 s); image ComfyUI peaks ~5 GB vs ~2 GB |
| 1080×1920 export | `fast`: part of the ~80 s assembly of a 1.5-min video; `ai`: ~1 s per frame |
| **One-call story** (`POST /stories`: new 70 s sung song as intro/outro, 9 scenes, 7 narrated, 9 SFX, pan & zoom, 1080×1920) | **~7.5 min**: song + stems + lyrics 2 min 53 s, narration 42 s, images 2 min, clips 13 s, SFX 22 s, assembly + export 82 s |
| Combine into 1 hour (API) | ~6 s for video (clips are joined without re-encoding), ~35 s for audio |
| AI upscale (Real-ESRGAN, API) | ~25 s per 768×512 image; video ~0.8 s per frame (a 30 s clip ≈ 10 min). `fast` resize: seconds |

For long upscaled videos, upscale the short clips first, then combine them: combining copies video instead of re-encoding it.

ComfyUI starts with two flags that matter on a 16 GB Mac:

- `--cache-none` releases each model once the workflow no longer needs it, so the 4.5 GB text encoder is gone before LTX-Video samples. Without it the machine swaps heavily.
- `--fp32-vae` decodes video in fp32. The default bf16 is ~11× slower for 3D convolutions on Apple GPUs, and fp16 overflows into garbage frames.
- Music runs in a **second ComfyUI instance with `--gpu-only`**: ACE-Step's 1.7B song planner is ~8× faster on the GPU than on the CPU, where ComfyUI would otherwise put it. `--gpu-only` slows LTX-Video by ~20%, so the two instances are kept apart; jobs run one at a time and idle instances hold almost no memory.
- Song audio is decoded in tiles: one MPS convolution can't output a whole song's samples.

## Layout

| Path | What it is |
|---|---|
| `backend/app.py` | REST + WebSocket API |
| `backend/jobs.py` | SQLite job store and a single sequential worker |
| `backend/comfy_client.py` | ComfyUI API client (queue, progress, download) |
| `backend/pipeline/` | Stages: `scenes` → `tts` → `image` → `video` → `assemble` (+ `captions`) |
| `backend/workflows/` | ComfyUI workflow JSONs (API format) |
| `frontend/` | Web UI (`MusicStudio.tsx` is the Music tab; `public/guide.html` the user guide) |
| `engine/ComfyUI/` | ComfyUI install (own venv; not committed) |
| `models/` | Model weights shared with ComfyUI via `extra_model_paths.yaml` |
| `outputs/<job_id>/` | Images, clips and `final.mp4` per video job; `v1/v2.mp3` and `.flac` per song |

## Roadmap

1. ✅ Environment + skeleton
2. ✅ Text → image (SDXL-Turbo workflow)
3. ✅ Ken Burns motion + crossfaded ffmpeg assembly → first end-to-end video
4. ✅ History gallery, re-run (new or same seed), edit & re-run, regenerate a single scene, delete
5. ✅ AI motion with LTX-Video image-to-video
6. ✅ Story mode: LLM storyboard (Ollama), Kokoro narration, timed subtitles (burned in, soft track, .srt/.vtt)
7. ✅ Presets (Quick preview, Narrated story, Vertical short, Cinematic) with time estimates; all of a job's images in one ComfyUI run; captions sized to the frame

Typical jobs: a 4-scene narrated story with pan & zoom takes about 1.5–2 minutes; with AI motion, about 6–7 minutes per scene.

## Music roadmap

1. ✅ M1 — ACE-Step 1.5 workflow, benchmarked and tuned for the M2
2. ✅ M2 — Music Studio tab: style tags, lyrics editor, instrumental, 2 takes, library with waveform players
3. ✅ M3 — Simple mode: describe a song; qwen2.5:7b drafts title, tags, lyrics (sized to the length) and BPM/key/language for review, or creates directly
4. ✅ M4 — Background music in videos: Off / Auto (LLM picks a style from the story, ACE-Step scores the exact length) / From library; looped or trimmed, faded, sidechain-ducked under narration, level control; Change music re-mixes a finished video
5. M5 — Polish: guide, presets with music

## Sound effects

- ✅ Sound FX tab: description with quick ideas, 3–45 s, seamless loops (equal-power tail-to-head crossfade), AI-detailed descriptions, 2 takes, library
- ✅ Ambience layer in videos: Off / Auto (AI describes the setting, makes a loop) / From Sound library; mixed with the music and ducked under the narration; Change sound swaps music and ambience, New score / New ambience regenerate Auto layers
