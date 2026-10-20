"""Song analysis tools that need their own Python (engine/audio-tools/.venv: PyTorch + Demucs, MLX Whisper).

    engine/audio-tools/.venv/bin/python backend/tools/audio_worker.py stems SONG.flac VOCALS.flac INSTRUMENTAL.flac
    engine/audio-tools/.venv/bin/python backend/tools/audio_worker.py words VOCALS.flac LANG [PROMPT]   → JSON on stdout

stems: Demucs htdemucs splits the song; drums + bass + other become the instrumental.
words: Whisper large-v3-turbo (MLX, Apple GPU) transcribes the sung vocals with a time for every word.
"""
import json
import sys


def stems(song: str, vocals_out: str, instrumental_out: str) -> None:
    import numpy as np
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    from torchaudio.functional import resample

    model = get_model("htdemucs").eval()
    audio, sr = sf.read(song, dtype="float32", always_2d=True)
    wav = resample(torch.from_numpy(audio.T.copy()), sr, model.samplerate)  # Demucs works at 44.1 kHz
    if wav.shape[0] == 1:
        wav = wav.repeat(2, 1)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    device = "cpu"  # htdemucs hits an MPS limit (conv output channels > 65536); the CPU takes ~1x real time
    with torch.inference_mode():
        out = apply_model(model, ((wav - mean) / std)[None], device=device, shifts=1, split=True, overlap=0.25)[0]
    out = out * std + mean
    names = model.sources  # drums, bass, other, vocals
    vocals = resample(out[names.index("vocals")].cpu(), model.samplerate, sr)  # back to the song's rate
    instrumental = resample(sum(out[i] for i, n in enumerate(names) if n != "vocals").cpu(), model.samplerate, sr)
    sf.write(vocals_out, vocals.T.numpy().astype(np.float32), sr)
    sf.write(instrumental_out, instrumental.T.numpy().astype(np.float32), sr)


def words(vocals: str, language: str, prompt: str = "") -> None:
    import mlx_whisper

    result = mlx_whisper.transcribe(vocals, path_or_hf_repo="mlx-community/whisper-large-v3-turbo", language=language,
                                    word_timestamps=True, initial_prompt=prompt or None, condition_on_previous_text=False)
    out = [{"word": w["word"], "start": round(w["start"], 3), "end": round(w["end"], 3)}
           for seg in result.get("segments", []) for w in seg.get("words", [])]
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    {"stems": stems, "words": words}[cmd](*args)
