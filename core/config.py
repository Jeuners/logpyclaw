"""
core/config.py — Pfad-Konstanten, URLs, Hilfsfunktionen.
Importiert nur core/state.py (für _DEBUG_LOG).
"""
import os
import sys
import json
import threading

from core.state import _DEBUG_LOG

# ── Pfad-Konfiguration ────────────────────────────────────────────────────────
# Im py2app-Bundle zeigt __file__ auf die .zip — CWD nutzen (gesetzt durch chdir in main_app.py)
BASE_DIR = (
    os.getcwd()
    if getattr(sys, "frozen", False)
    else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

AGENTS_FILE    = os.path.join(BASE_DIR, "agents.json")
HISTORY_FILE   = os.path.join(BASE_DIR, "history.json")
PROVIDERS_FILE = os.path.join(BASE_DIR, "providers.json")
WATCHDOGS_FILE = os.path.join(BASE_DIR, "watchdogs.json")
TASKS_FILE     = os.path.join(BASE_DIR, "tasks.json")
NODES_FILE     = os.path.join(BASE_DIR, "nodes.json")
BACKUP_DIR     = os.path.join(BASE_DIR, "backups")

# ── API URLs ──────────────────────────────────────────────────────────────────
MISTRAL_TTS_URL    = "https://api.mistral.ai/v1/audio/speech"
MISTRAL_VOICES_URL = "https://api.mistral.ai/v1/audio/voices"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GOOGLE_TTS_URL     = "https://texttospeech.googleapis.com/v1/text:synthesize"

# ── Embedding Config ──────────────────────────────────────────────────────────
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM   = 768

# ── History Limits ────────────────────────────────────────────────────────────
MAX_HISTORY_PER_AGENT = 500
MAX_CONTENT_LENGTH    = 32000   # erhöht für lange Transkriptionen (vorher 8000)


# ── Debug Logging ─────────────────────────────────────────────────────────────
def dlog(*args, tag="DEBUG"):
    """Conditional debug log — only prints when _DEBUG_LOG is True."""
    if _DEBUG_LOG:
        print(f"[{tag}]", *args, flush=True)


# ── Background Task Helper ────────────────────────────────────────────────────
def spawn_background(target, *args, **kwargs):
    """Spawn a daemon thread for background tasks (threading-compatible)."""
    t = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    t.start()
    return t


# ── JSON File Helpers ─────────────────────────────────────────────────────────
def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Error] Failed to write {path}: {e}", flush=True)
        raise
