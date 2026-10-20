"""Split a song into vocals and instrumental with Demucs (htdemucs), cached next to the song.

Runs backend/tools/audio_worker.py in engine/audio-tools/.venv (PyTorch + Demucs), on the CPU: ~40 s for a
70 s song on an M2. v1.flac → v1_vocals.flac + v1_instrumental.flac.
"""
import asyncio
import json
from pathlib import Path

from config import ROOT_DIR

PYTHON = ROOT_DIR / "engine" / "audio-tools" / ".venv" / "bin" / "python"
WORKER = Path(__file__).resolve().parent.parent / "tools" / "audio_worker.py"


async def _worker(*args: str) -> bytes:
    if not PYTHON.is_file():
        raise RuntimeError("The song tools aren't installed — see README (Setup → sung songs)")
    proc = await asyncio.create_subprocess_exec(str(PYTHON), str(WORKER), *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {err.decode()[-600:]}")
    return out


def paths(song: Path) -> tuple[Path, Path]:
    return song.with_name(f"{song.stem}_vocals.flac"), song.with_name(f"{song.stem}_instrumental.flac")


async def separate(song: Path) -> tuple[Path, Path]:
    """(vocals, instrumental) of a song, made once and reused."""
    vocals, instrumental = paths(song)
    if not (vocals.is_file() and instrumental.is_file()):
        await _worker("stems", str(song), str(vocals), str(instrumental))
    return vocals, instrumental


async def words(vocals: Path, language: str, prompt: str = "") -> list[dict]:
    """Whisper large-v3-turbo's words with start/end times, from the separated vocals."""
    return json.loads(await _worker("words", str(vocals), language, prompt))
