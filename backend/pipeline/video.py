"""Image → video clip.

kenburns: slow zoom/pan over the still with ffmpeg (under a second per clip).
none:     the still held as is (one scene of a script that mixes motions).
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
LTX_NEGATIVE = ("worst quality, inconsistent motion, blurry, jittery, distorted, deformed face, extra limbs, "
                "deformed hands, color bleeding, morphing, flicker, static, watermark")
LTX_MOTION_HINT = "smooth gentle animation, stable faces, consistent cartoon style"

LTX_MAX_SECONDS = 8  # longer LTX clips get slow and drift; anything beyond holds the last frame
# Steadier LTX (default): plain euler instead of euler_ancestral (no fresh noise each step, so faces and eyes
# wobble less), lighter input compression (keeps the clean picture's outlines), and the clip's last frame pinned
# to the start picture (LTXVAddGuide) so the motion returns to the clean drawing instead of drifting off-model.
LTX_STEADY = {"sampler": "euler", "img_compression": 20}


async def animate(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path, index: int,
                  seconds: float, seed: int = 0, on_progress: Callable[[float], None] = lambda _: None) -> Path:
    motion = settings.get("motion", "kenburns")
    if motion == "kenburns":
        return await ken_burns(image, settings, out_path, index, seconds)
    if motion == "ltx":
        return await ltx(comfy, image, prompt, settings, out_path, seconds, seed, on_progress)
    if motion == "none":
        return await still(image, settings, out_path, seconds)
    raise ValueError(f"Unknown motion '{motion}'")


async def still(image: Path, settings: dict, out_path: Path, seconds: float) -> Path:
    """The picture held without camera movement (a script scene with motion_mode "none")."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = f"scale={settings['width']}:{settings['height']}:flags=lanczos,fps={FPS},format=yuv420p"
    await ffmpeg.run("-loop", "1", "-i", str(image), "-vf", vf, "-t", f"{seconds:.3f}", "-r", str(FPS), *ffmpeg.ENCODE,
                     str(out_path))
    return out_path


async def ken_burns(image: Path, settings: dict, out_path: Path, index: int, seconds: float) -> Path:
    w, h = settings["width"], settings["height"]
    frames = round(seconds * FPS)
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


def _pin_last_frame(wf: dict) -> None:
    """Condition the last frame on the start picture too, then crop the guide latents off before decoding."""
    wf["guide"] = {"class_type": "LTXVAddGuide", "inputs": {
        "positive": ["i2v", 0], "negative": ["i2v", 1], "vae": ["ckpt", 2], "latent": ["i2v", 2],
        "image": ["preprocess", 0], "frame_idx": -1, "strength": 1.0}}
    wf["conditioning"]["inputs"].update(positive=["guide", 0], negative=["guide", 1])
    for node in ("model_sampling", "sigmas"):
        wf[node]["inputs"]["latent"] = ["guide", 2]
    wf["sampler"]["inputs"]["latent_image"] = ["guide", 2]
    wf["crop_guides"] = {"class_type": "LTXVCropGuides", "inputs": {
        "positive": ["conditioning", 0], "negative": ["conditioning", 1], "latent": ["sampler", 0]}}
    wf["decode"]["inputs"]["samples"] = ["crop_guides", 2]


async def ltx(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path,
              seconds: float, seed: int, on_progress: Callable[[float], None]) -> Path:
    w, h = settings["width"], settings["height"]
    gen = min(seconds, LTX_MAX_SECONDS)
    # LTX needs 8k+1 frames; generate at least the clip length, then trim to it exactly
    frames = 8 * max(1, round(gen * FPS / 8)) + 1

    wf = copy.deepcopy(LTX_WORKFLOW)
    wf["load_image"]["inputs"]["image"] = await comfy.upload_image(image, f"{out_path.parent.parent.name}_{image.name}")
    style = settings.get("style", "").strip()
    wf["positive"]["inputs"]["text"] = f"{prompt}, {LTX_MOTION_HINT}" + (f", {style}" if style else "")
    wf["negative"]["inputs"]["text"] = LTX_NEGATIVE
    wf["i2v"]["inputs"].update(width=w, height=h, length=frames)
    wf["sigmas"]["inputs"]["steps"] = settings.get("video_steps", 8)
    wf["sampler"]["inputs"]["noise_seed"] = seed
    if settings.get("ltx_steady", True):
        wf["sampler_select"]["inputs"]["sampler_name"] = LTX_STEADY["sampler"]
        wf["preprocess"]["inputs"]["img_compression"] = LTX_STEADY["img_compression"]
    if settings.get("ltx_end_frame", True):
        _pin_last_frame(wf)
    wf["save"]["inputs"]["filename_prefix"] = f"happica/{out_path.parent.parent.name}_{out_path.stem}"

    outputs = await comfy.run(wf, lambda value, total, _node: on_progress(value / total))
    files = outputs.get("save", {}).get("images", [])
    if not files:
        raise RuntimeError("LTX-Video finished but returned no video")

    # Re-encode to the same format as every other clip so crossfade assembly works
    raw = out_path.with_suffix(".webm")
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(await comfy.download(files[0]["filename"], files[0]["subfolder"], files[0]["type"]))
    hold = max(0.0, seconds - gen)
    vf = f"scale={w}:{h},fps={FPS}" + (f",tpad=stop_mode=clone:stop_duration={hold:.3f}" if hold else "")
    await ffmpeg.run("-i", str(raw), "-vf", vf, "-t", f"{seconds:.3f}", "-an", *ffmpeg.ENCODE, str(out_path))
    raw.unlink()
    return out_path
