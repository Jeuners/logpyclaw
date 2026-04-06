"""
storage/history.py — Chat-History laden und speichern (kein Cache).
"""
import json
import os

from core.config import HISTORY_FILE, MAX_HISTORY_PER_AGENT, MAX_CONTENT_LENGTH
from core.state import _history_lock


def load_history() -> dict:
    """Liest history.json direkt von Disk — kein Cache (verhindert Stale-State nach Reset)."""
    with _history_lock:
        if not os.path.exists(HISTORY_FILE):
            return {}
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


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
        except Exception as e:
            print(f"[History] save_history Fehler: {e}", flush=True)
            try:
                os.remove(tmp)
            except OSError:
                pass
