"""Happica backend: REST + WebSocket API in front of ComfyUI."""
import asyncio
import io
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import httpx

import apikeys
import auth
import pipeline
from apikeys import KeyStore
from comfy_client import ComfyClient
from config import FRONTEND_ORIGIN, MUSIC_COMFY_URL, OLLAMA_MODEL, OLLAMA_URL, OUTPUTS_DIR, VISION_MODEL
from pipeline import character, images, music, sfx, songwriter, tts, voiceover
from jobs import JobRunner, JobStore

comfy = ComfyClient()
music_comfy = ComfyClient(MUSIC_COMFY_URL)
store = JobStore()
runner = JobRunner(store, {
    "video": lambda job, job_dir, report: pipeline.run(job, job_dir, report, comfy, music_comfy),
    "music": lambda job, job_dir, report: music.run(job, job_dir, report, music_comfy),
    "sfx": lambda job, job_dir, report: sfx.run(job, job_dir, report, music_comfy),  # audio models share the GPU-only instance
    "image": lambda job, job_dir, report: images.run(job, job_dir, report, comfy),
    "voice": lambda job, job_dir, report: voiceover.run(job, job_dir, report),  # Kokoro runs in-process on the CPU
    "character": lambda job, job_dir, report: character.run(job, job_dir, report, comfy),
})


@asynccontextmanager
async def lifespan(_: FastAPI):
    if recovered := runner.recover():  # jobs a restart interrupted start again
        print(f"Re-queued {len(recovered)} job(s) interrupted by a restart: {', '.join(recovered)}", flush=True)
    worker = asyncio.create_task(runner.run_forever())
    yield
    worker.cancel()
    await comfy.aclose()
    await music_comfy.aclose()


