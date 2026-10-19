"""Join clips into the final video with ffmpeg, crossfading between scenes."""
from pathlib import Path

from . import ffmpeg


async def concat(clips: list[Path], out: Path, clip_seconds: float, fade: float = 0.5) -> Path:
    if len(clips) == 1 or fade <= 0:
        listing = out.with_suffix(".txt")
        listing.write_text("".join(f"file '{Path(c).resolve()}'\n" for c in clips))
        await ffmpeg.run("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out))
        return out

    # Each crossfade overlaps two clips by `fade` seconds, so clip k starts at k * (clip_seconds - fade).
    fade = min(fade, clip_seconds / 2)
    inputs = [arg for c in clips for arg in ("-i", str(c))]
    chain, prev = [], "[0:v]"
    for k in range(1, len(clips)):
        label = f"[v{k}]"
        chain.append(f"{prev}[{k}:v]xfade=transition=fade:duration={fade}:offset={k * (clip_seconds - fade):.3f}{label}")
        prev = label
    await ffmpeg.run(*inputs, "-filter_complex", ";".join(chain), "-map", prev, *ffmpeg.ENCODE, str(out))
    return out
