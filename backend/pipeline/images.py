"""Image Studio: text → 1–4 images in one SDXL-Turbo run (shared loader, a seed per image)."""
import random
from pathlib import Path

from comfy_client import ComfyClient
from config import OUTPUTS_DIR

from . import character, image


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    s = job["settings"]
    seed = s.get("seed")
    if seed is None:
        seed = random.randrange(2**32)
    n = s.get("count", 4)
    seeds = [seed + i for i in range(n)]
    paths = [job_dir / f"image_{i + 1}.png" for i in range(n)]
    ch = character.load(s.get("character", ""))
    if ch and ch.get("kind") == "art":  # a cartoon character: IP-Adapter from its pictures, plus its look
        from . import script

        prompt = script.expand(s["prompt"], {ch["name"]: ch.get("look", "")})
        if not character.find_art(prompt, [ch]):
            prompt = f"{ch['name']} ({ch.get('look', '')}), {prompt}"
        await image.generate_batch(comfy, [prompt] * n, s, paths, seeds, on_progress=lambda p: report("drawing", p),
                                   art=[ch] * n)
    else:
        prompt = character.prompt_for(s["prompt"], ch) if ch else s["prompt"]
        await image.generate_batch(comfy, [prompt] * n, s, paths, seeds, on_progress=lambda p: report("drawing", p), character=ch)
    return {"images": [str(p.relative_to(OUTPUTS_DIR)) for p in paths], "seeds": seeds, "seed": seed}
