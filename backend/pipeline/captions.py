"""Subtitles: timed caption chunks, an .srt file, and caption images for burning in.

This ffmpeg build has no libass/freetype (no `subtitles` or `drawtext` filter), so burned-in
captions are drawn with Pillow as transparent PNGs and overlaid by ffmpeg.
"""
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
MAX_CHARS = 42  # per caption chunk, so captions stay short enough to read


@dataclass
class Cue:
    start: float
    end: float
    text: str


def chunk(text: str) -> list[str]:
    """Split into the fewest chunks of at most ~MAX_CHARS, balanced so none is a one-word leftover."""
    words = text.split()
    n = max(1, math.ceil(len(text) / MAX_CHARS))
    target = len(text) / n
    chunks, line = [], ""
    for word in words:
        if line and len(chunks) < n - 1 and len(line) + 1 + len(word) / 2 > target:
            chunks.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return chunks + [line] if line else chunks


def cues_for(text: str, start: float, duration: float) -> list[Cue]:
    """Split one narration line into cues, timing each by its share of the characters."""
    parts = chunk(text)
    total = sum(len(p) for p in parts) or 1
    cues, t = [], start
    for p in parts:
        d = duration * len(p) / total
        cues.append(Cue(t, t + d, p))
        t += d
    return cues


def _ts(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: list[Cue], out: Path) -> Path:
    out.write_text("\n".join(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{c.text}\n" for i, c in enumerate(cues, 1)))
    return out


def write_vtt(cues: list[Cue], out: Path) -> Path:
    """WebVTT for the browser's <video> player, which ignores subtitle tracks inside the MP4."""
    body = "\n".join(f"{_ts(c.start).replace(',', '.')} --> {_ts(c.end).replace(',', '.')}\n{c.text}\n" for c in cues)
    out.write_text(f"WEBVTT\n\n{body}")
    return out


def render_png(text: str, width: int, height: int, out: Path) -> Path:
    """A full-frame transparent image with the caption centred near the bottom."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    size = max(16, round(height * 0.055))
    font = ImageFont.truetype(FONT, size)
    stroke = max(2, size // 12)
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = (width - (x1 - x0)) / 2 - x0
    y = height * 0.92 - (y1 - y0) - y0
    draw.text((x, y), text, font=font, fill="white", stroke_width=stroke, stroke_fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
