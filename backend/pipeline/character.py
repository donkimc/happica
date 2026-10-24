"""Characters: a face photo plus a short description, reusable in images and videos via PhotoMaker — or, for
cartoon characters ("art": 1–3 pictures of them), via IP-Adapter, since PhotoMaker only works for real faces.

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
    """Rewrite a prompt so PhotoMaker can place the character: it starts with "<kind> photomaker", the name becomes
    "the <kind>", and the look is added.

    ComfyUI's PhotoMakerEncode only applies the face when the prompt has the word "photomaker" on its own
    (space-separated — "photomaker," doesn't count) right after the class word; without it the photo is silently
    ignored and the face comes from the text alone. It finds the class word by counting words but replaces a
    token, so any multi-token word before it ("close-up") would put the face on the wrong token — hence the
    trigger always comes first, after the one-token class word."""
    kind = ch.get("kind", "person")
    name = (ch.get("name") or "").strip()
    text = re.sub(rf"\b{re.escape(name)}\b", f"the {kind}", prompt, flags=re.I) if name else prompt
    return f"{kind} photomaker {text}, {ch.get('look', '')}".strip(", ")


def mentions(prompt: str, ch: dict) -> bool:
    name = (ch.get("name") or "").strip()
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", prompt, re.I) is not None


ART_SHEET = [
    "{name}, {look}, full body, standing, smiling, simple pastel background",
    "{name}, {look}, waving happily, outdoors in a flower meadow",
    "{name}, {look}, running, side view, forest path",
    "{name}, {look}, close-up portrait, big smile",
]


def find_art(prompt: str, art: list[dict]) -> dict | None:
    """The first art character a prompt names."""
    hits = [(m.start(), a) for a in art if (m := re.search(rf"(?<!\w){re.escape(a['name'])}(?!\w)", prompt, re.I))]
    return min(hits, key=lambda h: h[0])[1] if hits else None


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    """Creating a character makes a 4-picture character sheet from the uploaded photo (or reference art)."""
    ch = job["settings"]
    if ch.get("kind") == "art":
        prompts = [p.format(name=ch["name"], look=ch.get("look", "")).replace(", ,", ",") for p in ART_SHEET]
        seed = random.randrange(2**32)
        paths = [job_dir / f"sheet_{i + 1}.png" for i in range(len(prompts))]
        settings = {"width": 512, "height": 512, "texture": ch.get("texture"),
                    "style": ch.get("style") or "cute 2D children's picture book illustration, clean outlines"}
        await image.generate_batch(comfy, prompts, settings, paths, [seed + i for i in range(len(prompts))],
                                   on_progress=lambda p: report("drawing the character sheet", p),
                                   art=[{"id": job["id"], **ch}] * len(prompts))
        return {"images": [str(p.relative_to(OUTPUTS_DIR)) for p in paths], "seed": seed}
    who = f"a {ch.get('kind', 'person')} photomaker "  # the space keeps "photomaker" a word of its own
    prompts = [p.format(who=who, look=ch.get("look", "")) for p in SHEET]
    seed = random.randrange(2**32)
    paths = [job_dir / f"sheet_{i + 1}.png" for i in range(len(prompts))]
    # 768 px: at 512 the eyes are only ~25 px across and SDXL-Turbo often draws them mismatched (two colours/shapes)
    settings = {"width": 768, "height": 768, "texture": ch.get("texture"), "image_model": ch.get("image_model") or "turbo",
                "style": "photorealistic, 35mm photo, detailed skin"}
    await image.generate_batch(comfy, prompts, settings, paths, [seed + i for i in range(len(prompts))],
                               on_progress=lambda p: report("drawing the character sheet", p), character=ch)
    return {"images": [str(p.relative_to(OUTPUTS_DIR)) for p in paths], "seed": seed}


# ---------- 3면도: a front / side / back turnaround sheet ----------
# One wide picture with "front, side and back view" doesn't work: SDXL-Turbo fills a wide canvas with 8–14 figures.
# So the front view is drawn first (with the face photo or the cartoon references), then the side and back views are
# drawn with IP-Adapter on that front view, which carries the face, outfit and colours over. The three are then put
# side by side.

VIEW_SIZE = (576, 1024)
VIEWS = {"front": "front view, facing the camera", "side": "side view, profile, facing to the side",
         "back": "back view, seen from behind, facing away from the camera"}
# no "character sheet" words: they make the model draw a whole design page (many figures, fake text) in each view
POSE = ("full body, standing alone, standing straight, arms relaxed, realistic body proportions, plain white background, "
        "nothing else in the picture")
# Turbo was trained on 512×512 squares: asked for a tall view it stretches the body (a long torso); drawn square it
# crops the figure at the hips, or seats it when told to zoom out (tested). RealVisXL draws at SDXL's native tall
# size (image.native_size) and gets normal proportions and true side and back views. A pose guide (pose.py) does the
# same for Turbo at ~8 min a sheet instead of ~16.
# The front view steers the side and back views only from 35% of the drawing on (the prompt sets the pose first —
# from the start it copies the front-facing pose). They don't use the face photo: on the side view it overrode the
# front view's outfit (another top, shorts), so the side view's face comes from the front view too.
VIEW_WEIGHT, VIEW_START = 0.8, 0.25


def _view_prompt(ch: dict, view: str, outfit: str = "") -> str:
    rest = f"{VIEWS[view]}, {POSE}" + (f", wearing {outfit}" if outfit else "")
    if ch.get("kind") == "art":
        return f"{ch['name']} ({ch.get('look', '')}), {rest}"
    return prompt_for(rest, {**ch, "name": ""})


async def turnaround(comfy: ComfyClient, ch: dict, settings: dict, out: Path, seed: int, report) -> list[Path]:
    """Draw one 3-view sheet; returns [sheet, front, side, back]."""
    from PIL import Image

    s = {**settings, "width": VIEW_SIZE[0], "height": VIEW_SIZE[1]}
    if ch.get("kind") != "art":
        s["style"] = settings.get("style") or "photorealistic, studio photo"
    else:
        s["style"] = settings.get("style") or ch.get("style") or "cute 2D children's picture book illustration, clean outlines"
    paths = {v: out.with_name(f"{out.stem}_{v}.png") for v in VIEWS}
    art = ch.get("kind") == "art"
    report("drawing the front view", 0.0)
    from . import pose

    # Real people: stick figures with human proportions hold every view to a normal body and a true side/back view.
    # Not for cartoons: a human skeleton forced human proportions onto a chibi character and its outfit drifted.
    guides = {v: None if art else pose.draw(v, VIEW_SIZE, out.with_name(f"{out.stem}_{v}_pose.png")) for v in VIEWS}
    await image.generate_batch(comfy, [_view_prompt(ch, "front")], s, [paths["front"]], [seed], poses=[guides["front"]],
                               on_progress=lambda p: report("drawing the front view", p * 0.4),
                               **({"art": [ch]} if art else {"character": ch}))
    front_ref = {"id": f"{out.parent.name}_{out.stem}_front", "name": ch["name"],
                 "refs": [str(paths["front"].relative_to(OUTPUTS_DIR))]}
    # The front view's clothes, read by the vision model, are written into the side and back prompts: the
    # front-view reference alone didn't hold them (the side view came out in another top and shorts)
    report("reading the outfit", 0.4)
    try:
        from . import vision

        outfit = await vision.outfit(paths["front"])
    except Exception:  # the vision model is optional: without it the reference picture alone carries the outfit
        outfit = ""
    side_back = ["side", "back"]
    await image.generate_batch(comfy, [_view_prompt(ch, v, outfit) for v in side_back], {**s, "art_weight": VIEW_WEIGHT, "art_start": VIEW_START},
                               [paths[v] for v in side_back], [seed + 1, seed + 2], poses=[guides[v] for v in side_back],
                               on_progress=lambda p: report("drawing the side and back views", 0.4 + p * 0.6),
                               art=[front_ref, front_ref],
                               **({} if art else {"character": ch, "with_face": [False, False]}))
    views = [Image.open(paths[v]).convert("RGB") for v in VIEWS]
    sheet = Image.new("RGB", (sum(v.width for v in views), views[0].height), "white")
    x = 0
    for v in views:
        sheet.paste(v, (x, 0))
        x += v.width
    sheet.save(out)
    return [out, *paths.values()], outfit

