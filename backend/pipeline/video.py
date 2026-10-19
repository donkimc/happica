"""Image → video clip.

kenburns: slow zoom/pan over the still with ffmpeg (under a second per clip).
ltx: AI motion with LTX-Video 2B (distilled) through ComfyUI (minutes per clip on an M2).
"""
import copy
import json
from pathlib import Path
from typing import Callable

from comfy_client import ComfyClient
from config import WORKFLOWS_DIR

from . import ffmpeg

FPS = 24
ZOOM = 0.15  # how far the camera travels: 15% zoom
CX, CY = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"  # keep the crop centred

# Camera moves, cycled per scene so consecutive shots don't all move the same way.
# Each takes `e`, an ffmpeg expression for eased time running 0 → 1, and returns (zoom, x, y).
MOVES = [
    lambda e: (f"1+{ZOOM}*{e}", CX, CY),                    # zoom in
    lambda e: (f"1+{ZOOM}", f"(iw-iw/zoom)*{e}", CY),       # pan right
    lambda e: (f"1+{ZOOM}*(1-{e})", CX, CY),                # zoom out
    lambda e: (f"1+{ZOOM}", f"(iw-iw/zoom)*(1-{e})", CY),   # pan left
]

LTX_WORKFLOW = json.loads((WORKFLOWS_DIR / "ltxv_i2v.json").read_text())
LTX_NEGATIVE = "worst quality, inconsistent motion, blurry, jittery, distorted, static, watermark"


async def animate(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path, index: int,
                  seed: int = 0, on_progress: Callable[[float], None] = lambda _: None) -> Path:
    motion = settings.get("motion", "kenburns")
    if motion == "kenburns":
        return await ken_burns(image, settings, out_path, index)
    if motion == "ltx":
        return await ltx(comfy, image, prompt, settings, out_path, seed, on_progress)
    raise ValueError(f"Unknown motion '{motion}'")


async def ken_burns(image: Path, settings: dict, out_path: Path, index: int) -> Path:
    w, h = settings["width"], settings["height"]
    frames = round(settings.get("clip_seconds", 3) * FPS)
    t = f"(on/{frames - 1})"  # zoompan's output frame number → 0..1
    eased = f"({t}*{t}*(3-2*{t}))"  # smoothstep: no mechanical start and stop
    z, x, y = MOVES[index % len(MOVES)](eased)

    # Upscaling 4x before zoompan hides its integer-pixel jitter; it then renders at the target size.
    vf = (f"scale={w * 4}:{h * 4}:flags=lanczos,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={FPS},"
          f"format=yuv420p")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run("-i", str(image), "-vf", vf, "-frames:v", str(frames), "-r", str(FPS), *ffmpeg.ENCODE, str(out_path))
    return out_path


async def ltx(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path,
              seed: int, on_progress: Callable[[float], None]) -> Path:
    w, h = settings["width"], settings["height"]
    seconds = settings.get("clip_seconds", 3)
    # LTX needs 8k+1 frames; generate at least the clip length, then trim to it exactly
    frames = 8 * max(1, round(seconds * FPS / 8)) + 1

    wf = copy.deepcopy(LTX_WORKFLOW)
    wf["load_image"]["inputs"]["image"] = await comfy.upload_image(image, f"{out_path.parent.parent.name}_{image.name}")
    style = settings.get("style", "").strip()
    wf["positive"]["inputs"]["text"] = f"{prompt}, natural subtle motion, {style}" if style else f"{prompt}, natural subtle motion"
    wf["negative"]["inputs"]["text"] = LTX_NEGATIVE
    wf["i2v"]["inputs"].update(width=w, height=h, length=frames)
    wf["sigmas"]["inputs"]["steps"] = settings.get("video_steps", 8)
    wf["sampler"]["inputs"]["noise_seed"] = seed
    wf["save"]["inputs"]["filename_prefix"] = f"happica/{out_path.parent.parent.name}_{out_path.stem}"

    outputs = await comfy.run(wf, lambda value, total: on_progress(value / total))
    files = outputs.get("save", {}).get("images", [])
    if not files:
        raise RuntimeError("LTX-Video finished but returned no video")

    # Re-encode to the same format as every other clip so crossfade assembly works
    raw = out_path.with_suffix(".webm")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(await comfy.download(files[0]["filename"], files[0]["subfolder"], files[0]["type"]))
    await ffmpeg.run("-i", str(raw), "-t", str(seconds), "-vf", f"scale={w}:{h},fps={FPS}", "-an", *ffmpeg.ENCODE, str(out_path))
    raw.unlink()
    return out_path
