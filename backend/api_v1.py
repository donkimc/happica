"""Happica REST API v1, for other apps on this Mac (or through the public site) to automate generation.

Authenticate with an API key (created in the web app): `Authorization: Bearer hap_…` or `X-API-Key: hap_…`.
Every generation call returns a job immediately; then either poll `GET /jobs/{id}`, block on
`GET /jobs/{id}/wait`, or pass `webhook_url` to be called when the job finishes. Finished jobs list their
files with download URLs (send the same API key header to download them).
"""
import asyncio
import json
import logging
import random
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

import app as main
from pipeline import combine, upscale

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
        if (job.get("settings") or {}).get("story"):
            out.append(("final", r["video"]))  # a story's finished 9:16 export
    for t in r.get("tracks") or []:
        out.append(("audio", t["mp3"]))
        if t.get("flac"):
            out.append(("audio_lossless", t["flac"]))
    if r.get("subtitles"):
        out.append(("subtitles", r["subtitles"]))
    if r.get("subtitles_vtt"):
        out.append(("subtitles_vtt", r["subtitles_vtt"]))
    role = "scene_image" if job["kind"] == "video" else "image"
    out += [(role, p) for p in r.get("images") or []]
    if (r.get("music") or {}).get("mp3"):
        out.append(("music", r["music"]["mp3"]))
    if r.get("cover"):
        out.append(("cover", r["cover"]))
    if r.get("caption"):
        out.append(("caption", r["caption"]))
    if (r.get("ambience") or {}).get("mp3"):
        out.append(("ambience", r["ambience"]["mp3"]))
    out += [("scene_sfx", c["mp3"]) for scene in r.get("sfx") or [] for c in scene if c.get("mp3")]
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


class CombineItem(BaseModel):
    job: str = Field(description="A finished video, music, sfx or voiceover job id")
    take: int = Field(default=1, ge=1, le=2, description="Which take of a music/sfx job (1 or 2)")


class CombineRequest(BaseModel):
    """Join finished jobs into one long file. All items must be videos, or all audio (music, sfx, voiceovers)."""
    items: list[CombineItem] = Field(min_length=1, max_length=200)
    target_seconds: float | None = Field(default=None, gt=0, le=combine.MAX_SECONDS,
                                         description="Repeat the items until this length (e.g. 3600 = 1 hour); omit to play them once")
    order: str = Field(default="sequence", pattern="^(sequence|shuffle)$", description="shuffle = a new random order each round")
    fade_in: float = Field(default=0, ge=0, le=30, description="Audio fade-in, seconds")
    fade_out: float = Field(default=0, ge=0, le=60, description="Audio fade-out, seconds")
    width: int | None = Field(default=None, ge=256, le=3840, description="Video size (default: the first clip's)")
    height: int | None = Field(default=None, ge=256, le=2160)
    seed: int | None = Field(default=None, description="Shuffle seed, for a repeatable order")
    webhook_url: str | None = WEBHOOK


def _combine_sources(items: list[dict]) -> list[tuple[str, Path]]:
    sources = []
    for it in items:
        job = main.store.get(it["job"])
        if not job:
            raise ValueError(f"job {it['job']} doesn't exist")
        sources.append(combine.source_of(job, it.get("take", 1)))
    if len({k for k, _ in sources}) > 1:
        raise ValueError("mix of video and audio items — combine videos and audio separately")
    return sources


async def _combine_task(job: dict, job_dir, report) -> dict:
    try:
        sources = _combine_sources(job["settings"]["items"])  # re-checked: a source may have been deleted meanwhile
    except ValueError as e:
        raise RuntimeError(str(e))
    return await combine.run(job, job_dir, report, sources)


main.runner.pipelines["combine"] = _combine_task


@router.post("/combine", status_code=202, summary="Join videos or audio into one long file (e.g. a 1-hour loop)")
async def create_combine(req: CombineRequest, request: Request):
    try:
        sources = _combine_sources([i.model_dump() for i in req.items])
    except ValueError as e:
        raise HTTPException(400, str(e))
    settings = req.model_dump()
    if settings["seed"] is None:
        settings["seed"] = random.randrange(2**31)
    length = f"{req.target_seconds / 60:.0f} min" if req.target_seconds else "one pass"
    title = f"Combined {len(req.items)} {sources[0][0]} item{'s' * (len(req.items) > 1)} ({length})"
    return _submit(main.runner.submit(title, settings, "combine"), request)


