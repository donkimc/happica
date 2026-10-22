"""Text → scenes → narration → images → clips → final video.

Each stage is its own module. Partial results are reported as they appear, so a job that
fails late still shows what it made. With narration on, each scene lasts at least as long as
its voiceover, and subtitles are timed to the speech. Background music is either scored for the
story (auto) or taken from the Music Studio library, and ducked under the narration.
"""
import asyncio
import json
import math
import random
import shutil
from pathlib import Path

from comfy_client import ComfyClient
from config import OUTPUTS_DIR

from . import assemble, captions, character, ffmpeg, image, lyrics_align, music, scenes, script, sfx, song, songwriter, story, tts, upscale, video, vision

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


async def _music_video_lyrics(settings: dict, report) -> dict | None:
    """Timed lyrics for a music video's song, burned as sung-lyric subtitles."""
    from jobs import JobStore

    track = settings["song_track"]
    music_job = JobStore().get(track.split("/")[0])
    s = music_job["settings"] if music_job else {}
    if s.get("instrumental") or not s.get("lyrics", "").strip():
        return None
    report("timing the lyrics", 0.0)
    info = {"placement": "musicvideo", "track": track, "lyrics_subtitles": True,
            "timing": await lyrics_align.timing(OUTPUTS_DIR / track, s["lyrics"], s.get("language", "en"))}
    info["lyric_cues"] = song.full_song_cues(info)
    return info


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
    if slides:  # the user's own pictures, each with optional narration
        shots = [{"prompt": sl["text"] or "a photograph", "narration": sl["text"], "motion": ""} for sl in settings["slides"]]
        ai_narration = settings.get("narrate") and settings.get("slides_narration") == "ai"
        if ai_narration or settings.get("motion") == "ltx":
            # A vision model looks at each picture: its description guides AI motion and the narration
            report("looking at your pictures", 0.0)
            descriptions = await vision.describe([OUTPUTS_DIR / sl["image"] for sl in settings["slides"]],
                                                 lambda p: report("looking at your pictures", p))
            for shot, d in zip(shots, descriptions):
                shot["prompt"] = d
            result["descriptions"] = descriptions
            if ai_narration:
                report("writing the narration", 0.0)
                lines = await vision.narrate(descriptions, tts.language_of(settings.get("voice", "af_heart")),
                                             settings.get("theme", ""))
                for shot, sl, line in zip(shots, settings["slides"], lines):
                    shot["narration"] = sl["text"].strip() or line  # anything the user wrote wins
    elif settings.get("scene_mode") == "musicvideo":
        shots, song_seconds = await _music_video_scenes(settings)
    elif settings.get("scene_mode") == "script":  # written outside Happica: no story AI
        shots = script.shots(settings)
    else:
        story = job["prompt"]
        if (ch := character.load(settings.get("character", ""))) and settings.get("scene_mode") == "llm":
            story += (f"\n\n(The main character is {ch['name']}, a {ch.get('kind', 'person')}: {ch.get('look', '')}. "
                      f"Write the name {ch['name']} in every image prompt where they appear.)")
        shots = await scenes.split(story, settings)
    result.update(scenes=[s["prompt"] for s in shots], narration=[s["narration"] for s in shots],
                  motion=[s["motion"] for s in shots])
    report("scenes", 1.0, result=result)

    # A sung song comes first: its lyric timing can decide how long scenes last
    if settings.get("song"):
        result["song"] = await song.prepare(job, job_dir, report, music_comfy)
    elif settings.get("scene_mode") == "musicvideo" and settings.get("subtitles", "off") != "off":
        result["song"] = await _music_video_lyrics(settings, report)

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
    placement = (result.get("song") or {}).get("placement")
    if placement == "sung_story":
        counts = [s.get("lyric_lines") for s in settings.get("script") or []]
        result["durations"] = song.sung_durations(result["song"], len(shots), fade, counts)
        result["song"]["lyric_cues"] = song.full_song_cues(result["song"])
    elif placement == "intro_outro":
        layout = song.intro_outro(result["song"], [bool(x) for x in speech], result["durations"], fade, LEAD,
                                  level(settings, "music_volume", 0.3, bool(settings.get("narrate"))))
        result.update(durations=layout.pop("durations"), leads=layout.pop("leads"))
        result["song"].update(layout)

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
        art_chars = [a for a in (character.load(i) for i in settings.get("art_characters") or []) if a]
        looks = {**{a["name"]: a.get("look", "") for a in art_chars}, **(settings.get("characters") or {})}
        prompts = [script.expand(s["prompt"], looks) for s in shots]
        art = [character.find_art(p, art_chars) for p in prompts]
        result["art"] = [a["id"] if a else None for a in art]
        ch = character.load(settings.get("character", ""))
        with_face = None
        if ch:
            # Scenes that name the character get their face; if none do (line mode), they're in every scene
            with_face = [character.mentions(p, ch) for p in prompts]
            if not any(with_face):
                with_face = [True] * len(prompts)
            prompts = [character.prompt_for(p, ch) if f else p for p, f in zip(prompts, with_face)]
            result["with_face"] = with_face
        if with_face:  # a scene with the real person's face can't also use an art character
            art = [None if f else a for a, f in zip(art, with_face)]
        await image.generate_batch(comfy, prompts, settings, image_paths, seeds,
                                   on_progress=lambda p: report("image", p), character=ch, with_face=with_face, art=art)
    result.update(images=[_rel(job_dir, p) for p in image_paths], seeds=seeds)
    report("image", 1.0, result=result)

    # Per-scene sound effects: written in the script, or suggested by the AI (sfx_auto)
    if settings.get("sfx_auto") and settings.get("scene_mode") in ("lines", "llm"):
        report("choosing sound effects", 0.0)
        for shot, sound in zip(shots, await scenes.suggest_sfx(shots)):
            shot["sfx"] = [{"prompt": sound}] if sound else []
    result["sfx"] = [[{"at": 0.15, "duration": 3.0, "volume": 0.6, **cue, "seed": seed + 1000 * (i + 1) + k}
                      for k, cue in enumerate(shot.get("sfx") or [])] for i, shot in enumerate(shots)]

    modes = script.modes(settings, len(shots))
    if settings.get("motion") == "none" and all(m == "none" for m in modes):
        return result  # images only
    result["motion_modes"] = modes

    if "ltx" in modes:
        await comfy.free()  # SDXL out, LTX in: both don't fit in 16 GB at once

    for i, shot in enumerate(shots):
        report("video", i / len(shots))
        p = _paths(job_dir, i)
        on_progress = lambda prog, i=i: report("video", (i + prog) / len(shots))
        await video.animate(comfy, p["image"], _video_prompt(shot), {**settings, "motion": modes[i]}, p["clip"], i,
                            seconds=result["durations"][i], seed=seed + i, on_progress=on_progress)
        result["clips"].append(_rel(job_dir, p["clip"]))
        report("video", (i + 1) / len(shots), result=result)

    await make_sfx(job_dir, result, report, music_comfy)
    result["music"] = await _pick_music(job, job_dir, result, report, music_comfy)
    result["ambience"] = await _pick_ambience(job, job_dir, result, report, music_comfy)
    report("assemble", 0.0)
    result.update(await _assemble(job_dir, settings, result, report))
    return result


