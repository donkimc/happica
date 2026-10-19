"""Describe a song in plain words; a local LLM (Ollama) drafts the title, style tags, lyrics and musical settings.

The draft goes back to the form for editing before anything is generated, like Suno's simple mode.
Keys, time signatures and languages are constrained to the values ACE-Step accepts.
"""
import json
import os
import re

import httpx

from config import OLLAMA_MODEL, OLLAMA_URL

# Lyrics need a bigger model than storyboards: in tests qwen2.5:3b wrote English lyrics for a Spanish
# request and long, unsingable lines; 7b followed the language and structure. Falls back to OLLAMA_MODEL.
SONGWRITER_MODEL = os.getenv("SONGWRITER_MODEL", "qwen2.5:7b")

SYSTEM = """You are a songwriter and music producer. Turn the user's idea into a song draft for an AI music generator.
Write:
- title: a short, memorable song title.
- tags: comma-separated tags describing only how the music SOUNDS: genre, sub-genre, mood, 3-5 instruments, production feel{vocal_rule}. 6-12 tags, lowercase. Not the song's topic, no artist names, no numbers, keys or tempos (those go in their own fields).
- lyrics: {lyrics_rule}
- bpm: a tempo that suits the genre and mood.
- keyscale, timesignature: suit the mood (minor keys for sad or dark songs).
- language: the language the lyrics are written in (ISO code).
Answer only with JSON."""

VOCAL_RULE = ", and the vocal type (e.g. female vocals, male vocals, duet)"
LYRICS_RULE = """the full lyrics in the language the user wrote in, unless they ask for another.
Mark every section on its own line in square brackets, e.g. [verse], [chorus], [bridge].
Structure for a {seconds}-second song: {structure}.
Short singable lines (4-9 words), simple rhymes, a chorus with a repeated hook line. No stage directions or notes."""
INSTRUMENTAL_RULE = 'exactly "[Instrumental]" (this is an instrumental track).'


STRUCTURES = [  # (up to this many seconds, sections that fit)
    (40, ["verse", "chorus"]),
    (75, ["verse", "chorus", "verse", "chorus"]),
    (150, ["intro", "verse", "chorus", "verse", "chorus", "bridge", "chorus", "outro"]),
    (999, ["intro", "verse", "pre-chorus", "chorus", "verse", "pre-chorus", "chorus", "bridge", "chorus", "chorus", "outro"]),
]


def sections_for(seconds: float) -> list[str]:
    return next(sections for limit, sections in STRUCTURES if seconds <= limit)


def structure_for(seconds: float) -> str:
    return ", ".join(f"[{s}]" for s in sections_for(seconds)) + " — 4 lines per verse and chorus, 2 for a bridge"


def _schema(options: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "tags": {"type": "string"},
            "lyrics": {"type": "string"},
            "bpm": {"type": "integer", "minimum": 50, "maximum": 200},
            "keyscale": {"type": "string", "enum": options["keys"]},
            "timesignature": {"type": "string", "enum": options["timesignatures"]},
            "language": {"type": "string", "enum": options["languages"]},
        },
        "required": ["title", "tags", "lyrics", "bpm", "keyscale", "timesignature", "language"],
    }


async def draft(description: str, instrumental: bool, seconds: float, options: dict) -> dict:
    system = SYSTEM.format(
        vocal_rule="" if instrumental else VOCAL_RULE,
        lyrics_rule=INSTRUMENTAL_RULE if instrumental else LYRICS_RULE.format(seconds=int(seconds), structure=structure_for(seconds)),
    )
    body = {
        "model": SONGWRITER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": description}],
        "format": _schema(options),
        "stream": False,
        "keep_alive": 0,  # unload right away: the music model needs the memory next
        "options": {"temperature": 0.8},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
            if r.status_code == 404 and SONGWRITER_MODEL != OLLAMA_MODEL:  # songwriter model not pulled
                r = await client.post(f"{OLLAMA_URL}/api/chat", json={**body, "model": OLLAMA_MODEL})
            r.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(f"Ollama is not running at {OLLAMA_URL}. Start the Ollama app, or write the song in Custom mode.")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Ollama error: {e.response.text[:300]} (is '{SONGWRITER_MODEL}' pulled?)")

    d = json.loads(r.json()["message"]["content"])
    tags = clean_tags(d["tags"], description)
    if instrumental and "instrumental" not in tags:
        tags += ", instrumental"
    elif not instrumental:  # an "instrumental" tag would suppress the vocals
        tags = ", ".join(t for t in tags.split(", ") if t != "instrumental")
    return {
        "title": d["title"].strip().strip('"'),
        "tags": tags,
        "lyrics": "" if instrumental else fit_sections(_tidy(d["lyrics"]), len(sections_for(seconds))),
        "bpm": max(50, min(200, int(d["bpm"]))),
        "keyscale": d["keyscale"],
        "timesignature": d["timesignature"],
        "language": d["language"],
        "instrumental": instrumental,
        "duration": seconds,
    }


def _tidy(lyrics: str) -> str:
    """One lyric line per line, a blank line only before each section tag (models often double-space every line)."""
    out: list[str] = []
    for line in (l.strip() for l in lyrics.strip().splitlines()):
        if line.startswith("[") and line.endswith("]"):
            if out:
                out.append("")
            out.append(line.lower())
        elif line:
            out.append(line.rstrip(","))
    return "\n".join(out)


# Tags that describe settings rather than sound: tempos, keys, time signatures, bare numbers
_TECHNICAL = re.compile(r"\d|\bbpm\b|\b(major|minor)\b|^[a-g](#|b)?$")


def clean_tags(raw: str, description: str) -> str:
    """Lowercase, de-duplicate, drop technical tags and words lifted straight from the song's topic."""
    topic = {w for w in re.findall(r"[a-z]+", description.lower()) if len(w) > 3}
    sound_words = {"song", "music", "vocals", "voice", "beat", "drums", "guitar", "piano", "choir", "strings", "synth", "bass",
                   "epic", "dark", "happy", "sad", "calm", "cozy", "dance", "upbeat"}
    seen, out = set(), []
    for tag in raw.lower().replace("_", " ").split(","):
        tag = " ".join(tag.split())
        words = set(tag.split())
        if not tag or tag in seen or _TECHNICAL.search(tag):
            continue
        if words and words <= topic and not words & sound_words:  # e.g. "lighthouse keeper" from the description
            continue
        seen.add(tag)
        out.append(tag)
    return ", ".join(out[:12])


def fit_sections(lyrics: str, max_sections: int) -> str:
    """Keep only as many sections as fit the song length; extra sections would be rushed or cut off."""
    blocks = re.split(r"\n(?=\[)", lyrics)
    return "\n".join(blocks[:max_sections]).strip()
