"""Understanding the user's own pictures with a local vision model (Ollama, Gemma 3 4B by default).

describe(): an English description of each picture — used as the scene prompt (AI motion reads it).
narrate():  a voiceover line per picture, as one connected story, in the narration voice's language.
"""
import asyncio
import base64
import io
import json
import re
from pathlib import Path

import httpx
from PIL import Image

from config import OLLAMA_URL, VISION_MODEL

DESCRIBE = ("Describe this picture in one or two sentences (max 45 words): the main subject, what is happening, "
            "the setting, the light and mood. Only describe what is visible. Answer in English with the description "
            "only — no introduction.")
# Small models still often open with "Here's a description…:" — that would waste the motion prompt's words
PREAMBLE = re.compile(r"^\s*(here(['’]s| is)|this is|sure|okay)[^:\n]{0,80}:\s*", re.I)

NARRATE = """You write the voiceover for a short video made from the user's own pictures, shown in this order.
Write exactly {n} narration lines, one per picture, in order, that together tell one connected story with a
beginning and an end — not a list of descriptions. Each line: one or two short spoken sentences, at most 25 words,
simple words, nothing in brackets, no emoji. Write every line in {language}.
{theme}Answer only with JSON."""


def _image_b64(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((768, 768))  # plenty for understanding the picture, and much faster
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


async def _chat(client: httpx.AsyncClient, body: dict) -> str:
    try:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json={"model": VISION_MODEL, "stream": False, **body})
        r.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(f"Ollama is not running at {OLLAMA_URL}. Start the Ollama app, or write the narration yourself.")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Vision model error: {e.response.text[:200]} (run: ollama pull {VISION_MODEL})")
    return r.json()["message"]["content"].strip()


async def describe(paths: list[Path], report=lambda p: None) -> list[str]:
    out = []
    async with httpx.AsyncClient(timeout=300) as client:
        for i, path in enumerate(paths):
            report(i / len(paths))
            image = await asyncio.to_thread(_image_b64, path)
            text = await _chat(client, {
                "keep_alive": "2m" if i < len(paths) - 1 else 0,  # stay loaded between pictures, unload after the last
                "options": {"temperature": 0.2},
                "messages": [{"role": "user", "content": DESCRIBE, "images": [image]}],
            })
            out.append(" ".join(PREAMBLE.sub("", text).replace('"', "").replace("“", "").replace("”", "").split()))
    return out


async def narrate(descriptions: list[str], language: str, theme: str = "") -> list[str]:
    n = len(descriptions)
    note = f"What the video is about, from the user: {theme.strip()}\n" if theme.strip() else ""
    schema = {"type": "object", "properties": {"narration": {"type": "array", "items": {"type": "string"},
                                                             "minItems": n, "maxItems": n}}, "required": ["narration"]}
    pictures = "\n".join(f"Picture {i + 1}: {d}" for i, d in enumerate(descriptions))
    async with httpx.AsyncClient(timeout=300) as client:
        for attempt in range(2):
            system = NARRATE.format(n=n, language=language, theme=note)
            if attempt:
                system += f"\nIMPORTANT: write only in {language}."
            text = await _chat(client, {"keep_alive": 0, "format": schema, "options": {"temperature": 0.6},
                                        "messages": [{"role": "system", "content": system},
                                                     {"role": "user", "content": pictures}]})
            lines = [" ".join(str(x).split()) for x in json.loads(text).get("narration", [])][:n]
            lines += [""] * (n - len(lines))
            if _in_language(lines, language):
                return lines
    raise RuntimeError(f"The AI didn't write the narration in {language}. Try again, or write it yourself.")


def _in_language(lines: list[str], language: str) -> bool:
    from .scenes import SCRIPTS  # letters each language must contain

    script = SCRIPTS.get(language)
    return not script or all(re.search(script, line) for line in lines if line)
