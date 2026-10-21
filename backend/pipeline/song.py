"""A sung song inside a story video (the job's `song` block).

placement:
  bed          the song under the narration, ducked (like background music)
  intro_outro  the sung chorus at full volume over the opening and the ending (no narration there), and the
               song's own instrumental (Demucs) under the narrated middle, ducked
  sung_story   no narration: the song tells the story, each scene lasts for its share of the lyric lines

Sung lyrics are timed with Whisper + alignment (lyrics_align) and burned as yellow subtitles.
"""
import random
from pathlib import Path

import soundfile as sf

from comfy_client import ComfyClient
from config import OUTPUTS_DIR

from . import lyrics_align, music, stems

MAX_PART = 20.0  # longest sung intro / outro, seconds
LEAD_IN = 0.25  # start a sung part this much before its first line, so the first syllable isn't clipped


async def prepare(job: dict, job_dir: Path, report, music_comfy: ComfyClient) -> dict:
    """The song file (made now from `spec`, or a library take), plus what its placement needs: lyric timing, stems."""
    from jobs import JobStore

    block = job["settings"]["song"]
    takes = None
    if block.get("track"):
        track = block["track"]
        music_job = JobStore().get(track.split("/")[0])
        s = music_job["settings"] if music_job else {}
        lyrics = "" if s.get("instrumental") else s.get("lyrics", "")
        language, title = s.get("language", "en"), s.get("title", "")
    else:
        spec = dict(block["spec"])
        lyrics, language, title = spec.get("lyrics", ""), spec.get("language", "ko"), spec.get("title", "")
        seed = spec.get("seed")
        if seed is None:
            seed = random.randrange(2**31)
        settings = {**spec, "instrumental": not lyrics.strip()}
        n = spec.get("takes", 2) if lyrics.strip() else 1
        tracks = await music.generate(music_comfy, settings, job_dir / "song", f"{job_dir.name}_song",
                                      [seed + k for k in range(n)], lambda stage, p: report(f"song: {stage}", p))
        track = tracks[0]["flac"]
        if n > 1:  # keep the take whose sung words come through clearest
            report("song: choosing the clearest take", 0.0)
            scores = [lyrics_align.clarity(await lyrics_align.timing(OUTPUTS_DIR / t["flac"], lyrics, language))
                      for t in tracks]
            track = tracks[scores.index(max(scores))]["flac"]
            takes = [{"track": t["flac"], "clarity": s} for t, s in zip(tracks, scores)]
    path = OUTPUTS_DIR / track
    if not path.is_file():
        raise RuntimeError(f"The song {track} doesn't exist (did its music job fail?)")
    info = {"placement": block.get("placement", "bed"), "track": track, "mp3": track.removesuffix(".flac") + ".mp3",
            "title": title, "language": language, "volume": block.get("volume", 0.5),
            "lyrics_subtitles": block.get("lyrics_subtitles", True), "duration": round(sf.info(path).duration, 2)}
    if takes:
        info["takes"] = takes  # every take and its clarity, for the job page
    if info["placement"] == "intro_outro":
        report("song: separating the instrumental", 0.0)
        _, instrumental = await stems.separate(path)
        info["instrumental"] = str(instrumental.relative_to(OUTPUTS_DIR))
    if lyrics.strip() and info["placement"] != "bed":
        report("song: timing the lyrics", 0.5)
        info["timing"] = await lyrics_align.timing(path, lyrics, language)
    return info


def sung_durations(song: dict, n: int, fade: float, counts: list[int | None] | None = None) -> list[float]:
    """Scene lengths for a sung story: scene k starts when its first lyric line starts; the last runs to the end.
    `counts` (lyric lines per scene, from the script) puts each cut exactly where the scene's own lines begin;
    otherwise the lines are shared out evenly."""
    lines, total = song.get("timing") or [], song["duration"]
    m = len(lines)
    if counts and len(counts) == n and all(c is not None for c in counts) and sum(counts) == m and m:
        first, bounds = 0, []
        for c in counts:
            bounds.append(0.0 if not bounds else max(0.0, lines[min(first, m - 1)]["start"] - 0.3))  # picture just before the singing
            first += c
    elif m >= n:
        bounds = [0.0] + [lines[round(k * m / n)]["start"] for k in range(1, n)]
    else:
        bounds = [total * k / n for k in range(n)]
    if any(b2 - b1 < max(1.0, fade + 0.3) for b1, b2 in zip(bounds, bounds[1:] + [total])):
        bounds = [total * k / n for k in range(n)]  # odd timing: share the song evenly
    return [round(b2 - b1 + fade, 3) for b1, b2 in zip(bounds, bounds[1:])] + [round(total - bounds[-1], 3)]


