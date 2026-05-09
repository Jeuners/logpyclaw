"""
lab/core/protocol.py — A2A/M2M Message-Spec für das Lab.

Klar getypte Messages mit garantierten Korrelations-IDs.
Jede Message gehört zu genau einer Mission und hat einen Trace-Pfad.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
import time
import uuid

# Core CDC importieren — Lab ist kein Duplikat, sondern Nutzer
from core.causal_dilation_clock import CausalDilationClock as _CoreCDC, CDCRelation


# ── Lifecycle States ──────────────────────────────────────────────────────────

class TaskState(str, Enum):
    CREATED   = "created"
    ASSIGNED  = "assigned"      # Task ist beim Empfänger angekommen, noch nicht gestartet
    RUNNING   = "running"
    WAITING   = "waiting"       # wartet auf Sub-Task
    COMPLETED = "completed"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELED  = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.FAILED, TaskState.TIMEOUT, TaskState.CANCELED)


class MessageType(str, Enum):
    REQUEST   = "request"      # "Mache X"
    RESPONSE  = "response"     # "X ist fertig, Result: ..."
    ERROR     = "error"        # "Konnte X nicht"
    HEARTBEAT = "heartbeat"    # "Lebe noch, arbeite an X"
    CANCEL    = "cancel"       # "Brich X ab"


# ── LabClock — Lab-Wrapper um core.CausalDilationClock ──────────────────────
# Kein Duplikat — nutzt die Core-Implementierung (vector + dilation/eigenzeit).
# Fügt Lab-spezifische Methoden hinzu: set_rate(), llm_summary(), to_dict().
#
# Core-CDC: dilation = kumulative eigenzeit (Σ op_weights) — unveränderlich
# Lab ergänzt: rate = ops/wall_sec — abgeleitet, wird separat im MockAgent
#              gehalten und beim tick() in die dilation-Map eingetragen.

class CausalDilationClock(_CoreCDC):
    """Core CDC + Lab-spezifische Methoden für Zeitgefühl + LLM-Lesbarkeit."""

    def tick_lab(self, agent_id: str, rate: float = 0.0) -> "CausalDilationClock":
        """Lab-Tick: Core-tick (Eigenzeit +1) + Rate in dilation speichern."""
        self.tick(agent_id, op_weight=1.0)
        if rate > 0:
            self.dilation[agent_id] = round(rate, 4)
        return self

    def merge_lab(self, other: "CausalDilationClock") -> "CausalDilationClock":
        """Merge eingehende Clock (in-place, wie Core) und gibt self zurück."""
        self.merge(other)
        return self

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["wall_ts"] = time.time()
        return d

    def llm_summary(self) -> str:
        """Kompakte Lesart für LLM: wer ist schnell, wer dehnt Zeit."""
        if not self.dilation:
            return "no temporal data"
        parts = []
        for agent, val in sorted(self.dilation.items(), key=lambda x: -x[1]):
            name = agent.replace("lab:", "")
            ez = self.vector.get(agent, 0)
            if val >= 2.0:   feel = "fast"
            elif val >= 0.8: feel = "normal"
            elif val >= 0.3: feel = "slow"
            else:            feel = "dilated"
            parts.append(f"{name}:{feel}(ez={ez},rate={val:.2f})")
        return " | ".join(parts)

    def relate_lab(self, other: "CausalDilationClock") -> str:
        """CDCRelation als String (für Trace + LLM)."""
        return self.relate(other).value


# ── IDs (immer mit Prefix damit nichts verwechselt wird) ──────────────────────

def new_task_id() -> str:
    return f"lab_t_{uuid.uuid4().hex[:10]}"

def new_msg_id() -> str:
    return f"lab_m_{uuid.uuid4().hex[:10]}"

def new_mission_id() -> str:
    return f"lab_mis_{uuid.uuid4().hex[:8]}"

def agent_id(name: str) -> str:
    """Gibt 'lab:martin' zurück — Prefix garantiert Trennung von echten Agenten."""
    return f"lab:{name}"


# ── Message ───────────────────────────────────────────────────────────────────

@dataclass
class Message:
    """Eine A2A-Nachricht. Trägt task_id (Korrelation), parent_task_id (Hierarchie)
    und clock (Causal-Dilation-Uhr für Zeitgefühl)."""
    msg_id: str
    mission_id: str
    task_id: str
    parent_task_id: str | None
    type: MessageType
    sender: str
    recipient: str
    payload: dict
    timestamp: float = field(default_factory=time.time)
    clock: CausalDilationClock = field(default_factory=CausalDilationClock)

    @classmethod
    def request(cls, mission_id: str, sender: str, recipient: str,
                content: str, parent_task_id: str | None = None,
                task_id: str | None = None,
                clock: CausalDilationClock | None = None) -> "Message":
        return cls(
            msg_id=new_msg_id(),
            mission_id=mission_id,
            task_id=task_id or new_task_id(),
            parent_task_id=parent_task_id,
            type=MessageType.REQUEST,
            sender=sender,
            recipient=recipient,
            payload={"content": content},
            clock=clock or CausalDilationClock(),
        )

    @classmethod
    def response(cls, request_msg: "Message", result: Any,
                 clock: CausalDilationClock | None = None) -> "Message":
        return cls(
            msg_id=new_msg_id(),
            mission_id=request_msg.mission_id,
            task_id=request_msg.task_id,
            parent_task_id=request_msg.parent_task_id,
            type=MessageType.RESPONSE,
            sender=request_msg.recipient,
            recipient=request_msg.sender,
            payload={"result": result},
            clock=clock or CausalDilationClock(),
        )

    @classmethod
    def error(cls, request_msg: "Message", reason: str,
              clock: CausalDilationClock | None = None) -> "Message":
        return cls(
            msg_id=new_msg_id(),
            mission_id=request_msg.mission_id,
            task_id=request_msg.task_id,
            parent_task_id=request_msg.parent_task_id,
            type=MessageType.ERROR,
            sender=request_msg.recipient,
            recipient=request_msg.sender,
            payload={"reason": reason},
            clock=clock or CausalDilationClock(),
        )

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "type": self.type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "clock": self.clock.to_dict(),
        }


# ── Task-Record (intern beim Empfänger gehalten) ──────────────────────────────

@dataclass
class TaskRecord:
    task_id: str
    mission_id: str
    parent_task_id: str | None
    owner: str                  # welcher Agent verarbeitet ihn
    requester: str              # wer hat den Task geschickt
    content: str
    state: TaskState = TaskState.CREATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    last_heartbeat: float = field(default_factory=time.time)
    result: Any = None
    error: str | None = None
    sub_task_ids: list[str] = field(default_factory=list)   # Tasks die dieser hier delegiert hat

    def transition(self, new_state: TaskState) -> None:
        self.state = new_state
        self.updated_at = time.time()
        if new_state == TaskState.RUNNING and self.started_at is None:
            self.started_at = time.time()
        if new_state.is_terminal and self.finished_at is None:
            self.finished_at = time.time()

    def heartbeat(self) -> None:
        self.last_heartbeat = time.time()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d
