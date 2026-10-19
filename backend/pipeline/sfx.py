"""Text → sound effects and ambiences with Stable Audio 3 (small SFX) on the music ComfyUI instance.

Takes come from one ComfyUI run (a latent batch). Ambiences can be made into seamless loops so they
can sit under a video of any length.
"""
import asyncio
import copy
import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf

from comfy_client import ComfyClient
from config import OLLAMA_MODEL, OLLAMA_URL, OUTPUTS_DIR, WORKFLOWS_DIR

from . import ffmpeg, music

WORKFLOW = json.loads((WORKFLOWS_DIR / "sa3_sfx.json").read_text())
LOOP_CROSSFADE = 1.5  # seconds of tail blended into the head to hide the loop point
TARGET_LUFS = -18  # effects sit a little below music (-14) so they layer well

ENHANCE_SYSTEM = """You are a sound designer writing prompts for an AI sound-effects generator.
Rewrite the user's idea as ONE vivid sentence (20-40 words) describing only what is heard: the sound sources,
their texture and rhythm, distance, and the acoustic space (indoor, open field, cave...). No music, no speech
unless asked, no visuals, no emotions. Answer with the sentence only."""


async def enhance(prompt: str) -> str:
    """Let the local LLM turn a short idea ("night sound") into a detailed sound description."""
    import httpx

    body = {"model": OLLAMA_MODEL, "stream": False, "keep_alive": 0, "options": {"temperature": 0.6},
            "messages": [{"role": "system", "content": ENHANCE_SYSTEM}, {"role": "user", "content": prompt}]}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
    text = r.json()["message"]["content"].strip().strip('"')
    return text or prompt


def _workflow(prompt: str, seconds: float, n: int, seed: int, name: str) -> dict:
    wf = copy.deepcopy(WORKFLOW)
    wf["positive"]["inputs"]["text"] = prompt
    wf["latent"]["inputs"].update(seconds=seconds, batch_size=n)
    wf["sampler"]["inputs"]["seed"] = seed
    wf["save"]["inputs"]["filename_prefix"] = f"happica/{name}"
    return wf


def make_loop(path: Path, crossfade: float = LOOP_CROSSFADE) -> float:
    """Rewrite a clip so its end flows into its start: the last `crossfade` seconds are blended (equal power)
    with the first ones, and the head they replace is dropped. Returns the new duration."""
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    x = int(crossfade * sr)
    if len(audio) < 3 * x:
        return len(audio) / sr
    t = np.linspace(0, np.pi / 2, x, dtype=np.float32)[:, None]
    tail = audio[-x:] * np.cos(t) + audio[:x] * np.sin(t)  # fades out the end while fading in the start
    looped = np.concatenate([audio[x:-x], tail])
    sf.write(path, looped, sr)
    return len(looped) / sr


async def _master(raw: Path, loop: bool, out_flac: Path, out_mp3: Path) -> float:
    """Normalise loudness, then either make a seamless loop or add short fades; returns the duration."""
    work = raw.with_suffix(".norm.wav")
    await ffmpeg.run("-i", str(raw), "-af", f"loudnorm=I={TARGET_LUFS}:TP=-1:LRA=15", "-ar", "48000", str(work))
    if loop:
        duration = await asyncio.to_thread(make_loop, work)
        await ffmpeg.run("-i", str(work), str(out_flac), "-b:a", "192k", str(out_mp3))
    else:
        duration = (await asyncio.to_thread(sf.info, work)).duration
        fades = f"afade=t=in:d=0.05,afade=t=out:st={max(0.0, duration - 0.5):.3f}:d=0.5"
        await ffmpeg.run("-i", str(work), "-af", fades, str(out_flac), "-af", fades, "-b:a", "192k", str(out_mp3))
    work.unlink()
    raw.unlink()
    return duration


async def generate(comfy: ComfyClient, prompt: str, seconds: float, loop: bool, n: int, seed: int,
                   out_dir: Path, name: str, report=lambda stage, progress: None) -> list[dict]:
    """Make n takes into out_dir; paths in the result are relative to OUTPUTS_DIR."""
    # A loop loses LOOP_CROSSFADE seconds to the blend, so generate that much extra
    gen_seconds = seconds + (LOOP_CROSSFADE if loop else 0)
    report("loading models", 0.0)
    outputs = await comfy.run(_workflow(prompt, gen_seconds, n, seed, name),
                              lambda value, total, _node: report("generating", value / total * 0.9))
    files = outputs.get("save", {}).get("audio", [])
    if len(files) < n:
        raise RuntimeError("Stable Audio returned no audio")

    report("mastering", 0.95)
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = lambda p: str(p.relative_to(OUTPUTS_DIR))
    takes = []
    for i, f in enumerate(files[:n]):
        raw = out_dir / f"s{i + 1}.raw.flac"
        raw.write_bytes(await comfy.download(f["filename"], f["subfolder"], f["type"]))
        flac, mp3 = out_dir / f"s{i + 1}.flac", out_dir / f"s{i + 1}.mp3"
        duration = await _master(raw, loop, flac, mp3)
        takes.append({"mp3": rel(mp3), "flac": rel(flac), "duration": round(duration, 2), "seed": seed,
                      "peaks": await asyncio.to_thread(music._peaks, flac)})
    return takes


async def run(job: dict, job_dir: Path, report, comfy: ComfyClient) -> dict:
    """A Sound Effects job: 1–2 takes of one sound."""
    s = job["settings"]
    seed = s.get("seed")
    if seed is None:
        seed = random.randrange(2**31)
    prompt = s["prompt"]
    if s.get("enhance"):
        report("describing the sound", 0.0)
        prompt = await enhance(prompt)
    takes = await generate(comfy, prompt, float(s["duration"]), bool(s.get("loop")), s.get("variations", 2), seed,
                           job_dir, job["id"], report)
    return {"tracks": takes, "seed": seed, "prompt_used": prompt}
