"""Tests für AsyncAgent Basis + Conductor + A2A-Gateway."""

import pytest

from backend.agents.a2a_gateway import A2AGatewayAgent
from backend.agents.base import AsyncAgent
from backend.agents.conductor import Conductor
from backend.core.cdc import CausalDilationClock
from backend.core.protocol import Message, MessageType, new_mission_id

# ── Hilfs-Agent für Tests ────────────────────────────────────────────────────

class EchoAgent(AsyncAgent):
    """Gibt den empfangenen Content als Response zurück."""
    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, f"echo: {msg.payload.get('content')}", clock=clock)

class ErrorAgent(AsyncAgent):
    """Wirft immer einen Fehler."""
    async def handle(self, msg: Message) -> Message:
        raise RuntimeError("intentional error")


# ── AsyncAgent Base ───────────────────────────────────────────────────────────

class TestAsyncAgent:
    def test_initial_clock_empty(self):
        a = EchoAgent("agent:echo", "Echo")
        assert a.clock.vector == {}

    @pytest.mark.asyncio
    async def test_advance_clock_ticks_self(self):
        a = EchoAgent("agent:echo", "Echo")
        await a.start()
        clk = a.advance_clock()
        assert clk.vector.get("agent:echo", 0) == 1

    def test_advance_clock_merges_incoming(self):
        a = EchoAgent("agent:echo", "Echo")
        incoming = CausalDilationClock(vector={"agent:sender": 5}, dilation={"agent:sender": 5.0})
        clk = a.advance_clock(incoming)
        assert clk.vector.get("agent:sender") == 5

    def test_to_dict(self):
        a = EchoAgent("agent:echo", "Echo")
        d = a.to_dict()
        assert d["agent_id"] == "agent:echo"
        assert "clock" in d


# ── Conductor ─────────────────────────────────────────────────────────────────

class TestConductor:
    @pytest.fixture
    def conductor(self):
        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        return c

    @pytest.mark.asyncio
    async def test_dispatch_returns_response(self, conductor):
        await conductor.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", "agent:echo", "hello")
        resp = await conductor.dispatch(msg)
        assert resp.type == MessageType.RESPONSE
        assert "echo: hello" in resp.payload["result"]
        await conductor.stop()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_agent_returns_error(self, conductor):
        await conductor.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", "agent:ghost", "hello")
        resp = await conductor.dispatch(msg)
        assert resp.type == MessageType.ERROR
        assert "not found" in resp.payload["reason"]
        await conductor.stop()

    @pytest.mark.asyncio
    async def test_dispatch_records_trace(self, conductor):
        await conductor.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", "agent:echo", "hello")
        await conductor.dispatch(msg)
        trace = conductor.store.get_trace(mid)
        assert len(trace) == 2  # request + response
        await conductor.stop()

    @pytest.mark.asyncio
    async def test_dispatch_task_completed(self, conductor):
        await conductor.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", "agent:echo", "hello")
        await conductor.dispatch(msg)
        tasks = conductor.store.list_tasks(mid)
        assert len(tasks) == 1
        assert tasks[0].state.value == "completed"
        await conductor.stop()

    @pytest.mark.asyncio
    async def test_dispatch_agent_error_returns_error(self):
        c = Conductor()
        c.register(ErrorAgent("agent:error", "Error"))
        await c.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", "agent:error", "crash")
        resp = await c.dispatch(msg)
        assert resp.type == MessageType.ERROR
        await c.stop()

    @pytest.mark.asyncio
    async def test_start_mission(self, conductor):
        await conductor.start()
        result = await conductor.start_mission("Test", "agent:echo", "do X")
        assert result["state"] == "completed"
        assert "echo: do X" in result["result"]["result"]
        await conductor.stop()


# ── A2A Gateway ───────────────────────────────────────────────────────────────

class TestA2AGateway:
    def make_a2a_task(self, text: str, task_id: str = "task-1") -> dict:
        return {
            "id": task_id,
            "message": {"parts": [{"type": "text", "text": text}]},
        }

    def test_wrap_a2a_task(self):
        gw = A2AGatewayAgent(default_recipient="agent:alice")
        task = self.make_a2a_task("do something")
        msg = gw.wrap_a2a_task(task)
        assert msg.payload["content"] == "do something"
        assert msg.sender == "ext:a2a"
        assert msg.recipient == "agent:alice"
        assert isinstance(msg.clock, CausalDilationClock)
        assert msg.clock.vector == {}  # neutrale Clock

    def test_unwrap_response(self):
        gw = A2AGatewayAgent()
        mid = new_mission_id()
        req = Message.request(mid, "ext:a2a", "agent:alice", "do X")
        resp = Message.response(req, "done!")
        artifact = gw.unwrap_cdc_response(resp, "task-1")
        assert artifact["status"]["state"] == "completed"
        assert artifact["artifacts"][0]["parts"][0]["text"] == "done!"
        assert "cdc_clock" in artifact["metadata"]

    def test_unwrap_error(self):
        gw = A2AGatewayAgent()
        mid = new_mission_id()
        req = Message.request(mid, "ext:a2a", "agent:alice", "do X")
        err = Message.error(req, "failed!")
        artifact = gw.unwrap_cdc_response(err, "task-1")
        assert artifact["status"]["state"] == "failed"

    def test_agent_card_structure(self):
        card = A2AGatewayAgent.agent_card("http://localhost:5050")
        assert "name" in card
        assert "skills" in card
        assert "capabilities" in card
        assert card["version"].startswith("3.")

    def test_malformed_a2a_task_handled(self):
        gw = A2AGatewayAgent(default_recipient="agent:default")
        msg = gw.wrap_a2a_task({"broken": True})
        assert msg.recipient == "agent:default"
