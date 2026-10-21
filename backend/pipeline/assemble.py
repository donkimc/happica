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
        listing.unlink()
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


async def finalize(video: Path, out: Path, total: float, narration: Path | None = None,
                   cues: list[captions.Cue] | None = None, burn: bool = False,
                   size: tuple[int, int] = (768, 512), beds: list[tuple[Path, float]] | None = None,
                   sfx: list[tuple[Path, float, float, float]] | None = None,
                   speech: list[tuple[float, float]] | None = None, segments: list[dict] | None = None,
                   caption_style: dict | None = None) -> Path:
    """Add narration, background beds (music, ambience — each looped to length at its own gain, and ducked
    together under the narration), sound effects (path, start, length, gain — placed at their time; `speech` is
    when the narration talks, where they play 6 dB lower), song segments (a stretch of a file — src, length — placed
    at dst; `duck` ones go under the narration with the music, the others play at their own level) and subtitles.

    Subtitles go in as a soft track, and are also burned into the picture when `burn`.
    """
    args = ["-i", str(video)]
    graph: list[str] = []
    video_out, audio_out = "0:v", None
    codec = ["-c:v", "copy"]
    n_inputs = 1

    if narration:
        args += ["-i", str(narration)]
        voice, n_inputs = f"{n_inputs}:a", n_inputs + 1
    stereo = "aformat=sample_rates=48000:channel_layouts=stereo"
    music_in, loose = [], []
    for k, (bed, gain) in enumerate(beds or []):
        args += ["-stream_loop", "-1", "-i", str(bed)]  # loop a short track under a long video
        graph.append(f"[{n_inputs}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,{stereo},volume={gain:.3f},"
                     f"afade=t=in:d=1,afade=t=out:st={max(0.0, total - 2):.3f}:d=2[bed{k}]")
        music_in.append(f"[bed{k}]")
        n_inputs += 1
    for k, seg in enumerate(segments or []):
        if seg["length"] <= 0:
            continue
        if seg.get("loop"):
            args += ["-stream_loop", "-1"]
        args += ["-i", str(seg["path"])]
        ms, length = max(0, round(seg["dst"] * 1000)), seg["length"]
        graph.append(f"[{n_inputs}:a]atrim=start={seg['src']:.3f}:end={seg['src'] + length:.3f},asetpts=PTS-STARTPTS,{stereo},"
                     f"volume={seg['gain']:.3f},afade=t=in:d={seg['fade_in']:.2f},"
                     f"afade=t=out:st={max(0.0, length - seg['fade_out']):.3f}:d={seg['fade_out']:.2f},adelay={ms}|{ms}[seg{k}]")
        (music_in if seg.get("duck") else loose).append(f"[seg{k}]")
        n_inputs += 1
    if music_in:
        graph.append("".join(music_in) + (f"amix=inputs={len(music_in)}:normalize=0:duration=longest" if len(music_in) > 1
                                          else "anull") + "[music]")
        if narration:
            # Duck: the music drops whenever the narration speaks, then comes back up between lines
            graph.append(f"[{voice}]{stereo},asplit=2[voice][key]")
            graph.append("[music][key]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[ducked]")
            layers = ["[voice]", "[ducked]"]
        else:
            layers = ["[music]"]
    elif narration and (sfx or loose):
        graph.append(f"[{voice}]{stereo}[voice]")
        layers = ["[voice]"]
    else:
        layers = []
    layers += loose

    if sfx:
        # Sound-effect bus: each cue at its own time, NOT ducked like the music, but ~-6 dB while someone speaks
        for k, (path, start, length, gain) in enumerate(sfx):
            args += ["-i", str(path)]
            ms = max(0, round(start * 1000))
            graph.append(f"[{n_inputs}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,{stereo},"
                         f"afade=t=out:st={max(0.0, length - 0.3):.3f}:d=0.3,volume={gain:.3f},adelay={ms}|{ms}[fx{k}]")
            n_inputs += 1
        graph.append("".join(f"[fx{k}]" for k in range(len(sfx))) + f"amix=inputs={len(sfx)}:normalize=0,"
                     f"apad=whole_dur={total:.3f}[fxmix]")
        talking = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in speech or [])
        graph.append(f"[fxmix]volume='if({talking},0.5,1)':eval=frame[fxbus]" if talking else "[fxmix]anull[fxbus]")
        layers.append("[fxbus]")

    if narration and not music_in and not loose and not sfx:
        audio_out = voice  # narration alone: as is
    elif layers:
        graph.append("".join(layers) + (f"amix=inputs={len(layers)}:normalize=0:duration=longest," if len(layers) > 1 else "")
                     + "alimiter=limit=0.95[mix]")
        audio_out = "[mix]"

    if cues and burn:
        # Overlay one caption image per cue, each enabled only during its time window
        work = out.parent / "captions"
        prev = "[0:v]"
        for k, cue in enumerate(cues):
            png = captions.render_png(cue.text, *size, work / f"cue_{k:03d}.png", caption_style, cue.style)
            args += ["-i", str(png)]
            label = f"[c{k}]"
            graph.append(f"{prev}[{n_inputs}:v]overlay=enable='between(t,{cue.start:.3f},{cue.end:.3f})'{label}")
            prev, n_inputs = label, n_inputs + 1
        video_out = prev
        codec = list(ffmpeg.ENCODE)  # a copy: the subtitle and audio options get appended below

    maps = ["-map", video_out]
    if audio_out:
        maps += ["-map", audio_out]
        codec += ["-c:a", "aac", "-b:a", "192k"]
    if cues:  # soft subtitle track too, so players can turn captions on and off
        srt = captions.write_srt(cues, out.with_suffix(".srt"))
        args += ["-i", str(srt)]
        maps += ["-map", f"{n_inputs}:s"]
        codec += ["-c:s", "mov_text"]
    if graph:
        args += ["-filter_complex", ";".join(graph)]
    await ffmpeg.run(*args, *maps, *codec, "-t", f"{total:.3f}", str(out))
    if audio_out:
        await normalize_loudness(out)
    return out


LOUDNESS = "I=-14:TP=-1.5:LRA=11"  # TikTok / YouTube / Reels level; the mix above peaks well under it otherwise


async def normalize_loudness(video: Path) -> None:
    """Two-pass loudnorm of the finished video's sound (measure, then a linear gain), in place; the picture and
    subtitle tracks are copied untouched. One pass would compress dynamically and pump under the narration."""
    import asyncio
    import json

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-i", str(video), "-af", f"loudnorm={LOUDNESS}:print_format=json", "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    text = err.decode()
    m = json.loads(text[text.rindex("{"):text.rindex("}") + 1])
    measured = (f":measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
                f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    tmp = video.with_name(f"{video.stem}.loud{video.suffix}")
    await ffmpeg.run("-i", str(video), "-map", "0", "-c", "copy", "-af", f"loudnorm={LOUDNESS}{measured}",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", str(tmp))
    tmp.replace(video)