class UpscaleRequest(BaseModel):
    """Make a finished job's images or video (or an upload) bigger. Give `job` or `path`, and `target_height` or `scale`."""
    job: str | None = Field(default=None, description="A finished video, image or character job id")
    path: str | None = Field(default=None, description="Or an upload's path from /uploads (image or video)")
    target_height: int | None = Field(default=None, ge=256, le=2160,
                                      description="e.g. 1080, 1440 or 2160; the short side for portrait clips")
    scale: float | None = Field(default=None, ge=1.5, le=4, description="Or a factor (default 2); the long side stops at 3840")
    method: str = Field(default="ai", pattern="^(ai|anime|fast)$",
                        description="ai = Real-ESRGAN, redraws detail (slow for video: ~0.8 s/frame); anime = Real-ESRGAN for cartoons; fast = plain resize")
    webhook_url: str | None = WEBHOOK


def _upscale_sources(s: dict) -> list[Path]:
    if bool(s.get("job")) == bool(s.get("path")):
        raise ValueError("give either job or path")
    if s.get("job"):
        job = main.store.get(s["job"])
        if not job:
            raise ValueError(f"job {s['job']} doesn't exist")
        return upscale.sources_of(job)
    path = (main.OUTPUTS_DIR / s["path"]).resolve()
    if path.parent != main.UPLOADS.resolve() or not path.is_file():
        raise ValueError("path must be an upload from /uploads")
    return [path]


async def _upscale_task(job: dict, job_dir, report) -> dict:
    try:
        sources = _upscale_sources(job["settings"])
    except ValueError as e:
        raise RuntimeError(str(e))
    cancelled = lambda: (main.store.get(job["id"]) or {}).get("state") == "cancelled"
    return await upscale.run(job, job_dir, report, sources, cancelled)


main.runner.pipelines["upscale"] = _upscale_task


@router.post("/upscale", status_code=202, summary="Upscale images or a video to a higher resolution (e.g. 1080p, 4K)")
async def create_upscale(req: UpscaleRequest, request: Request):
    settings = req.model_dump()
    try:
        sources = _upscale_sources(settings)
        first = await upscale.probe(sources[0])
    except ValueError as e:
        raise HTTPException(400, str(e))
    w, h = upscale.target_size(first["width"], first["height"], req.scale, req.target_height)
    what = "video" if sources[0].suffix.lower() not in upscale.IMAGE_EXT else f"{len(sources)} image{'s' * (len(sources) > 1)}"
    title = f"Upscaled {what} {first['width']}×{first['height']} → {w}×{h} ({req.method})"
    return _submit(main.runner.submit(title, settings, "upscale"), request)


VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MAX_VIDEO_UPLOAD = 1024 * 1024 * 1024


class StoryScene(BaseModel):
    image: str = Field(min_length=2, max_length=1000, description="English picture description (image_prompt)")
    narration: str = Field(default="", max_length=600, description="Spoken exactly as written; empty = no narration")
    sfx: str | list[main.SfxCue] = Field(default_factory=list, description="A sound description, or a list of cues")
    motion: str = Field(default="", max_length=300, description="What moves (AI motion)")
    motion_mode: str | None = Field(default=None, pattern="^(kenburns|parallax|ltx|none)$")
    lyric_lines: int | None = Field(default=None, ge=0, le=40, description="Sung story: lyric lines this scene shows")


class StorySong(main.SongSpec):
    """The story's song: a new one (the spec fields), or `track` from the Music library."""
    tags: str = Field(default="", max_length=500)
    track: str = Field(default="", description="Use this Music library take instead of making a song")
    instrumental: bool = False
    placement: str | None = Field(default=None, pattern="^(bed|intro_outro|sung_story)$",
                                  description="Default: intro_outro for a song with lyrics, bed for an instrumental")
    volume: float = Field(default=0.5, ge=0, le=1)
    lyrics_subtitles: bool = True


