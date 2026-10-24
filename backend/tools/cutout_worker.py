"""Parallax cut-out in its own process, so the BiRefNet model's memory goes back to the system when it exits
(kept in the backend it grew the server by gigabytes). Run with the backend's Python:

    backend/.venv/bin/python backend/tools/cutout_worker.py PICTURE FOREGROUND.png BACKGROUND.png

Writes the character as RGBA and the background with the hole behind it filled; exits with 3 when no clear
subject is found (then the caller falls back to pan & zoom).
"""
import sys

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

MODEL = "birefnet-general-lite"  # BiRefNet (MIT) via rembg


def main(src: str, fg_out: str, bg_out: str) -> int:
    img = Image.open(src).convert("RGB")
    small = img.copy()
    small.thumbnail((1024, 1024))
    mask = remove(small, session=new_session(MODEL), only_mask=True).resize(img.size, Image.Resampling.BILINEAR)
    share = (np.asarray(mask) > 128).mean()
    if not 0.02 <= share <= 0.7:  # nothing found, or "everything is foreground": not worth layering
        return 3
    fg = img.convert("RGBA")
    fg.putalpha(mask)
    fg.save(fg_out)
    # Fill the hole behind the character from its surroundings (at half size: OpenCV's inpaint is slow on big frames)
    half = (img.width // 2, img.height // 2)
    hole = cv2.dilate((np.asarray(mask.resize(half)) > 40).astype(np.uint8) * 255, np.ones((25, 25), np.uint8))
    filled = cv2.inpaint(np.asarray(img.resize(half)), hole, 9, cv2.INPAINT_TELEA)
    bg = np.asarray(img).copy()
    big_hole = np.asarray(Image.fromarray(hole).resize(img.size)) > 0
    bg[big_hole] = np.asarray(Image.fromarray(filled).resize(img.size, Image.Resampling.BICUBIC))[big_hole]
    Image.fromarray(bg).save(bg_out)
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
