"""
backend/core/protocol.py — CDC-natives Message-Protokoll für LogpyClaw v3.

Jede Message trägt eine CausalDilationClock. CDC ist kein optionales Feld.
ID-Prefixe verhindern Verwechslungen zwischen Task-, Message- und Mission-IDs.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from backend.core.cdc import CausalDilationClock

# ── ID-Factories ─────────────────────────────────────────────────────────────

def new_task_id() -> str:
    return f"t_{uuid.uuid4().hex[:10]}"

def new_msg_id() -> str:
    return f"m_{uuid.uuid4().hex[:10]}"

def new_mission_id() -> str:
    return f"mis_{uuid.uuid4().hex[:8]}"

def new_team_id() -> str:
    return f"team_{uuid.uuid4().hex[:8]}"

def agent_ref(name: str) -> str:
    """Kanonische Agent-Referenz: 'agent:alice'. Präfix verhindert Kollisionen."""
    return f"agent:{name}"

def external_ref(name: str) -> str:
    """Externe Referenz (A2A-Gateway, User): 'ext:name'."""
    return f"ext:{name}"


# ── Enums ────────────────────────────────────────────────────────────────────

class TaskState(StrEnum):
    CREATED   = "created"
    ASSIGNED  = "assigned"
    RUNNING   = "running"
    WAITING   = "waiting"
    COMPLETED = "completed"
    FAILED    = "failed"
    TIMEOUT   = "timeout"
    CANCELED  = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            TaskState.COMPLETED, TaskState.FAILED,
            TaskState.TIMEOUT, TaskState.CANCELED,
        )


class MessageType(StrEnum):
    REQUEST   = "request"
    RESPONSE  = "response"
    ERROR     = "error"
    HEARTBEAT = "heartbeat"
    CANCEL    = "cancel"


# ── Message ───────────────────────────────────────────────────────────────────

@dataclass
class Message:
    """CDC-native A2A-Nachricht.

    Jede Message hat:
    - task_id          : Korrelations-ID (Request + Response teilen sie)
    - parent_task_id   : Delegations-Hierarchie (None = Root)
    - clock            : CausalDilationClock zum Sendezeitpunkt (Pflicht)
    """
    msg_id:         str
    mission_id:     str
    task_id:        str
    parent_task_id: str | None
    type:           MessageType
    sender:         str
    recipient:      str
    payload:        dict
    timestamp:      float                = field(default_factory=time.time)
    clock:          CausalDilationClock  = field(default_factory=CausalDilationClock)

    # ── Factory-Methoden ─────────────────────────────────────────────────────

    @classmethod
    def request(
        cls,
        mission_id: str,
        sender: str,
        recipient: str,
        content: str,
        parent_task_id: str | None = None,
        task_id: str | None = None,
        clock: CausalDilationClock | None = None,
    ) -> Message:
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
    def response(
        cls,
        request_msg: Message,
        result: Any,
        clock: CausalDilationClock | None = None,
    ) -> Message:
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
    def error(
        cls,
        request_msg: Message,
        reason: str,
        clock: CausalDilationClock | None = None,
    ) -> Message:
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

    @classmethod
    def heartbeat(
        cls,
        request_msg: Message,
        progress: str = "",
        clock: CausalDilationClock | None = None,
    ) -> Message:
        return cls(
            msg_id=new_msg_id(),
            mission_id=request_msg.mission_id,
            task_id=request_msg.task_id,
            parent_task_id=request_msg.parent_task_id,
            type=MessageType.HEARTBEAT,
            sender=request_msg.recipient,
            recipient=request_msg.sender,
            payload={"progress": progress},
            clock=clock or CausalDilationClock(),
        )

    def to_dict(self) -> dict:
        return {
            "msg_id":         self.msg_id,
            "mission_id":     self.mission_id,
            "task_id":        self.task_id,
            "parent_task_id": self.parent_task_id,
            "type":           self.type.value,
            "sender":         self.sender,
            "recipient":      self.recipient,
            "payload":        self.payload,
            "timestamp":      self.timestamp,
            "clock":          self.clock.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        return cls(
            msg_id=d["msg_id"],
            mission_id=d["mission_id"],
            task_id=d["task_id"],
            parent_task_id=d.get("parent_task_id"),
            type=MessageType(d["type"]),
            sender=d["sender"],
            recipient=d["recipient"],
            payload=d.get("payload", {}),
            timestamp=d.get("timestamp", time.time()),
            clock=CausalDilationClock.from_dict(d.get("clock", {})),
        )


# ── TaskRecord ────────────────────────────────────────────────────────────────

@dataclass
class TaskRecord:
    task_id:        str
    mission_id:     str
    parent_task_id: str | None
    owner:          str
    requester:      str
    content:        str
    state:          TaskState = TaskState.CREATED
    created_at:     float     = field(default_factory=time.time)
    updated_at:     float     = field(default_factory=time.time)
    started_at:     float | None = None
    finished_at:    float | None = None
    last_heartbeat: float     = field(default_factory=time.time)
    result:         Any       = None
    error:          str | None = None
    sub_task_ids:   list[str] = field(default_factory=list)

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