app = FastAPI(title="Happica", lifespan=lifespan, docs_url="/api/v1/docs", redoc_url=None,
              openapi_url="/api/v1/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_ORIGIN], allow_methods=["*"], allow_headers=["*"])
app.mount("/outputs", StaticFiles(directory=OUTPUTS_DIR), name="outputs")
keys = KeyStore()


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Everything — API, generated files, docs — needs a login session or an API key (other apps), except
    logging in itself. Managing API keys needs a real login, so a leaked key can't make or keep keys."""
    path = request.url.path
    if path in auth.OPEN_PATHS or auth.valid(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if not path.startswith("/api/keys") and (key := apikeys.from_headers(request.headers)):
        who = auth.client_id(request)
        if not auth.allowed_attempt(who):
            return JSONResponse({"detail": "Too many invalid keys — try again in 15 minutes"}, status_code=429)
        if record := keys.verify(key):
            request.state.api_key = record
            return await call_next(request)
        auth.note_failure(who)
        return JSONResponse({"detail": "Invalid or revoked API key"}, status_code=401)
    return JSONResponse({"detail": "Please log in, or send an API key (Authorization: Bearer hap_…)"}, status_code=401)


class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=60)


@app.get("/api/keys")
async def list_keys():
    return keys.list()


@app.post("/api/keys")
async def create_key(req: NewKey):
    record, key = keys.create(req.name.strip())
    return {**record, "key": key}  # the only time the full key is shown


@app.delete("/api/keys/{key_id}")
async def revoke_key(key_id: str):
    if not keys.revoke(key_id):
        raise HTTPException(404, "key not found")
    return {"revoked": key_id}


class Login(BaseModel):
    password: str = Field(max_length=200)


@app.post("/api/login")
async def login(req: Login, request: Request):
    who = auth.client_id(request)
    if not auth.allowed_attempt(who):
        raise HTTPException(429, "Too many wrong passwords — try again in 15 minutes")
    if not auth.check_password(who, req.password):
        await asyncio.sleep(1)  # slows down guessing
        raise HTTPException(401, "Wrong password")
    response = JSONResponse({"ok": True})
    response.set_cookie(auth.COOKIE, auth.new_session(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=auth.is_secure(request), path="/")
    return response


@app.get("/api/session")
async def session(request: Request):
    return {"logged_in": auth.valid(request.cookies.get(auth.COOKIE))}


@app.post("/api/logout")
async def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


class Slide(BaseModel):
    image: str  # a path under outputs/: an upload, or an image from the Image library or a video
    text: str = Field(default="", max_length=400)  # narration / caption for this slide


class SfxCue(BaseModel):
    prompt: str = Field(min_length=2, max_length=300, description="What is heard, in English, e.g. \"magical sparkle chime\"")
    at: float = Field(default=0.15, ge=0, le=60, description="Seconds after the scene appears")
    duration: float = Field(default=3, ge=1, le=10, description="Length of the sound, seconds")
    volume: float = Field(default=0.6, ge=0, le=1, description="Level relative to the narration")


class ScriptScene(BaseModel):
    image_prompt: str = Field(min_length=2, max_length=1000,
                              description="English picture description; character names are expanded with their look")
    motion: str = Field(default="", max_length=300, description="What moves (used by AI motion)")
    narration: str = Field(default="", max_length=600, description="Spoken exactly as written")
    sfx: list[SfxCue] = Field(default_factory=list, max_length=4, description="Sound effects in this scene")
    motion_mode: str | None = Field(default=None, pattern="^(kenburns|ltx|none)$",
                                    description="Overrides the job's motion for this scene; none = a still picture")
    lyric_lines: int | None = Field(default=None, ge=0, le=40,
                                    description="Sung story: how many lyric lines this scene shows (in order); when every "
                                                "scene sets it, scenes change exactly where their lines start")


class CaptionStyle(BaseModel):
    font_size_scale: float = Field(default=1.0, ge=0.5, le=2.0, description="1 = default size")
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$", description="Narration subtitles")
    lyric_color: str = Field(default="#ffe14d", pattern=r"^#[0-9a-fA-F]{6}$", description="Sung-lyric subtitles")
    position: str = Field(default="bottom", pattern="^(bottom|lower_third)$")
    box: bool = Field(default=False, description="A rounded dark panel behind the text (instead of an outline)")
    font: str = Field(default="rounded", pattern="^(rounded|handwritten|system)$",
                      description="Korean text: rounded (Jua), handwritten (Gamja Flower) or system (Apple SD Gothic Neo)")


class SongSpec(BaseModel):
    """A song made for this video (like POST /music, one take)."""
    title: str = Field(default="", max_length=200)
    tags: str = Field(min_length=1, max_length=500, description="Style, e.g. \"korean children's song, cheerful, cute female vocal\"")
    lyrics: str = Field(default="", max_length=5000, description="With [verse]/[chorus] tags; empty = instrumental")
    bpm: int = Field(default=110, ge=30, le=300)
    keyscale: str = "C major"
    timesignature: str = Field(default="4", pattern="^[2346]$")
    language: str = Field(default="ko", pattern="^[a-z]{2,3}$")
    duration: float = Field(default=60, ge=10, le=240)
    seed: int | None = None
    takes: int = Field(default=2, ge=1, le=3, description="Songs with lyrics: make this many takes and keep the one whose "
                                                          "words Whisper hears most clearly (each extra take ~2 min)")


class SongBlock(BaseModel):
    """A sung song in the video. placement: "bed" — under the narration, ducked (like music); "intro_outro" — the
    sung chorus at full volume over the opening and the ending, its instrumental under the narrated middle;
    "sung_story" — no narration, the song tells the story and scenes follow its lyric lines."""
    track: str = Field(default="", description="A Music library take (.flac path); or give spec")
    spec: SongSpec | None = Field(default=None, description="Or make a new song for this video")
    placement: str = Field(default="bed", pattern="^(bed|intro_outro|sung_story)$")
    volume: float = Field(default=0.5, ge=0, le=1, description="Level of the sung parts (intro/outro, sung story); 0.5 sits a little above the narration")
    lyrics_subtitles: bool = Field(default=True, description="Burn the sung lyrics as (yellow) subtitles")


class JobRequest(BaseModel):
    prompt: str = Field(min_length=1, description="The idea or story; in script mode, the video's title")
    width: int = 768
    height: int = 512
    max_scenes: int = Field(default=4, ge=1, le=12)
    motion: str = Field(default="kenburns", pattern="^(none|kenburns|ltx)$")
    clip_seconds: float = Field(default=3, ge=1, le=10)
    fade: float = Field(default=0.5, ge=0, le=2)
    style: str = ""
    scene_mode: str = Field(default="lines", pattern="^(lines|llm|slides|musicvideo|script)$")
    script: list[ScriptScene] = Field(default_factory=list, max_length=16,
                                      description="script mode: the scenes, written outside Happica (no story AI)")
    characters: dict[str, str] = Field(default_factory=dict, max_length=12,
                                       description="Name → look; every image prompt naming a character gets the look spelled out")
    sfx_auto: bool = Field(default=False, description="lines/llm modes: the AI adds a sound effect to every scene")
    song: SongBlock | None = Field(default=None, description="A sung song: as a bed, as intro/outro, or as the whole story")
    caption_style: CaptionStyle | None = Field(default=None, description="How burned-in subtitles look")
    output_height: int | None = Field(default=None, ge=256, le=2160,
                                      description="Final video height, e.g. 1920: 576×1024 is exported as 1080×1920")
    upscale: str = Field(default="fast", pattern="^(fast|ai|anime)$",
                         description="How the final video is enlarged to output_height: fast (lanczos, seconds) or ai "
                                     "(Real-ESRGAN, ~1 s per frame); captions are drawn at the final size either way")
    song_track: str = ""  # musicvideo: a Music library take's .flac under outputs/
    character: str = Field(default="", description="A real-person character id: scenes that name them get their face")
    art_characters: list[str] = Field(default_factory=list, max_length=4,
                                      description="Cartoon (art) character ids: scenes that name one are drawn from its reference pictures")
    art_weight: float = Field(default=0.7, ge=0.3, le=1.0, description="How strongly art characters follow their pictures")
    slides: list[Slide] = Field(default_factory=list, max_length=30)
    slides_narration: str = Field(default="mine", pattern="^(mine|ai)$")  # "ai": a vision model writes it from the pictures
    theme: str = Field(default="", max_length=500)  # what a My pictures video is about (guides AI narration)
    narrate: bool = False
    voice: str = Field(default="af_heart", pattern=tts.VOICE_ID)
    subtitles: str = Field(default="burn", pattern="^(off|soft|burn)$")
    image_steps: int = Field(default=4, ge=1, le=8)
    image_model: str = Field(default="turbo", pattern="^(turbo|lightning|flux|zimage)$",
                             description="turbo = SDXL-Turbo; lightning = SDXL-Lightning (same speed, ~15 s a picture at 576x1024, cleaner; both work with characters); zimage = Z-Image Turbo (~4 min a picture, follows character descriptions closely); flux = FLUX.1-schnell (~4-5 min a picture). zimage and flux can't use characters")
    texture: str | None = Field(default=None, pattern="^(smooth|detailed)$",
                                description="인물 질감: smooth = a short extra AI pass for softer skin (Turbo/Lightning; FLUX and "
                                            "Z-Image get smoothing words only); detailed = sharpened with a fine film grain. "
                                            "Omit to keep the picture as drawn")
    video_steps: int = Field(default=8, ge=4, le=30)
    ltx_steady: bool = Field(default=True, description="AI motion: steadier sampling (euler, light input compression); "
                                                       "faces and eyes wobble less")
    ltx_end_frame: bool = Field(default=True, description="AI motion: pin each clip's last frame to its picture, so the "
                                                          "motion returns to the clean drawing instead of drifting")
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


def _check_song(req: "JobRequest") -> None:
    song = req.song
    if bool(song.track) == bool(song.spec):
        raise HTTPException(400, "song: give either track (a Music library take) or spec (a new song)")
    if song.track:
        music_job = store.get(song.track.split("/")[0])
        if not music_job or music_job["kind"] != "music":
            raise HTTPException(400, "song.track must be a Music library take")
        if music_job["state"] not in ("queued", "running"):  # a song still being made (story batches) is checked when used
            _check_library_track("library", song.track, "song from the Music library")
        lyrics = "" if music_job["settings"].get("instrumental") else music_job["settings"].get("lyrics", "")
    else:
        lyrics = song.spec.lyrics
    if song.placement == "sung_story" and not lyrics.strip():
        raise HTTPException(400, "A sung story needs a song with lyrics")
    if song.placement == "sung_story":
        req.narrate = False  # the song tells the story
    req.music = "off"  # the song is the music


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
        out["vision"] = "ok" if any(m["name"] == VISION_MODEL for m in tags.get("models", [])) else "model missing"
    except Exception:
        out["ollama"] = "down"
    out["llm_model"] = OLLAMA_MODEL
    return out


@app.get("/api/voices")
async def list_voices():
    return await asyncio.to_thread(tts.voices)


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    return submit_video(req)


def submit_video(req: JobRequest, extra: dict | None = None) -> dict:
    """Check a video request and queue it; `extra` settings ride along (e.g. a story's title and hashtags)."""
    _check_library_track(req.music, req.music_track)
    _check_library_track(req.ambience, req.ambience_track, "sound from the Sound library")
    _check_character(req.character)
    _check_image_model(req.image_model, req.character, req.art_characters)
    for art_id in req.art_characters:
        ch = character.load(art_id)
        if not ch or ch.get("kind") != "art":
            raise HTTPException(400, f"{art_id} isn't a cartoon (art) character")
    if req.scene_mode == "musicvideo":
        _check_library_track("library", req.song_track, "song for the music video")
        song = store.get(req.song_track.split("/")[0])
        if not song or song["kind"] != "music":
            raise HTTPException(400, "Pick a song from the Music library")
        # The song is the soundtrack, at full level (nothing to duck under); no narration. Subtitles are the sung lyrics.
        req.music, req.music_track, req.music_title = "library", req.song_track, song["settings"].get("title", "")
        req.music_volume, req.narrate = 0.4, False
        if song["settings"].get("instrumental"):
            req.subtitles = "off"
    for name, look in req.characters.items():
        if not 1 <= len(name.strip()) <= 40 or len(look) > 300:
            raise HTTPException(400, "Character names are 1-40 characters and looks at most 300")
    if req.scene_mode == "script":
        if not req.script:
            raise HTTPException(400, "Add at least one scene to the script")
        if "narrate" not in req.model_fields_set:  # a script with narration is meant to be spoken
            req.narrate = any(s.narration.strip() for s in req.script)
    if req.song:
        _check_song(req)
    if req.scene_mode == "slides":
        if not req.slides:
            raise HTTPException(400, "Add at least one image")
        for slide in req.slides:
            path = (OUTPUTS_DIR / slide.image).resolve()
            if not path.is_relative_to(OUTPUTS_DIR.resolve()) or path.suffix != ".png" or not path.is_file():
                raise HTTPException(400, f"Image not found: {slide.image}")
    return runner.submit(req.prompt, {**req.model_dump(exclude={"prompt"}), **(extra or {})})


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


UPLOADS = OUTPUTS_DIR / "uploads"
MAX_UPLOAD = 20 * 1024 * 1024


@app.post("/api/uploads")
async def upload_image(file: UploadFile = File(...)):
    """Accept a photo for slideshows (and later characters). It is decoded and re-saved as PNG, so only real
    images get stored, with a random name inside outputs/uploads/."""
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Images must be under 20 MB")
    try:
        img = await asyncio.to_thread(_decode_image, data)
    except Exception:
        raise HTTPException(400, "That file isn't an image this app can read (JPEG, PNG, WebP, HEIC…)")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    out = UPLOADS / f"{uuid.uuid4().hex[:16]}.png"
    await asyncio.to_thread(img.save, out)
    return {"path": str(out.relative_to(OUTPUTS_DIR)), "width": img.width, "height": img.height}


def _decode_image(data: bytes):
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener

    register_heif_opener()  # iPhone photos are HEIC by default
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img).convert("RGB")  # phone photos carry their rotation in EXIF
    img.thumbnail((2048, 2048))  # plenty for 768-1024 px video, keeps storage small
    return img


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=2, max_length=1000)
    style: str = ""
    width: int = Field(default=768, ge=256, le=1024, multiple_of=64)
    height: int = Field(default=512, ge=256, le=1024, multiple_of=64)
    count: int = Field(default=4, ge=1, le=4)
    character: str = ""  # a character id: their face via PhotoMaker
    image_steps: int = Field(default=4, ge=1, le=8)
    image_model: str = Field(default="turbo", pattern="^(turbo|lightning|flux|zimage)$",
                             description="turbo = SDXL-Turbo; lightning = SDXL-Lightning (same speed, ~15 s a picture at 576x1024, cleaner; both work with characters); zimage = Z-Image Turbo (~4 min a picture, follows character descriptions closely); flux = FLUX.1-schnell (~4-5 min a picture). zimage and flux can't use characters")
    texture: str | None = Field(default=None, pattern="^(smooth|detailed)$",
                                description="인물 질감: smooth = a short extra AI pass for softer skin (Turbo/Lightning; FLUX and "
                                            "Z-Image get smoothing words only); detailed = sharpened with a fine film grain. "
                                            "Omit to keep the picture as drawn")
    seed: int | None = None


