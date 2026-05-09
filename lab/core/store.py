"""
lab/core/store.py — In-Memory State für das Lab.
NIEMALS persistiert, NIEMALS in echte DB. Reset bei App-Neustart ist gewollt.
"""
from __future__ import annotations
from threading import RLock
from typing import TYPE_CHECKING

from .protocol import Message, TaskRecord

if TYPE_CHECKING:
    from .mock_agent import MockAgent

# Globaler Lock für alle Mutations — wir bleiben einfach und sicher
_lock = RLock()

# agent_id ("lab:martin") → MockAgent
AGENTS: dict[str, "MockAgent"] = {}

# task_id ("lab_t_...") → TaskRecord
TASKS: dict[str, TaskRecord] = {}

# mission_id → list[Message]  — chronologische Trace
TRACES: dict[str, list[Message]] = {}

# mission_id → metadata dict
MISSIONS: dict[str, dict] = {}


def reset() -> None:
    """Komplett alles zurücksetzen — nur für Tests / UI-Reset-Button."""
    with _lock:
        # Erst alle Agenten stoppen
        for agent in list(AGENTS.values()):
            try:
                agent.stop()
            except Exception:
                pass
        AGENTS.clear()
        TASKS.clear()
        TRACES.clear()
        MISSIONS.clear()


def with_lock():
    """Context-Manager für atomare Mehrfach-Mutations."""
    return _lock


def record_message(msg: Message) -> None:
    """Append-only Trace pro Mission."""
    with _lock:
        TRACES.setdefault(msg.mission_id, []).append(msg)


def get_trace(mission_id: str) -> list[Message]:
    with _lock:
        return list(TRACES.get(mission_id, []))


def upsert_task(task: TaskRecord) -> None:
    with _lock:
        TASKS[task.task_id] = task


def get_task(task_id: str) -> TaskRecord | None:
    with _lock:
        return TASKS.get(task_id)


def list_tasks(mission_id: str | None = None) -> list[TaskRecord]:
    with _lock:
        if mission_id is None:
            return list(TASKS.values())
        return [t for t in TASKS.values() if t.mission_id == mission_id]


def register_agent(agent: "MockAgent") -> None:
    with _lock:
        if agent.id in AGENTS:
            raise ValueError(f"Agent {agent.id} existiert bereits")
        AGENTS[agent.id] = agent


def remove_agent(agent_id: str) -> None:
    with _lock:
        agent = AGENTS.pop(agent_id, None)
        if agent:
            try:
                agent.stop()
            except Exception:
                pass


def get_agent(agent_id: str) -> "MockAgent | None":
    with _lock:
        return AGENTS.get(agent_id)


def list_agents() -> list["MockAgent"]:
    with _lock:
        return list(AGENTS.values())


def register_mission(mission_id: str, meta: dict) -> None:
    with _lock:
        MISSIONS[mission_id] = meta


def get_mission(mission_id: str) -> dict | None:
    with _lock:
        return MISSIONS.get(mission_id)


def list_missions() -> list[dict]:
    with _lock:
        return list(MISSIONS.values())
