"""Text → scenes → narration → images → clips → final video.

Each stage is its own module. Partial results are reported as they appear, so a job that
fails late still shows what it made. With narration on, each scene lasts at least as long as
its voiceover, and subtitles are timed to the speech. Background music is either scored for the
story (auto) or taken from the Music Studio library, and ducked under the narration.
"""
import asyncio
import math
import random
from pathlib import Path

from comfy_client import ComfyClient
from config import OUTPUTS_DIR

from . import assemble, captions, character, image, music, scenes, sfx, songwriter, tts, video

LEAD = 0.35  # narration starts this long after its scene appears
TAIL = 0.4   # breathing room after the narration before the next scene's crossfade


def _paths(job_dir: Path, i: int) -> dict[str, Path]:
    name = f"scene_{i + 1:02d}"
    return {"image": job_dir / "images" / f"{name}.png", "clip": job_dir / "clips" / f"{name}.mp4",
            "audio": job_dir / "audio" / f"{name}.wav"}


def _rel(job_dir: Path, p: Path) -> str:
    return str(p.relative_to(job_dir.parent))


async def _music_video_scenes(settings: dict) -> tuple[list[dict], float]:
    """Storyboard a music video from the song's title, style tags and lyrics; one scene per ~clip_seconds."""
    import soundfile as sf

    song_dir = (OUTPUTS_DIR / settings["song_track"]).parent
    from jobs import JobStore  # the song's settings hold its title, tags and lyrics

    song = JobStore().get(song_dir.name)
    seconds = (await asyncio.to_thread(sf.info, OUTPUTS_DIR / settings["song_track"])).duration
    n = max(2, min(16, round(seconds / max(2.0, settings.get("clip_seconds", 6)))))
    s = song["settings"] if song else {}
    lines = [] if s.get("instrumental") else [l.strip() for l in s.get("lyrics", "").splitlines()
                                              if l.strip() and not l.strip().startswith("[")]
    head = f"A music video for the song \"{s.get('title') or 'Untitled'}\" (mood and look: {s.get('tags', '')}).\n"
    rules = ("Illustrate what the words are ABOUT — their subject and imagery — not the band or instruments; show "
             "musicians only if the lyrics are literally about them. Every scene must show something visibly different "
             "(new place, action or camera angle). Keep one consistent visual world and recurring characters, described "
             "the same way each time. The narration fields can stay short; they are not spoken.")
    if lines:
        # Tie each scene to its own slice of the lyrics, in order, so scenes follow the song and don't repeat
        groups = [lines[round(k * len(lines) / n): round((k + 1) * len(lines) / n)] or [lines[min(k, len(lines) - 1)]]
                  for k in range(n)]
        plan = "\n".join(f"Scene {k + 1} illustrates: " + " / ".join(g) for k, g in enumerate(groups))
        brief = f"{head}Write exactly {n} scenes, one per line below, in this order:\n{plan}\n{rules}"
    else:
        brief = f"{head}It is instrumental: tell a visual story in exactly {n} scenes that fits its mood.\n{rules}"

    try:  # lyrics-to-pictures needs the bigger model; fall back if it isn't installed
        shots = (await scenes.storyboard(brief, n, songwriter.SONGWRITER_MODEL))[:n]
    except RuntimeError:
        shots = (await scenes.storyboard(brief, n))[:n]
    seen: dict[str, int] = {}
    for shot in shots:
        shot["narration"] = ""  # the song is the soundtrack
        # Small models sometimes repeat a scene; vary the shot instead of showing it twice
        k = seen.get(shot["prompt"], 0)
        seen[shot["prompt"]] = k + 1
        if k:
            shot["prompt"] += ", " + SHOT_VARIANTS[(k - 1) % len(SHOT_VARIANTS)]
    return shots, seconds


SHOT_VARIANTS = ["extreme close-up", "wide establishing shot", "low angle shot", "overhead view", "silhouette against the light"]


def _fit_image(src: Path, out: Path, width: int, height: int) -> None:
    """Scale and centre-crop a photo to fill the video frame (no letterboxing)."""
    from PIL import Image, ImageOps

    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        ImageOps.fit(img.convert("RGB"), (width, height), Image.Resampling.LANCZOS).save(out)