@app.post("/api/images")
async def create_images(req: ImageRequest):
    _check_character(req.character)
    _check_image_model(req.image_model, req.character)
    return runner.submit(req.prompt.strip()[:80], req.model_dump(), "image")


class CharacterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40, pattern=r"^[\w .'-]+$",
                      description="The name scenes use for them, e.g. Ttori")
    photo: str = Field(default="", description="A face photo (an upload) for a real person")
    kind: str = Field(default="person", pattern="^(woman|man|girl|boy|person|art)$",
                      description="art = a cartoon character from reference pictures (IP-Adapter); others = a real face")
    refs: list[str] = Field(default_factory=list, max_length=3, description="art: 1-3 uploaded pictures of the character")
    look: str = Field(default="", max_length=300, description="Hair, colours, clothes… spelled out in every prompt")
    style: str = Field(default="", max_length=300, description="art: the drawing style of the character sheet")
    texture: str | None = Field(default=None, pattern="^(smooth|detailed)$",
                                description="인물 질감: smooth = a short extra AI pass for softer skin (Turbo/Lightning; FLUX and "
                                            "Z-Image get smoothing words only); detailed = sharpened with a fine film grain. "
                                            "Omit to keep the picture as drawn")
    consent: bool = Field(default=False, description="Real people only: the photo is of you or someone who agreed")


