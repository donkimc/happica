"""Text → image via the SDXL-Turbo ComfyUI workflow.

All of a job's scenes go into one ComfyUI run: a shared checkpoint loader feeding one
branch per scene. ComfyUI runs with --cache-none (see scripts/start.sh), so separate runs
would reload SDXL for every image.
"""
import copy
import json
from pathlib import Path
from typing import Callable

from comfy_client import ComfyClient
from config import WORKFLOWS_DIR

WORKFLOW = json.loads((WORKFLOWS_DIR / "sdxl_turbo_t2i.json").read_text())
NEGATIVE = "blurry, low quality, distorted, text, watermark"
PER_SCENE = ("positive", "sampler", "decode", "save")  # duplicated per scene; the other nodes are shared


def _batch_workflow(prompts: list[str], seeds: list[int], names: list[str], settings: dict) -> dict:
    base = copy.deepcopy(WORKFLOW)
    base["negative"]["inputs"]["text"] = NEGATIVE
    base["latent"]["inputs"].update(width=settings["width"], height=settings["height"])
    base["sigmas"]["inputs"]["steps"] = settings.get("image_steps", 4)
    wf = {nid: node for nid, node in base.items() if nid not in PER_SCENE}

    style = settings.get("style", "").strip()
    for i, (prompt, seed, name) in enumerate(zip(prompts, seeds, names)):
        branch = {f"{nid}_{i}": copy.deepcopy(base[nid]) for nid in PER_SCENE}
        for node in branch.values():  # point links at this scene's copies of the per-scene nodes
            for key, value in node["inputs"].items():
                if isinstance(value, list) and value[0] in PER_SCENE:
                    node["inputs"][key] = [f"{value[0]}_{i}", value[1]]
        branch[f"positive_{i}"]["inputs"]["text"] = f"{prompt}, {style}" if style else prompt
        branch[f"sampler_{i}"]["inputs"]["noise_seed"] = seed
        branch[f"save_{i}"]["inputs"]["filename_prefix"] = f"happica/{name}"
        wf.update(branch)
    return wf


async def generate_batch(
    comfy: ComfyClient,
    prompts: list[str],
    settings: dict,
    out_paths: list[Path],
    seeds: list[int],
    on_progress: Callable[[float], None] = lambda _: None,
) -> list[Path]:
    """Generate one image per prompt in a single ComfyUI run; progress runs 0 → 1 across all of them."""
    n = len(prompts)
    names = [f"{p.parent.parent.name}_{p.stem}" for p in out_paths]
    wf = _batch_workflow(prompts, seeds, names, settings)

    done: dict[int, float] = {}

    def progress(value: int, total: int, node: str | None):
        if node and node.startswith("sampler_"):
            done[int(node.rsplit("_", 1)[1])] = value / total
            on_progress(sum(done.values()) / n)

    outputs = await comfy.run(wf, progress)
    for i, out_path in enumerate(out_paths):
        images = outputs.get(f"save_{i}", {}).get("images", [])
        if not images:
            raise RuntimeError(f"ComfyUI returned no image for scene {i + 1}")
        img = images[0]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(await comfy.download(img["filename"], img["subfolder"], img["type"]))
    return out_paths


async def generate(comfy: ComfyClient, prompt: str, settings: dict, out_path: Path, seed: int,
                   on_progress: Callable[[float], None] = lambda _: None) -> Path:
    return (await generate_batch(comfy, [prompt], settings, [out_path], [seed], on_progress))[0]