def _blocks(timing: list[dict]) -> list[tuple[int, int]]:
    """(first, last) line index of every chorus section."""
    out, i = [], 0
    while i < len(timing):
        if "chorus" in timing[i]["section"]:
            j = i
            while j + 1 < len(timing) and timing[j + 1]["section"] == timing[i]["section"]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _part(timing: list[dict], block: tuple[int, int] | None, song_path: Path) -> tuple[float, float, list[dict]]:
    """(start, length, lines) of a sung part: a chorus cut at a line end within MAX_PART, or the loudest stretch."""
    if block:
        lines = timing[block[0]: block[1] + 1]
        start = max(0.0, lines[0]["start"] - LEAD_IN)
        keep = [l for l in lines if l["end"] - start <= MAX_PART] or lines[:1]
        return start, min(keep[-1]["end"] - start + 0.4, MAX_PART), keep
    a, b = lyrics_align.loudest(song_path, 15.0)
    return a, b - a, []


def intro_outro(song: dict, narrated: list[bool], durations: list[float], fade: float, lead: float,
                music_level: float) -> dict:
    """Lay out a sung intro and outro around the narrated scenes: new scene durations, per-scene narration leads,
    the audio segments and the lyric cues."""
    timing = song.get("timing") or []
    path = OUTPUTS_DIR / song["track"]
    blocks = _blocks(timing)
    in_src, in_len, in_lines = _part(timing, blocks[0] if blocks else None, path)
    out_src, out_len, out_lines = _part(timing, blocks[-1] if blocks else None, path)

    n = len(durations)
    durations, leads = list(durations), [lead] * n
    a = next((i for i, v in enumerate(narrated) if v), n)  # silent scenes before the first narration
    b = 0 if a == n else next((i for i, v in enumerate(reversed(narrated)) if v), 0)  # silent scenes after the last
    if a:
        for i in range(a):
            durations[i] = in_len / a + fade
    else:  # no title scene: the first scene shows during the intro, its narration waits
        durations[0] += in_len
        leads[0] += in_len
    if b:
        for i in range(n - b, n):
            durations[i] = out_len / b + (fade if i < n - 1 else 0)
    else:
        durations[-1] += out_len
    starts, t = [], 0.0
    for d in durations:
        starts.append(t)
        t += d - fade
    total = starts[-1] + durations[-1]
    out_at = total - out_len

    segments = [
        {"path": song["track"], "src": in_src, "dst": 0.0, "length": in_len, "gain": song["volume"],
         "fade_in": 0.05, "fade_out": 1.0, "duck": False, "loop": False},
        {"path": song["instrumental"], "src": in_src + in_len - 1.0, "dst": max(0.0, in_len - 1.0),
         "length": max(0.0, out_at + 1.0 - max(0.0, in_len - 1.0)), "gain": music_level,
         "fade_in": 1.0, "fade_out": 1.0, "duck": True, "loop": True},
        {"path": song["track"], "src": out_src, "dst": out_at, "length": out_len, "gain": song["volume"],
         "fade_in": 0.5, "fade_out": 1.5, "duck": False, "loop": False},
    ]
    cues = [{"start": round(l["start"] - in_src, 2), "end": round(min(l["end"], in_src + in_len) - in_src, 2), "text": l["text"]}
            for l in in_lines]
    cues += [{"start": round(l["start"] - out_src + out_at, 2), "end": round(min(l["end"], out_src + out_len) - out_src + out_at, 2),
              "text": l["text"]} for l in out_lines]
    return {"durations": [round(d, 3) for d in durations], "leads": [round(x, 3) for x in leads],
            "segments": segments, "lyric_cues": cues, "intro": [0.0, round(in_len, 2)], "outro": [round(out_at, 2), round(total, 2)]}


def full_song_cues(song: dict, offset: float = 0.0) -> list[dict]:
    """Lyric cues for a song that plays from its start (sung story, music video)."""
    return [{"start": round(l["start"] + offset, 2), "end": round(l["end"] + offset, 2), "text": l["text"]}
            for l in song.get("timing") or []]
