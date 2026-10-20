"""Narration: Kokoro for its 9 languages, Supertonic 3 for 24 more (Korean, German, Russian, Arabic…).

Both run on the CPU via onnxruntime. Every voice's output is written at 24 kHz so the rest of the
pipeline (narration mixing, voiceovers) never has to care which engine spoke.
"""
import asyncio
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import soundfile as sf

from config import TTS_DIR

SAMPLE_RATE = 24000  # Kokoro's native rate; Supertonic (44.1 kHz) is resampled to it

# Kokoro voice ids start with a language letter and a gender letter: af_heart = American English, female
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}
LANG_NAMES = {"a": "American English", "b": "British English", "e": "Spanish", "f": "French", "h": "Hindi",
              "i": "Italian", "j": "Japanese", "p": "Portuguese (BR)", "z": "Mandarin"}

# Supertonic 3 voices are ids like "st-ko-F1": any of its 10 voices can speak any of its languages.
# Only languages Kokoro doesn't cover are offered here, so each language has one engine.
ST_LANGS = {"ko": "Korean", "ar": "Arabic", "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German",
            "el": "Greek", "et": "Estonian", "fi": "Finnish", "hr": "Croatian", "hu": "Hungarian",
            "id": "Indonesian", "lt": "Lithuanian", "lv": "Latvian", "nl": "Dutch", "pl": "Polish",
            "ro": "Romanian", "ru": "Russian", "sk": "Slovak", "sl": "Slovenian", "sv": "Swedish",
            "tr": "Turkish", "uk": "Ukrainian", "vi": "Vietnamese"}
ST_VOICES = [f"{g}{n}" for g in "FM" for n in range(1, 6)]
ST_ID = re.compile(r"^st-([a-z]{2})-([FM][1-5])$")
VOICE_ID = r"^([a-z]{2}_[a-z]+|st-[a-z]{2}-[FM][1-5])$"  # for request validation


@lru_cache(maxsize=1)
def _kokoro():
    from kokoro_onnx import Kokoro  # imported lazily: only jobs with narration pay the load time

    return Kokoro(str(TTS_DIR / "kokoro-v1.0.onnx"), str(TTS_DIR / "voices-v1.0.bin"))


@lru_cache(maxsize=1)
def _supertonic():
    from supertonic import TTS

    return TTS(model_dir=str(TTS_DIR / "supertonic-3"), auto_download=False)


def voices() -> list[dict]:
    out = []
    for v in sorted(_kokoro().get_voices()):
        lang, gender = v[0], v[1]
        out.append({"id": v, "name": v.split("_", 1)[1].capitalize(), "language": LANG_NAMES.get(lang, lang),
                    "gender": "female" if gender == "f" else "male"})
    if (TTS_DIR / "supertonic-3").is_dir():
        for code, language in sorted(ST_LANGS.items(), key=lambda kv: kv[1]):
            for v in ST_VOICES:
                out.append({"id": f"st-{code}-{v}", "name": f"{'Female' if v[0] == 'F' else 'Male'} {v[1]}",
                            "language": language, "gender": "female" if v[0] == "F" else "male"})
    return out


def language_of(voice: str) -> str:
    """Human name of a voice's language (for hints and prompts)."""
    if m := ST_ID.match(voice):
        return ST_LANGS.get(m.group(1), m.group(1))
    return LANG_NAMES.get(voice[:1], "English")


def _resample(audio: np.ndarray, rate: int) -> np.ndarray:
    """Change the sample rate by FFT (band-limited) resampling — clean for speech, and needs no extra packages."""
    if rate == SAMPLE_RATE:
        return audio
    n = int(round(len(audio) * SAMPLE_RATE / rate))
    # FFT resampling keeps the speech band intact without needing scipy
    spectrum = np.fft.rfft(audio)
    keep = n // 2 + 1
    spectrum = spectrum[:keep] if keep <= len(spectrum) else np.pad(spectrum, (0, keep - len(spectrum)))
    return (np.fft.irfft(spectrum, n) * (n / len(audio))).astype(np.float32)


def _speak(text: str, voice: str, speed: float, out: Path) -> float:
    if m := ST_ID.match(voice):
        tts = _supertonic()
        wav, _ = tts.synthesize(text, voice_style=tts.get_voice_style(m.group(2)), lang=m.group(1),
                                speed=1.05 * speed)  # 1.05 is Supertonic's natural pace
        audio = _resample(np.asarray(wav, dtype=np.float32).squeeze(), tts.sample_rate)
    else:
        audio, sr = _kokoro().create(text, voice=voice, speed=speed, lang=LANGS.get(voice[0], "en-us"))
        audio = _resample(np.asarray(audio, dtype=np.float32), sr)
    out.parent.mkdir(parents=True, exist_ok=True)
    sf.write(out, audio, SAMPLE_RATE)
    return len(audio) / SAMPLE_RATE


async def speak(text: str, voice: str, out: Path, speed: float = 1.0) -> float:
    """Write `text` as speech to a 24 kHz WAV file; returns its duration in seconds."""
    return await asyncio.to_thread(_speak, text, voice, speed, out)
