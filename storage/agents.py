"""
storage/agents.py — Agent laden, speichern, patchen.
"""
import json
import logging
import os
import shutil

from core.config import AGENTS_FILE, _read_json, _write_json
from core.state import _agents_lock

logger = logging.getLogger(__name__)

# ── In-Memory Cache ───────────────────────────────────────────────────────────
_agents_cache: list | None = None


def _invalidate_agents_cache():
    global _agents_cache
    _agents_cache = None


def load_agents() -> list:
    global _agents_cache
    with _agents_lock:
        if _agents_cache is not None:
            return _agents_cache
        data = _read_json(AGENTS_FILE, None)
        if data is None:
            print("[Agent] WARN: agents.json fehlt, lade leere Liste", flush=True)
            return []
        _agents_cache = data
        return _agents_cache


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

        _invalidate_agents_cache()

        try:
            if create_backup and os.path.exists(AGENTS_FILE + ".backup"):
                os.remove(AGENTS_FILE + ".backup")
        except Exception:
            pass

        print(f"[Agent] Saved {len(agents)} agents successfully", flush=True)

    # SQLite synchron halten (außerhalb des _agents_lock)
    _sync_to_sqlite(agents)


def _sync_to_sqlite(agents: list) -> None:
    """Hält SQLite skills_json, model, provider und soul mit agents.json synchron."""
    try:
        from storage.database import get_session, AgentDB
        from sqlmodel import select
        with get_session() as session:
            for a in agents:
                row = session.exec(select(AgentDB).where(AgentDB.id == a["id"])).first()
                if row:
                    row.skills_json = json.dumps(a.get("skills", []))
                    row.model       = a.get("model", row.model)
                    row.provider    = a.get("provider", row.provider)
                    row.soul        = a.get("system_prompt", row.soul or "")
                    session.add(row)
            session.commit()
        logger.debug("SQLite sync: %d Agenten aktualisiert", len(agents))
    except Exception as e:
        logger.warning("SQLite sync fehlgeschlagen (nicht kritisch): %s", e)


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
        _invalidate_agents_cache()
