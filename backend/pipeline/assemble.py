"""Join clips into the final video: crossfades, narration track and subtitles."""
from pathlib import Path

import numpy as np
import soundfile as sf

from . import captions, ffmpeg

SAMPLE_RATE = 24000


def scene_starts(durations: list[float], fade: float) -> list[float]:
    """When each scene starts in the final video; consecutive scenes overlap by `fade` seconds."""
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d - fade
    return starts


async def concat(clips: list[Path], durations: list[float], out: Path, fade: float = 0.5) -> Path:
    if len(clips) == 1 or fade <= 0:
        listing = out.with_suffix(".txt")
        listing.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips))
        await ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out))
        return out

    inputs = [arg for c in clips for arg in ("-i", str(c))]
    starts = scene_starts(durations, fade)
    chain, prev = [], "[0:v]"
    for k in range(1, len(clips)):
        label = f"[v{k}]"
        chain.append(f"{prev}[{k}:v]xfade=transition=fade:duration={fade}:offset={starts[k]:.3f}{label}")
        prev = label
    await ffmpeg.run(*inputs, "-filter_complex", ";".join(chain), "-map", prev, *ffmpeg.ENCODE, str(out))
    return out


def mix_narration(audio: list[Path | None], starts: list[float], lead: float, total: float, out: Path) -> Path:
    """Place each scene's narration `lead` seconds after its scene starts, on one silent track."""
    track = np.zeros(int(total * SAMPLE_RATE) + 1, dtype=np.float32)
    for path, start in zip(audio, starts):
        if not path:
            continue
        clip, sr = sf.read(path, dtype="float32")
        assert sr == SAMPLE_RATE, f"unexpected sample rate {sr}"
        i = int((start + lead) * SAMPLE_RATE)
        clip = clip[: len(track) - i]
        track[i : i + len(clip)] += clip
    sf.write(out, track, SAMPLE_RATE)
    return out


async def finalize(video: Path, out: Path, narration: Path | None = None,
                   cues: list[captions.Cue] | None = None, burn: bool = False,
                   size: tuple[int, int] = (768, 512)) -> Path:
    """Add the narration track and subtitles (soft track, plus burned in when `burn`)."""
    args = ["-i", str(video)]
    maps = ["-map", "0:v"]
    codec = ["-c:v", "copy"]
    n_inputs = 1

    if narration:
        args += ["-i", str(narration)]
        maps += ["-map", f"{n_inputs}:a"]
        n_inputs += 1

    if cues and burn:
        # Overlay one caption image per cue, each enabled only during its time window
        work = out.parent / "captions"
        chain, prev = [], "[0:v]"
        for k, cue in enumerate(cues):
            png = captions.render_png(cue.text, *size, work / f"cue_{k:03d}.png")
            args += ["-i", str(png)]
            label = f"[c{k}]"
            chain.append(f"{prev}[{n_inputs}:v]overlay=enable='between(t,{cue.start:.3f},{cue.end:.3f})'{label}")
            prev, n_inputs = label, n_inputs + 1
        args += ["-filter_complex", ";".join(chain)]
        maps[1] = prev  # replaces "0:v"
        codec = list(ffmpeg.ENCODE)  # a copy: the subtitle and audio options get appended below

    if cues:  # soft subtitle track too, so players can turn captions on and off
        srt = captions.write_srt(cues, out.with_suffix(".srt"))
        args += ["-i", str(srt)]
        maps += ["-map", f"{n_inputs}:s"]
        codec += ["-c:s", "mov_text"]

    if narration:
        codec += ["-c:a", "aac", "-b:a", "160k"]
    await ffmpeg.run(*args, *maps, *codec, str(out))
    return out