class StoryRequest(BaseModel):
    """A whole story in one call (the story-file format): song, pictures, narration, sound effects, video and
    the final 9:16 export, plus cover.png and caption.txt."""
    id: str = Field(default="", max_length=80, description="Your story id (kept in the job settings)")
    title: str = Field(min_length=1, max_length=200)
    style: str = Field(default="", max_length=500)
    characters: dict[str, str] = Field(default_factory=dict, max_length=12, description="Name → look")
    art_characters: list[str] = Field(default_factory=list, max_length=4, description="Cartoon character ids (reference art)")
    voice: str = Field(default="st-ko-F1", pattern=main.tts.VOICE_ID)
    voice_speed: float = Field(default=1.0, ge=0.6, le=1.5,
                               description="Narration pace: 0.9 is a calm storyteller, 1.0 normal")
    motion: str = Field(default="kenburns", pattern="^(kenburns|parallax|ltx|none)$")
    scenes: list[StoryScene] = Field(min_length=1, max_length=16)
    song: StorySong | None = None
    width: int = Field(default=512, ge=256, le=1024, multiple_of=64,
                       description="Picture size; 512×896 (9:16) is the smallest the picture models handle well")
    height: int = Field(default=896, ge=256, le=1024, multiple_of=64)
    output_height: int | None = Field(default=1920, ge=256, le=2160)
    upscale: str = Field(default="fast", pattern="^(fast|ai|anime)$",
                         description="fast = resize; ai = Real-ESRGAN general; anime = Real-ESRGAN for cartoons (best for this art)")
    clip_seconds: float = Field(default=3, ge=1, le=10)
    fade: float = Field(default=0.5, ge=0, le=2)
    music_volume: float = Field(default=0.35, ge=0, le=1, description="The song's instrumental / bed under the narration")
    subtitles: str = Field(default="burn", pattern="^(off|soft|burn)$")
    caption_style: main.CaptionStyle | None = None
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    caption: str = Field(default="", max_length=2000, description="Text for caption.txt, between the title and hashtags")
    ltx_steady: bool = Field(default=True, description="AI motion: steadier sampling; faces and eyes wobble less")
    ltx_end_frame: bool = Field(default=True, description="AI motion: each clip ends on its clean picture")
    ltx_short_side: int | None = Field(default=384, ge=256, le=1024,
                                       description="AI motion draw size (short side); null = the full scene size")
    image_model: str = Field(default="turbo", pattern="^(turbo|lightning|realvis|flux|zimage)$",
                             description="realvis = RealVisXL (Quality: best real-person faces, ~2 min a picture, works with characters); turbo = SDXL-Turbo; lightning = SDXL-Lightning (same speed, ~15 s a picture at 576x1024, cleaner; both work with characters); zimage = Z-Image Turbo (~4 min a picture, follows character descriptions closely); flux = FLUX.1-schnell (~4-5 min a picture). zimage and flux can't use characters")
    seed: int | None = None
    webhook_url: str | None = WEBHOOK


def _story_settings(story: StoryRequest, song_track: str = "") -> main.JobRequest:
    """A story file → a script-mode video job."""
    script = [main.ScriptScene(image_prompt=sc.image, narration=sc.narration, motion=sc.motion, motion_mode=sc.motion_mode,
                               lyric_lines=sc.lyric_lines,
                               sfx=[main.SfxCue(prompt=sc.sfx)] if isinstance(sc.sfx, str) and len(sc.sfx.strip()) >= 2
                               else [] if isinstance(sc.sfx, str) else sc.sfx)
              for sc in story.scenes]
    song = None
    if story.song:
        s = story.song
        lyrics = "" if s.instrumental else s.lyrics
        placement = s.placement or ("intro_outro" if lyrics.strip() else "bed")
        track = song_track or s.track
        spec = None if track else main.SongSpec(**{**s.model_dump(include=set(main.SongSpec.model_fields)), "lyrics": lyrics,
                                                   "tags": s.tags or "children's song"})
        song = main.SongBlock(track=track, spec=spec, placement=placement, volume=s.volume, lyrics_subtitles=s.lyrics_subtitles)
    req = main.JobRequest(
        prompt=story.title, scene_mode="script", script=script, characters=story.characters,
        art_characters=story.art_characters, style=story.style, voice=story.voice, voice_speed=story.voice_speed, motion=story.motion,
        narrate=any(sc.narration.strip() for sc in story.scenes), subtitles=story.subtitles, width=story.width,
        height=story.height, output_height=story.output_height, upscale=story.upscale, clip_seconds=story.clip_seconds,
        fade=story.fade, music_volume=story.music_volume, caption_style=story.caption_style, seed=story.seed, song=song,
        image_model=story.image_model, ltx_steady=story.ltx_steady, ltx_end_frame=story.ltx_end_frame,
        ltx_short_side=story.ltx_short_side)
    return req


async def _create_story(story: StoryRequest, request: Request, song_track: str = "") -> dict:
    meta = {"id": story.id, "title": story.title, "hashtags": story.hashtags, "caption": story.caption}
    job = main.submit_video(_story_settings(story, song_track), {"story": meta, "webhook_url": story.webhook_url})
    return _submit(job, request)