@app.post("/api/characters")
async def create_character(req: CharacterRequest):
    if req.kind == "art":
        if not req.refs:
            raise HTTPException(400, "Upload 1-3 pictures of the character")
        for ref in req.refs:
            path = (OUTPUTS_DIR / ref).resolve()
            if path.parent != UPLOADS.resolve() or not path.is_file():
                raise HTTPException(400, f"Upload the pictures first ({ref} isn't an upload)")
        req.photo = req.refs[0]  # the thumbnail
        return runner.submit(req.name, req.model_dump(), "character")
    if not req.consent:
        raise HTTPException(400, "Only use photos of yourself, or of people who agreed to it")
    path = (OUTPUTS_DIR / req.photo).resolve()
    if path.parent != UPLOADS.resolve() or not path.is_file():
        raise HTTPException(400, "Upload a photo first")
    return runner.submit(req.name, req.model_dump(), "character")


class TurnaroundRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=2, description="How many sheets (variations) to draw")
    image_model: str = Field(default="turbo", pattern="^(turbo|lightning)$", description="Characters need an SDXL model")
    texture: str | None = Field(default=None, pattern="^(smooth|detailed)$")
    seed: int | None = None


@app.post("/api/characters/{character_id}/turnaround")
async def create_turnaround(character_id: str, req: TurnaroundRequest = TurnaroundRequest()):
    """3면도: a front / side / back sheet of a character, also cut into three pictures."""
    ch = character.load(character_id)
    if not ch:
        raise HTTPException(404, "character not found")
    settings = {"prompt": f"{ch['name']} 3-view sheet", "character": character_id, "turnaround": True,
                "width": character.VIEW_SIZE[0], "height": character.VIEW_SIZE[1], "style": "",
                **req.model_dump()}
    return runner.submit(f"{ch['name']} 3-view sheet", settings, "image")


