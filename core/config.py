"""
core/config.py — Pfad-Konstanten, URLs, Hilfsfunktionen.
Importiert nur core/state.py (für _DEBUG_LOG).

Werte-Duplikate werden aus config.settings bezogen (Single Source of Truth).
Diese Datei bleibt als Fassade bestehen, damit alle bestehenden Imports weiter funktionieren.
"""
import os
import sys
import json
import threading

from core.state import _DEBUG_LOG
from config.settings import settings

# ── Pfad-Konfiguration ────────────────────────────────────────────────────────
# Im py2app-Bundle zeigt __file__ auf die .zip — CWD nutzen (gesetzt durch chdir in main_app.py)
BASE_DIR = (
    os.getcwd()
    if getattr(sys, "frozen", False)
    else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

# DATA_DIR kann via env überschrieben werden (Tests, alternative Deployments)
DATA_DIR       = os.environ.get("AGENTCLAW_DATA_DIR") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)  # Beim ersten Start automatisch anlegen
AGENTS_FILE    = os.path.join(DATA_DIR, "agents.json")
HISTORY_FILE   = os.path.join(DATA_DIR, "history.json")
PROVIDERS_FILE = os.path.join(DATA_DIR, "providers.json")
WATCHDOGS_FILE = os.path.join(DATA_DIR, "watchdogs.json")
TASKS_FILE     = os.path.join(DATA_DIR, "tasks.json")
NODES_FILE     = os.path.join(DATA_DIR, "nodes.json")
BACKUP_DIR     = os.path.join(BASE_DIR, "backups")

# ── API URLs — aus config.settings (Single Source of Truth) ──────────────────
MISTRAL_TTS_URL     = settings.MISTRAL_TTS_URL
MISTRAL_VOICES_URL  = settings.MISTRAL_VOICES_URL
OPENROUTER_BASE_URL = settings.OPENROUTER_BASE_URL
GOOGLE_TTS_URL      = settings.GOOGLE_TTS_URL

# ── Embedding Config — aus config.settings ────────────────────────────────────
EMBED_MODEL = settings.EMBED_MODEL
EMBED_DIM   = settings.EMBED_DIM

# ── History Limits — aus config.settings ──────────────────────────────────────
MAX_HISTORY_PER_AGENT = settings.MAX_HISTORY_PER_AGENT
MAX_CONTENT_LENGTH    = settings.MAX_CONTENT_LENGTH


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
