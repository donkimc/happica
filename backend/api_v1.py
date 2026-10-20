"""Happica REST API v1, for other apps on this Mac (or through the public site) to automate generation.

Authenticate with an API key (created in the web app): `Authorization: Bearer hap_…` or `X-API-Key: hap_…`.
Every generation call returns a job immediately; then either poll `GET /jobs/{id}`, block on
`GET /jobs/{id}/wait`, or pass `webhook_url` to be called when the job finishes. Finished jobs list their
files with download URLs (send the same API key header to download them).
"""
import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

import app as main

log = logging.getLogger("happica.api")
router = APIRouter(prefix="/api/v1")
FINAL = ("done", "failed", "cancelled")
WEBHOOK = Field(default=None, pattern=r"^https?://", max_length=500,
                description="Optional URL that gets a POST with the job (JSON) when it finishes, fails or is cancelled")


# ---------- requests: the web app's models plus an optional webhook ----------

class VideoRequest(main.JobRequest):
    """scene_mode: "lines" (one line per scene), "llm" (story: AI writes the scenes), "slides" (your pictures —
    upload them first via /uploads), "musicvideo" (song_track from the Music library)."""
    webhook_url: str | None = WEBHOOK


class ImageRequest(main.ImageRequest):
    webhook_url: str | None = WEBHOOK


class MusicRequest(main.MusicRequest):
    webhook_url: str | None = WEBHOOK


class SfxRequest(main.SfxRequest):
    webhook_url: str | None = WEBHOOK


class VoiceoverRequest(main.VoiceRequest):
    webhook_url: str | None = WEBHOOK


class CharacterRequest(main.CharacterRequest):
    webhook_url: str | None = WEBHOOK


class Rerun(BaseModel):
    keep_seed: bool = False


# ---------- responses ----------

def _base_url(request: Request) -> str:
    """The address the caller used — through the Railway site it's the public domain, not the tunnel."""
    h = request.headers
    host = h.get("x-forwarded-host") or h.get("host") or request.url.netloc
    proto = h.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


def _files(job: dict) -> list[dict]:
    """Every downloadable output of a job, with a role so automation can pick what it needs."""
    r = job.get("result") or {}
    out: list[tuple[str, str]] = []
    if r.get("video"):
        out.append(("video", r["video"]))
    for t in r.get("tracks") or []:
        out += [("audio", t["mp3"]), ("audio_lossless", t["flac"])]
    if r.get("subtitles"):
        out.append(("subtitles", r["subtitles"]))
    if r.get("subtitles_vtt"):
        out.append(("subtitles_vtt", r["subtitles_vtt"]))
    role = "scene_image" if job["kind"] == "video" else "image"
    out += [(role, p) for p in r.get("images") or []]
    if (r.get("music") or {}).get("mp3"):
        out.append(("music", r["music"]["mp3"]))
    if (r.get("ambience") or {}).get("mp3"):
        out.append(("ambience", r["ambience"]["mp3"]))
    return [{"role": role, "path": path, "name": path.rsplit("/", 1)[-1]} for role, path in out]


def public(job: dict, base: str = "") -> dict:
    files = [{**f, "url": f"{base}/outputs/{f['path']}"} for f in _files(job)]
    return {
        "id": job["id"], "kind": job["kind"], "state": job["state"], "stage": job["stage"],
        "progress": job["progress"], "error": job["error"], "title": job["prompt"],
        "created": job["created"], "updated": job["updated"], "settings": job["settings"],
        "files": files, "result": job["result"],
    }


def _submit(job: dict, request: Request) -> dict:
    base = _base_url(request)
    if job["settings"].get("webhook_url"):
        # Remember the caller's address so the webhook can give full download URLs too
        job = main.store.update(job["id"], settings={**job["settings"], "api_base_url": base})
    return public(job, base)


# ---------- generation ----------

@router.post("/videos", status_code=202, summary="Make a video (story, lines, your pictures or a music video)")
async def create_video(req: VideoRequest, request: Request):
    return _submit(await main.create_job(req), request)


@router.post("/images", status_code=202, summary="Make 1-4 images")
async def create_images(req: ImageRequest, request: Request):
    return _submit(await main.create_images(req), request)


@router.post("/music", status_code=202, summary="Make a song or instrumental (1-2 takes)")
async def create_music(req: MusicRequest, request: Request):
    return _submit(await main.create_music(req), request)


@router.post("/music/draft", summary="Let the AI write a song (title, style, lyrics, BPM, key) from a description")
async def draft_song(req: main.DraftRequest):
    return await main.draft_song(req)