class UseViews(BaseModel):
    job: str = Field(description="A finished 3-view sheet job of this character")


@app.post("/api/characters/{character_id}/refs")
async def use_turnaround_as_refs(character_id: str, req: UseViews):
    """Cartoon characters: use a 3-view sheet's front, side and back pictures as the reference pictures, so
    scenes keep the character from every angle."""
    job = _get_or_404(character_id)
    if job["kind"] != "character" or job["settings"].get("kind") != "art":
        raise HTTPException(400, "Only cartoon (art) characters use reference pictures")
    sheet = store.get(req.job)
    if not sheet or sheet["settings"].get("character") != character_id or not (sheet["result"] or {}).get("turnaround"):
        raise HTTPException(400, "Pick a finished 3-view sheet of this character")
    views = [p for p in sheet["result"]["images"] if p.endswith(("_front.png", "_side.png", "_back.png"))][:3]
    # copies: the character keeps its references even if the sheet's job is deleted later
    UPLOADS.mkdir(parents=True, exist_ok=True)
    refs = []
    for v in views:
        copy = UPLOADS / f"{uuid.uuid4().hex[:16]}.png"
        shutil.copyfile(OUTPUTS_DIR / v, copy)
        refs.append(str(copy.relative_to(OUTPUTS_DIR)))
    settings = {**job["settings"], "refs": refs, "photo": refs[0]}
    return store.update(character_id, settings=settings)


