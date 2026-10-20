"""Subtitles: timed caption chunks, an .srt file, and caption images for burning in.

This ffmpeg build has no libass/freetype (no `subtitles` or `drawtext` filter), so burned-in
captions are drawn with Pillow as transparent PNGs and overlaid by ffmpeg.
"""
import math
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import ROOT_DIR

FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
# Rounded, friendly Korean faces for kids' videos (SIL Open Font License, in fonts/); Apple SD Gothic Neo otherwise
KOREAN_FAMILIES = {"rounded": ROOT_DIR / "fonts" / "Jua-Regular.ttf", "handwritten": ROOT_DIR / "fonts" / "GamjaFlower-Regular.ttf"}
KOREAN_FONT = ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 6)  # face 6 = Bold
UNICODE_FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"  # Cyrillic, Greek, CJK…
HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯]")
# Scripts whose letters join or reorder (Arabic, Hebrew, Indic): this Pillow has no shaping engine (raqm),
# so they can't be drawn correctly into the picture; those videos get the player-rendered subtitle track.
NEEDS_SHAPING = re.compile(r"[֐-ࣿऀ-෿יִ-﷿ﹰ-﻿]")


def font_for(text: str, size: int, family: str = "system") -> ImageFont.FreeTypeFont:
    if HANGUL.search(text):
        if (path := KOREAN_FAMILIES.get(family)) and path.is_file():
            return ImageFont.truetype(str(path), size)
        return ImageFont.truetype(KOREAN_FONT[0], size, index=KOREAN_FONT[1])
    if any(ord(c) > 0x24F for c in text):
        return ImageFont.truetype(UNICODE_FONT, size)
    return ImageFont.truetype(FONT, size)


def can_burn(cues: list["Cue"]) -> bool:
    return not any(NEEDS_SHAPING.search(c.text) for c in cues)
MAX_CHARS = 42  # per caption chunk at 768 px wide; narrower frames get proportionally shorter chunks


def max_chars_for(width: int) -> int:
    return max(16, round(MAX_CHARS * width / 768))


@dataclass
class Cue:
    start: float
    end: float
    text: str
    style: str = "narration"  # "lyric": sung lyrics, drawn in yellow


def chunk(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Split into the fewest chunks of at most ~max_chars, balanced so none is a one-word leftover."""
    words = text.split()
    n = max(1, math.ceil(len(text) / max_chars))
    target = len(text) / n
    chunks, line = [], ""
    for word in words:
        if line and len(chunks) < n - 1 and len(line) + 1 + len(word) / 2 > target:
            chunks.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return chunks + [line] if line else chunks


SENTENCE_END = re.compile(r'(?:(?<=[.!?…。！？])|(?<=[.!?…。！？]["”’」]))\s+')


def sentences(text: str, limit: int) -> list[str]:
    """Sentences, with short neighbours packed together up to `limit` characters."""
    out: list[str] = []
    for s in (x.strip() for x in SENTENCE_END.split(text.strip())):
        if not s:
            continue
        if out and len(out[-1]) + 1 + len(s) <= limit:
            out[-1] += " " + s
        else:
            out.append(s)
    return out or [text]


def cues_for(text: str, start: float, duration: float, max_chars: int = MAX_CHARS, lines: int = 1) -> list[Cue]:
    """Split one narration line into cues, timing each by its share of the characters. With lines=2 (narrow
    portrait frames) each cue holds two lines, so short lines don't flash by as many quick cues."""
    limit = round(max_chars * (1.5 if lines > 1 else 1))
    parts = []
    for sentence in sentences(text, limit):  # a cue never ends mid-sentence when the sentence fits in one
        parts += chunk(sentence, limit)
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


DEFAULT_STYLE = {"font_size_scale": 1.0, "color": "#ffffff", "lyric_color": "#ffe14d", "position": "bottom",
                 "box": False, "font": "rounded"}


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[k: k + 2], 16) for k in (0, 2, 4)) if re.fullmatch(r"[0-9a-fA-F]{6}", color) else (255, 255, 255)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, stroke: int, max_width: float) -> list[str]:
    """One line if it fits, else two lines split at the space that balances them best."""
    width = lambda t: draw.textbbox((0, 0), t, font=font, stroke_width=stroke)[2]
    if width(text) <= max_width or " " not in text:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda k: max(width(" ".join(words[:k])), width(" ".join(words[k:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def render_png(text: str, width: int, height: int, out: Path, style: dict | None = None, kind: str = "narration") -> Path:
    """A full-frame transparent image with the caption (one or two lines) near the bottom or in the lower third.

    style: font_size_scale, color / lyric_color (hex), position ("bottom" | "lower_third"), box (a rounded dark
    panel behind the text), font ("rounded" Jua | "handwritten" Gamja Flower | "system")."""
    st = {**DEFAULT_STYLE, **{k: v for k, v in (style or {}).items() if v is not None}}
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Portrait frames are narrow: size by the width, a little larger, so kids can read it on a phone
    base = width * 0.062 if height > width else min(width, height) * 0.055
    size = max(16, round(base * float(st["font_size_scale"])))
    while True:  # shrink until it fits in two lines with a margin on both sides
        font = font_for(text, size, st["font"])
        stroke = 0 if st["box"] else max(2, size // 12)
        lines = _wrap(draw, text, font, stroke, width * 0.9)
        widths = [draw.textbbox((0, 0), l, font=font, stroke_width=stroke)[2] for l in lines]
        if (max(widths) <= width * 0.9 and len(lines) <= 2) or size <= 12:
            break
        size -= 1
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * 1.08)
    block_h = line_h * len(lines)
    bottom = height * (0.92 if st["position"] == "bottom" else 0.80)
    top = bottom - block_h
    if st["box"]:
        pad = round(size * 0.45)
        w = max(widths)
        draw.rounded_rectangle(((width - w) / 2 - pad, top - pad * 0.7, (width + w) / 2 + pad, bottom + pad * 0.5),
                               radius=round(size * 0.5), fill=(0, 0, 0, 150))
    color = _rgb(st["lyric_color"] if kind == "lyric" else st["color"])
    for k, (line, w) in enumerate(zip(lines, widths)):
        draw.text(((width - w) / 2, top + k * line_h), line, font=font, fill=color, stroke_width=stroke, stroke_fill="black")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
