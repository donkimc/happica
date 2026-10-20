"""When each lyric line is sung: Whisper hears the separated vocals, then the KNOWN lyrics are aligned to what it
heard, syllable by syllable (Needleman–Wunsch). The lyrics are never replaced by the transcription — Whisper only
supplies the times, so its spelling mistakes (산소 for 산속) don't matter, and near misses still count.

The timing is cached next to the song (v1_lyrics.json) and copied into the music job's result (`lyrics_timing`).
"""
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from config import OUTPUTS_DIR

from . import stems

TAG = re.compile(r"^\s*\[(.*?)\]\s*$")
LETTER = re.compile(r"[가-힣a-z0-9]")
MATCH, NEAR, MISS, GAP = 2, 1, -1, -1


def lyric_lines(lyrics: str) -> list[dict]:
    """Sung lines in order, each with its section ("verse", "chorus"…)."""
    out, section = [], ""
    for raw in lyrics.splitlines():
        if m := TAG.match(raw):
            section = m.group(1).strip().lower()
        elif raw.strip():
            out.append({"text": raw.strip(), "section": section})
    return out


def _letters(text: str) -> list[str]:
    return LETTER.findall(text.lower())


def _score(a: str, b: str) -> int:
    if a == b:
        return MATCH
    if "가" <= a <= "힣" and "가" <= b <= "힣":  # same initial consonant and vowel, e.g. 떴/떨, 속/소
        ca, cb = ord(a) - 0xAC00, ord(b) - 0xAC00
        if ca // 28 == cb // 28:
            return NEAR
    return MISS


def _align(ref: list[str], hyp: list[str]) -> list[int | None]:
    """For each lyric letter, the index of the heard letter it lines up with (None for a gap)."""
    n, m = len(ref), len(hyp)
    score = np.zeros((n + 1, m + 1), dtype=np.int32)
    score[:, 0] = np.arange(n + 1) * GAP
    score[0, :] = np.arange(m + 1) * GAP
    move = np.zeros((n + 1, m + 1), dtype=np.int8)  # 0 diagonal, 1 up (lyric letter unheard), 2 left (extra heard)
    move[1:, 0], move[0, 1:] = 1, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d = score[i - 1, j - 1] + _score(ref[i - 1], hyp[j - 1])
            u = score[i - 1, j] + GAP
            l = score[i, j - 1] + GAP
            best = max(d, u, l)
            score[i, j] = best
            move[i, j] = 0 if best == d else 1 if best == u else 2
    out: list[int | None] = [None] * n
    i, j = n, m
    while i > 0 or j > 0:
        mv = move[i, j]
        if mv == 0:
            if _score(ref[i - 1], hyp[j - 1]) > 0:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif mv == 1:
            i -= 1
        else:
            j -= 1
    return out


def align(lines: list[dict], words: list[dict], song_seconds: float) -> list[dict]:
    """Start and end time for every lyric line."""
    heard, times = [], []
    for w in words:  # spread each word's time over its letters
        letters = _letters(w["word"])
        span = max(0.0, w["end"] - w["start"]) / max(1, len(letters))
        for k, ch in enumerate(letters):
            heard.append(ch)
            times.append((w["start"] + k * span, w["start"] + (k + 1) * span))
    ref, owner = [], []
    for li, line in enumerate(lines):
        for ch in _letters(line["text"]):
            ref.append(ch)
            owner.append(li)
    matched = _align(ref, heard) if ref and heard else [None] * len(ref)

    timed: list[dict] = []
    for li, line in enumerate(lines):
        hits = [matched[k] for k in range(len(ref)) if owner[k] == li and matched[k] is not None]
        size = sum(1 for o in owner if o == li) or 1
        sure = len(hits) / size >= 0.4
        timed.append({**line, "start": times[hits[0]][0] if sure else None, "end": times[hits[-1]][1] if sure else None})

    # Lines Whisper missed: share the gap between the nearest timed neighbours by length
    k = 0
    while k < len(timed):
        if timed[k]["start"] is not None:
            k += 1
            continue
        j = k
        while j < len(timed) and timed[j]["start"] is None:
            j += 1
        lo = timed[k - 1]["end"] if k else 0.0
        hi = timed[j]["start"] if j < len(timed) else song_seconds
        sizes = [len(_letters(t["text"])) or 1 for t in timed[k:j]]
        t = lo
        for idx, s in zip(range(k, j), sizes):
            d = (hi - lo) * s / sum(sizes)
            timed[idx].update(start=t, end=t + d)
            t += d
        k = j
    for a, b in zip(timed, timed[1:]):  # no overlaps; a line holds until shortly before the next
        a["end"] = min(a["end"], b["start"])
    return [{**t, "start": round(t["start"], 2), "end": round(t["end"], 2)} for t in timed]


async def timing(song: Path, lyrics: str, language: str) -> list[dict]:
    """Per-line timing of a song's lyrics (cached by the lyrics' hash next to the song)."""
    key = hashlib.sha1(f"{language}\n{lyrics}".encode()).hexdigest()[:12]
    cache = song.with_name(f"{song.stem}_lyrics.json")
    if cache.is_file() and (data := json.loads(cache.read_text())).get("key") == key:
        return data["lines"]
    lines = lyric_lines(lyrics)
    if not lines:
        return []
    vocals, _ = await stems.separate(song)
    words = await stems.words(vocals, language, " ".join(l["text"] for l in lines)[:600])
    seconds = sf.info(song).duration
    timed = align(lines, words, seconds)
    cache.write_text(json.dumps({"key": key, "lines": timed}, ensure_ascii=False))
    _save_to_music_job(song, timed)
    return timed


def _save_to_music_job(song: Path, timed: list[dict]) -> None:
    """Music library songs keep their lyric timing in the job's result too, for the API and other videos."""
    from jobs import JobStore

    try:
        rel = str(song.relative_to(OUTPUTS_DIR))
    except ValueError:
        return
    store = JobStore()
    job = store.get(rel.split("/")[0])
    if not job or job["kind"] != "music" or not job["result"]:
        return
    result = job["result"]
    for t in result.get("tracks", []):
        if t.get("flac") == rel:
            t["lyrics_timing"] = timed
    store.update(job["id"], result=result)


def chorus(timed: list[dict]) -> tuple[float, float] | None:
    """The first chorus: from its first sung line to its last."""
    first = next((i for i, t in enumerate(timed) if "chorus" in t["section"]), None)
    if first is None:
        return None
    last = first
    while last + 1 < len(timed) and timed[last + 1]["section"] == timed[first]["section"]:
        last += 1
    return timed[first]["start"], timed[last]["end"]


def loudest(song: Path, seconds: float = 15.0) -> tuple[float, float]:
    """The loudest stretch of the song (for picking a chorus when the lyrics have no [chorus])."""
    audio, sr = sf.read(song, dtype="float32", always_2d=True)
    energy = np.convolve(np.square(audio.mean(axis=1)), np.ones(sr) / sr, mode="valid")[::sr // 10]  # 1 s RMS, every 0.1 s
    width = int(seconds * 10)
    if len(energy) <= width:
        return 0.0, len(audio) / sr
    sums = np.convolve(energy, np.ones(width), mode="valid")
    start = int(np.argmax(sums)) / 10
    return start, start + seconds
