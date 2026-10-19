"""Text → scenes → images → clips → final video.

Each stage is its own module. Partial results are reported as they appear, so a job that
fails late still shows the images it made.
"""
import random
from pathlib import Path

from comfy_client import ComfyClient

from . import assemble, image, scenes, video


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    settings = job["settings"]
    rel = lambda p: str(Path(p).relative_to(job_dir.parent))
    result: dict = {"scenes": [], "images": [], "clips": [], "video": None}

    report("scenes", 0.0)
    shots = await scenes.split(job["prompt"], settings)
    result["scenes"] = shots

    # One base seed per job keeps a similar look across scenes; it is saved so the job can be reproduced.
    seed = settings.get("seed")
    if seed is None:
        seed = random.randrange(2**32)
        result["seed"] = seed

    images = []
    for i, shot in enumerate(shots):
        on_progress = lambda p, i=i: report("image", (i + p) / len(shots))
        images.append(await image.generate(comfy, shot, settings, job_dir / "images" / f"scene_{i + 1:02d}.png",
                                           seed=seed + i, on_progress=on_progress))
        result["images"].append(rel(images[-1]))
        report("image", (i + 1) / len(shots), result=result)

    if settings.get("motion") == "none":
        return result

    clips = []
    for i, img in enumerate(images):
        report("video", i / len(images))
        clips.append(await video.animate(comfy, img, shots[i], settings, job_dir / "clips" / f"scene_{i + 1:02d}.mp4", i))
        result["clips"].append(rel(clips[-1]))
        report("video", (i + 1) / len(images), result=result)

    report("assemble", 0.0)
    final = await assemble.concat(clips, job_dir / "final.mp4", settings.get("clip_seconds", 3), settings.get("fade", 0.5))
    result["video"] = rel(final)
    return result
