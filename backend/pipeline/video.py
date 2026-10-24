"""Image → video clip.

kenburns: slow zoom/pan over the still with ffmpeg (under a second per clip).
parallax: the character cut out (BiRefNet) and moved a little faster than the background behind it.
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
    if motion == "parallax":
        return await parallax(image, settings, out_path, index, seconds)
    raise ValueError(f"Unknown motion '{motion}'")


async def still(image: Path, settings: dict, out_path: Path, seconds: float, size: tuple[int, int] | None = None) -> Path:
    """The picture held without camera movement (a script scene with motion_mode "none")."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size or (settings["width"], settings["height"])
    vf = f"scale={w}:{h}:flags=lanczos,fps={FPS},format=yuv420p"
    await ffmpeg.run("-loop", "1", "-i", str(image), "-vf", vf, "-t", f"{seconds:.3f}", "-r", str(FPS), *ffmpeg.ENCODE,
                     str(out_path))
    return out_path


async def ken_burns(image: Path, settings: dict, out_path: Path, index: int, seconds: float,
                    size: tuple[int, int] | None = None, prescale: int = 4) -> Path:
    """`size`: render at this size (default the scene size); `prescale`: how much bigger the picture is made before
    zoompan (4 hides its integer-pixel jitter; an already enlarged picture needs only 2)."""
    w, h = size or (settings["width"], settings["height"])
    frames = round(seconds * FPS)
    t = f"(on/{frames - 1})"  # zoompan's output frame number → 0..1
    eased = f"({t}*{t}*(3-2*{t}))"  # smoothstep: no mechanical start and stop
    z, x, y = MOVES[index % len(MOVES)](eased)

    # Upscaling 4x before zoompan hides its integer-pixel jitter; it then renders at the target size.
    vf = (f"scale={w * prescale}:{h * prescale}:flags=lanczos,"
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


# LTX-Video 2B was trained around 768×512; on a 16 GB Mac 576×1024 clips take 18–25 min. By default (ltx_short_side)
# clips are drawn with this short side (512×896 → 384×672) and scaled back to the scene size — faster, but a
# two-character scene at 448×768 drifted and warped eyes in a test, so stories that need it draw at full size.
LTX_SHORT_SIDE = 384


def ltx_size(w: int, h: int, short_side: int = LTX_SHORT_SIDE) -> tuple[int, int]:
    """The size LTX draws at: the scene size shrunk to `short_side` (never enlarged), in multiples of 32."""
    f = min(1.0, short_side / min(w, h))
    return max(32, round(w * f / 32) * 32), max(32, round(h * f / 32) * 32)


async def ltx(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path,
              seconds: float, seed: int, on_progress: Callable[[float], None]) -> Path:
    w, h = settings["width"], settings["height"]
    # jobs made before this setting (no key) keep drawing at the full scene size; None also means full size
    short = settings.get("ltx_short_side")
    gw, gh = ltx_size(w, h, short) if short else (w, h)
    gen = min(seconds, LTX_MAX_SECONDS)
    # LTX needs 8k+1 frames; generate at least the clip length, then trim to it exactly
    frames = 8 * max(1, round(gen * FPS / 8)) + 1

    wf = copy.deepcopy(LTX_WORKFLOW)
    wf["load_image"]["inputs"]["image"] = await comfy.upload_image(image, f"{out_path.parent.parent.name}_{image.name}")
    style = settings.get("style", "").strip()
    wf["positive"]["inputs"]["text"] = f"{prompt}, {LTX_MOTION_HINT}" + (f", {style}" if style else "")
    wf["negative"]["inputs"]["text"] = LTX_NEGATIVE
    wf["i2v"]["inputs"].update(width=gw, height=gh, length=frames)
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


# ---------- parallax: the character moves in front of the background (layered depth, no AI redrawing) ----------
# The cut-out (BiRefNet via rembg) runs in tools/cutout_worker.py: in the backend the model kept gigabytes for good.
CUTOUT_WORKER = Path(__file__).resolve().parent.parent / "tools" / "cutout_worker.py"


def _cut_out(image: Path):
    """(foreground RGBA, background RGB with the hole filled in), or None when no clear subject is found."""
    import subprocess
    import sys
    import tempfile

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        fg_path, bg_path = Path(tmp) / "fg.png", Path(tmp) / "bg.png"
        done = subprocess.run([sys.executable, str(CUTOUT_WORKER), str(image), str(fg_path), str(bg_path)],
                              capture_output=True, text=True)
        if done.returncode == 3:
            return None
        if done.returncode != 0:
            raise RuntimeError(f"parallax cut-out failed: {done.stderr[-500:]}")
        fg, bg = Image.open(fg_path), Image.open(bg_path)
        fg.load(), bg.load()
        return fg, bg


def _parallax_frames(image: Path, out_path: Path, index: int, seconds: float, size: tuple[int, int]) -> bool:
    import subprocess

    from PIL import Image

    img = Image.open(image).convert("RGB")
    layers = _cut_out(image)
    if not layers:
        return False
    fg, bg = layers
    w, h = size
    frames = round(seconds * FPS)
    direction = 1 if index % 2 == 0 else -1  # alternate drift direction between scenes
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                             "-r", str(FPS), "-i", "-", *ffmpeg.ENCODE, str(out_path)], stdin=subprocess.PIPE)

    def view(layer, zoom, shift_x):
        """Crop the layer (sized like the picture) for a zoom and a horizontal shift, scaled to the frame."""
        cw, ch = img.width / zoom, img.height / zoom
        x0 = (img.width - cw) / 2 + shift_x * img.width
        y0 = (img.height - ch) / 2
        return layer.resize((w, h), Image.Resampling.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch))

    for k in range(frames):
        t = k / max(1, frames - 1)
        e = t * t * (3 - 2 * t)  # smoothstep
        back = view(bg, 1.04 + 0.03 * e, direction * 0.01 * e)  # background: slow
        front = view(fg, 1.04 + 0.08 * e, -direction * 0.015 * e)  # character: closer, so it moves more
        back.paste(front, (0, 0), front)
        proc.stdin.write(back.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError("ffmpeg failed while making a parallax clip")
    return True


async def parallax(image: Path, settings: dict, out_path: Path, index: int, seconds: float,
                   size: tuple[int, int] | None = None) -> Path:
    """Layered motion: the cut-out character drifts and grows a little faster than the background behind it.
    Falls back to pan & zoom when no clear subject can be cut out."""
    import asyncio

    out_path.parent.mkdir(parents=True, exist_ok=True)
    size = size or (settings["width"], settings["height"])
    if await asyncio.to_thread(_parallax_frames, image, out_path, index, seconds, size):
        return out_path
    return await ken_burns(image, settings, out_path, index, seconds, size=size, prescale=2)
