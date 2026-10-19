"""Text → image via the SDXL-Turbo ComfyUI workflow."""
import copy
import json
from pathlib import Path
from typing import Callable

from comfy_client import ComfyClient
from config import WORKFLOWS_DIR

WORKFLOW = json.loads((WORKFLOWS_DIR / "sdxl_turbo_t2i.json").read_text())
NEGATIVE = "blurry, low quality, distorted, text, watermark"


async def generate(
    comfy: ComfyClient,
    prompt: str,
    settings: dict,
    out_path: Path,
    seed: int,
    on_progress: Callable[[float], None] = lambda _: None,
) -> Path:
    wf = copy.deepcopy(WORKFLOW)
    style = settings.get("style", "").strip()
    wf["positive"]["inputs"]["text"] = f"{prompt}, {style}" if style else prompt
    wf["negative"]["inputs"]["text"] = NEGATIVE
    wf["latent"]["inputs"].update(width=settings["width"], height=settings["height"])
    wf["sigmas"]["inputs"]["steps"] = settings.get("image_steps", 4)
    wf["sampler"]["inputs"]["noise_seed"] = seed
    wf["save"]["inputs"]["filename_prefix"] = f"happica/{out_path.stem}"

    outputs = await comfy.run(wf, lambda value, total: on_progress(value / total))
    images = outputs.get("save", {}).get("images", [])
    if not images:
        raise RuntimeError("ComfyUI finished but returned no image")

    img = images[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(await comfy.download(img["filename"], img["subfolder"], img["type"]))
    return out_path
