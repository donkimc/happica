"""Make low-resolution images and videos bigger — e.g. 768×512 → 1080p or 4K. API only (POST /api/v1/upscale).

"ai" runs Real-ESRGAN on the Apple GPU (backend/tools/upscale_worker.py, in ComfyUI's Python, which has PyTorch):
it redraws detail rather than just stretching pixels. About 30 s per image; for video about 0.8 s per frame
on an M2 (a 30 s clip ≈ 10 min), so upscale short clips first, then combine them into a long one.
"fast" is a plain lanczos resize with ffmpeg — seconds, for when AI time isn't worth it.
"""
import asyncio
import json
import os
from pathlib import Path

from config import OUTPUTS_DIR, ROOT_DIR

from . import ffmpeg

PYTHON = ROOT_DIR / "engine" / "ComfyUI" / ".venv" / "bin" / "python"
WORKER = Path(__file__).resolve().parent.parent / "tools" / "upscale_worker.py"
MODELS = ROOT_DIR / "models" / "upscale_models"
IMAGE_MODEL = MODELS / "RealESRGAN_x4plus.pth"  # best detail for pictures
VIDEO_MODEL = MODELS / "realesr-general-x4v3.pth"  # ~4x faster, steady from frame to frame
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
MAX_SIDE = 3840


def sources_of(job: dict) -> list[Path]:
    """The files of a finished job to upscale: a video job's final video, or every image of an image/character job."""
    r = job.get("result") or {}
    if job["state"] != "done":
        raise ValueError(f"job {job['id']} isn't finished (state: {job['state']})")
    if r.get("video"):
        return [OUTPUTS_DIR / r["video"]]
    if r.get("images"):
        return [OUTPUTS_DIR / p for p in r["images"]]
    raise ValueError(f"job {job['id']} ({job['kind']}) has no image or video to upscale")


async def probe(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
        "-of", "json", str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    info = json.loads(out or b"{}")
    s = (info.get("streams") or [{}])[0]
    if not s.get("width"):
        raise ValueError(f"{path.name} isn't an image or video this app can read")
    return {"width": s["width"], "height": s["height"], "duration": float(info.get("format", {}).get("duration", 0) or 0)}


def target_size(w: int, h: int, scale: float | None, height: int | None) -> tuple[int, int]:
    """Output size keeping the aspect ratio: a scale factor, or a target height (for portrait clips, the short side)."""
    if height:
        f = height / min(w, h)
    else:
        f = scale or 2
    f = min(f, MAX_SIDE / max(w, h))  # never past 4K on the long side
    even = lambda v: max(2, int(round(v / 2)) * 2)
    return even(w * f), even(h * f)


async def run(job: dict, job_dir: Path, report, sources: list[Path], is_cancelled) -> dict:
    s = job["settings"]
    method = s.get("method", "ai")
    out_dir = job_dir / "upscaled"
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = lambda p: str(p.relative_to(OUTPUTS_DIR))
    images, video, sizes = [], None, []
    for n, src in enumerate(sources):
        info = await probe(src)
        w, h = target_size(info["width"], info["height"], s.get("scale"), s.get("target_height"))
        is_video = src.suffix.lower() not in IMAGE_EXT
        out = out_dir / (f"{src.stem}_{w}x{h}" + (".mp4" if is_video else ".png"))
        step = lambda f, _n=n: report(f"upscaling {'video' if is_video else 'image'}"
                                      + (f" {_n + 1}/{len(sources)}" if len(sources) > 1 else ""),
                                      (_n + f) / len(sources))
        step(0)
        if method == "fast":
            vf = f"scale={w}:{h}:flags=lanczos"
            if is_video:
                await ffmpeg.run("-i", str(src), "-vf", vf, *ffmpeg.ENCODE, "-c:a", "copy", str(out))
            else:
                await ffmpeg.run("-i", str(src), "-vf", vf, str(out))
        else:
            await _ai(src, out, w, h, is_video, step, is_cancelled)
        sizes.append([w, h])
        if is_video:
            video = rel(out)
        else:
            images.append(rel(out))
    result = {"method": method, "sizes": sizes}
    if video:
        result["video"] = video
    if images:
        result["images"] = images
    return result


async def _ai(src: Path, out: Path, w: int, h: int, is_video: bool, step, is_cancelled) -> None:
    proc = await asyncio.create_subprocess_exec(
        str(PYTHON), str(WORKER), "--model", str(VIDEO_MODEL if is_video else IMAGE_MODEL), "--input", str(src),
        "--output", str(out), "--width", str(w), "--height", str(h), "--kind", "video" if is_video else "image",
        "--batch", "1", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTORCH_ENABLE_MPS_FALLBACK": "1"})
    err = asyncio.create_task(proc.stderr.read())
    try:
        async for line in proc.stdout:
            parts = line.decode().split()
            if len(parts) == 3 and parts[0] == "PROGRESS":
                done, total = int(parts[1]), int(parts[2]) or 1
                step(min(done / total, 0.99))
                if is_cancelled():
                    proc.kill()
                    break
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    if is_cancelled():
        out.unlink(missing_ok=True)
        raise RuntimeError("cancelled")
    if proc.returncode != 0:
        raise RuntimeError(f"upscaling failed: {(await err).decode()[-800:]}")