async def make_sfx(job_dir: Path, result: dict, report, music_comfy: ComfyClient) -> None:
    """Generate the scene sound effects that don't have a file yet (all in one run), into outputs/<job>/sfx/.
    Cues that already have their file are kept, so re-assembling or changing one cue doesn't redo the others."""
    todo = [cue for cues in result.get("sfx") or [] for cue in cues
            if not (cue.get("flac") and (OUTPUTS_DIR / cue["flac"]).is_file())]
    if not todo:
        return
    report("sound effects", 0.0)
    dirs = [job_dir / "sfx" / f"{cue['seed']}" for cue in todo]
    takes = await sfx.generate_cues(music_comfy, [(c["prompt"], float(c["duration"]), c["seed"]) for c in todo], dirs,
                                    f"{job_dir.name}_fx", lambda p: report("sound effects", p))
    for cue, take in zip(todo, takes):
        cue.update(flac=take["flac"], mp3=take["mp3"])
    report("sound effects", 1.0, result=result)


async def change_scene_sfx(job: dict, job_dir: Path, report, music_comfy: ComfyClient, index: int, k: int,
                           prompt: str | None = None) -> dict:
    """Make one scene sound effect again (a new take, or a new description), then re-mix the video."""
    result = dict(job["result"])
    cue = result["sfx"][index][k]
    if cue.get("flac") and (old := (OUTPUTS_DIR / cue["flac"]).parent).parent == job_dir / "sfx":
        shutil.rmtree(old, ignore_errors=True)
    cue.update(seed=random.randrange(2**31), flac=None, mp3=None, **({"prompt": prompt} if prompt else {}))
    await make_sfx(job_dir, result, report, music_comfy)
    if result.get("video"):
        report("assemble", 0.0)
        result.update(await _assemble(job_dir, job["settings"], result, report))
    return result


