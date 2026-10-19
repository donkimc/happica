"""Text → scenes → images → clips → final video.

Each stage is its own module. Partial results are reported as they appear, so a job that
fails late still shows the images it made.
"""
import random
from pathlib import Path

from comfy_client import ComfyClient

from . import assemble, image, scenes, video


def _paths(job_dir: Path, i: int) -> tuple[Path, Path]:
    name = f"scene_{i + 1:02d}"
    return job_dir / "images" / f"{name}.png", job_dir / "clips" / f"{name}.mp4"


def _rel(job_dir: Path, p: Path) -> str:
    return str(p.relative_to(job_dir.parent))


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    settings = job["settings"]
    result: dict = {"scenes": [], "seeds": [], "images": [], "clips": [], "video": None}

    report("scenes", 0.0)
    shots = await scenes.split(job["prompt"], settings)
    result["scenes"] = shots

    # One base seed per job keeps a similar look across scenes; every scene's seed is saved so it can be reproduced.
    seed = settings.get("seed")
    if seed is None:
        seed = random.randrange(2**32)
    result["seed"] = seed

    for i, shot in enumerate(shots):
        img_path, _ = _paths(job_dir, i)
        on_progress = lambda p, i=i: report("image", (i + p) / len(shots))
        await image.generate(comfy, shot, settings, img_path, seed=seed + i, on_progress=on_progress)
        result["images"].append(_rel(job_dir, img_path))
        result["seeds"].append(seed + i)
        report("image", (i + 1) / len(shots), result=result)

    if settings.get("motion") == "none":
        return result

    for i, shot in enumerate(shots):
        report("video", i / len(shots))
        img_path, clip_path = _paths(job_dir, i)
        await video.animate(comfy, img_path, shot, settings, clip_path, i)
        result["clips"].append(_rel(job_dir, clip_path))
        report("video", (i + 1) / len(shots), result=result)

    report("assemble", 0.0)
    result["video"] = _rel(job_dir, await _assemble(job_dir, settings, len(shots)))
    return result


async def regenerate_scene(job: dict, job_dir: Path, report, comfy: ComfyClient,
                           index: int, prompt: str | None = None) -> dict:
    """Redo one scene's image (new seed, optionally a new prompt), then its clip and the final video."""
    settings, result = job["settings"], dict(job["result"])
    shots = list(result["scenes"])
    if prompt:
        shots[index] = prompt
    seeds = list(result.get("seeds") or [None] * len(shots))
    seeds[index] = random.randrange(2**32)
    result.update(scenes=shots, seeds=seeds)

    img_path, clip_path = _paths(job_dir, index)
    await image.generate(comfy, shots[index], settings, img_path, seed=seeds[index],
                         on_progress=lambda p: report(f"scene {index + 1} image", p))

    # Only rebuild video if this job made one (an "images only" job or one that failed before video stays as is)
    if result.get("video"):
        report(f"scene {index + 1} clip", 0.0)
        await video.animate(comfy, img_path, shots[index], settings, clip_path, index)
        report("assemble", 0.0)
        await _assemble(job_dir, settings, len(shots))
    return result


async def _assemble(job_dir: Path, settings: dict, n: int) -> Path:
    clips = [_paths(job_dir, i)[1] for i in range(n)]
    return await assemble.concat(clips, job_dir / "final.mp4", settings.get("clip_seconds", 3), settings.get("fade", 0.5))
