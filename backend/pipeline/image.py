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
MODELS = ("turbo", "lightning", "realvis", "flux", "zimage")
# SDXL-Lightning (ByteDance, 4-step): the same SDXL graph as Turbo, so faces (PhotoMaker) and art characters
# (IP-Adapter) still work. Distilled at 1024 px (Turbo at 512 px): in a 12-scene test at 576×1024 it drew cleaner
# pictures with fewer stray extra figures at the same speed, but art characters stayed less on-model than with
# Turbo. It wants plain euler on an sgm_uniform schedule.
LIGHTNING = {"ckpt_name": "sdxl_lightning_4step.safetensors", "sampler": "euler", "scheduler": "sgm_uniform"}
# RealVisXL V5.0 (photoreal SDXL, OpenRAIL++): the "Quality" model for real-person characters. A full (not distilled)
# model: 25 steps with guidance and a negative prompt. Tested on a character: clearly straighter, more natural faces
# that look more like the photo than Turbo, ~106 s a picture at 768×768 instead of ~35 s. Works with PhotoMaker.
REALVIS = {"ckpt_name": "RealVisXL_V5.0_fp16.safetensors", "sampler": "dpmpp_2m", "scheduler": "karras", "steps": 25,
           "cfg": 5.0, "face_start": 0.2,
           "negative": "deformed, asymmetric face, crooked eyes, cross-eyed, distorted face, bad anatomy, blurry, lowres, "
                       "extra fingers, text, watermark, long torso, elongated body, stretched body, disproportionate body"}
# SDXL's training sizes (~1 megapixel). RealVisXL draws at the one closest to the asked shape, then the picture is
# scaled to the asked size: drawn directly at 512×896 (below its training size) bodies come out stretched.
SDXL_SIZES = [(1024, 1024), (896, 1152), (832, 1216), (768, 1344), (640, 1536), (1152, 896), (1216, 832), (1344, 768), (1536, 640)]


def native_size(w: int, h: int) -> tuple[int, int]:
    return min(SDXL_SIZES, key=lambda s: abs(s[0] / s[1] - w / h))
SDXL_MODELS = ("turbo", "lightning", "realvis")
IPADAPTER = json.loads((WORKFLOWS_DIR / "ipadapter_t2i.json").read_text())
ART_WEIGHT = 0.7  # IP-Adapter strength: higher copies the reference pose too, lower drifts off-model
ART_STEPS = 6  # like PhotoMaker, the extra conditioning needs a couple more turbo steps
ART_START = 0.15  # the first steps follow only the prompt (layout, pose, setting), so the reference picture's pose and plain background don't take over
NEGATIVE = "blurry, low quality, distorted, text, watermark"
# Texture (인물 질감), tested on real-person portraits:
#   smooth   — a short AI pass over each finished picture with smoothing words (redraws the last 30%): softer skin
#   (none)   — the picture as drawn
#   detailed — sharpened, with a fine luminance grain: skin, hair and fabric read as more textured. (An AI redraw
#              asking for "visible pores" didn't work: Turbo smooths skin whatever the words, even after enlarging.)
SMOOTH = ("smooth flawless skin, soft even shading, clean airbrushed finish", 0.30)
TEXTURES = ("smooth", "detailed")
PER_SCENE = ("positive", "sampler", "decode", "save")  # duplicated per scene; the other nodes are shared


CHARACTER_STEPS = 6  # PhotoMaker needs a couple more turbo steps to settle the face
# The first part of sampling follows only the prompt (composition, era, clothes, setting); the face comes after.
# Applied from the very start, PhotoMaker copies the photo's background and outfit and ignores "1920s portrait";
# from 40% the face came out crooked more often. 10% (tested on a character) gives straight faces closer to the photo.
FACE_START = 0.1


def _art_nodes(wf: dict, key: str, refs: list[str], weight: float, start: float = ART_START) -> None:
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
    apply["inputs"].update(image=prev, weight=weight, start_at=start)
    wf[f"apply_{key}"] = apply


