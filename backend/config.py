"""Central settings. Override any value with an environment variable of the same name."""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

COMFY_URL = os.getenv("COMFY_URL", "http://127.0.0.1:8188")
MUSIC_COMFY_URL = os.getenv("MUSIC_COMFY_URL", "http://127.0.0.1:8189")  # see scripts/start.sh
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", ROOT_DIR / "outputs"))
DB_PATH = Path(os.getenv("DB_PATH", ROOT_DIR / "outputs" / "jobs.db"))
WORKFLOWS_DIR = Path(__file__).resolve().parent / "workflows"
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
TTS_DIR = Path(os.getenv("TTS_DIR", ROOT_DIR / "models" / "tts"))

OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
