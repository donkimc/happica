"""Text → image via the SDXL-Turbo ComfyUI workflow, SDXL-Lightning (`image_model: "lightning"`), FLUX.1-schnell
(`"flux"`) or Z-Image Turbo (`"zimage"`, 6B, Q5 GGUF + Qwen3-4B Q6 GGUF text encoder, 8 steps).

FLUX follows long descriptions far better (a character's colours, horns, outfit) and draws cleaner faces and eyes,
at ~4–5 min per image (576×1024, ~60 s per step on the M2) instead of ~5 s. It runs as a Q5 GGUF (ComfyUI-GGUF custom nodes) so it fits in 16 GB, and
ignores negative prompts (schnell is distilled: cfg 1). Faces and art characters need SDXL, so they stay on Turbo.

Characters: a real person's face via PhotoMaker, or a cartoon character from 1–3 pictures of it ("art"
characters) via IP-Adapter Plus for SDXL (workflows/ipadapter_t2i.json), which carries its colours and shape.

All of a job's scenes go into one ComfyUI run: a shared checkpoint loader feeding one
branch per scene. ComfyUI runs with --cache-none (see scripts/start.sh), so separate runs
would reload SDXL for every image.
"""
import copy
import json
from pathlib import Path
from typing import Callable

from comfy_client import ComfyClient
from config import OUTPUTS_DIR, WORKFLOWS_DIR

WORKFLOW = json.loads((WORKFLOWS_DIR / "sdxl_turbo_t2i.json").read_text())
FLUX_WORKFLOW = json.loads((WORKFLOWS_DIR / "flux_schnell_t2i.json").read_text())
ZIMAGE_WORKFLOW = json.loads((WORKFLOWS_DIR / "zimage_turbo_t2i.json").read_text())
MODELS = ("turbo", "lightning", "flux", "zimage")
# SDXL-Lightning (ByteDance, 4-step): the same SDXL graph as Turbo, so faces (PhotoMaker) and art characters
# (IP-Adapter) still work. Distilled at 1024 px (Turbo at 512 px): in a 12-scene test at 576×1024 it drew cleaner
# pictures with fewer stray extra figures at the same speed, but art characters stayed less on-model than with
# Turbo. It wants plain euler on an sgm_uniform schedule.
LIGHTNING = {"ckpt_name": "sdxl_lightning_4step.safetensors", "sampler": "euler", "scheduler": "sgm_uniform"}
SDXL_MODELS = ("turbo", "lightning")
IPADAPTER = json.loads((WORKFLOWS_DIR / "ipadapter_t2i.json").read_text())
ART_WEIGHT = 0.7  # IP-Adapter strength: higher copies the reference pose too, lower drifts off-model
ART_STEPS = 6  # like PhotoMaker, the extra conditioning needs a couple more turbo steps
ART_START = 0.15  # the first steps follow only the prompt (layout, pose, setting), so the reference picture's pose and plain background don't take over
NEGATIVE = "blurry, low quality, distorted, text, watermark"
PER_SCENE = ("positive", "sampler", "decode", "save")  # duplicated per scene; the other nodes are shared


CHARACTER_STEPS = 6  # PhotoMaker needs a couple more turbo steps to settle the face
# The first 40% of sampling follows only the prompt (composition, era, clothes, setting); the face comes after.
# Applied from the start, PhotoMaker copies the photo's background and outfit too and ignores "1920s portrait".
FACE_START = 0.4