async def regenerate_scene(job: dict, job_dir: Path, report, comfy: ComfyClient,
                           index: int, prompt: str | None = None, motion: str | None = None,
                           keep_image: bool = False) -> dict:
    """Redo one scene's image (new seed, optionally a new prompt), then its clip and the final video.
    `motion` replaces the scene's motion text; `keep_image` keeps the picture and only animates it again.

    The narration and the scene's length stay the same.
    """
    settings, result = job["settings"], dict(job["result"])
    prompts = list(result["scenes"])
    if prompt:
        prompts[index] = prompt
    seeds = list(result.get("seeds") or [None] * len(prompts))
    clip_seed = random.randrange(2**32)
    if not keep_image:
        seeds[index] = clip_seed
    motions = list(result.get("motion") or [""] * len(prompts))
    if motion:
        motions[index] = motion
    result.update(scenes=prompts, seeds=seeds, motion=motions)

    p = _paths(job_dir, index)
    if keep_image and p["image"].is_file():
        return await _reanimate(job_dir, settings, result, report, comfy, index, prompts, clip_seed)
    ch = character.load(settings.get("character", ""))
    face = bool(ch) and (result.get("with_face") or [True] * len(prompts))[index]
    art_chars = [a for a in (character.load(i) for i in settings.get("art_characters") or []) if a]
    text = script.expand(prompts[index], {**{a["name"]: a.get("look", "") for a in art_chars}, **(settings.get("characters") or {})})
    art = None if face else character.find_art(text, art_chars)
    await image.generate(comfy, character.prompt_for(text, ch) if face else text, settings,
                         p["image"], seed=seeds[index], on_progress=lambda prog: report(f"scene {index + 1} image", prog),
                         character=ch if face else None, art=art)

    # Only rebuild video if this job made one (an "images only" job or one that failed before video stays as is)
    if result.get("video"):
        return await _reanimate(job_dir, settings, result, report, comfy, index, prompts, seeds[index])
    return result


async def _reanimate(job_dir: Path, settings: dict, result: dict, report, comfy: ComfyClient, index: int,
                     prompts: list[str], seed: int) -> dict:
    """Make one scene's clip again from its picture, then re-assemble the final video."""
    p = _paths(job_dir, index)
    mode = (result.get("motion_modes") or script.modes(settings, len(prompts)))[index]
    if mode == "ltx":
        await comfy.free()
    motion = (result.get("motion") or [""] * len(prompts))[index]
    durations = result.get("durations") or [settings.get("clip_seconds", 3)] * len(prompts)
    await video.animate(comfy, p["image"], _video_prompt({"prompt": prompts[index], "motion": motion}),
                        {**settings, "motion": mode},
                        p["clip"], index, seconds=durations[index], seed=seed,
                        on_progress=lambda prog: report(f"scene {index + 1} clip", prog))
    report("assemble", 0.0)
    result.update(await _assemble(job_dir, settings, result, report))
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
    result.update(await _assemble(job_dir, job["settings"], result, report))
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


def output_size(settings: dict) -> tuple[int, int]:
    """The final video's size: the generation size, or scaled up to output_height (e.g. 576×1024 → 1080×1920)."""
    w, h = settings["width"], settings["height"]
    if not settings.get("output_height") or settings["output_height"] <= h:
        return w, h
    f = settings["output_height"] / h
    return int(round(w * f / 2)) * 2, int(round(h * f / 2)) * 2


async def _picture(job_dir: Path, settings: dict, clips: list[Path], durations: list[float], fade: float,
                   report) -> tuple[Path, tuple[int, int]]:
    """The joined clips without sound, at the final size. Cached by what it's made of, so changing the sound or a
    sound effect re-mixes without joining (or upscaling, which with AI takes ~1 s a frame) again."""
    import hashlib

    size = output_size(settings)
    method = settings.get("upscale", "fast")
    key = hashlib.sha1(json.dumps([[str(c), c.stat().st_mtime] for c in clips] + [durations, fade, size, method]).encode()).hexdigest()[:16]
    out = job_dir / "video_only.mp4"
    stamp = job_dir / "video_only.key"
    if out.is_file() and stamp.is_file() and stamp.read_text() == key:
        return out, size
    joined = await assemble.concat(clips, durations, job_dir / "video_joined.mp4", fade)
    if size == (settings["width"], settings["height"]):
        joined.replace(out)
    elif method in ("ai", "anime"):
        report("upscaling (AI)", 0.0)
        await upscale._ai(joined, out, *size, True, lambda p: report("upscaling (AI)", p), lambda: False,
                          anime=method == "anime")
        joined.unlink()
    else:
        await ffmpeg.run("-i", str(joined), "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos", *ffmpeg.ENCODE, str(out))
        joined.unlink()
    stamp.write_text(key)
    return out, size