def _video_prompt(shot: dict) -> str:
    return f"{shot['prompt']}. {shot['motion']}" if shot.get("motion") else shot["prompt"]


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient, music_comfy: ComfyClient) -> dict:
    settings = job["settings"]
    fade = settings.get("fade", 0.5)
    result: dict = {"scenes": [], "narration": [], "motion": [], "seeds": [], "durations": [],
                    "images": [], "clips": [], "audio": [], "video": None, "subtitles": None}

    report("scenes", 0.0)
    slides = settings.get("scene_mode") == "slides"
    song_seconds = None
    if slides:  # the user's own images, each with optional narration
        shots = [{"prompt": sl["text"] or "a photograph", "narration": sl["text"], "motion": ""} for sl in settings["slides"]]
    elif settings.get("scene_mode") == "musicvideo":
        shots, song_seconds = await _music_video_scenes(settings)
    else:
        story = job["prompt"]
        if (ch := character.load(settings.get("character", ""))) and settings.get("scene_mode") == "llm":
            story += (f"\n\n(The main character is {ch['name']}, a {ch.get('kind', 'person')}: {ch.get('look', '')}. "
                      f"Write the name {ch['name']} in every image prompt where they appear.)")
        shots = await scenes.split(story, settings)
    result.update(scenes=[s["prompt"] for s in shots], narration=[s["narration"] for s in shots],
                  motion=[s["motion"] for s in shots])
    report("scenes", 1.0, result=result)

    # Narration first: its length decides how long each scene lasts
    speech = [0.0] * len(shots)
    if settings.get("narrate"):
        for i, shot in enumerate(shots):
            if not shot["narration"].strip():  # a slide without text stays silent
                continue
            report("narration", i / len(shots))
            speech[i] = await tts.speak(shot["narration"], settings.get("voice", "af_heart"), _paths(job_dir, i)["audio"])
            result["audio"].append(_rel(job_dir, _paths(job_dir, i)["audio"]))
    result["speech"] = [round(s, 3) for s in speech]
    result["durations"] = [round(max(settings.get("clip_seconds", 3), s + LEAD + TAIL + fade if s else 0), 2) for s in speech]
    if song_seconds:  # cut the visuals to the song: n scenes overlapping by `fade` add up to its exact length
        n = len(shots)
        result["durations"] = [round((song_seconds + (n - 1) * fade) / n, 3)] * n

    # One base seed per job keeps a similar look across scenes; every scene's seed is saved so it can be reproduced.
    seed = settings.get("seed")
    if seed is None:
        seed = random.randrange(2**32)
    result["seed"] = seed

    image_paths = [_paths(job_dir, i)["image"] for i in range(len(shots))]
    seeds = [seed + i for i in range(len(shots))]
    if slides:
        report("image", 0.0)
        for sl, out in zip(settings["slides"], image_paths):
            await asyncio.to_thread(_fit_image, OUTPUTS_DIR / sl["image"], out, settings["width"], settings["height"])
    else:
        prompts = [s["prompt"] for s in shots]
        ch = character.load(settings.get("character", ""))
        with_face = None
        if ch:
            # Scenes that name the character get their face; if none do (line mode), they're in every scene
            with_face = [character.mentions(p, ch) for p in prompts]
            if not any(with_face):
                with_face = [True] * len(prompts)
            prompts = [character.prompt_for(p, ch) if f else p for p, f in zip(prompts, with_face)]
            result["with_face"] = with_face
        await image.generate_batch(comfy, prompts, settings, image_paths, seeds,
                                   on_progress=lambda p: report("image", p), character=ch, with_face=with_face)
    result.update(images=[_rel(job_dir, p) for p in image_paths], seeds=seeds)
    report("image", 1.0, result=result)

    if settings.get("motion") == "none":
        return result

    if settings.get("motion") == "ltx":
        await comfy.free()  # SDXL out, LTX in: both don't fit in 16 GB at once

    for i, shot in enumerate(shots):
        report("video", i / len(shots))
        p = _paths(job_dir, i)
        on_progress = lambda prog, i=i: report("video", (i + prog) / len(shots))
        await video.animate(comfy, p["image"], _video_prompt(shot), settings, p["clip"], i,
                            seconds=result["durations"][i], seed=seed + i, on_progress=on_progress)
        result["clips"].append(_rel(job_dir, p["clip"]))
        report("video", (i + 1) / len(shots), result=result)

    result["music"] = await _pick_music(job, job_dir, result, report, music_comfy)
    result["ambience"] = await _pick_ambience(job, job_dir, result, report, music_comfy)
    report("assemble", 0.0)
    result.update(await _assemble(job_dir, settings, result))
    return result


