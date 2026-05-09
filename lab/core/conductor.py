"""
lab/core/conductor.py — Mission-Orchestrator + Watchdog.

Verantwortet:
1. Mission starten (initialer Task an Start-Agent)
2. Watchdog-Loop: Tasks ohne Heartbeat in N Sekunden → TIMEOUT
3. Mission-Ende erkennen (root-task ist terminal)
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass

from . import store, tracer
from .protocol import (
    Message, TaskState, agent_id, new_mission_id
)


@dataclass
class MissionSpec:
    """Definition einer Test-Mission."""
    title: str
    start_agent: str           # Name OHNE lab:-Prefix
    initial_content: str
    timeout_sec: float = 60.0
    heartbeat_timeout_sec: float = 15.0   # Task gilt als hängend nach so vielen s ohne Heartbeat


@dataclass
class Mission:
    id: str
    spec: MissionSpec
    root_task_id: str
    started_at: float
    finished_at: float | None = None
    final_state: str = "running"   # running | completed | failed | timeout
    final_result: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.spec.title,
            "start_agent": agent_id(self.spec.start_agent),
            "initial_content": self.spec.initial_content,
            "root_task_id": self.root_task_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "final_state": self.final_state,
            "final_result": self.final_result,
            "timeout_sec": self.spec.timeout_sec,
            "heartbeat_timeout_sec": self.spec.heartbeat_timeout_sec,
        }


class Conductor:
    """Singleton — startet Missionen + Watchdog läuft im Hintergrund."""

    _instance: "Conductor | None" = None

    def __init__(self):
        self._missions: dict[str, Mission] = {}
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._lock = threading.Lock()

    @classmethod
    def get(cls) -> "Conductor":
        if cls._instance is None:
            cls._instance = Conductor()
            cls._instance._start_watchdog()
        return cls._instance

    # ── Mission starten ───────────────────────────────────────────────────

    def start_mission(self, spec: MissionSpec) -> Mission:
        start_id = agent_id(spec.start_agent)
        agent = store.get_agent(start_id)
        if agent is None:
            raise ValueError(f"Start-Agent {start_id} nicht registriert")

        mission_id = new_mission_id()
        # Initiale Request-Message — sender = "lab:_user" um den Conductor/User zu kennzeichnen
        msg = Message.request(
            mission_id=mission_id,
            sender="lab:_user",
            recipient=start_id,
            content=spec.initial_content,
        )

        mission = Mission(
            id=mission_id,
            spec=spec,
            root_task_id=msg.task_id,
            started_at=time.time(),
        )
        with self._lock:
            self._missions[mission_id] = mission
        store.register_mission(mission_id, mission.to_dict())

        store.record_message(msg)
        tracer.emit(mission_id, "mission_started",
                    mission_id=mission_id, title=spec.title,
                    start_agent=start_id, root_task=msg.task_id)
        tracer.emit(mission_id, "message",
                    msg_id=msg.msg_id, type=msg.type.value,
                    sender=msg.sender, recipient=msg.recipient,
                    task_id=msg.task_id, payload=msg.payload)

        agent.receive(msg)
        return mission

    def get_mission(self, mission_id: str) -> Mission | None:
        with self._lock:
            return self._missions.get(mission_id)

    def list_missions(self) -> list[Mission]:
        with self._lock:
            return list(self._missions.values())

    # ── Watchdog ──────────────────────────────────────────────────────────

    def _start_watchdog(self) -> None:
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="LabWatchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._watchdog_stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[lab.conductor] Watchdog error: {e}", flush=True)
            self._watchdog_stop.wait(1.0)

    def _tick(self) -> None:
        now = time.time()
        with self._lock:
            missions = list(self._missions.values())

        for mission in missions:
            if mission.finished_at is not None:
                continue

            # Mission-Timeout?
            if now - mission.started_at > mission.spec.timeout_sec:
                self._finish_mission(mission, "timeout", "mission timeout")
                continue

            # Hängende Tasks finden
            tasks = store.list_tasks(mission.id)
            for task in tasks:
                if task.state.is_terminal:
                    continue
                if task.state == TaskState.WAITING:
                    continue  # Delegator wartet absichtlich auf Sub-Task
                if now - task.last_heartbeat > mission.spec.heartbeat_timeout_sec:
                    task.transition(TaskState.TIMEOUT)
                    task.error = "heartbeat timeout"
                    store.upsert_task(task)
                    tracer.emit(mission.id, "task_timeout",
                                task_id=task.task_id, agent=task.owner,
                                age=now - task.last_heartbeat)
                    # Delegator-Parent benachrichtigen damit er nicht ewig wartet
                    if task.parent_task_id and task.requester:
                        from .protocol import Message, MessageType, new_msg_id
                        parent_owner = store.get_agent(task.requester)
                        if parent_owner:
                            err_msg = Message(
                                msg_id=new_msg_id(),
                                mission_id=task.mission_id,
                                task_id=task.task_id,
                                parent_task_id=task.parent_task_id,
                                type=MessageType.ERROR,
                                sender=task.owner,
                                recipient=task.requester,
                                payload={"reason": "sub-task timeout"},
                            )
                            store.record_message(err_msg)
                            tracer.emit(mission.id, "synthetic_error",
                                        task_id=task.task_id,
                                        recipient=task.requester,
                                        reason="sub-task timeout")
                            parent_owner.receive(err_msg)

            # Root-Task fertig?
            root = store.get_task(mission.root_task_id)
            if root and root.state.is_terminal:
                state_name = root.state.value
                result = root.result if root.state == TaskState.COMPLETED else (root.error or "")
                self._finish_mission(mission, state_name, result)

    def _finish_mission(self, mission: Mission, state: str, result: str) -> None:
        mission.finished_at = time.time()
        mission.final_state = state
        mission.final_result = result
        store.register_mission(mission.id, mission.to_dict())
        tracer.emit(mission.id, "mission_finished",
                    mission_id=mission.id, state=state, result=result,
                    duration=mission.finished_at - mission.started_at)
