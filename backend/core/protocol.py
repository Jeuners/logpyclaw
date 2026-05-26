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
    CREATED = "created"
    ASSIGNED = "assigned"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.TIMEOUT,
            TaskState.CANCELED,
        )


class MessageType(StrEnum):
    REQUEST = "request"
    RESPONSE = "response"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    CANCEL = "cancel"


# ── Message ───────────────────────────────────────────────────────────────────


@dataclass
class Message:
    """CDC-native A2A-Nachricht.

    Jede Message hat:
    - task_id          : Korrelations-ID (Request + Response teilen sie)
    - parent_task_id   : Delegations-Hierarchie (None = Root)
    - clock            : CausalDilationClock zum Sendezeitpunkt (Pflicht)
    """

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

    # ── PQC Audit-Trail (optional, ab Phase r9) ──
    # chain_idx:  0-basierter Index innerhalb der Mission
    # prev_hash:  SHA-256 hex der vorherigen Message (Genesis: "0"*64)
    # msg_hash:   SHA-256(prev_hash || canonical_payload) — diese Message
    # signer_id:  ID des Signer-Keypairs ("signer-<ts>")
    # sig:        base64 ML-DSA-65 Signatur über canonical_payload
    chain_idx: int | None = None
    prev_hash: str | None = None
    msg_hash:  str | None = None
    signer_id: str | None = None
    sig: str | None = None

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
        d = {
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
        if self.chain_idx is not None: d["chain_idx"] = self.chain_idx
        if self.prev_hash is not None: d["prev_hash"] = self.prev_hash
        if self.msg_hash  is not None: d["msg_hash"]  = self.msg_hash
        if self.signer_id is not None: d["signer_id"] = self.signer_id
        if self.sig       is not None: d["sig"]       = self.sig
        return d

    def signing_payload(self) -> bytes:
        """Was tatsächlich signiert wird — canonical über die Kern-Felder
        OHNE sig/msg_hash (zirkulär), aber MIT prev_hash + chain_idx
        (in die Chain integriert).

        Vom Clock nur vector + dilation — wall_ts ist Konvenienz, nicht
        Teil der kausalen Identität (wäre wegen time.time() instabil).
        """
        from backend.core.pqsign import canonical_json
        return canonical_json({
            "msg_id":    self.msg_id,
            "mission_id": self.mission_id,
            "task_id":   self.task_id,
            "parent_task_id": self.parent_task_id,
            "type":      self.type.value,
            "sender":    self.sender,
            "recipient": self.recipient,
            "payload":   self.payload,
            "timestamp": self.timestamp,
            "clock":     {
                "vector":   dict(self.clock.vector),
                "dilation": {k: float(v) for k, v in self.clock.dilation.items()},
            },
            "chain_idx": self.chain_idx,
            "prev_hash": self.prev_hash,
        })

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
            chain_idx=d.get("chain_idx"),
            prev_hash=d.get("prev_hash"),
            msg_hash=d.get("msg_hash"),
            signer_id=d.get("signer_id"),
            sig=d.get("sig"),
        )


# ── TaskRecord ────────────────────────────────────────────────────────────────


@dataclass
class TaskRecord:
    task_id: str
    mission_id: str
    parent_task_id: str | None
    owner: str
    requester: str
    content: str
    state: TaskState = TaskState.CREATED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    last_heartbeat: float = field(default_factory=time.time)
    result: Any = None
    error: str | None = None
    sub_task_ids: list[str] = field(default_factory=list)

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
