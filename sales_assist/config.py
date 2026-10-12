"""
Centralized configuration loader.

Reads settings from a .env file (via python-dotenv) and exposes them
as module-level constants with validation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from project root, current directory, or executable directory
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent
_possible_env_paths = [
    _PROJECT_ROOT / ".env",
    Path.cwd() / ".env",
    Path(sys.executable).parent / ".env",
    _PROJECT_ROOT.parent / ".env",
]

_env_path = None
for p in _possible_env_paths:
    if p.exists():
        _env_path = p
        break

if _env_path is not None:
    load_dotenv(_env_path)
else:
    load_dotenv()


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ---------------------------------------------------------------------------
# STT settings
# ---------------------------------------------------------------------------
STT_LANGUAGE: str = os.getenv("STT_LANGUAGE", "auto")
STT_MODEL: str = os.getenv("STT_MODEL", "saaras:v4")

# Sarvam real-time WebSocket endpoint
SARVAM_WS_URL: str = "wss://api.sarvam.ai/speech-to-text-realtime/ws"

# ---------------------------------------------------------------------------
# Audio capture settings
# ---------------------------------------------------------------------------
AUDIO_SAMPLE_RATE: int = 16_000          # 16 kHz required by Sarvam
AUDIO_CHANNELS: int = 1                  # mono
AUDIO_SAMPLE_WIDTH: int = 2              # 16-bit PCM = 2 bytes
AUDIO_CHUNK_DURATION_MS: int = 100       # send chunks every 100 ms
AUDIO_CHUNK_FRAMES: int = AUDIO_SAMPLE_RATE * AUDIO_CHUNK_DURATION_MS // 1000  # 1600 frames


# ---------------------------------------------------------------------------
# LLM & Generation settings
# ---------------------------------------------------------------------------
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "150"))
SPECULATIVE_PREFETCH: bool = os.getenv("SPECULATIVE_PREFETCH", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Embedding settings (Local BAAI/bge-small-en-v1.5 or OpenAI)
# ---------------------------------------------------------------------------
EMBEDDING_PROVIDER: str = os.getenv("EMBEDDING_PROVIDER", "local").lower()
LOCAL_EMBEDDING_MODEL: str = os.getenv("LOCAL_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
LOCAL_MODELS_DIR: str = os.getenv("LOCAL_MODELS_DIR", "models")

# ---------------------------------------------------------------------------
# Document Ingestion settings
# ---------------------------------------------------------------------------
# The folder to automatically watch and ingest on startup
DOCS_FOLDER: str = os.getenv("DOCS_FOLDER", "knowledge_base")

def validate() -> list[str]:
    """Return a list of missing-but-required config values."""
    errors: list[str] = []
    if not SARVAM_API_KEY:
        errors.append("SARVAM_API_KEY is not set in .env")
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set in .env")
    return errors
