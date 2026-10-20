"""Extras for one-call story videos (POST /api/v1/stories): a cover picture for the thumbnail and a caption file.

cover.png  the first scene's picture at the video's final size with the title drawn across the top
caption.txt  title, caption text and hashtags, ready to paste into TikTok
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import captions


def make_cover(scene: Path, title: str, size: tuple[int, int], out: Path) -> Path:
    w, h = size
    with Image.open(scene) as img:
        cover = img.convert("RGB").resize((w, h), Image.Resampling.LANCZOS).convert("RGBA")
    # a soft dark band behind the title so it reads on any picture
    band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(band).rectangle((0, 0, w, round(h * 0.26)), fill=(0, 0, 0, 110))
    cover = Image.alpha_composite(cover, band.filter(ImageFilter.GaussianBlur(h * 0.03)))

    draw = ImageDraw.Draw(cover)
    size_px = round(w * 0.105)
    while True:  # up to two lines, as big as fits
        font = captions.font_for(title, size_px, "rounded")
        stroke = max(3, size_px // 10)
        lines = captions._wrap(draw, title, font, stroke, w * 0.88)
        widths = [draw.textbbox((0, 0), l, font=font, stroke_width=stroke)[2] for l in lines]
        if (max(widths) <= w * 0.88 and len(lines) <= 2) or size_px <= 24:
            break
        size_px -= 2
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * 1.1)
    top = h * 0.13 - line_h * len(lines) / 2
    for k, (line, lw) in enumerate(zip(lines, widths)):
        draw.text(((w - lw) / 2, top + k * line_h), line, font=font, fill=(255, 255, 255), stroke_width=stroke,
                  stroke_fill=(40, 30, 60))
    cover.convert("RGB").save(out)
    return out


def write_caption(title: str, caption: str, hashtags: list[str], out: Path) -> Path:
    tags = " ".join(f"#{t.strip().lstrip('#').replace(' ', '')}" for t in hashtags if t.strip().lstrip("#"))
    out.write_text("\n\n".join(part for part in (title.strip(), caption.strip(), tags) if part) + "\n")
    return out
