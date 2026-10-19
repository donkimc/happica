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
from config import FRONTEND_ORIGIN, OLLAMA_MODEL, OLLAMA_URL, OUTPUTS_DIR
from pipeline import tts
from jobs import JobRunner, JobStore

comfy = ComfyClient()
store = JobStore()
runner = JobRunner(store, lambda job, job_dir, report: pipeline.run(job, job_dir, report, comfy))


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = asyncio.create_task(runner.run_forever())
    yield
    worker.cancel()
    await comfy.aclose()


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


@app.get("/api/health")
async def health():
    out = {"backend": "ok"}
    try:
        stats = await comfy.system_stats()
        out.update(comfyui="ok", devices=[d.get("type") for d in stats.get("devices", [])])
    except Exception as e:
        out.update(comfyui="down", error=str(e))
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
    return runner.submit(req.prompt, req.model_dump(exclude={"prompt"}))


@app.get("/api/jobs")
async def list_jobs():
    return store.list()


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
    return runner.submit(job["prompt"], settings)


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
        await comfy.interrupt()
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
