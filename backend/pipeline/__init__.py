"""Text → scenes → narration → images → clips → final video.

Each stage is its own module. Partial results are reported as they appear, so a job that
fails late still shows what it made. With narration on, each scene lasts at least as long as
its voiceover, and subtitles are timed to the speech.
"""
import random
from pathlib import Path

from comfy_client import ComfyClient

from . import assemble, captions, image, scenes, tts, video

LEAD = 0.35  # narration starts this long after its scene appears
TAIL = 0.4   # breathing room after the narration before the next scene's crossfade


def _paths(job_dir: Path, i: int) -> dict[str, Path]:
    name = f"scene_{i + 1:02d}"
    return {"image": job_dir / "images" / f"{name}.png", "clip": job_dir / "clips" / f"{name}.mp4",
            "audio": job_dir / "audio" / f"{name}.wav"}


def _rel(job_dir: Path, p: Path) -> str:
    return str(p.relative_to(job_dir.parent))


def _video_prompt(shot: dict) -> str:
    return f"{shot['prompt']}. {shot['motion']}" if shot.get("motion") else shot["prompt"]


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    settings = job["settings"]
    fade = settings.get("fade", 0.5)
    result: dict = {"scenes": [], "narration": [], "motion": [], "seeds": [], "durations": [],
                    "images": [], "clips": [], "audio": [], "video": None, "subtitles": None}

    report("scenes", 0.0)
    shots = await scenes.split(job["prompt"], settings)
    result.update(scenes=[s["prompt"] for s in shots], narration=[s["narration"] for s in shots],
                  motion=[s["motion"] for s in shots])
    report("scenes", 1.0, result=result)

    # Narration first: its length decides how long each scene lasts
    speech = [0.0] * len(shots)
    if settings.get("narrate"):
        for i, shot in enumerate(shots):
            report("narration", i / len(shots))
            speech[i] = await tts.speak(shot["narration"], settings.get("voice", "af_heart"), _paths(job_dir, i)["audio"])
            result["audio"].append(_rel(job_dir, _paths(job_dir, i)["audio"]))
    result["speech"] = [round(s, 3) for s in speech]
    result["durations"] = [round(max(settings.get("clip_seconds", 3), s + LEAD + TAIL + fade if s else 0), 2) for s in speech]

    # One base seed per job keeps a similar look across scenes; every scene's seed is saved so it can be reproduced.
    seed = settings.get("seed")
    if seed is None:
        seed = random.randrange(2**32)
    result["seed"] = seed

    for i, shot in enumerate(shots):
        on_progress = lambda p, i=i: report("image", (i + p) / len(shots))
        await image.generate(comfy, shot["prompt"], settings, _paths(job_dir, i)["image"], seed=seed + i, on_progress=on_progress)
        result["images"].append(_rel(job_dir, _paths(job_dir, i)["image"]))
        result["seeds"].append(seed + i)
        report("image", (i + 1) / len(shots), result=result)

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
    await image.generate(comfy, prompts[index], settings, p["image"], seed=seeds[index],
                         on_progress=lambda prog: report(f"scene {index + 1} image", prog))

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


async def _assemble(job_dir: Path, settings: dict, result: dict) -> dict:
    n = len(result["scenes"])
    fade = settings.get("fade", 0.5)
    durations = result.get("durations") or [settings.get("clip_seconds", 3)] * n
    clips = [_paths(job_dir, i)["clip"] for i in range(n)]
    silent = await assemble.concat(clips, durations, job_dir / "video_only.mp4", fade)

    narration_track, cues = None, []
    if result.get("audio"):
        starts = assemble.scene_starts(durations, fade)
        total = starts[-1] + durations[-1]
        audio = [_paths(job_dir, i)["audio"] for i in range(n)]
        narration_track = assemble.mix_narration(audio, starts, LEAD, total, job_dir / "narration.wav")
        if settings.get("subtitles", "off") != "off":
            for i, line in enumerate(result["narration"]):
                cues += captions.cues_for(line, starts[i] + LEAD, result["speech"][i])

    final = await assemble.finalize(silent, job_dir / "final.mp4", narration_track, cues,
                                    burn=settings.get("subtitles") == "burn",
                                    size=(settings["width"], settings["height"]))
    silent.unlink()
    if not cues:
        return {"video": _rel(job_dir, final), "subtitles": None, "subtitles_vtt": None}
    vtt = captions.write_vtt(cues, final.with_suffix(".vtt"))
    return {"video": _rel(job_dir, final), "subtitles": _rel(job_dir, final.with_suffix(".srt")),
            "subtitles_vtt": _rel(job_dir, vtt)}
