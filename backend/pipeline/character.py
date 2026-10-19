"""Characters: a face photo plus a short description, reusable in images and videos via PhotoMaker.

PhotoMaker carries the face; the stored appearance description ("short dark curly hair, freckles") carries
what it misses (hair, skin details), which in tests made young/old/costume versions clearly the same person.
"""
import random
import re
from pathlib import Path

from comfy_client import ComfyClient
from config import OUTPUTS_DIR

from . import image

SHEET = [
    "portrait photo of {who}, {look}, neutral expression, looking at the camera, soft studio light",
    "photo of {who}, {look}, laughing, outdoors in a park, golden hour",
    "full body photo of {who}, {look}, standing, casual clothes, city street",
    "side profile portrait of {who}, {look}, window light",
]


def load(character_id: str) -> dict | None:
    """A finished character's settings (name, photo, kind, look), or None."""
    from jobs import JobStore

    job = JobStore().get(character_id) if character_id else None
    if not job or job["kind"] != "character":
        return None
    return {"id": job["id"], **job["settings"]}


def prompt_for(prompt: str, ch: dict) -> str:
    """Rewrite a prompt so PhotoMaker can place the character: the name becomes "<kind> img" (its trigger word)
    and the look is added."""
    who = f"{ch.get('kind', 'person')} img"
    name = (ch.get("name") or "").strip()
    if name and re.search(rf"\b{re.escape(name)}\b", prompt, re.I):
        text = re.sub(rf"\b{re.escape(name)}\b", who, prompt, count=1, flags=re.I)
    else:
        text = f"{who}, {prompt}"
    return f"{text}, {ch.get('look', '')}".strip(", ")


def mentions(prompt: str, ch: dict) -> bool:
    name = (ch.get("name") or "").strip()
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", prompt, re.I) is not None


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    """Creating a character makes a 4-picture character sheet from the uploaded photo."""
    ch = job["settings"]
    who = f"a {ch.get('kind', 'person')} img"
    prompts = [p.format(who=who, look=ch.get("look", "")) for p in SHEET]
    seed = random.randrange(2**32)
    paths = [job_dir / f"sheet_{i + 1}.png" for i in range(len(prompts))]
    settings = {"width": 512, "height": 512, "style": "photorealistic, 35mm photo, detailed skin"}
    await image.generate_batch(comfy, prompts, settings, paths, [seed + i for i in range(len(prompts))],
                               on_progress=lambda p: report("drawing the character sheet", p), character=ch)
    return {"images": [str(p.relative_to(OUTPUTS_DIR)) for p in paths], "seed": seed}
