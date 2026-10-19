"""Voiceover: text → narration with Kokoro, sentence by sentence, with natural pauses and timed subtitles."""
import asyncio
import re
from pathlib import Path

import numpy as np
import soundfile as sf

from config import OUTPUTS_DIR

from . import captions, ffmpeg, music, tts

SENTENCE_PAUSE = 0.25   # seconds between sentences
PARAGRAPH_PAUSE = 0.7   # between paragraphs
MAX_CHUNK = 300         # characters per synthesis call; Kokoro trims very long inputs


def chunks(text: str) -> list[tuple[str, bool]]:
    """Split into sentence-sized pieces; the flag marks the last piece of a paragraph."""
    out: list[tuple[str, bool]] = []
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        sentences = re.split(r"(?<=[.!?。！？])\s+", " ".join(para.split()))
        piece = ""
        for sent in sentences:
            if piece and len(piece) + 1 + len(sent) > MAX_CHUNK:
                out.append((piece, False))
                piece = sent
            else:
                piece = f"{piece} {sent}".strip()
        if piece:
            out.append((piece, True))
    return out


async def run(job: dict, job_dir: Path, report, _comfy=None) -> dict:
    s = job["settings"]
    pieces = chunks(s["text"])
    if not pieces:
        raise RuntimeError("Nothing to read")
    work = job_dir / "parts"
    audio_parts, cues, t = [], [], 0.0
    for i, (piece, para_end) in enumerate(pieces):
        report("speaking", i / len(pieces))
        part = work / f"{i:03d}.wav"
        seconds = await tts.speak(piece, s.get("voice", "af_heart"), part, s.get("speed", 1.0))
        clip, sr = await asyncio.to_thread(sf.read, part, dtype="float32")
        cues += captions.cues_for(piece, t, seconds)
        pause = PARAGRAPH_PAUSE if para_end else SENTENCE_PAUSE
        audio_parts += [clip, np.zeros(int(pause * sr), dtype=np.float32)]
        t += seconds + pause

    report("mastering", 0.95)
    raw = job_dir / "voice.raw.wav"
    await asyncio.to_thread(sf.write, raw, np.concatenate(audio_parts[:-1]), sr)  # no pause after the last piece
    flac, mp3 = job_dir / "voice.flac", job_dir / "voice.mp3"
    await ffmpeg.run("-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", str(flac),
                     "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-b:a", "160k", str(mp3))
    raw.unlink()
    for f in work.glob("*.wav"):
        f.unlink()
    work.rmdir()
    srt = captions.write_srt(cues, job_dir / "voice.srt")
    duration = (await asyncio.to_thread(sf.info, flac)).duration
    rel = lambda p: str(p.relative_to(OUTPUTS_DIR))
    return {"tracks": [{"mp3": rel(mp3), "flac": rel(flac), "duration": round(duration, 2), "seed": 0,
                        "peaks": await asyncio.to_thread(music._peaks, flac)}],
            "subtitles": rel(srt)}
