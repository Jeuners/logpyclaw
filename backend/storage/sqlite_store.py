"""
backend/storage/sqlite_store.py — SQLite-Persistenz-Layer via SQLModel.

PersistentMissionStore erweitert MissionStore: schreibt missions/traces/tasks
zusätzlich in SQLite und lädt sie beim Start zurück in den In-Memory-Cache.
SSE-Queues bleiben rein in-memory (sind transient).
"""

from __future__ import annotations

import json
import time

from sqlmodel import Field, Session, SQLModel, create_engine, select

from backend.core.protocol import Message, TaskRecord, TaskState
from backend.storage.mission_store import MissionStore

# ── SQLModel-Tabellen ─────────────────────────────────────────────────────────


class MissionRow(SQLModel, table=True):
    __tablename__ = "missions"
    mission_id: str = Field(primary_key=True)
    meta_json: str = Field(default="{}")  # JSON-serialisierte Metadaten


class MessageRow(SQLModel, table=True):
    __tablename__ = "messages"
    id: int | None = Field(default=None, primary_key=True)
    mission_id: str = Field(index=True)
    msg_json: str  # JSON-serialisierte Message


class TaskRow(SQLModel, table=True):
    __tablename__ = "tasks"
    task_id: str = Field(primary_key=True)
    mission_id: str = Field(index=True)
    task_json: str  # JSON-serialisierter TaskRecord


# ── PersistentMissionStore ────────────────────────────────────────────────────


class PersistentMissionStore(MissionStore):
    """MissionStore mit SQLite-Backend.

    Alle Schreibvorgänge landen zusätzlich in der DB.
    Beim __init__ wird der in-memory-Cache aus der DB befüllt.
    """

    def __init__(self, db_url: str) -> None:
        super().__init__()
        self._engine = create_engine(db_url, connect_args={"check_same_thread": False})
        SQLModel.metadata.create_all(self._engine)
        self._load_from_db()

    # ── Boot: DB → Memory ────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        with Session(self._engine) as session:
            for row in session.exec(select(MissionRow)).all():
                meta = json.loads(row.meta_json)
                self._missions[row.mission_id] = meta

            for row in session.exec(select(MessageRow)).all():
                msg = Message.from_dict(json.loads(row.msg_json))
                self._traces[row.mission_id].append(msg)

            for row in session.exec(select(TaskRow)).all():
                task_data = json.loads(row.task_json)
                task = _task_from_dict(task_data)
                self._tasks[task.task_id] = task

    # ── Overrides: Memory + DB ────────────────────────────────────────────────

    def register_mission(self, mission_id: str, metadata: dict) -> None:
        super().register_mission(mission_id, metadata)
        self._db_upsert_mission(mission_id, self._missions[mission_id])

    def update_mission(self, mission_id: str, **kwargs) -> None:
        super().update_mission(mission_id, **kwargs)
        self._db_upsert_mission(mission_id, self._missions.get(mission_id, {}))

    def delete_mission(self, mission_id: str) -> bool:
        existed = super().delete_mission(mission_id)
        if existed:
            self._db_delete_mission(mission_id)
        return existed

    def record_message(self, msg: Message) -> None:
        super().record_message(msg)
        self._db_insert_message(msg)

    def upsert_task(self, task: TaskRecord) -> None:
        super().upsert_task(task)
        self._db_upsert_task(task)

    # ── Sync DB-Writes (laufen im Thread-Pool) ────────────────────────────────

    def _db_upsert_mission(self, mission_id: str, meta: dict) -> None:
        with Session(self._engine) as session:
            existing = session.get(MissionRow, mission_id)
            if existing:
                existing.meta_json = json.dumps(meta, default=str)
            else:
                session.add(
                    MissionRow(mission_id=mission_id, meta_json=json.dumps(meta, default=str))
                )
            session.commit()

    def _db_delete_mission(self, mission_id: str) -> None:
        with Session(self._engine) as session:
            mission = session.get(MissionRow, mission_id)
            if mission:
                session.delete(mission)
            for row in session.exec(select(MessageRow).where(MessageRow.mission_id == mission_id)).all():
                session.delete(row)
            for row in session.exec(select(TaskRow).where(TaskRow.mission_id == mission_id)).all():
                session.delete(row)
            session.commit()

    def _db_insert_message(self, msg: Message) -> None:
        with Session(self._engine) as session:
            session.add(
                MessageRow(
                    mission_id=msg.mission_id,
                    msg_json=json.dumps(msg.to_dict()),
                )
            )
            session.commit()

    def _db_upsert_task(self, task: TaskRecord) -> None:
        with Session(self._engine) as session:
            existing = session.get(TaskRow, task.task_id)
            data = json.dumps(_task_to_dict(task))
            if existing:
                existing.task_json = data
            else:
                session.add(
                    TaskRow(
                        task_id=task.task_id,
                        mission_id=task.mission_id,
                        task_json=data,
                    )
                )
            session.commit()


# ── Helpers: TaskRecord ↔ dict ────────────────────────────────────────────────


def _task_to_dict(task: TaskRecord) -> dict:
    return {
        "task_id": task.task_id,
        "mission_id": task.mission_id,
        "parent_task_id": task.parent_task_id,
        "owner": task.owner,
        "requester": task.requester,
        "content": task.content,
        "state": task.state.value,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "last_heartbeat": task.last_heartbeat,
        "sub_task_ids": list(task.sub_task_ids),
    }


def _task_from_dict(d: dict) -> TaskRecord:
    task = TaskRecord(
        task_id=d["task_id"],
        mission_id=d["mission_id"],
        parent_task_id=d.get("parent_task_id"),
        owner=d["owner"],
        requester=d["requester"],
        content=d["content"],
    )
    task.state = TaskState(d["state"])
    task.created_at = d.get("created_at", time.time())
    task.started_at = d.get("started_at")
    task.finished_at = d.get("finished_at")
    task.last_heartbeat = d.get("last_heartbeat")
    task.sub_task_ids = set(d.get("sub_task_ids", []))
    return task


# ── Factory ───────────────────────────────────────────────────────────────────


def make_store(db_url: str) -> MissionStore:
    """Gibt PersistentMissionStore für SQLite-URLs, sonst In-Memory zurück."""
    if db_url == "sqlite:///:memory:" or not db_url.startswith("sqlite"):
        return MissionStore()
    return PersistentMissionStore(db_url)
