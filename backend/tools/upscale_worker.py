"""AI upscaling with Real-ESRGAN (via spandrel) on the Apple GPU. Runs with ComfyUI's Python, which has PyTorch:

    engine/ComfyUI/.venv/bin/python backend/tools/upscale_worker.py --model M --input IN --output OUT --width W --height H

Images: one pass of the model (4x), then resized to the exact size asked for.
Videos: frames stream ffmpeg → model → ffmpeg, so long videos never sit in memory or on disk as frames;
the original audio is copied as-is. Progress is printed as "PROGRESS <done> <total>" lines.
"""
import argparse
import json
import subprocess
import sys

import numpy as np
import torch
from PIL import Image
from spandrel import ModelLoader


def load(path: str, half: bool):
    model = ModelLoader().load_from_file(path).model.eval().to("mps")
    return model.half() if half else model


@torch.inference_mode()
def upscale(model, frames: np.ndarray, half: bool, tile: int) -> np.ndarray:
    """uint8 [N,H,W,3] → uint8 [N,4H,4W,3]."""
    x = torch.from_numpy(frames).to("mps").permute(0, 3, 1, 2)
    x = (x.half() if half else x.float()) / 255
    if tile and max(x.shape[2:]) > tile:
        y = _tiled(model, x, tile)
    else:
        y = model(x)
    return (y.clamp(0, 1) * 255).round().byte().permute(0, 2, 3, 1).cpu().numpy()


def _tiled(model, x: torch.Tensor, tile: int, pad: int = 16) -> torch.Tensor:
    """Upscale in overlapping tiles (keeps memory low for the big image model on large pictures)."""
    n, c, h, w = x.shape
    s = 4
    out = torch.zeros((n, c, h * s, w * s), dtype=x.dtype, device=x.device)
    for top in range(0, h, tile):
        for left in range(0, w, tile):
            t0, l0 = max(top - pad, 0), max(left - pad, 0)
            t1, l1 = min(top + tile + pad, h), min(left + tile + pad, w)
            y = model(x[:, :, t0:t1, l0:l1])
            bt, bl = (top - t0) * s, (left - l0) * s
            hh, ww = (min(top + tile, h) - top) * s, (min(left + tile, w) - left) * s
            out[:, :, top * s: top * s + hh, left * s: left * s + ww] = y[:, :, bt: bt + hh, bl: bl + ww]
    return out


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,r_frame_rate,nb_read_packets", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    num, den = s["r_frame_rate"].split("/")
    return {"w": int(s["width"]), "h": int(s["height"]), "fps": f"{num}/{den}", "frames": int(s.get("nb_read_packets") or 0)}


def video(model, args, half: bool) -> None:
    info = probe(args.input)
    w, h, total = info["w"], info["h"], info["frames"]
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", args.input, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE)
    writer = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w * 4}x{h * 4}", "-r", info["fps"],
         "-i", "-", "-i", args.input, "-map", "0:v", "-map", "1:a?", "-vf", f"scale={args.width}:{args.height}:flags=lanczos",
         "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-movflags", "+faststart", args.output], stdin=subprocess.PIPE)
    size, done = w * h * 3, 0
    while True:
        batch = []
        for _ in range(args.batch):
            buf = reader.stdout.read(size)
            if len(buf) < size:
                break
            batch.append(np.frombuffer(buf, np.uint8).reshape(h, w, 3))
        if not batch:
            break
        # Tiled like images: one MPS convolution over a whole large frame returns garbage (striped noise)
        writer.stdin.write(upscale(model, np.stack(batch), half, args.tile).tobytes())
        done += len(batch)
        print(f"PROGRESS {done} {total}", flush=True)
    writer.stdin.close()
    if reader.wait() or writer.wait():
        sys.exit("ffmpeg failed while upscaling the video")


def image(model, args, half: bool) -> None:
    img = np.asarray(Image.open(args.input).convert("RGB"))
    out = upscale(model, img[None], half, args.tile)[0]
    Image.fromarray(out).resize((args.width, args.height), Image.Resampling.LANCZOS).save(args.output)
    print("PROGRESS 1 1", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--kind", choices=["image", "video"], required=True)
    p.add_argument("--fp32", action="store_true", help="full precision (slower; fp16 is the default)")
    p.add_argument("--batch", type=int, default=4)
    p.add_argument("--tile", type=int, default=512)
    a = p.parse_args()
    m = load(a.model, not a.fp32)
    (video if a.kind == "video" else image)(m, a, not a.fp32)
