"""
backend/agents/conductor.py — Mission-Dispatcher.

Registriert Agenten, dispatcht CDC-Messages, überwacht Timeouts (Watchdog).
Asyncio-basiert — kein Threading.
"""

from __future__ import annotations

import asyncio
import time

from backend.core.cdc import CDCRelation
from backend.core.faction_protocol import FactionRegistry, classify_drift
from backend.core.logging import get_logger
from backend.core.protocol import (
    Message,
    MessageType,
    TaskRecord,
    TaskState,
    external_ref,
    new_mission_id,
)
from backend.storage.mission_store import MissionStore
from backend.storage.sqlite_store import make_store

log = get_logger(__name__)

_DEFAULT_TASK_TIMEOUT = 900.0  # 15 min — LTX-Video braucht auf RTX 4070 ~10-12 min
_WATCHDOG_INTERVAL = 5.0
_MARTIN_ID = "agent:martin"  # Operator-Bridge für ADVERSARIAL-Verkehr


class Conductor:
    def __init__(self, store: MissionStore | None = None, db_url: str = "") -> None:
        self._agents: dict[str, object] = {}  # agent_id → AsyncAgent
        self.store = store or (make_store(db_url) if db_url else MissionStore())
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
        self.store.register_mission(
            mission_id,
            {
                "mission_id": mission_id,
                "title": title,
                "state": "running",
                "started_at": time.time(),
                "timeout_sec": timeout_sec,
            },
        )
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
            "state": state,
            "result": result_msg.payload,
        }

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def dispatch(self, msg: Message) -> Message:
        """Leite eine CDC-Message an den Empfänger-Agenten weiter.

        Verdrahtet das Fraktionssystem: FactionEnvelope wird vor der
        Zustellung gebaut, ADVERSARIAL-Verkehr über Martins Bridge
        umgeleitet, und nach Abschluss lernen Trust/γ automatisch.
        """
        registry = FactionRegistry.get()
        envelope = registry.build_envelope(msg.sender, msg.recipient)
        if envelope and "_faction" not in msg.payload:
            msg.payload["_faction"] = envelope.to_dict()

        # Bridge-Umleitung: ADVERSARIAL-Paare laufen über Martins Operator-Bridge.
        # Das _bridged-Flag verhindert Rekursion, falls die Umleitung selbst
        # wieder über dispatch() läuft.
        if (
            envelope
            and envelope.requires_bridge
            and msg.recipient != _MARTIN_ID
            and not msg.payload.get("_bridged")
        ):
            if _MARTIN_ID not in self._agents:
                # Fail-closed: ADVERSARIAL-Verkehr ohne verfügbare Bridge wird
                # abgelehnt statt still direkt zugestellt.
                log.error(
                    "Bridge nicht verfügbar: ADVERSARIAL %s → %s abgelehnt",
                    msg.sender,
                    msg.recipient,
                )
                return Message.error(msg, "bridge unavailable: adversarial traffic requires operator bridge")
            log.info(
                "Bridge-Umleitung %s → %s (stance=adversarial) via %s",
                msg.sender,
                msg.recipient,
                _MARTIN_ID,
            )
            msg.payload["_bridged"] = True
            msg.recipient = _MARTIN_ID

        # Async-Pfad: Signatur (CPU) + SQLite-Commit laufen via to_thread,
        # damit der Event-Loop (Token-Streaming!) nicht blockiert.
        await self.store.record_message_async(msg)
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
        await self.store.upsert_task_async(task)

        task.transition(TaskState.RUNNING)
        await self.store.upsert_task_async(task)

        # Per-Mission-Timeout aus den Mission-Metadaten lesen (gleiche Quelle wie
        # der Watchdog), Fallback auf den harten Default.
        mission = self.store.get_mission(msg.mission_id)
        timeout = (
            mission.get("timeout_sec", _DEFAULT_TASK_TIMEOUT)
            if mission
            else _DEFAULT_TASK_TIMEOUT
        )

        try:
            response = await asyncio.wait_for(
                agent.handle(msg),
                timeout=timeout,
            )
        except TimeoutError:
            task.transition(TaskState.TIMEOUT)
            await self.store.upsert_task_async(task)
            return Message.error(msg, "task timeout")
        except Exception as e:
            task.transition(TaskState.FAILED)
            task.error = str(e)
            await self.store.upsert_task_async(task)
            return Message.error(msg, str(e))

        final_state = (
            TaskState.COMPLETED if response.type == MessageType.RESPONSE else TaskState.FAILED
        )
        task.transition(final_state)
        task.result = response.payload.get("result")
        # Response-Message zuerst → JS sieht sie vor task_completed
        await self.store.record_message_async(response)
        await self.store.upsert_task_async(task)

        # Outcome-Feedback: Trust/γ lernen automatisch nach jeder Interaktion.
        # Bei externen Sendern ("ext:user") liefert faction_of None und
        # record_cross_faction_outcome returnt früh — das ist ok.
        sender_rate = msg.clock.dilation.get(msg.sender, 0.0)
        recipient_rate = response.clock.dilation.get(msg.recipient, 0.0)
        registry.record_cross_faction_outcome(
            msg.sender,
            msg.recipient,
            success=(response.type == MessageType.RESPONSE),
            sender_rate=sender_rate,
            recipient_rate=recipient_rate,
        )

        self._log_drift(msg, response, registry, sender_rate, recipient_rate)
        return response

    def _log_drift(
        self,
        msg: Message,
        response: Message,
        registry: FactionRegistry,
        sender_rate: float,
        recipient_rate: float,
    ) -> None:
        """Drift-Logging nach Abschluss — fraktions-bewusst klassifiziert."""
        rel = msg.clock.relate(response.clock)
        if rel is CDCRelation.ORDERED:
            return

        if rel is CDCRelation.INCONSISTENT:
            log.error(
                "CDC INCONSISTENT zwischen %s und %s (task=%s) — Clock-Korruption?",
                msg.sender,
                msg.recipient,
                msg.task_id,
            )
            return

        observed_ratio = sender_rate / recipient_rate if recipient_rate > 0 else 0.0
        label = classify_drift(
            rel,
            registry.faction_of(msg.sender),
            registry.faction_of(msg.recipient),
            observed_ratio,
            registry,
        )
        if label != rel.value:
            # EXPECTED_DRIFT / FACTION_RACE — strukturell erwartet, kein Alarm
            log.debug(
                "CDC %s zwischen %s und %s (task=%s, ratio=%.3f)",
                label,
                msg.sender,
                msg.recipient,
                msg.task_id,
                observed_ratio,
            )
        else:
            log.warning(
                "CDC %s zwischen %s und %s (task=%s)",
                label,
                msg.sender,
                msg.recipient,
                msg.task_id,
            )

    # ── Watchdog ──────────────────────────────────────────────────────────────

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(_WATCHDOG_INTERVAL)
            now = time.time()
            # Snapshot unter dem Store-Lock ziehen, damit paralleles upsert_task()
            # während dispatch() kein "dictionary changed size during iteration" wirft.
            with self.store._lock:
                tasks = list(self.store._tasks.values())
            for task in tasks:
                if task.state.is_terminal:
                    continue
                age = now - task.last_heartbeat
                mission = self.store.get_mission(task.mission_id)
                timeout = (
                    mission.get("timeout_sec", _DEFAULT_TASK_TIMEOUT)
                    if mission
                    else _DEFAULT_TASK_TIMEOUT
                )
                if age > timeout:
                    task.transition(TaskState.TIMEOUT)
                    await self.store.upsert_task_async(task)
