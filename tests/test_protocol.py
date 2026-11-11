"""Tests für das CDC-native Message-Protokoll."""
import time

from backend.core.cdc import CausalDilationClock
from backend.core.protocol import (
    Message,
    MessageType,
    TaskRecord,
    TaskState,
    agent_ref,
    new_mission_id,
    new_msg_id,
    new_task_id,
)


class TestIDs:
    def test_task_id_prefix(self):
        assert new_task_id().startswith("t_")

    def test_msg_id_prefix(self):
        assert new_msg_id().startswith("m_")

    def test_mission_id_prefix(self):
        assert new_mission_id().startswith("mis_")

    def test_agent_ref(self):
        assert agent_ref("alice") == "agent:alice"

    def test_ids_are_unique(self):
        ids = {new_task_id() for _ in range(100)}
        assert len(ids) == 100


class TestMessageFactory:
    def setup_method(self):
        self.mid = new_mission_id()

    def test_request_fields(self):
        msg = Message.request(self.mid, "agent:alice", "agent:bob", "do X")
        assert msg.type == MessageType.REQUEST
        assert msg.sender == "agent:alice"
        assert msg.recipient == "agent:bob"
        assert msg.payload["content"] == "do X"
        assert msg.task_id.startswith("t_")
        assert isinstance(msg.clock, CausalDilationClock)

    def test_response_mirrors_task_id(self):
        req = Message.request(self.mid, "agent:alice", "agent:bob", "do X")
        res = Message.response(req, "done")
        assert res.task_id == req.task_id
        assert res.sender == req.recipient
        assert res.recipient == req.sender
        assert res.payload["result"] == "done"

    def test_error_mirrors_task_id(self):
        req = Message.request(self.mid, "agent:alice", "agent:bob", "do X")
        err = Message.error(req, "timeout")
        assert err.task_id == req.task_id
        assert err.type == MessageType.ERROR
        assert err.payload["reason"] == "timeout"

    def test_heartbeat_type(self):
        req = Message.request(self.mid, "agent:alice", "agent:bob", "do X")
        hb = Message.heartbeat(req, "50% done")
        assert hb.type == MessageType.HEARTBEAT
        assert hb.payload["progress"] == "50% done"

    def test_parent_task_id_propagated(self):
        parent_tid = new_task_id()
        req = Message.request(self.mid, "a", "b", "sub", parent_task_id=parent_tid)
        res = Message.response(req, "ok")
        assert res.parent_task_id == parent_tid

    def test_clock_carried(self):
        clock = CausalDilationClock()
        clock.tick("agent:alice")
        msg = Message.request(self.mid, "agent:alice", "agent:bob", "x", clock=clock)
        assert msg.clock.vector.get("agent:alice", 0) == 1


class TestClockDefaults:
    """Vergessene Clock darf die Kausalhistorie nicht abreißen (§ Clock-Defaults)."""

    def setup_method(self):
        self.mid = new_mission_id()
        clock = CausalDilationClock()
        clock.tick_with_rate("agent:alice", rate=2.0)
        clock.tick("agent:alice")
        self.req = Message.request(self.mid, "agent:alice", "agent:bob", "do X", clock=clock)

    def test_request_default_clock_is_fresh(self):
        """Root-Messages haben keine Historie → frische Clock ist korrekt."""
        msg = Message.request(self.mid, "agent:alice", "agent:bob", "do X")
        assert msg.clock.vector == {}

    def test_response_inherits_request_clock(self):
        res = Message.response(self.req, "done")
        assert res.clock.vector.get("agent:alice") == 2
        assert res.clock.dilation.get("agent:alice") == 2.0
        assert res.clock.tau.get("agent:alice") == 2.0

    def test_error_inherits_request_clock(self):
        err = Message.error(self.req, "boom")
        assert err.clock.vector.get("agent:alice") == 2

    def test_heartbeat_inherits_request_clock(self):
        hb = Message.heartbeat(self.req, "50%")
        assert hb.clock.vector.get("agent:alice") == 2

    def test_inherited_clock_is_a_copy(self):
        """Die geerbte Clock ist eine Kopie — kein Aliasing auf die Request-Clock."""
        res = Message.response(self.req, "done")
        res.clock.tick("agent:bob")
        assert "agent:bob" not in self.req.clock.vector

    def test_explicit_clock_wins(self):
        own = CausalDilationClock()
        own.tick("agent:bob")
        res = Message.response(self.req, "done", clock=own)
        assert res.clock.vector.get("agent:bob") == 1


class TestMessageSerialization:
    def test_round_trip(self):
        mid = new_mission_id()
        msg = Message.request(mid, "agent:alice", "agent:bob", "do X")
        msg.clock.tick("agent:alice")
        d = msg.to_dict()
        msg2 = Message.from_dict(d)
        assert msg2.task_id == msg.task_id
        assert msg2.type == msg.type
        assert msg2.clock.vector.get("agent:alice") == 1


class TestTaskState:
    def test_terminal_states(self):
        assert TaskState.COMPLETED.is_terminal
        assert TaskState.FAILED.is_terminal
        assert TaskState.TIMEOUT.is_terminal
        assert TaskState.CANCELED.is_terminal

    def test_non_terminal_states(self):
        assert not TaskState.CREATED.is_terminal
        assert not TaskState.RUNNING.is_terminal
        assert not TaskState.WAITING.is_terminal


class TestTaskRecord:
    def make_record(self) -> TaskRecord:
        return TaskRecord(
            task_id=new_task_id(),
            mission_id=new_mission_id(),
            parent_task_id=None,
            owner="agent:bob",
            requester="agent:alice",
            content="do X",
        )

    def test_initial_state(self):
        r = self.make_record()
        assert r.state == TaskState.CREATED
        assert r.started_at is None
        assert r.finished_at is None

    def test_transition_to_running_sets_started_at(self):
        r = self.make_record()
        r.transition(TaskState.RUNNING)
        assert r.started_at is not None

    def test_transition_to_terminal_sets_finished_at(self):
        r = self.make_record()
        r.transition(TaskState.RUNNING)
        r.transition(TaskState.COMPLETED)
        assert r.finished_at is not None

    def test_heartbeat_updates_timestamp(self):
        r = self.make_record()
        before = r.last_heartbeat
        time.sleep(0.01)
        r.heartbeat()
        assert r.last_heartbeat > before

    def test_to_dict_state_is_string(self):
        r = self.make_record()
        d = r.to_dict()
        assert d["state"] == "created"