def _batch_workflow(prompts: list[str], seeds: list[int], names: list[str], settings: dict,
                    face: str | None = None, with_face: list[bool] | None = None,
                    art: list[str | None] | None = None, art_refs: dict[str, list[str]] | None = None,
                    poses: list[str | None] | None = None) -> dict:
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
    elif model == "realvis":
        base["latent"]["inputs"].update(zip(("width", "height"), native_size(settings["width"], settings["height"])))
        base["ckpt"]["inputs"]["ckpt_name"] = REALVIS["ckpt_name"]
        base["negative"]["inputs"]["text"] = REALVIS["negative"]
        base["sampler_select"]["inputs"]["sampler_name"] = REALVIS["sampler"]
        base["sampler"]["inputs"]["cfg"] = REALVIS["cfg"]
        base["sigmas"] = {"class_type": "BasicScheduler", "inputs": {
            "model": ["ckpt", 0], "scheduler": REALVIS["scheduler"], "steps": REALVIS["steps"], "denoise": 1.0}}
    wf = {nid: node for nid, node in base.items() if nid not in PER_SCENE}
    with_face = with_face or [False] * len(prompts)
    FACE_NODE = "PhotoMakerEncodeMulti" if isinstance(face, list) and len(face) > 1 else "PhotoMakerEncode"
    if face and any(with_face):
        wf["photomaker"] = {"class_type": "PhotoMakerLoader", "inputs": {"photomaker_model_name": "photomaker-v1.bin"}}
        faces = face if isinstance(face, list) else [face]
        if len(faces) == 1:
            wf["face"] = {"class_type": "LoadImage", "inputs": {"image": faces[0]}}
        else:  # several photos of the person: one image batch for PhotoMakerEncodeMulti
            prev = None
            for k, name in enumerate(faces):
                wf[f"face_load_{k}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
                wf[f"face_sq_{k}"] = {"class_type": "ImageScale", "inputs": {
                    "image": [f"face_load_{k}", 0], "upscale_method": "lanczos", "width": 512, "height": 512, "crop": "center"}}
                if prev is None:
                    prev = [f"face_sq_{k}", 0]
                else:
                    wf[f"face_batch_{k}"] = {"class_type": "ImageBatch", "inputs": {"image1": prev, "image2": [f"face_sq_{k}", 0]}}
                    prev = [f"face_batch_{k}", 0]
            wf["face"] = {"class_type": "RepeatImageBatch", "inputs": {"image": prev, "amount": 1}}
        wf["sigmas_face"] = copy.deepcopy(base["sigmas"])
        if model != "realvis":  # the full model already takes 25 steps
            wf["sigmas_face"]["inputs"]["steps"] = CHARACTER_STEPS
    art = art or [None] * len(prompts)
    for key, refs in (art_refs or {}).items():
        if key in art:
            _art_nodes(wf, key, refs, float(settings.get("art_weight", ART_WEIGHT)), float(settings.get("art_start", ART_START)))
    if any(art):
        wf["sigmas_art"] = copy.deepcopy(base["sigmas"])
        if model != "realvis":
            wf["sigmas_art"]["inputs"]["steps"] = ART_STEPS

    smooth = settings.get("texture") == "smooth"
    if smooth:
        wf["sigmas_tex"] = copy.deepcopy(base["sigmas"])
        wf["sigmas_tex"]["inputs"].update(steps=8 if model == "realvis" else 4, denoise=SMOOTH[1])

    style = settings.get("style", "").strip()
    for i, (prompt, seed, name) in enumerate(zip(prompts, seeds, names)):
        branch = {f"{nid}_{i}": copy.deepcopy(base[nid]) for nid in PER_SCENE}
        for node in branch.values():  # point links at this scene's copies of the per-scene nodes
            for key, value in node["inputs"].items():
                if isinstance(value, list) and value[0] in PER_SCENE:
                    node["inputs"][key] = [f"{value[0]}_{i}", value[1]]
        text = f"{prompt}, {style}" if style else prompt
        if face and with_face[i]:
            start = float(settings.get("face_start", REALVIS["face_start"] if model == "realvis" else FACE_START))
            wf[f"face_cond_{i}"] = {"class_type": FACE_NODE, "inputs": {
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
            if art[i]:  # face plus a reference picture (a 3-view sheet's side view: the front view's outfit)
                branch[f"sampler_{i}"]["inputs"]["model"] = [f"apply_{art[i]}", 0]
        else:
            branch[f"positive_{i}"]["inputs"]["text"] = text
            if art[i]:  # this scene shows an art character: sample with its IP-Adapter model
                branch[f"sampler_{i}"]["inputs"].update(model=[f"apply_{art[i]}", 0], sigmas=["sigmas_art", 0])
        branch[f"sampler_{i}"]["inputs"]["noise_seed"] = seed
        branch[f"save_{i}"]["inputs"]["filename_prefix"] = f"happica/{name}"
        if poses and poses[i]:  # hold this picture to a stick figure (pose.py): normal proportions, a true view
            from . import pose

            if "pose_net" not in wf:
                wf["pose_net"] = {"class_type": "ControlNetLoader", "inputs": {"control_net_name": pose.MODEL}}
            wf[f"pose_img_{i}"] = {"class_type": "LoadImage", "inputs": {"image": poses[i]}}
            wf[f"pose_apply_{i}"] = {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": branch[f"sampler_{i}"]["inputs"]["positive"], "negative": branch[f"sampler_{i}"]["inputs"]["negative"],
                "control_net": ["pose_net", 0], "image": [f"pose_img_{i}", 0], "strength": pose.STRENGTH,
                "start_percent": 0.0, "end_percent": pose.END, "vae": ["ckpt", 2]}}
            branch[f"sampler_{i}"]["inputs"].update(positive=[f"pose_apply_{i}", 0], negative=[f"pose_apply_{i}", 1])
        if smooth:
            _smooth_pass(wf, branch, i, text, seed, face and with_face[i])
        if model == "realvis" and native_size(settings["width"], settings["height"]) != (settings["width"], settings["height"]):
            wf[f"scale_{i}"] = {"class_type": "ImageScale", "inputs": {
                "image": [f"decode_{i}", 0], "upscale_method": "lanczos", "width": settings["width"],
                "height": settings["height"], "crop": "center"}}
            branch[f"save_{i}"]["inputs"]["images"] = [f"scale_{i}", 0]
        wf.update(branch)
    return wf


def _smooth_pass(wf: dict, branch: dict, i: int, text: str, seed: int, face: bool) -> None:
    """Redraw the last part of scene i with smoothing words: a second sampler starts from the first one's result."""
    tex_text = f"{text}, {SMOOTH[0]}"
    if face:  # keep the face: the pass is conditioned on the photo too
        wf[f"tex_cond_{i}"] = {"class_type": "PhotoMakerEncodeMulti" if "face_load_0" in wf else "PhotoMakerEncode", "inputs": {
            "photomaker": ["photomaker", 0], "image": ["face", 0], "clip": ["ckpt", 1], "text": tex_text}}
    else:
        wf[f"tex_cond_{i}"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["ckpt", 1], "text": tex_text}}
    first = branch[f"sampler_{i}"]["inputs"]
    wf[f"tex_sampler_{i}"] = {"class_type": "SamplerCustom", "inputs": {
        "model": first["model"], "add_noise": True, "noise_seed": seed + 1, "cfg": first.get("cfg", 1.0),
        "positive": [f"tex_cond_{i}", 0], "negative": first["negative"], "sampler": first["sampler"],
        "sigmas": ["sigmas_tex", 0], "latent_image": [f"sampler_{i}", 0]}}
    branch[f"decode_{i}"]["inputs"]["samples"] = [f"tex_sampler_{i}", 0]


def add_texture(path: Path, seed: int = 0) -> None:
    """"detailed" texture: unsharp mask plus a fine luminance grain, in place."""
    import numpy as np
    from PIL import Image, ImageFilter

    with Image.open(path) as img:
        sharp = img.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.6, percent=70, threshold=2))
    a = np.asarray(sharp).astype(np.float32)
    a += np.random.default_rng(seed).normal(0, 5.0, a.shape[:2])[..., None]
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).save(path)


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
        words = SMOOTH[0] if settings.get("texture") == "smooth" else ""  # FLUX / Z-Image: smoothing words only
        branch[f"positive_{i}"]["inputs"]["text"] = ", ".join(x for x in (prompt, style, words) if x)
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
    poses: list[Path | None] | None = None,
) -> list[Path]:
    """Generate one image per prompt in a single ComfyUI run; progress runs 0 → 1 across all of them.

    With a `character` ({"photo": path under outputs/, ...}), prompts flagged in `with_face` (all by default)
    get that face via PhotoMaker; build those prompts with character.prompt_for()."""
    n = len(prompts)
    names = [f"{p.parent.parent.name}_{p.stem}" for p in out_paths]
    face = None
    if character:
        photos = character.get("photos") or [character["photo"]]
        face = [await comfy.upload_image(OUTPUTS_DIR / p, f"character_{Path(p).name}") for p in photos]
        face = face[0] if len(face) == 1 else face
        with_face = with_face if with_face is not None else [True] * n
    art_keys, art_refs = None, {}
    if art and any(art):
        art_keys = [a["id"] if a else None for a in art]
        for a in {a["id"]: a for a in art if a}.values():
            art_refs[a["id"]] = [await comfy.upload_image(_square(OUTPUTS_DIR / r), f"art_{a['id']}_{k}.png")
                                 for k, r in enumerate(a["refs"])]
    pose_names = None
    if poses and any(poses):
        pose_names = [await comfy.upload_image(p, f"pose_{p.parent.name}_{p.name}") if p else None for p in poses]
    wf = _batch_workflow(prompts, seeds, names, settings, face, with_face, art_keys, art_refs, pose_names)

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
        if settings.get("texture") == "detailed":
            add_texture(out_path, seeds[i])
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
