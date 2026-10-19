"""Split the user's text into shot prompts.

Phase 1: one shot per non-empty line. Phase 6 replaces this with a local LLM (Ollama).
"""


async def split(text: str, settings: dict) -> list[str]:
    shots = [line.strip() for line in text.splitlines() if line.strip()]
    return shots[: settings.get("max_scenes", 6)] or [text.strip()]
