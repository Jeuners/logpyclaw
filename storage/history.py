"""
storage/history.py — Chat-History laden und speichern.
"""
import json
import os

from core.config import HISTORY_FILE, MAX_HISTORY_PER_AGENT, MAX_CONTENT_LENGTH
from core.state import _history_lock

# ── In-Memory Cache ───────────────────────────────────────────────────────────
_history_cache: dict | None = None


def _invalidate_history_cache():
    global _history_cache
    _history_cache = None


def load_history() -> dict:
    """Liest history.json — gecacht, Invalidierung bei jedem save_history()."""
    global _history_cache
    with _history_lock:
        if _history_cache is not None:
            return _history_cache
        if not os.path.exists(HISTORY_FILE):
            _history_cache = {}
            return _history_cache
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                _history_cache = json.load(f)
                return _history_cache
        except Exception:
            _history_cache = {}
            return _history_cache


def save_history(history: dict):
    """Speichert history.json atomar (tmp → replace). Trunciert zu lange Historien."""
    with _history_lock:
        for agent_id in history:
            msgs = history[agent_id]
            if len(msgs) > MAX_HISTORY_PER_AGENT:
                history[agent_id] = msgs[-MAX_HISTORY_PER_AGENT:]
            for msg in history[agent_id]:
                if (
                    isinstance(msg.get("content"), str)
                    and len(msg["content"]) > MAX_CONTENT_LENGTH
                ):
                    msg["content"] = msg["content"][:MAX_CONTENT_LENGTH] + " […]"
        tmp = HISTORY_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            os.replace(tmp, HISTORY_FILE)
            _history_cache = dict(history)  # Cache direkt aktualisieren statt invalidieren
        except Exception as e:
            print(f"[History] save_history Fehler: {e}", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
