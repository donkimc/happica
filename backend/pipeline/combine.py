"""Combine finished jobs into one long video or audio file — e.g. a 1-hour ambience, music mix or video loop.
API only (POST /api/v1/combine).

Items play in order (or shuffled each round) and repeat until the target length. Video clips are first made
uniform (size, 24 fps, an audio track) so they can be joined without re-encoding — a 1-hour video is mostly a
file copy. Audio is joined and encoded once to MP3.
"""
import asyncio
import json
import random
from pathlib import Path

from config import OUTPUTS_DIR

from . import ffmpeg

FPS = 24
MAX_SECONDS = 4 * 3600
ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]


def source_of(job: dict, take: int) -> tuple[str, Path]:
    """("video" | "audio", file) for a finished job: its final video, or the take's lossless audio."""
    r = job.get("result") or {}
    if job["state"] != "done":
        raise ValueError(f"job {job['id']} isn't finished (state: {job['state']})")
    if r.get("video"):  # a video job, or an upscaled or combined video
        return "video", OUTPUTS_DIR / r["video"]
    tracks = r.get("tracks") or []
    if tracks:
        if not 1 <= take <= len(tracks):
            raise ValueError(f"job {job['id']} has {len(tracks)} take(s); take {take} doesn't exist")
        t = tracks[take - 1]
        return "audio", OUTPUTS_DIR / (t.get("flac") or t["mp3"])
    raise ValueError(f"job {job['id']} ({job['kind']}) has no video or audio to combine")


async def _probe(path: Path) -> dict:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    info = json.loads(out or b"{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"duration": float(info.get("format", {}).get("duration", 0) or 0),
            "audio": any(s.get("codec_type") == "audio" for s in streams),
            "width": video.get("width"), "height": video.get("height")}


async def _normalize_video(src: Path, out: Path, w: int, h: int) -> None:
    info = await _probe(src)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={FPS},format=yuv420p"
    if info["audio"]:
        await ffmpeg.run("-i", str(src), "-vf", vf, *ENCODE, str(out))
    else:  # every part needs an audio track, or the joined file loses sound after the first silent clip
        await ffmpeg.run("-i", str(src), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-vf", vf,
                         "-map", "0:v", "-map", "1:a", "-shortest", *ENCODE, str(out))


def playlist(n: int, durations: list[float], target: float | None, order: str, seed: int) -> list[int]:
    """Item indexes in play order: one pass, or rounds until the target length (shuffled per round if asked)."""
    rng = random.Random(seed)
    out, total = [], 0.0
    while True:
        round_ = list(range(n))
        if order == "shuffle":
            rng.shuffle(round_)
            if out and n > 1 and round_[0] == out[-1]:  # avoid the same item twice in a row across rounds
                round_[0], round_[-1] = round_[-1], round_[0]
        for i in round_:
            out.append(i)
            total += durations[i]
            if target and total >= target:
                return out
        if not target:
            return out


async def run(job: dict, job_dir: Path, report, sources: list[tuple[str, Path]]) -> dict:
    s = job["settings"]
    kind = sources[0][0]
    target = s.get("target_seconds")
    parts_dir = job_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    # 1) one uniform part per distinct source
    unique: dict[Path, Path] = {}
    if kind == "video":
        first = await _probe(sources[0][1])
        w, h = s.get("width") or first["width"] or 768, s.get("height") or first["height"] or 512
        distinct = list(dict.fromkeys(p for _, p in sources))  # each clip is prepared once, however often it repeats
        for i, src in enumerate(distinct):
            report("preparing clips", i / len(distinct))
            unique[src] = parts_dir / f"{i:03d}.mp4"
            await _normalize_video(src, unique[src], w, h)
    else:
        distinct = list(dict.fromkeys(p for _, p in sources))
        for i, src in enumerate(distinct):
            report("preparing audio", i / len(distinct))
            unique[src] = parts_dir / f"{i:03d}.wav"
            await ffmpeg.run("-i", str(src), "-vn", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(unique[src]))

    parts = [unique[src] for _, src in sources]
    durations = [(await _probe(p))["duration"] for p in parts]
    order = playlist(len(parts), durations, target, s.get("order", "sequence"), s.get("seed") or 0)
    listing = job_dir / "playlist.txt"
    listing.write_text("".join(f"file '{parts[i].resolve()}'\n" for i in order))
    length = min(target or sum(durations[i] for i in order), sum(durations[i] for i in order), MAX_SECONDS)

    # 2) join
    report("joining", 0.5)
    fade_in, fade_out = float(s.get("fade_in", 0)), float(s.get("fade_out", 0))
    fades = ",".join(f for f in (fade_in and f"afade=t=in:d={fade_in}",
                                 fade_out and f"afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out}") if f)
    rel = lambda p: str(p.relative_to(OUTPUTS_DIR))
    if kind == "video":
        out = job_dir / "combined.mp4"
        audio = ["-af", fades, "-c:a", "aac", "-b:a", "192k"] if fades else ["-c:a", "copy"]
        await ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-t", f"{length:.3f}",
                         "-c:v", "copy", *audio, "-movflags", "+faststart", str(out))
        result = {"video": rel(out)}
    else:
        out = job_dir / "combined.mp3"
        await ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-t", f"{length:.3f}",
                         *(["-af", fades] if fades else []), "-c:a", "libmp3lame", "-b:a", "192k", str(out))
        result = {"tracks": [{"mp3": rel(out), "duration": round(length, 2), "seed": s.get("seed") or 0, "peaks": []}]}

    for p in unique.values():
        p.unlink(missing_ok=True)
    parts_dir.rmdir()
    listing.unlink()
    result.update(items=len(sources), plays=len(order), duration=round((await _probe(out))["duration"], 2))
    return result
