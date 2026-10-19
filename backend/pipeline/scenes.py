"""Turn the user's text into scenes: an image prompt, a motion hint and a line of narration each.

lines: one scene per non-empty line; the line is both the image prompt and the narration.
llm:   a local LLM (Ollama) storyboards free-form text into scenes.
"""
import json

import httpx

from config import OLLAMA_MODEL, OLLAMA_URL

SYSTEM = """You are a storyboard writer for short narrated videos.
Split the user's story into at most {n} scenes, in story order. For each scene write:
- image_prompt: a concrete visual description for an image generator: subject, setting, lighting, composition, 15-40 words. Describe recurring characters the same way in every scene (appearance, clothing) because each image is generated separately. No text or captions in the image.
- motion: one short sentence describing what moves in the shot (subject motion and camera movement).
- narration: the voiceover for this scene, one or two sentences, at most 25 words, in the same language as the story.
Answer only with JSON."""

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
        return await storyboard(text, n)
    lines = [line.strip() for line in text.splitlines() if line.strip()] or [text.strip()]
    return [{"prompt": line, "motion": "", "narration": line} for line in lines[:n]]


async def storyboard(text: str, n: int) -> list[dict]:
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": SYSTEM.format(n=n)}, {"role": "user", "content": text}],
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
    return scenes
