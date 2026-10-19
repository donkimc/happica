"""Text → music with ACE-Step 1.5 (turbo) on the music ComfyUI instance.

Each variation is its own branch in one ComfyUI run: shared model loaders, but its own seed and
its own song plan from the 1.7B planner, so variations differ the way two takes would.
"""
import asyncio
import copy
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf

from comfy_client import ComfyClient
from config import WORKFLOWS_DIR

from . import ffmpeg

WORKFLOW = json.loads((WORKFLOWS_DIR / "ace_step15_t2m.json").read_text())
PER_TRACK = ("positive", "negative", "sampler", "decode", "save")  # duplicated per variation
PEAKS = 160  # bars in the waveform the UI draws
FADE_OUT = 1.5

# Share of a variation's time per node, measured on an M2: planning ~50%, sampling ~10%, and the
# remaining ~40% is decoding, which reports no progress (the bar waits there, then jumps)
WEIGHTS = {"positive": 0.5, "sampler": 0.1}
STAGES = {"positive": "writing the song", "sampler": "rendering audio"}


def _workflow(settings: dict, seeds: list[int], prefix: str) -> dict:
    base = copy.deepcopy(WORKFLOW)
    duration = float(settings["duration"])
    base["latent"]["inputs"]["seconds"] = duration
    lyrics = "[Instrumental]" if settings.get("instrumental") else settings.get("lyrics", "").strip()
    tags = settings.get("tags", "").strip()
    if settings.get("instrumental") and "instrumental" not in tags.lower():
        tags = f"{tags}, instrumental" if tags else "instrumental"
    base["positive"]["inputs"].update(
        tags=tags, lyrics=lyrics or "[Instrumental]", duration=duration, bpm=int(settings.get("bpm", 110)),
        keyscale=settings.get("keyscale", "C major"), timesignature=str(settings.get("timesignature", "4")),
        language=settings.get("language", "en"),
    )
    wf = {nid: node for nid, node in base.items() if nid not in PER_TRACK}
    for i, seed in enumerate(seeds):
        branch = {f"{nid}_{i}": copy.deepcopy(base[nid]) for nid in PER_TRACK}
        for node in branch.values():  # point links at this variation's copies
            for key, value in node["inputs"].items():
                if isinstance(value, list) and value[0] in PER_TRACK:
                    node["inputs"][key] = [f"{value[0]}_{i}", value[1]]
        branch[f"positive_{i}"]["inputs"]["seed"] = seed
        branch[f"sampler_{i}"]["inputs"]["seed"] = seed
        branch[f"save_{i}"]["inputs"]["filename_prefix"] = f"happica/{prefix}_v{i + 1}"
        wf.update(branch)
    return wf


def _peaks(path: Path) -> list[float]:
    audio, _ = sf.read(path, dtype="float32", always_2d=True)
    mono = np.abs(audio).max(axis=1)
    bars = np.array_split(mono, PEAKS)
    peaks = np.array([b.max() if len(b) else 0.0 for b in bars])
    return [round(float(p), 3) for p in peaks / (peaks.max() or 1.0)]


async def _master(raw: Path, out_flac: Path, out_mp3: Path) -> float:
    """Trim trailing silence, normalise to streaming loudness, fade out; returns the duration.

    The trim threshold is low (-60 dB) because quiet tracks (ambient piano) sit near -45 dB raw,
    and a higher threshold cuts their tails as if they were silence.
    """
    trimmed = raw.with_suffix(".trimmed.wav")
    await ffmpeg.run("-i", str(raw), "-af",
                     "areverse,silenceremove=start_periods=1:start_threshold=-60dB:start_silence=0.5,areverse,"
                     "loudnorm=I=-14:TP=-1:LRA=11", "-ar", "48000", str(trimmed))
    duration = (await asyncio.to_thread(sf.info, trimmed)).duration
    fade = f"afade=t=out:st={max(0.0, duration - FADE_OUT):.3f}:d={FADE_OUT}"
    await ffmpeg.run("-i", str(trimmed), "-af", fade, str(out_flac), "-af", fade, "-b:a", "192k", str(out_mp3))
    trimmed.unlink()
    raw.unlink()
    return duration


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    settings = job["settings"]
    n = settings.get("variations", 2)
    seed = settings.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    seeds = [seed + i for i in range(n)]

    progress: dict[int, float] = {}

    def on_progress(value: int, total: int, node: str | None):
        if not node or "_" not in node:
            return
        kind, i = node.rsplit("_", 1)
        if kind not in WEIGHTS:
            return
        base = sum(w for k, w in WEIGHTS.items() if list(WEIGHTS).index(k) < list(WEIGHTS).index(kind))
        progress[int(i)] = base + WEIGHTS[kind] * value / total
        report(STAGES[kind], sum(progress.values()) / n)

    report("loading models", 0.0)
    outputs = await comfy.run(_workflow(settings, seeds, job["id"]), on_progress)

    report("mastering", 0.95)
    tracks = []
    for i in range(n):
        files = outputs.get(f"save_{i}", {}).get("audio", [])
        if not files:
            raise RuntimeError(f"ACE-Step returned no audio for variation {i + 1}")
        f = files[0]
        raw = job_dir / f"v{i + 1}.raw.flac"
        raw.write_bytes(await comfy.download(f["filename"], f["subfolder"], f["type"]))
        flac, mp3 = job_dir / f"v{i + 1}.flac", job_dir / f"v{i + 1}.mp3"
        duration = await _master(raw, flac, mp3)
        tracks.append({
            "mp3": str(mp3.relative_to(job_dir.parent)), "flac": str(flac.relative_to(job_dir.parent)),
            "duration": round(duration, 2), "seed": seeds[i], "peaks": await asyncio.to_thread(_peaks, flac),
        })
    return {"tracks": tracks, "seed": seed}