async def regenerate_scene(job: dict, job_dir: Path, report, comfy: ComfyClient,
                           index: int, prompt: str | None = None) -> dict:
    """Redo one scene's image (new seed, optionally a new prompt), then its clip and the final video.

    The narration and the scene's length stay the same.
    """
    settings, result = job["settings"], dict(job["result"])
    prompts = list(result["scenes"])
    if prompt:
        prompts[index] = prompt
    seeds = list(result.get("seeds") or [None] * len(prompts))
    seeds[index] = random.randrange(2**32)
    result.update(scenes=prompts, seeds=seeds)

    p = _paths(job_dir, index)
    ch = character.load(settings.get("character", ""))
    face = bool(ch) and (result.get("with_face") or [True] * len(prompts))[index]
    await image.generate(comfy, character.prompt_for(prompts[index], ch) if face else prompts[index], settings,
                         p["image"], seed=seeds[index], on_progress=lambda prog: report(f"scene {index + 1} image", prog),
                         character=ch if face else None)

    # Only rebuild video if this job made one (an "images only" job or one that failed before video stays as is)
    if result.get("video"):
        if settings.get("motion") == "ltx":
            await comfy.free()
        motion = (result.get("motion") or [""] * len(prompts))[index]
        durations = result.get("durations") or [settings.get("clip_seconds", 3)] * len(prompts)
        await video.animate(comfy, p["image"], _video_prompt({"prompt": prompts[index], "motion": motion}), settings,
                            p["clip"], index, seconds=durations[index], seed=seeds[index],
                            on_progress=lambda prog: report(f"scene {index + 1} clip", prog))
        report("assemble", 0.0)
        result.update(await _assemble(job_dir, settings, result))
    return result


async def change_sound(job: dict, job_dir: Path, report, music_comfy: ComfyClient, regenerate: set[str]) -> dict:
    """Swap a finished video's music and/or ambience (settings already updated) and re-mix; no images or clips
    are redone. Only the layers named in `regenerate` are picked again (a new auto score costs minutes)."""
    result = dict(job["result"])
    if "music" in regenerate:
        result["music"] = await _pick_music(job, job_dir, result, report, music_comfy)
    if "ambience" in regenerate:
        result["ambience"] = await _pick_ambience(job, job_dir, result, report, music_comfy)
    report("assemble", 0.0)
    result.update(await _assemble(job_dir, job["settings"], result))
    return result


def _total(settings: dict, result: dict) -> float:
    n = len(result["scenes"])
    fade = settings.get("fade", 0.5)
    durations = result.get("durations") or [settings.get("clip_seconds", 3)] * n
    starts = assemble.scene_starts(durations, fade)
    return starts[-1] + durations[-1]


async def _pick_music(job: dict, job_dir: Path, result: dict, report, music_comfy: ComfyClient) -> dict | None:
    """The background track for this video: none, a library song, or a new instrumental scored for the story."""
    settings = job["settings"]
    choice = settings.get("music", "off")
    if choice == "library":
        track = settings.get("music_track") or ""
        if not (OUTPUTS_DIR / track).is_file():
            raise RuntimeError("The chosen library song no longer exists — pick another in Change music.")
        return {"source": "library", "track": track, "title": settings.get("music_title", "")}
    if choice != "auto":
        return None

    total = _total(settings, result)
    report("music: choosing a style", 0.0)
    story = job["prompt"] if settings.get("scene_mode") == "llm" else " / ".join(result["scenes"])
    narration = " ".join(result.get("narration") or [])
    brief = (f"Instrumental background score for a short narrated video. It must support the voiceover, not compete "
             f"with it: no vocals, gentle dynamics. Story: {story[:800]}" + (f" Narration: {narration[:600]}" if narration else ""))
    draft = await songwriter.draft(brief, True, total, await music.options(music_comfy))
    score = {"tags": draft["tags"], "bpm": draft["bpm"], "keyscale": draft["keyscale"],
             "timesignature": draft["timesignature"], "instrumental": True, "lyrics": "", "language": "en",
             # ACE-Step makes 10–240 s; a little extra so the fade-out lands on music, and loops cover longer videos
             "duration": min(240, max(10, math.ceil(total) + 2))}
    tracks = await music.generate(music_comfy, score, job_dir / "music", f"{job_dir.name}_score",
                                  [random.randrange(2**31)], lambda stage, p: report(f"music: {stage}", p))
    return {"source": "auto", "track": tracks[0]["flac"], "mp3": tracks[0]["mp3"], "title": draft["title"],
            "tags": draft["tags"]}


