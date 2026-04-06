"""
storage/agents.py — Agent laden, speichern, patchen.
"""
import os
import shutil

from core.config import AGENTS_FILE, _read_json, _write_json
from core.state import _agents_lock


def load_agents() -> list:
    with _agents_lock:
        data = _read_json(AGENTS_FILE, None)
        if data is None:
            print("[Agent] WARN: agents.json fehlt, lade leere Liste", flush=True)
            return []
        return data


def save_agents(agents: list, create_backup: bool = True):
    with _agents_lock:
        if create_backup and os.path.exists(AGENTS_FILE):
            shutil.copy2(AGENTS_FILE, AGENTS_FILE + ".backup")

        temp_path = AGENTS_FILE + ".tmp"
        _write_json(temp_path, agents)

        # Verify
        try:
            verified = _read_json(temp_path, None)
            if verified is None:
                raise Exception("Verification failed - file is empty")
        except Exception as e:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise Exception(f"Save validation failed: {e}")

        try:
            os.replace(temp_path, AGENTS_FILE)
        except Exception as e:
            raise Exception(f"Failed to replace file: {e}")

        try:
            if create_backup and os.path.exists(AGENTS_FILE + ".backup"):
                os.remove(AGENTS_FILE + ".backup")
        except Exception:
            pass

        print(f"[Agent] Saved {len(agents)} agents successfully", flush=True)


def patch_agent_heartbeat(agent_id: str, **fields):
    """Atomically update heartbeat fields on one agent without a race.
    Holds _agents_lock across the entire read-modify-write cycle.
    """
    with _agents_lock:
        data = _read_json(AGENTS_FILE, None)
        if data is None:
            return
        for a in data:
            if a["id"] == agent_id:
                hb = a.setdefault("heartbeat", {})
                hb.update(fields)
                break
        temp_path = AGENTS_FILE + ".tmp"
        _write_json(temp_path, data)
        os.replace(temp_path, AGENTS_FILE)
