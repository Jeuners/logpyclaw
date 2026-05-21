"""tests/test_sqlite_store.py — Tests für SQLite-Persistenz-Layer."""
from __future__ import annotations

import pytest

from backend.core.protocol import (
    Message,
    TaskRecord,
    TaskState,
    external_ref,
    new_mission_id,
    new_task_id,
)
from backend.storage.sqlite_store import PersistentMissionStore, make_store


@pytest.fixture
def db(tmp_path):
    db_url = f"sqlite:///{tmp_path}/test.db"
    return PersistentMissionStore(db_url), db_url


class TestPersistentMissionStore:
    def test_register_and_get_mission(self, db):
        store, _ = db
        mid = new_mission_id()
        store.register_mission(mid, {"mission_id": mid, "title": "test", "state": "running"})
        result = store.get_mission(mid)
        assert result is not None
        assert result["title"] == "test"

    def test_mission_survives_reload(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/persist.db"
        mid = new_mission_id()

        store1 = PersistentMissionStore(db_url)
        store1.register_mission(mid, {"mission_id": mid, "title": "survived", "state": "done"})

        store2 = PersistentMissionStore(db_url)
        loaded = store2.get_mission(mid)
        assert loaded is not None
        assert loaded["title"] == "survived"

    def test_update_mission_persists(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/update.db"
        mid = new_mission_id()

        store1 = PersistentMissionStore(db_url)
        store1.register_mission(mid, {"mission_id": mid, "title": "init", "state": "running"})
        store1.update_mission(mid, state="completed")

        store2 = PersistentMissionStore(db_url)
        loaded = store2.get_mission(mid)
        assert loaded["state"] == "completed"

    def test_messages_survive_reload(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/msgs.db"
        mid = new_mission_id()

        store1 = PersistentMissionStore(db_url)
        store1.register_mission(mid, {"mission_id": mid, "title": "msgs"})
        msg = Message.request(
            mission_id=mid,
            sender=external_ref("test"),
            recipient="agent:alice",
            content="hello persistent world",
        )
        store1.record_message(msg)

        store2 = PersistentMissionStore(db_url)
        trace = store2.get_trace(mid)
        assert len(trace) == 1
        assert trace[0].payload.get("content") == "hello persistent world"

    def test_tasks_survive_reload(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/tasks.db"
        mid = new_mission_id()

        store1 = PersistentMissionStore(db_url)
        store1.register_mission(mid, {"mission_id": mid, "title": "tasks"})
        task = TaskRecord(
            task_id=new_task_id(),
            mission_id=mid,
            parent_task_id=None,
            owner="agent:alice",
            requester=external_ref("test"),
            content="task content",
        )
        task.transition(TaskState.COMPLETED)
        store1.upsert_task(task)

        store2 = PersistentMissionStore(db_url)
        tasks = store2.list_tasks(mid)
        assert len(tasks) == 1
        assert tasks[0].state == TaskState.COMPLETED
        assert tasks[0].content == "task content"

    def test_list_missions(self, db):
        store, _ = db
        for i in range(3):
            mid = new_mission_id()
            store.register_mission(mid, {"mission_id": mid, "title": f"m{i}"})
        assert len(store.list_missions()) == 3

    def test_sse_subscribe_still_works(self, db):
        store, _ = db
        mid = new_mission_id()
        store.register_mission(mid, {"mission_id": mid, "title": "sse"})
        q = store.subscribe(mid)
        assert q is not None
        store.unsubscribe(mid, q)


class TestMakeStore:
    def test_memory_url_returns_base(self):
        store = make_store("sqlite:///:memory:")
        from backend.storage.mission_store import MissionStore
        assert type(store) is MissionStore

    def test_file_url_returns_persistent(self, tmp_path):
        db_url = f"sqlite:///{tmp_path}/factory.db"
        store = make_store(db_url)
        assert isinstance(store, PersistentMissionStore)
