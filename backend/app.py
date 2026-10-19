"""Happica backend: REST + WebSocket API in front of ComfyUI."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import httpx

import pipeline
from comfy_client import ComfyClient
from config import FRONTEND_ORIGIN, MUSIC_COMFY_URL, OLLAMA_MODEL, OLLAMA_URL, OUTPUTS_DIR
from pipeline import music, sfx, songwriter, tts
from jobs import JobRunner, JobStore

comfy = ComfyClient()
music_comfy = ComfyClient(MUSIC_COMFY_URL)
store = JobStore()
runner = JobRunner(store, {
    "video": lambda job, job_dir, report: pipeline.run(job, job_dir, report, comfy, music_comfy),
    "music": lambda job, job_dir, report: music.run(job, job_dir, report, music_comfy),
    "sfx": lambda job, job_dir, report: sfx.run(job, job_dir, report, music_comfy),  # audio models share the GPU-only instance
})


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = asyncio.create_task(runner.run_forever())
    yield
    worker.cancel()
    await comfy.aclose()
    await music_comfy.aclose()


app = FastAPI(title="Happica", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_ORIGIN], allow_methods=["*"], allow_headers=["*"])
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")


class JobRequest(BaseModel):
    prompt: str = Field(min_length=1)
    width: int = 768
    height: int = 512
    max_scenes: int = Field(default=4, ge=1, le=12)
    motion: str = Field(default="kenburns", pattern="^(none|kenburns|ltx)$")
    clip_seconds: float = Field(default=3, ge=1, le=10)
    fade: float = Field(default=0.5, ge=0, le=2)
    style: str = ""
    scene_mode: str = Field(default="lines", pattern="^(lines|llm)$")
    narrate: bool = False
    voice: str = Field(default="af_heart", pattern="^[a-z]{2}_[a-z]+$")
    subtitles: str = Field(default="burn", pattern="^(off|soft|burn)$")
    image_steps: int = Field(default=4, ge=1, le=8)
    video_steps: int = Field(default=8, ge=4, le=30)
    seed: int | None = None
    music: str = Field(default="off", pattern="^(off|auto|library)$")
    music_track: str = ""  # a library take's .flac path under outputs/, when music == "library"
    music_title: str = ""
    music_volume: float = Field(default=0.3, ge=0, le=1)
    ambience: str = Field(default="off", pattern="^(off|auto|library)$")
    ambience_track: str = ""  # a Sound library take's .flac path under outputs/, when ambience == "library"
    ambience_title: str = ""
    ambience_volume: float = Field(default=0.35, ge=0, le=1)


def _check_library_track(choice: str, track: str, what: str = "song from the Music library") -> None:
    """A library pick must be a generated .flac inside outputs/<job>/ (no paths elsewhere on the Mac)."""
    if choice != "library":
        return
    path = (OUTPUTS_DIR / track).resolve()
    if not track or path.parent.parent != OUTPUTS_DIR.resolve() or path.suffix != ".flac" or not path.is_file():
        raise HTTPException(400, f"Pick a {what}")


@app.get("/api/health")
async def health():
    out = {"backend": "ok"}
    try:
        stats = await comfy.system_stats()
        out.update(comfyui="ok", devices=[d.get("type") for d in stats.get("devices", [])])
    except Exception as e:
        out.update(comfyui="down", error=str(e))
    try:
        await music_comfy.system_stats()
        out["music"] = "ok"
    except Exception:
        out["music"] = "down"
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            tags = (await client.get(f"{OLLAMA_URL}/api/tags")).json()
        out["ollama"] = "ok" if any(m["name"] == OLLAMA_MODEL for m in tags.get("models", [])) else "model missing"
    except Exception:
        out["ollama"] = "down"
    out["llm_model"] = OLLAMA_MODEL
    return out


@app.get("/api/voices")
async def list_voices():
    return await asyncio.to_thread(tts.voices)


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    _check_library_track(req.music, req.music_track)
    _check_library_track(req.ambience, req.ambience_track, "sound from the Sound library")
    return runner.submit(req.prompt, req.model_dump(exclude={"prompt"}))


class SoundChange(BaseModel):
    music: str = Field(pattern="^(off|auto|library)$")
    music_track: str = ""
    music_title: str = ""
    music_volume: float = Field(default=0.3, ge=0, le=1)
    ambience: str = Field(default="off", pattern="^(off|auto|library)$")
    ambience_track: str = ""
    ambience_title: str = ""
    ambience_volume: float = Field(default=0.35, ge=0, le=1)


@app.post("/api/jobs/{job_id}/sound")
async def change_sound(job_id: str, req: SoundChange):
    """Replace a video's music and/or ambience or their levels and re-mix; images and clips stay as they are.

    A layer is only picked again when its choice changed, or when it is "auto" and was asked for again
    (so moving a level slider never re-generates a score)."""
    job = _get_or_404(job_id)
    _require_idle(job)
    if job["kind"] != "video" or not (job["result"] or {}).get("video"):
        raise HTTPException(400, "Only finished videos can change their sound")
    _check_library_track(req.music, req.music_track)
    _check_library_track(req.ambience, req.ambience_track, "sound from the Sound library")
    old, new = job["settings"], req.model_dump()
    regenerate = {
        layer for layer in ("music", "ambience")
        if new[layer] != old.get(layer, "off") or new[f"{layer}_track"] != old.get(f"{layer}_track", "")
        or (new[layer] == "auto" and not (job["result"] or {}).get(layer))
    }
    store.update(job_id, settings={**old, **new})
    task = lambda j, job_dir, report: pipeline.change_sound(j, job_dir, report, music_comfy, regenerate)
    return runner.enqueue(job_id, task, stage="sound")


class AutoAgain(BaseModel):
    layer: str = Field(pattern="^(music|ambience)$")


@app.post("/api/jobs/{job_id}/sound/regenerate")
async def regenerate_sound(job_id: str, req: AutoAgain):
    """Make a fresh auto score or ambience for a finished video, keeping everything else."""
    job = _get_or_404(job_id)
    _require_idle(job)
    if job["settings"].get(req.layer) != "auto":
        raise HTTPException(400, f"{req.layer} isn't set to Auto")
    task = lambda j, job_dir, report: pipeline.change_sound(j, job_dir, report, music_comfy, {req.layer})
    return runner.enqueue(job_id, task, stage=req.layer)


@app.get("/api/jobs")
async def list_jobs(kind: str | None = None):
    return store.list(kind)


class MusicRequest(BaseModel):
    title: str = ""
    tags: str = Field(min_length=1, max_length=500)
    lyrics: str = Field(default="", max_length=5000)
    instrumental: bool = False
    duration: float = Field(default=60, ge=10, le=240)
    bpm: int = Field(default=110, ge=30, le=300)
    keyscale: str = "C major"
    timesignature: str = Field(default="4", pattern="^[2346]$")
    language: str = Field(default="en", pattern="^[a-z]{2,3}$")
    variations: int = Field(default=2, ge=1, le=2)
    seed: int | None = None


@app.post("/api/music")
async def create_music(req: MusicRequest):
    if not req.instrumental and not req.lyrics.strip():
        raise HTTPException(400, "Add lyrics, or switch on Instrumental")
    settings = req.model_dump()
    return runner.submit(req.title.strip() or req.tags.strip()[:60], settings, "music")


class DraftRequest(BaseModel):
    description: str = Field(min_length=3, max_length=1000)
    instrumental: bool = False
    duration: float = Field(default=60, ge=10, le=240)


@app.post("/api/music/draft")
async def draft_song(req: DraftRequest):
    """Ask the local LLM for a title, style tags, lyrics and settings; nothing is generated yet."""
    try:
        return await songwriter.draft(req.description, req.instrumental, req.duration, await music_options())
    except RuntimeError as e:
        raise HTTPException(503, str(e))


class SfxRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=500)
    duration: float = Field(default=10, ge=1, le=47)
    loop: bool = False
    enhance: bool = True
    variations: int = Field(default=2, ge=1, le=2)
    seed: int | None = None


@app.post("/api/sfx")
async def create_sfx(req: SfxRequest):
    return runner.submit(req.prompt.strip()[:80], req.model_dump(), "sfx")


@app.get("/api/music/options")
async def music_options():
    """Keys, time signatures and lyric languages ACE-Step accepts, read from the music instance."""
    try:
        return await music.options(music_comfy)
    except Exception:
        raise HTTPException(503, "The music engine isn't running")


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_or_404(job_id)


def _get_or_404(job_id: str) -> dict:
    job = store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def _require_idle(job: dict) -> None:
    if job["state"] in ("queued", "running"):
        raise HTTPException(409, "job is still queued or running")


class RerunRequest(BaseModel):
    keep_seed: bool = False


@app.post("/api/jobs/{job_id}/rerun")
async def rerun_job(job_id: str, req: RerunRequest = RerunRequest()):
    """Start a new job with the same prompt and settings (a fresh seed unless keep_seed)."""
    job = _get_or_404(job_id)
    settings = dict(job["settings"])
    settings["seed"] = (job["result"] or {}).get("seed", settings.get("seed")) if req.keep_seed else None
    return runner.submit(job["prompt"], settings, job["kind"])


class RegenerateRequest(BaseModel):
    prompt: str | None = None


@app.post("/api/jobs/{job_id}/scenes/{index}/regenerate")
async def regenerate_scene(job_id: str, index: int, req: RegenerateRequest = RegenerateRequest()):
    job = _get_or_404(job_id)
    _require_idle(job)
    if not job["result"] or not 0 <= index < len(job["result"].get("images", [])):
        raise HTTPException(400, "scene has no image to regenerate")
    task = lambda j, job_dir, report: pipeline.regenerate_scene(j, job_dir, report, comfy, index, req.prompt)
    return runner.enqueue(job_id, task, stage=f"scene {index + 1}")


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    _require_idle(_get_or_404(job_id))
    runner.delete(job_id)
    return {"deleted": job_id}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    job = _get_or_404(job_id)
    if job["state"] == "running":
        await (comfy if job["kind"] == "video" else music_comfy).interrupt()
    if job["state"] in ("queued", "running"):
        job = store.update(job_id, state="cancelled")
    return job


@app.websocket("/ws/jobs/{job_id}")
async def job_events(ws: WebSocket, job_id: str):
    await ws.accept()
    job = store.get(job_id)
    if not job:
        await ws.close(code=4404)
        return
    q = runner.subscribe(job_id)
    try:
        await ws.send_json(job)
        while job["state"] in ("queued", "running"):
            job = await q.get()
            await ws.send_json(job)
    except WebSocketDisconnect:
        pass
    finally:
        runner.unsubscribe(job_id, q)