async def _assemble(job_dir: Path, settings: dict, result: dict, report=lambda *a, **k: None) -> dict:
    n = len(result["scenes"])
    fade = settings.get("fade", 0.5)
    durations = result.get("durations") or [settings.get("clip_seconds", 3)] * n
    clips = [_paths(job_dir, i)["clip"] for i in range(n)]
    silent, size = await _picture(job_dir, settings, clips, durations, fade, report)
    total = _total(settings, result)

    narration_track, cues, speech = None, [], []
    starts = assemble.scene_starts(durations, fade)
    talk = [s + lead for s, lead in zip(starts, result.get("leads") or [LEAD] * n)]  # when each narration begins
    if result.get("audio"):
        audio = [p if (p := _paths(job_dir, i)["audio"]).is_file() else None for i in range(n)]
        narration_track = assemble.mix_narration(audio, talk, 0.0, total, job_dir / "narration.wav")
        speech = [(talk[i], talk[i] + result["speech"][i]) for i in range(n) if result["speech"][i]]
        if settings.get("subtitles", "off") != "off":
            for i, line in enumerate(result["narration"]):
                cues += captions.cues_for(line, talk[i], result["speech"][i], captions.max_chars_for(settings["width"]),
                                          lines=2 if settings["height"] > settings["width"] else 1)

    narrated = bool(narration_track)
    beds, segments = [], []
    sung = result.get("song") or {}
    if sung.get("placement") == "bed":
        beds.append((OUTPUTS_DIR / sung["track"], level(settings, "music_volume", 0.3, narrated)))
    elif sung.get("placement") == "sung_story":
        beds.append((OUTPUTS_DIR / sung["track"], sung.get("volume", 0.5)))
    elif sung.get("placement") == "intro_outro":
        segments = [{**seg, "path": OUTPUTS_DIR / seg["path"]} for seg in sung["segments"]]
    if sung.get("lyrics_subtitles") and settings.get("subtitles", "off") != "off":
        cues += [captions.Cue(c["start"], c["end"], c["text"], "lyric") for c in sung.get("lyric_cues") or []]
        cues.sort(key=lambda c: c.start)
    if result.get("music") and settings.get("music", "off") != "off":
        beds.append((OUTPUTS_DIR / result["music"]["track"], level(settings, "music_volume", 0.3, narrated)))
    if result.get("ambience") and settings.get("ambience", "off") != "off":
        beds.append((OUTPUTS_DIR / result["ambience"]["track"], level(settings, "ambience_volume", 0.35, narrated)))
    final = await assemble.finalize(silent, job_dir / "final.mp4", total, narration_track, cues,
                                    burn=settings.get("subtitles") == "burn" and captions.can_burn(cues),
                                    size=size, beds=beds, speech=speech,
                                    segments=segments, caption_style=settings.get("caption_style"),
                                    sfx=[(OUTPUTS_DIR / c["flac"], starts[i] + c["at"], c["duration"], c["volume"])
                                         for i, scene in enumerate(result.get("sfx") or []) for c in scene
                                         if c.get("flac")])
    out = {"video": _rel(job_dir, final), "subtitles": None, "subtitles_vtt": None}
    if cues:
        vtt = captions.write_vtt(cues, final.with_suffix(".vtt"))
        out.update(subtitles=_rel(job_dir, final.with_suffix(".srt")), subtitles_vtt=_rel(job_dir, vtt))
    if meta := settings.get("story"):  # one-call story: a thumbnail and a ready-to-paste caption
        cover = story.make_cover(_paths(job_dir, 0)["image"], meta.get("title", ""), size, job_dir / "cover.png")
        text = story.write_caption(meta.get("title", ""), meta.get("caption", ""), meta.get("hashtags") or [],
                                   job_dir / "caption.txt")
        out.update(cover=_rel(job_dir, cover), caption=_rel(job_dir, text))
    return out
