"""Turn the user's text into scenes: an image prompt, a motion hint and a line of narration each.

lines: one scene per non-empty line; the line is both the image prompt and the narration.
llm:   a local LLM (Ollama) storyboards free-form text into scenes.
"""
import json
import re

import httpx

from config import OLLAMA_MODEL, OLLAMA_URL

from . import songwriter, tts

SYSTEM = """You are a storyboard writer for short narrated videos.
Split the user's story into at most {n} scenes, in story order. For each scene write:
- image_prompt: in ENGLISH, a concrete visual description for an image generator: subject, setting, lighting, composition, 15-40 words. Describe recurring characters the same way in every scene (appearance, clothing) because each image is generated separately. No text or captions in the image.
- motion: in English, one short sentence describing what moves in the shot (subject motion and camera movement).
- narration: the voiceover for this scene, one or two sentences, at most 25 words, {narration_language}.
Answer only with JSON."""

# Letters a narration in these languages must contain — small models sometimes answer in another language
# (qwen drifts into Chinese with Korean or Japanese input)
SCRIPTS = {
    "Korean": r"[가-힯]", "Japanese": r"[぀-ヿ]", "Mandarin": r"[一-鿿]",
    "Russian": r"[Ѐ-ӿ]", "Ukrainian": r"[Ѐ-ӿ]", "Bulgarian": r"[Ѐ-ӿ]",
    "Greek": r"[Ͱ-Ͽ]", "Arabic": r"[؀-ۿ]", "Hindi": r"[ऀ-ॿ]",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image_prompt": {"type": "string"},
                    "motion": {"type": "string"},
                    "narration": {"type": "string"},
                },
                "required": ["image_prompt", "motion", "narration"],
            },
        }
    },
    "required": ["scenes"],
}


async def split(text: str, settings: dict) -> list[dict]:
    n = settings.get("max_scenes", 6)
    if settings.get("scene_mode") == "llm":
        language = tts.language_of(settings.get("voice", "af_heart")) if settings.get("narrate") else None
        if not language or "English" in language:
            return await storyboard(text, n, language=language)
        # Other languages: the bigger model follows the language rule far better; check the result and retry once
        shots = await _bigger_model(text, n, language)
        if not _in_language(shots, language):
            shots = await _bigger_model(text + f"\n\nIMPORTANT: every narration must be written only in {language}.", n, language)
        if not _in_language(shots, language):
            raise RuntimeError(f"The story AI didn't write the narration in {language}. Try again, or write the "
                               f"story itself in {language}.")
        return shots
    lines = [line.strip() for line in text.splitlines() if line.strip()] or [text.strip()]
    return [{"prompt": line, "motion": "", "narration": line} for line in lines[:n]]


async def _bigger_model(text: str, n: int, language: str) -> list[dict]:
    try:
        return await storyboard(text, n, songwriter.SONGWRITER_MODEL, language)
    except RuntimeError:  # the bigger model isn't installed
        return await storyboard(text, n, language=language)


def _in_language(shots: list[dict], language: str) -> bool:
    script = SCRIPTS.get(language)
    return not script or all(re.search(script, s["narration"]) for s in shots if s["narration"].strip())


async def storyboard(text: str, n: int, model: str = OLLAMA_MODEL, language: str | None = None) -> list[dict]:
    rule = f"written in {language}" if language else "in the same language as the story"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM.format(n=n, narration_language=rule)},
                     {"role": "user", "content": text}],
        "format": SCHEMA,
        "stream": False,
        "keep_alive": 0,  # unload right away: ComfyUI needs the memory next
        "options": {"temperature": 0.4},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(f"Ollama is not running at {OLLAMA_URL}. Start the Ollama app, or use one line per scene.")
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Ollama error: {e.response.text[:300]} (is '{OLLAMA_MODEL}' pulled?)")

    scenes = json.loads(r.json()["message"]["content"]).get("scenes", [])
    scenes = [
        {"prompt": s["image_prompt"].strip(), "motion": s["motion"].strip(), "narration": s["narration"].strip()}
        for s in scenes
        if s.get("image_prompt", "").strip()
    ][:n]
    if not scenes:
        raise RuntimeError("The LLM returned no scenes; try rephrasing the story.")
    return await _english_prompts(scenes, model)


NON_LATIN = re.compile(r"[^\u0000-ɏ -⁯]")


async def _english_prompts(scenes: list[dict], model: str) -> list[dict]:
    """The image and video models only understand English; small LLMs sometimes write the picture prompts in the
    story's language anyway, so translate any that aren't English."""
    todo = [i for i, s in enumerate(scenes) if NON_LATIN.search(s["prompt"] + s["motion"])]
    if not todo:
        return scenes
    items = [{"image": scenes[i]["prompt"], "motion": scenes[i]["motion"]} for i in todo]
    body = {
        "model": model, "stream": False, "keep_alive": 0, "options": {"temperature": 0.2},
        "messages": [{"role": "system", "content": "Translate each item's image and motion descriptions into natural "
                      "English, keeping every visual detail. Answer only with JSON in the same order."},
                     {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)}],
        "format": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
            "image": {"type": "string"}, "motion": {"type": "string"}}, "required": ["image", "motion"]}}}, "required": ["items"]},
    }
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
    translated = json.loads(r.json()["message"]["content"]).get("items", [])
    for i, t in zip(todo, translated):
        if t.get("image", "").strip() and not NON_LATIN.search(t["image"]):
            scenes[i] = {**scenes[i], "prompt": t["image"].strip(), "motion": t.get("motion", "").strip()}
    return scenes


SFX_SYSTEM = """You are a sound designer for a short narrated video. For each scene, write ONE short English
description (4-10 words) of a sound effect heard in it: a concrete sound source and action, e.g. "door creaks open
slowly", "magical sparkle chime". Only sounds, no music, no speech. Use "" for a scene with nothing to hear.
Answer only with JSON, one item per scene, in order."""


async def suggest_sfx(shots: list[dict], model: str = OLLAMA_MODEL) -> list[str]:
    """A sound effect per scene (sfx_auto), from its picture and narration."""
    items = [{"scene": i + 1, "picture": s["prompt"], "narration": s["narration"]} for i, s in enumerate(shots)]
    body = {
        "model": model, "stream": False, "keep_alive": 0, "options": {"temperature": 0.5},
        "messages": [{"role": "system", "content": SFX_SYSTEM},
                     {"role": "user", "content": json.dumps({"scenes": items}, ensure_ascii=False)}],
        "format": {"type": "object", "properties": {"sounds": {"type": "array", "items": {"type": "string"}}},
                   "required": ["sounds"]},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
            r.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(f"Ollama is not running at {OLLAMA_URL}; it suggests the sound effects (sfx_auto).")
    sounds = [str(x).strip().strip('"')[:300] for x in json.loads(r.json()["message"]["content"]).get("sounds", [])]
    sounds = [x if len(x) >= 2 and not NON_LATIN.search(x) else "" for x in sounds]
    return (sounds + [""] * len(shots))[: len(shots)]
