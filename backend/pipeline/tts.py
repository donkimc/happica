"""Narration with Kokoro TTS (82M params, runs on the CPU via onnxruntime)."""
import asyncio
from functools import lru_cache
from pathlib import Path

import soundfile as sf

from config import TTS_DIR

# Kokoro voice ids start with a language letter and a gender letter: af_heart = American English, female
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}
LANG_NAMES = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
              "i": "Italian", "j": "Japanese", "p": "Portuguese (BR)", "z": "Mandarin"}


@lru_cache(maxsize=1)
def _kokoro():
    from kokoro_onnx import Kokoro  # imported lazily: only jobs with narration pay the load time

    return Kokoro(str(TTS_DIR / "kokoro-v1.0.onnx"), str(TTS_DIR / "voices-v1.0.bin"))


def voices() -> list[dict]:
    out = []
    for v in sorted(_kokoro().get_voices()):
        lang, gender = v[0], v[1]
        out.append({"id": v, "name": v.split("_", 1)[1].capitalize(), "language": LANG_NAMES.get(lang, lang),
                    "gender": "female" if gender == "f" else "male"})
    return out


def _speak(text: str, voice: str, speed: float, out: Path) -> float:
    audio, sr = _kokoro().create(text, voice=voice, speed=speed, lang=LANGS.get(voice[0], "en-us"))
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, audio, sr)
    return len(audio) / sr


async def speak(text: str, voice: str, out: Path, speed: float = 1.0) -> float:
    """Write `text` as speech to a WAV file; returns its duration in seconds."""
    return await asyncio.to_thread(_speak, text, voice, speed, out)
