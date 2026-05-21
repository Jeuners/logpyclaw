"""
backend/agents/conductor.py — Mission-Dispatcher.

Registriert Agenten, dispatcht CDC-Messages, überwacht Timeouts (Watchdog).
Asyncio-basiert — kein Threading.
"""
from __future__ import annotations

import asyncio
import time

from backend.core.protocol import (
    Message,
    MessageType,
    TaskRecord,
    TaskState,
    external_ref,
    new_mission_id,
)
from backend.storage.mission_store import MissionStore

_DEFAULT_TASK_TIMEOUT = 120.0
_WATCHDOG_INTERVAL    = 5.0


class Conductor:
    def __init__(self, store: MissionStore | None = None) -> None:
        self._agents: dict[str, object] = {}   # agent_id → AsyncAgent
        self.store = store or MissionStore()
        self._watchdog_task: asyncio.Task | None = None

    # ── Agenten-Registry ──────────────────────────────────────────────────────

    def register(self, agent) -> None:
        self._agents[agent.agent_id] = agent

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def get_agent(self, agent_id: str):
        return self._agents.get(agent_id)

    def list_agents(self) -> list:
        return list(self._agents.values())

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        for agent in self._agents.values():
            await agent.start()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        if self._watchdog_task:
            self._watchdog_task.cancel()
        for agent in self._agents.values():
            await agent.stop()

    # ── Mission starten ───────────────────────────────────────────────────────

    async def start_mission(
        self,
        title: str,
        start_agent_id: str,
        content: str,
        timeout_sec: float = _DEFAULT_TASK_TIMEOUT,
    ) -> dict:
        mission_id = new_mission_id()
        self.store.register_mission(mission_id, {
            "mission_id": mission_id,
            "title":      title,
            "state":      "running",
            "started_at": time.time(),
            "timeout_sec": timeout_sec,
        })
        msg = Message.request(
            mission_id=mission_id,
            sender=external_ref("user"),
            recipient=start_agent_id,
            content=content,
        )
        result_msg = await self.dispatch(msg)
        state = "completed" if result_msg.type == MessageType.RESPONSE else "failed"
        self.store.update_mission(mission_id, state=state, finished_at=time.time())
        return {
            "mission_id": mission_id,
            "state":      state,
            "result":     result_msg.payload,
        }

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def dispatch(self, msg: Message) -> Message:
        """Leite eine CDC-Message an den Empfänger-Agenten weiter."""
        self.store.record_message(msg)
        agent = self._agents.get(msg.recipient)
        if agent is None:
            return Message.error(msg, f"agent not found: {msg.recipient}")

        task = TaskRecord(
            task_id=msg.task_id,
            mission_id=msg.mission_id,
            parent_task_id=msg.parent_task_id,
            owner=msg.recipient,
            requester=msg.sender,
            content=msg.payload.get("content", ""),
        )
        task.transition(TaskState.ASSIGNED)
        self.store.upsert_task(task)

        task.transition(TaskState.RUNNING)
        self.store.upsert_task(task)

        try:
            response = await asyncio.wait_for(
                agent.handle(msg),
                timeout=_DEFAULT_TASK_TIMEOUT,
            )
        except TimeoutError:
            task.transition(TaskState.TIMEOUT)
            self.store.upsert_task(task)
            return Message.error(msg, "task timeout")
        except Exception as e:
            task.transition(TaskState.FAILED)
            task.error = str(e)
            self.store.upsert_task(task)
            return Message.error(msg, str(e))

        final_state = (
            TaskState.COMPLETED if response.type == MessageType.RESPONSE
            else TaskState.FAILED
        )
        task.transition(final_state)
        task.result = response.payload.get("result")
        self.store.record_message(response)  # response message first → JS sees it before task_completed
        self.store.upsert_task(task)
        return response

    # ── Watchdog ──────────────────────────────────────────────────────────────

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL)
            now = time.time()
            for task in self.store._tasks.values():
                if task.state.is_terminal:
                    continue
                age = now - task.last_heartbeat
                mission = self.store.get_mission(task.mission_id)
                timeout = mission.get("timeout_sec", _DEFAULT_TASK_TIMEOUT) if mission else _DEFAULT_TASK_TIMEOUT
                if age > timeout:
                    task.transition(TaskState.TIMEOUT)
                    self.store.upsert_task(task)
