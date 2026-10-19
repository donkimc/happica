"""Image → video clip.

kenburns: slow zoom/pan over the still with ffmpeg (seconds per clip).
ltx: AI motion with LTX-Video through ComfyUI (phase 5).
"""
from pathlib import Path

from comfy_client import ComfyClient

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


async def animate(comfy: ComfyClient, image: Path, prompt: str, settings: dict, out_path: Path, index: int) -> Path:
    motion = settings.get("motion", "kenburns")
    if motion == "kenburns":
        return await ken_burns(image, settings, out_path, index)
    raise NotImplementedError(f"Motion '{motion}' arrives in phase 5.")


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
