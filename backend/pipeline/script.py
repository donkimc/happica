"""Script mode: scenes written outside Happica (picture, motion, narration and sound effects per scene).

No story AI runs; the narration is spoken exactly as written. Named characters get their look spelled out in
every picture prompt, so a cartoon character is described the same way in every scene.
"""
import re


def expand(prompt: str, characters: dict[str, str]) -> str:
    """"Ttori waves" → "Ttori (a tiny blue goblin…) waves" — the first mention of each character only, to keep
    prompts short; the look is what makes separately generated pictures show the same character."""
    for name, look in (characters or {}).items():
        name, look = name.strip(), look.strip()
        if name and look:
            prompt = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", lambda m: f"{m.group(0)} ({look})", prompt, count=1)
    return prompt


def shots(settings: dict) -> list[dict]:
    return [{"prompt": s["image_prompt"].strip(), "motion": s.get("motion", "").strip(),
             "narration": s.get("narration", "").strip(), "sfx": s.get("sfx") or []} for s in settings["script"]]


def modes(settings: dict, n: int) -> list[str]:
    """Each scene's motion: its own motion_mode (script mode) or the job's."""
    script = settings.get("script") or []
    default = settings.get("motion", "kenburns")
    return [(script[i].get("motion_mode") if i < len(script) else None) or default for i in range(n)]
