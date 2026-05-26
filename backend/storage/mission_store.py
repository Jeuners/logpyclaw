"""
backend/storage/mission_store.py — In-Memory Mission-State.

Append-only Traces, Task-State, Mission-Metadaten, SSE-Tracer.
Kein Disk-Persist — nach Neustart leer. Für Persistenz: Phase 3 DB-Layer.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict

from backend.core.protocol import Message, TaskRecord


class MissionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # mission_id → list[Message] (append-only)
        self._traces: dict[str, list[Message]] = defaultdict(list)
        # task_id → TaskRecord
        self._tasks: dict[str, TaskRecord] = {}
        # mission_id → metadata dict
        self._missions: dict[str, dict] = {}
        # SSE: mission_id → list[asyncio.Queue]
        self._subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)

    # ── Missions ─────────────────────────────────────────────────────────────

    def register_mission(self, mission_id: str, metadata: dict) -> None:
        with self._lock:
            self._missions[mission_id] = {**metadata, "registered_at": time.time()}

    def get_mission(self, mission_id: str) -> dict | None:
        return self._missions.get(mission_id)

    def list_missions(self) -> list[dict]:
        return list(self._missions.values())

    def update_mission(self, mission_id: str, **kwargs) -> None:
        with self._lock:
            if mission_id in self._missions:
                self._missions[mission_id].update(kwargs)

    def delete_mission(self, mission_id: str) -> bool:
        """Löscht Mission inkl. Trace, Tasks, Subscriber-Queues."""
        with self._lock:
            existed = mission_id in self._missions
            self._missions.pop(mission_id, None)
            self._traces.pop(mission_id, None)
            for tid in [t.task_id for t in self._tasks.values() if t.mission_id == mission_id]:
                self._tasks.pop(tid, None)
            self._subscribers.pop(mission_id, None)
        return existed

    def delete_stale_missions(self, states: tuple[str, ...] = ("running", "timeout", "failed"),
                              min_age_sec: float = 600.0) -> list[str]:
        """Löscht Missionen in den angegebenen States, älter als min_age_sec.

        Default: hängende ('running' > 10min) + alle timeout/failed.
        """
        now = time.time()
        to_delete: list[str] = []
        with self._lock:
            for mid, meta in self._missions.items():
                state = meta.get("state")
                if state not in states:
                    continue
                started = meta.get("started_at", now)
                if (now - started) >= min_age_sec:
                    to_delete.append(mid)
        for mid in to_delete:
            self.delete_mission(mid)
        return to_delete

    # ── Traces ────────────────────────────────────────────────────────────────

    def record_message(self, msg: Message) -> None:
        with self._lock:
            self._traces[msg.mission_id].append(msg)
        d = msg.to_dict()
        d.pop("mission_id", None)  # bereits als erster _emit-Parameter übergeben
        self._emit(msg.mission_id, "message", **d)

    def get_trace(self, mission_id: str) -> list[Message]:
        with self._lock:
            return list(self._traces.get(mission_id, []))

    # ── Tasks ─────────────────────────────────────────────────────────────────

    def upsert_task(self, task: TaskRecord) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
        self._emit(
            task.mission_id,
            f"task_{task.state.value}",
            task_id=task.task_id,
            state=task.state.value,
            owner=task.owner,
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def list_tasks(self, mission_id: str) -> list[TaskRecord]:
        with self._lock:
            return [t for t in self._tasks.values() if t.mission_id == mission_id]

    # ── SSE Tracer ────────────────────────────────────────────────────────────

    def subscribe(self, mission_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers[mission_id].append(q)
        return q

    def unsubscribe(self, mission_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(mission_id, [])
            if q in subs:
                subs.remove(q)

    def emit_token(self, mission_id: str, task_id: str, token: str) -> None:
        """Streamt ein LLM-Token live an alle SSE-Subscriber."""
        self._emit(mission_id, "thinking_token", task_id=task_id, token=token)

    def emit_step_progress(
        self,
        mission_id: str,
        step_idx: int,
        total: int,
        agent_id: str,
        state: str,  # "started" | "completed" | "failed"
        result: str = "",
    ) -> None:
        """Streamt Zwischen-Updates während Multi-Step-Plans (Martin)."""
        self._emit(
            mission_id, "step_progress",
            step_idx=step_idx, total=total,
            agent_id=agent_id, state=state,
            result=result[:500],
        )

    def _emit(self, mission_id: str, event: str, **data) -> None:
        payload = {"event": event, "ts": time.time(), **data}
        with self._lock:
            queues = list(self._subscribers.get(mission_id, []))
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    # ── Reset ─────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        with self._lock:
            self._traces.clear()
            self._tasks.clear()
            self._missions.clear()
            self._subscribers.clear()