async def _pick_ambience(job: dict, job_dir: Path, result: dict, report, music_comfy: ComfyClient) -> dict | None:
    """A looping background ambience: none, a Sound library take, or a new one described from the story."""
    settings = job["settings"]
    choice = settings.get("ambience", "off")
    if choice == "library":
        track = settings.get("ambience_track") or ""
        if not (OUTPUTS_DIR / track).is_file():
            raise RuntimeError("The chosen library sound no longer exists — pick another in Change sound.")
        return {"source": "library", "track": track, "title": settings.get("ambience_title", "")}
    if choice != "auto":
        return None

    report("ambience: describing the setting", 0.0)
    story = job["prompt"] if settings.get("scene_mode") == "llm" else " / ".join(result["scenes"])
    description = await sfx.enhance(f"The background ambience (environment sounds only, no music) for this scene: {story[:800]}")
    # Loops cover any length, so a short take is enough and keeps it fast
    seconds = min(30.0, max(8.0, _total(settings, result)))
    takes = await sfx.generate(music_comfy, description, seconds, True, 1, random.randrange(2**31), job_dir / "ambience",
                               f"{job_dir.name}_amb", lambda stage, p: report(f"ambience: {stage}", p))
    return {"source": "auto", "track": takes[0]["flac"], "mp3": takes[0]["mp3"], "title": description}


def level(settings: dict, key: str, default: float, narrated: bool) -> float:
    """Level sliders (0–1) are relative to the voice. Without narration the beds are the only sound,
    so the same slider position plays them louder (e.g. the music default 0.3 becomes 0.75)."""
    value = settings.get(key, default)
    return value if narrated else min(1.0, value * 2.5)


async def _assemble(job_dir: Path, settings: dict, result: dict) -> dict:
    n = len(result["scenes"])
    fade = settings.get("fade", 0.5)
    durations = result.get("durations") or [settings.get("clip_seconds", 3)] * n
    clips = [_paths(job_dir, i)["clip"] for i in range(n)]
    silent = await assemble.concat(clips, durations, job_dir / "video_only.mp4", fade)
    total = _total(settings, result)

    narration_track, cues = None, []
    if result.get("audio"):
        starts = assemble.scene_starts(durations, fade)
        audio = [p if (p := _paths(job_dir, i)["audio"]).is_file() else None for i in range(n)]
        narration_track = assemble.mix_narration(audio, starts, LEAD, total, job_dir / "narration.wav")
        if settings.get("subtitles", "off") != "off":
            for i, line in enumerate(result["narration"]):
                cues += captions.cues_for(line, starts[i] + LEAD, result["speech"][i], captions.max_chars_for(settings["width"]))

    narrated = bool(narration_track)
    beds = []
    if result.get("music") and settings.get("music", "off") != "off":
        beds.append((OUTPUTS_DIR / result["music"]["track"], level(settings, "music_volume", 0.3, narrated)))
    if result.get("ambience") and settings.get("ambience", "off") != "off":
        beds.append((OUTPUTS_DIR / result["ambience"]["track"], level(settings, "ambience_volume", 0.35, narrated)))
    final = await assemble.finalize(silent, job_dir / "final.mp4", total, narration_track, cues,
                                    burn=settings.get("subtitles") == "burn",
                                    size=(settings["width"], settings["height"]), beds=beds)
    silent.unlink()
    if not cues:
        return {"video": _rel(job_dir, final), "subtitles": None, "subtitles_vtt": None}
    vtt = captions.write_vtt(cues, final.with_suffix(".vtt"))
    return {"video": _rel(job_dir, final), "subtitles": _rel(job_dir, final.with_suffix(".srt")),
            "subtitles_vtt": _rel(job_dir, vtt)}
