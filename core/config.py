"""
core/config.py — Pfad-Konstanten und Datei-I/O-Helpers.

Scope:
- BASE_DIR + DATA_DIR + abgeleitete *_FILE Pfade
- _read_json / _write_json (JSON-Atomkasten)
- dlog (conditional Debug-Logger)

Werte (URLs, Limits, Embed-Config) leben in `config.settings` (pydantic).
Thread-Helper liegen in `core.background`.

Re-Exports werden bewusst NICHT mehr hier gepflegt — jede Call-Site soll
direkt die Quelle importieren (vermeidet Fassaden-Drift).
Ausnahme: `spawn_background` wird als Legacy-Re-Export beibehalten, da
bereits ~7 Stellen darauf zugreifen.
"""
import json
import os
import sys

from core.state import _DEBUG_LOG

# ── Pfad-Konfiguration ────────────────────────────────────────────────────────
# Im py2app-Bundle zeigt __file__ auf die .zip — CWD nutzen (gesetzt durch chdir
# in main_app.py)
BASE_DIR = (
    os.getcwd()
    if getattr(sys, "frozen", False)
    else os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
)

# DATA_DIR kann via env überschrieben werden (Tests, alternative Deployments).
DATA_DIR = os.environ.get("AGENTCLAW_DATA_DIR") or os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

AGENTS_FILE    = os.path.join(DATA_DIR, "agents.json")
HISTORY_FILE   = os.path.join(DATA_DIR, "history.json")
PROVIDERS_FILE = os.path.join(DATA_DIR, "providers.json")
WATCHDOGS_FILE = os.path.join(DATA_DIR, "watchdogs.json")
TASKS_FILE     = os.path.join(DATA_DIR, "tasks.json")
NODES_FILE     = os.path.join(DATA_DIR, "nodes.json")
BACKUP_DIR     = os.path.join(BASE_DIR, "backups")


# ── Legacy-Re-Export ──────────────────────────────────────────────────────────
# Behalten, da bestehende Call-Sites darauf zugreifen (Aufwand/Nutzen).
# Neue Call-Sites sollten `from core.background import spawn_background` nutzen.
from core.background import spawn_background  # noqa: E402,F401


# ── Debug Logging ─────────────────────────────────────────────────────────────
def dlog(*args, tag="DEBUG"):
    """Conditional debug log — only prints when _DEBUG_LOG is True."""
    if _DEBUG_LOG:
        print(f"[{tag}]", *args, flush=True)


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