@router.post("/sfx", status_code=202, summary="Make a sound effect or ambience loop")
async def create_sfx(req: SfxRequest, request: Request):
    return _submit(await main.create_sfx(req), request)


@router.post("/voiceovers", status_code=202, summary="Turn text into narration (MP3 + subtitles)")
async def create_voiceover(req: VoiceoverRequest, request: Request):
    return _submit(await main.create_voiceover(req), request)


@router.post("/characters", status_code=202, summary="Create a reusable character from an uploaded face photo")
async def create_character(req: CharacterRequest, request: Request):
    return _submit(await main.create_character(req), request)


@router.post("/uploads", summary="Upload a picture (for slides or characters); returns its path")
async def upload(file: UploadFile = File(...)):
    return await main.upload_image(file)


# ---------- jobs ----------

@router.get("/jobs", summary="List jobs, newest first")
async def list_jobs(request: Request, kind: str | None = Query(None, pattern="^(video|image|music|sfx|voice|character)$"),
                    state: str | None = Query(None, pattern="^(queued|running|done|failed|cancelled)$"),
                    limit: int = Query(50, ge=1, le=500)):
    base = _base_url(request)
    return [public(j, base) for j in main.store.list(kind, limit, state)]


@router.get("/jobs/{job_id}", summary="A job's status, progress and files")
async def get_job(job_id: str, request: Request):
    return public(main._get_or_404(job_id), _base_url(request))


@router.get("/jobs/{job_id}/wait", summary="Wait until a job finishes (or the timeout passes), then return it")
async def wait_job(job_id: str, request: Request, timeout: float = Query(600, ge=1, le=3600)):
    job = main._get_or_404(job_id)
    if job["state"] not in FINAL:
        q = main.runner.subscribe(job_id)
        try:
            deadline = time.monotonic() + timeout
            while job["state"] not in FINAL and (left := deadline - time.monotonic()) > 0:
                try:
                    job = await asyncio.wait_for(q.get(), left)
                except asyncio.TimeoutError:
                    break
        finally:
            main.runner.unsubscribe(job_id, q)
        job = main.store.get(job_id) or job
    return public(job, _base_url(request))


@router.post("/jobs/{job_id}/cancel", summary="Cancel a queued or running job")
async def cancel_job(job_id: str, request: Request):
    return public(await main.cancel_job(job_id), _base_url(request))


@router.post("/jobs/{job_id}/rerun", status_code=202, summary="Run a job again with the same settings")
async def rerun_job(job_id: str, request: Request, req: Rerun = Rerun()):
    return public(await main.rerun_job(job_id, main.RerunRequest(keep_seed=req.keep_seed)), _base_url(request))


@router.delete("/jobs/{job_id}", summary="Delete a finished job and its files")
async def delete_job(job_id: str):
    return await main.delete_job(job_id)


# ---------- info ----------

@router.get("/health", summary="Whether the generation engines are running")
async def health():
    return await main.health()


@router.get("/voices", summary="All narration voices (id, name, language, gender)")
async def voices():
    return await main.list_voices()


@router.get("/music/options", summary="Keys, time signatures and lyric languages for /music")
async def music_options():
    return await main.music_options()


# ---------- webhooks ----------

def notify(job: dict) -> None:
    """Runner hook: POST the finished job to its webhook_url, if it has one (3 tries, in the background)."""
    url = (job.get("settings") or {}).get("webhook_url")
    if url:
        asyncio.get_running_loop().create_task(_post_webhook(url, job))


async def _post_webhook(url: str, job: dict) -> None:
    body = json.dumps({"event": f"job.{job['state']}", "job": public(job, job["settings"].get("api_base_url", ""))}, default=str)
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(3):
            try:
                r = await client.post(url, content=body, headers={"Content-Type": "application/json",
                                                                   "User-Agent": "Happica-Webhook/1"})
                if r.status_code < 500:
                    return
            except httpx.HTTPError as e:
                log.warning("webhook %s failed: %s", url, e)
            await asyncio.sleep(2 ** attempt * 5)


# ---------- docs: only the v1 API, with the API-key scheme ----------

def openapi() -> dict:
    if main.app.openapi_schema:
        return main.app.openapi_schema
    # Built from this router (its routes carry the /api/v1 prefix), so the web app's internal routes stay out
    schema = get_openapi(title="Happica API", version="1.0.0", description=__doc__, routes=router.routes)
    schema.setdefault("components", {})["securitySchemes"] = {
        "ApiKey": {"type": "http", "scheme": "bearer", "description": "An API key from the web app (hap_…)"}}
    schema["security"] = [{"ApiKey": []}]
    main.app.openapi_schema = schema
    return schema