def _check_character(character_id: str) -> None:
    if character_id and not character.load(character_id):
        raise HTTPException(400, "That character doesn't exist any more")


def _check_image_model(model: str, character_id: str = "", art: list[str] | None = None) -> None:
    if model in ("flux", "zimage") and (character_id or art):
        raise HTTPException(400, "Characters need the Turbo or Lightning picture model (PhotoMaker and IP-Adapter are "
                                 "SDXL-only); with FLUX or Z-Image, describe the character in the story's characters instead")


class VoiceRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(default="af_heart", pattern=tts.VOICE_ID)
    speed: float = Field(default=1.0, ge=0.6, le=1.5)


@app.post("/api/voiceover")
async def create_voiceover(req: VoiceRequest):
    return runner.submit(" ".join(req.text.split())[:80], req.model_dump(), "voice")


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
    prompt: str | None = Field(default=None, description="A new picture description for this scene")
    motion: str | None = Field(default=None, max_length=300,
                               description="New motion text (AI motion): e.g. gentler actions after a clip went wrong")
    keep_image: bool = Field(default=False, description="Keep the picture and only animate it again (new motion seed)")


@app.post("/api/jobs/{job_id}/scenes/{index}/regenerate")
async def regenerate_scene(job_id: str, index: int, req: RegenerateRequest = RegenerateRequest()):
    job = _get_or_404(job_id)
    _require_idle(job)
    if job["settings"].get("scene_mode") == "slides":
        raise HTTPException(400, "Slideshow images are your own — replace them with Edit & re-run")
    if not job["result"] or not 0 <= index < len(job["result"].get("images", [])):
        raise HTTPException(400, "scene has no image to regenerate")
    task = lambda j, job_dir, report: pipeline.regenerate_scene(j, job_dir, report, comfy, index, req.prompt,
                                                                req.motion, req.keep_image)
    return runner.enqueue(job_id, task, stage=f"scene {index + 1}")


class SfxChange(BaseModel):
    prompt: str | None = Field(default=None, min_length=2, max_length=300,
                               description="A new description; omit for another take of the same sound")


@app.post("/api/jobs/{job_id}/scenes/{index}/sfx/{cue}/regenerate")
async def regenerate_scene_sfx(job_id: str, index: int, cue: int, req: SfxChange = SfxChange()):
    """Make one scene sound effect again and re-mix the video; nothing else is redone."""
    job = _get_or_404(job_id)
    _require_idle(job)
    scenes_sfx = (job["result"] or {}).get("sfx") or []
    if not 0 <= index < len(scenes_sfx) or not 0 <= cue < len(scenes_sfx[index]):
        raise HTTPException(400, "That scene has no such sound effect")
    task = lambda j, job_dir, report: pipeline.change_scene_sfx(j, job_dir, report, music_comfy, index, cue, req.prompt)
    return runner.enqueue(job_id, task, stage=f"scene {index + 1} sound")


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
        if job["kind"] == "video":  # its song and sound effects run on the music engine
            await music_comfy.interrupt()
    if job["state"] in ("queued", "running"):
        job = runner.cancel(job_id)
    return job


@app.websocket("/ws/jobs/{job_id}")
async def job_events(ws: WebSocket, job_id: str):
    # HTTP middleware doesn't see WebSockets: accept a login session or an API key here too
    key = apikeys.from_headers(ws.headers)
    if not auth.valid(ws.cookies.get(auth.COOKIE)) and not (key and keys.verify(key)):
        await ws.close(code=4401)
        return
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


# REST API v1 for other apps (defined in api_v1.py, which builds on the endpoints above)
import api_v1  # noqa: E402

app.include_router(api_v1.router)
app.openapi = api_v1.openapi
runner.on_finished = api_v1.notify