def _art_nodes(wf: dict, key: str, refs: list[str], weight: float) -> None:
    """Shared IP-Adapter loaders plus, for one art character, its reference pictures and its adapted model `apply_<key>`."""
    if "ipa_loader" not in wf:
        wf["ipa_loader"] = copy.deepcopy(IPADAPTER["ipa_loader"])
        wf["clip_vision"] = copy.deepcopy(IPADAPTER["clip_vision"])
    prev = None
    for k, name in enumerate(refs):
        wf[f"ref_{key}_{k}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        if prev is None:
            prev = [f"ref_{key}_{k}", 0]
        else:
            wf[f"refs_{key}_{k}"] = {"class_type": "ImageBatch", "inputs": {"image1": prev, "image2": [f"ref_{key}_{k}", 0]}}
            prev = [f"refs_{key}_{k}", 0]
    apply = copy.deepcopy(IPADAPTER["apply"])
    apply["inputs"].update(image=prev, weight=weight, start_at=ART_START)
    wf[f"apply_{key}"] = apply


def _batch_workflow(prompts: list[str], seeds: list[int], names: list[str], settings: dict,
                    face: str | None = None, with_face: list[bool] | None = None,
                    art: list[str | None] | None = None, art_refs: dict[str, list[str]] | None = None) -> dict:
    """One branch per prompt. Branches flagged in `with_face` are conditioned on the uploaded face with PhotoMaker
    (build their prompts with character.prompt_for(): it adds the "photomaker" trigger word ComfyUI needs)."""
    model = settings.get("image_model", "turbo")
    if model not in SDXL_MODELS:
        if face or any(art or []):
            raise RuntimeError(f"{model} can't use characters (PhotoMaker and IP-Adapter are SDXL-only); "
                               "pick Turbo or Lightning, or describe the character in the prompt instead")
        return _flow_workflow(FLUX_WORKFLOW if model == "flux" else ZIMAGE_WORKFLOW, prompts, seeds, names, settings,
                              steps=settings.get("image_steps", 4) if model == "flux" else 8)
    base = copy.deepcopy(WORKFLOW)
    base["negative"]["inputs"]["text"] = NEGATIVE
    base["latent"]["inputs"].update(width=settings["width"], height=settings["height"])
    base["sigmas"]["inputs"]["steps"] = settings.get("image_steps", 4)
    if model == "lightning":
        base["ckpt"]["inputs"]["ckpt_name"] = LIGHTNING["ckpt_name"]
        base["sampler_select"]["inputs"]["sampler_name"] = LIGHTNING["sampler"]
        base["sigmas"] = {"class_type": "BasicScheduler", "inputs": {
            "model": ["ckpt", 0], "scheduler": LIGHTNING["scheduler"], "steps": 4, "denoise": 1.0}}
    wf = {nid: node for nid, node in base.items() if nid not in PER_SCENE}
    with_face = with_face or [False] * len(prompts)
    if face and any(with_face):
        wf["photomaker"] = {"class_type": "PhotoMakerLoader", "inputs": {"photomaker_model_name": "photomaker-v1.bin"}}
        wf["face"] = {"class_type": "LoadImage", "inputs": {"image": face}}
        wf["sigmas_face"] = copy.deepcopy(base["sigmas"])
        wf["sigmas_face"]["inputs"]["steps"] = CHARACTER_STEPS
    art = art or [None] * len(prompts)
    for key, refs in (art_refs or {}).items():
        if key in art:
            _art_nodes(wf, key, refs, float(settings.get("art_weight", ART_WEIGHT)))
    if any(art):
        wf["sigmas_art"] = copy.deepcopy(base["sigmas"])
        wf["sigmas_art"]["inputs"]["steps"] = ART_STEPS

    style = settings.get("style", "").strip()
    for i, (prompt, seed, name) in enumerate(zip(prompts, seeds, names)):
        branch = {f"{nid}_{i}": copy.deepcopy(base[nid]) for nid in PER_SCENE}
        for node in branch.values():  # point links at this scene's copies of the per-scene nodes
            for key, value in node["inputs"].items():
                if isinstance(value, list) and value[0] in PER_SCENE:
                    node["inputs"][key] = [f"{value[0]}_{i}", value[1]]
        text = f"{prompt}, {style}" if style else prompt
        if face and with_face[i]:
            start = float(settings.get("face_start", FACE_START))
            wf[f"face_cond_{i}"] = {"class_type": "PhotoMakerEncode", "inputs": {
                "photomaker": ["photomaker", 0], "image": ["face", 0], "clip": ["ckpt", 1], "text": text}}
            wf[f"plain_cond_{i}"] = {"class_type": "CLIPTextEncode", "inputs": {
                "clip": ["ckpt", 1], "text": text.replace(" photomaker ", " ", 1)}}
            wf[f"plain_range_{i}"] = {"class_type": "ConditioningSetTimestepRange", "inputs": {
                "conditioning": [f"plain_cond_{i}", 0], "start": 0.0, "end": start}}
            wf[f"face_range_{i}"] = {"class_type": "ConditioningSetTimestepRange", "inputs": {
                "conditioning": [f"face_cond_{i}", 0], "start": start, "end": 1.0}}
            branch[f"positive_{i}"] = {"class_type": "ConditioningCombine", "inputs": {
                "conditioning_1": [f"plain_range_{i}", 0], "conditioning_2": [f"face_range_{i}", 0]}}
            branch[f"sampler_{i}"]["inputs"]["sigmas"] = ["sigmas_face", 0]
        else:
            branch[f"positive_{i}"]["inputs"]["text"] = text
            if art[i]:  # this scene shows an art character: sample with its IP-Adapter model
                branch[f"sampler_{i}"]["inputs"].update(model=[f"apply_{art[i]}", 0], sigmas=["sigmas_art", 0])
        branch[f"sampler_{i}"]["inputs"]["noise_seed"] = seed
        branch[f"save_{i}"]["inputs"]["filename_prefix"] = f"happica/{name}"
        wf.update(branch)
    return wf


def _flow_workflow(template: dict, prompts: list[str], seeds: list[int], names: list[str], settings: dict,
                   steps: int) -> dict:
    """FLUX.1-schnell or Z-Image Turbo: the same shape as the Turbo batch (shared loaders and latent, one branch
    per scene). Both are distilled (cfg 1), so negative prompts do nothing."""
    base = copy.deepcopy(template)
    base["latent"]["inputs"].update(width=settings["width"], height=settings["height"])
    wf = {nid: node for nid, node in base.items() if nid not in PER_SCENE}
    style = settings.get("style", "").strip()
    for i, (prompt, seed, name) in enumerate(zip(prompts, seeds, names)):
        branch = {f"{nid}_{i}": copy.deepcopy(base[nid]) for nid in PER_SCENE}
        for node in branch.values():
            for key, value in node["inputs"].items():
                if isinstance(value, list) and value[0] in PER_SCENE:
                    node["inputs"][key] = [f"{value[0]}_{i}", value[1]]
        branch[f"positive_{i}"]["inputs"]["text"] = f"{prompt}, {style}" if style else prompt
        branch[f"sampler_{i}"]["inputs"].update(seed=seed, steps=steps)
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
    character: dict | None = None,
    with_face: list[bool] | None = None,
    art: list[dict | None] | None = None,
) -> list[Path]:
    """Generate one image per prompt in a single ComfyUI run; progress runs 0 → 1 across all of them.

    With a `character` ({"photo": path under outputs/, ...}), prompts flagged in `with_face` (all by default)
    get that face via PhotoMaker; build those prompts with character.prompt_for()."""
    n = len(prompts)
    names = [f"{p.parent.parent.name}_{p.stem}" for p in out_paths]
    face = None
    if character:
        face = await comfy.upload_image(OUTPUTS_DIR / character["photo"], f"character_{Path(character['photo']).name}")
        with_face = with_face if with_face is not None else [True] * n
    art_keys, art_refs = None, {}
    if art and any(art):
        art_keys = [a["id"] if a else None for a in art]
        for a in {a["id"]: a for a in art if a}.values():
            art_refs[a["id"]] = [await comfy.upload_image(_square(OUTPUTS_DIR / r), f"art_{a['id']}_{k}.png")
                                 for k, r in enumerate(a["refs"])]
    wf = _batch_workflow(prompts, seeds, names, settings, face, with_face, art_keys, art_refs)

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
                   on_progress: Callable[[float], None] = lambda _: None, character: dict | None = None,
                   art: dict | None = None) -> Path:
    return (await generate_batch(comfy, [prompt], settings, [out_path], [seed], on_progress, character,
                                 art=[art] if art else None))[0]


def _square(path: Path) -> Path:
    """A reference picture padded (not cropped) to a 512 px square on white, so IP-Adapter's centre crop keeps
    the whole character and several references batch together."""
    from PIL import Image, ImageOps

    out = path.with_name(f"{path.stem}_sq512.png")
    if not out.is_file():
        with Image.open(path) as img:
            img = img.convert("RGBA")
            flat = Image.new("RGB", img.size, "white")
            flat.paste(img, mask=img.split()[3])
            ImageOps.pad(flat, (512, 512), color="white").save(out)
    return out