@router.post("/stories", status_code=202,
             summary="A whole story video in one call: song, pictures, narration, sound effects, 9:16 export, cover")
async def create_story(story: StoryRequest, request: Request):
    return await _create_story(story, request)


class StoryBatch(BaseModel):
    stories: list[StoryRequest] = Field(min_length=1, max_length=20)
    song_reuse: bool = Field(default=False, description="Make the first story's song once and use it in every story")


@router.post("/stories/batch", status_code=202, summary="Queue several stories in order; returns their jobs")
async def create_story_batch(batch: StoryBatch, request: Request):
    song_track, jobs = "", []
    first = batch.stories[0].song
    if batch.song_reuse and first and not first.track:
        spec = main.MusicRequest(**{**first.model_dump(include=set(main.MusicRequest.model_fields)),
                                    "tags": first.tags or "children's song", "variations": 1})
        song_job = await main.create_music(spec)
        jobs.append(public(song_job, _base_url(request)))
        song_track = f"{song_job['id']}/v1.flac"  # ready by the time the stories (queued after it) need it
    elif batch.song_reuse and first:
        song_track = first.track
    for story in batch.stories:
        if song_track and not story.song:  # with song_reuse, a story without its own song block shares the first one's
            story = story.model_copy(update={"song": first})
        jobs.append(await _create_story(story, request, song_track))
    return {"jobs": jobs, "ids": [j["id"] for j in jobs]}


@router.post("/characters/{character_id}/turnaround", status_code=202,
             summary="3면도: a front / side / back sheet of a character (also cut into three pictures)")
async def create_turnaround(character_id: str, request: Request, req: main.TurnaroundRequest = main.TurnaroundRequest()):
    return _submit(await main.create_turnaround(character_id, req), request)


@router.post("/characters/{character_id}/refs", summary="Cartoon characters: use a 3-view sheet's pictures as references")
async def use_turnaround_as_refs(character_id: str, req: main.UseViews, request: Request):
    return public(await main.use_turnaround_as_refs(character_id, req), _base_url(request))


@router.post("/uploads", summary="Upload a picture (for slides, characters or upscaling) or a video (for upscaling); returns its path")
async def upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT and not (file.content_type or "").startswith("video/"):
        return await main.upload_image(file)
    main.UPLOADS.mkdir(parents=True, exist_ok=True)
    out = main.UPLOADS / f"{main.uuid.uuid4().hex[:16]}{ext if ext in VIDEO_EXT else '.mp4'}"
    size = 0
    with out.open("wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_VIDEO_UPLOAD:
                f.close(); out.unlink()
                raise HTTPException(413, "Videos must be under 1 GB")
            f.write(chunk)
    try:
        info = await upscale.probe(out)
    except ValueError:
        out.unlink()
        raise HTTPException(400, "That file isn't a video this app can read (MP4, MOV, WebM, MKV)")
    return {"path": str(out.relative_to(main.OUTPUTS_DIR)), "width": info["width"], "height": info["height"],
            "duration": round(info["duration"], 2)}


# ---------- jobs ----------

@router.get("/jobs", summary="List jobs, newest first")
async def list_jobs(request: Request, kind: str | None = Query(None, pattern="^(video|image|music|sfx|voice|character|combine|upscale)$"),
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


@router.post("/jobs/{job_id}/scenes/{index}/regenerate", status_code=202,
             summary="Redo one scene's picture (and its clip), optionally with a new image prompt")
async def regenerate_scene(job_id: str, index: int, request: Request, req: main.RegenerateRequest = main.RegenerateRequest()):
    return public(await main.regenerate_scene(job_id, index, req), _base_url(request))


@router.post("/jobs/{job_id}/scenes/{index}/clip", status_code=202,
             summary="Use your own video for one scene (multipart `file`; fit=trim|stretch); the story is re-assembled")
async def upload_scene_clip(job_id: str, index: int, request: Request, file: UploadFile = File(...),
                            fit: str = Query("trim", pattern="^(trim|stretch)$")):
    return public(await main.upload_scene_clip(job_id, index, file, fit), _base_url(request))


@router.post("/jobs/{job_id}/scenes/{index}/sfx/{cue}/regenerate", status_code=202,
             summary="Redo one scene sound effect (another take, or a new description) and re-mix")
async def regenerate_scene_sfx(job_id: str, index: int, cue: int, request: Request, req: main.SfxChange = main.SfxChange()):
    return public(await main.regenerate_scene_sfx(job_id, index, cue, req), _base_url(request))


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
