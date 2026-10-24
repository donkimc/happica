"""OpenPose stick figures with realistic human proportions (about 7.5 heads tall), drawn for the SDXL OpenPose
ControlNet (xinsir, Apache-2.0). They hold a full-body picture to a normal body — fast models otherwise stretch the
torso in tall frames — and to a true front, side or back view.
"""
from pathlib import Path

from PIL import Image, ImageDraw

MODEL = "openpose-sdxl-xinsir.safetensors"  # in engine/ComfyUI/models/controlnet
STRENGTH, END = 0.8, 0.6  # strong enough to hold the pose, released before the end so details stay free

# COCO-18 keypoints, in head units: x from the body's centre line (+ = image right), y down from the top of the head
_FRONT = {0: (0, 0.55), 14: (-0.18, 0.45), 15: (0.18, 0.45), 16: (-0.42, 0.5), 17: (0.42, 0.5), 1: (0, 1.25),
          2: (-0.95, 1.4), 3: (-1.15, 2.6), 4: (-1.2, 3.7), 5: (0.95, 1.4), 6: (1.15, 2.6), 7: (1.2, 3.7),
          8: (-0.5, 3.75), 9: (-0.5, 5.4), 10: (-0.48, 7.1), 11: (0.5, 3.75), 12: (0.5, 5.4), 13: (0.48, 7.1)}
# seen from behind: the body's right is on the image right, and no face
_BACK = {k: (-x, y) for k, (x, y) in _FRONT.items() if k not in (0, 14, 15)}
# profile facing image left: shoulders and hips overlap, one eye, one ear
_SIDE = {0: (-0.45, 0.6), 14: (-0.3, 0.45), 16: (0.1, 0.5), 1: (0, 1.25), 2: (-0.08, 1.4), 3: (-0.05, 2.6),
         4: (-0.1, 3.7), 5: (0.08, 1.4), 6: (0.1, 2.6), 7: (0.05, 3.7), 8: (-0.05, 3.75), 9: (-0.1, 5.4),
         10: (-0.1, 7.1), 11: (0.05, 3.75), 12: (0.1, 5.4), 13: (0.12, 7.1)}
POSES = {"front": _FRONT, "side": _SIDE, "back": _BACK}

_LIMBS = [(1, 2), (1, 5), (2, 3), (3, 4), (5, 6), (6, 7), (1, 8), (8, 9), (9, 10), (1, 11), (11, 12), (12, 13),
          (1, 0), (0, 14), (14, 16), (0, 15), (15, 17)]
_COLORS = [(255, 0, 0), (255, 85, 0), (255, 170, 0), (255, 255, 0), (170, 255, 0), (85, 255, 0), (0, 255, 0),
           (0, 255, 85), (0, 255, 170), (0, 255, 255), (0, 170, 255), (0, 85, 255), (0, 0, 255), (85, 0, 255),
           (170, 0, 255), (255, 0, 255), (255, 0, 170), (255, 0, 85)]


def draw(view: str, size: tuple[int, int], out: Path, height_share: float = 0.8) -> Path:  # 0.86 cut the feet off
    """A standard OpenPose picture (coloured limbs and joints on black) of one standing figure, centred."""
    w, h = size
    head = h * height_share / 7.5
    top = (h - head * 7.5) / 2
    pts = {k: (w / 2 + x * head, top + y * head) for k, (x, y) in POSES[view].items()}
    img = Image.new("RGB", size, "black")
    d = ImageDraw.Draw(img)
    width = max(3, round(head * 0.12))
    for n, (a, b) in enumerate(_LIMBS):
        if a in pts and b in pts:
            d.line([pts[a], pts[b]], fill=tuple(int(c * 0.6) for c in _COLORS[n]), width=width)
    for k, (x, y) in pts.items():
        r = max(3, round(head * 0.07))
        d.ellipse((x - r, y - r, x + r, y + r), fill=_COLORS[k])
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
