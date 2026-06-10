"""Tests für Peer-Dispatch (Conductor.initiate) und den Initiative-Loop."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.agents.base import AsyncAgent
from backend.agents.conductor import Conductor
from backend.core.faction_protocol import FactionRegistry
from backend.core.protocol import Message, MessageType
from backend.services.initiative import InitiativeService


class EchoAgent(AsyncAgent):
    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, f"echo:{msg.payload.get('content', '')}", clock=clock)


@pytest.fixture(autouse=True)
def fresh_registry():
    FactionRegistry.reset()
    yield
    FactionRegistry.reset()


@pytest.fixture
async def conductor_with_two():
    c = Conductor()
    c.register(EchoAgent("agent:alice", "Alice"))
    c.register(EchoAgent("agent:bob", "Bob"))
    await c.start()
    yield c
    await c.stop()


# ── Conductor.initiate ─────────────────────────────────────────────────────────

class TestInitiate:
    async def test_agent_is_mission_sender(self, conductor_with_two):
        c = conductor_with_two
        res = await c.initiate("agent:alice", "agent:bob", "hallo bob")
        assert res["state"] == "completed"

        # Mission-Metadaten tragen initiated_by = Agent-ID
        mission = c.store.get_mission(res["mission_id"])
        assert mission["initiated_by"] == "agent:alice"
        assert mission["title"] == "initiative:agent:alice>agent:bob"

        # Trace-Sender der Request-Message ist die Agent-ID, nicht ext:user
        trace = c.store._traces[res["mission_id"]]
        request = next(m for m in trace if m.type == MessageType.REQUEST)
        assert request.sender == "agent:alice"
        assert request.sender != "ext:user"

    async def test_custom_title(self, conductor_with_two):
        c = conductor_with_two
        res = await c.initiate("agent:alice", "agent:bob", "x", title="mein titel")
        assert c.store.get_mission(res["mission_id"])["title"] == "mein titel"

    async def test_unregistered_sender_returns_error(self, conductor_with_two):
        c = conductor_with_two
        res = await c.initiate("agent:ghost", "agent:bob", "x")
        assert "error" in res
        assert "ghost" in res["error"]

    async def test_recipient_equals_sender_returns_error(self, conductor_with_two):
        c = conductor_with_two
        res = await c.initiate("agent:alice", "agent:alice", "x")
        assert "error" in res

    async def test_peer_traffic_feeds_trust_learning(self):
        """Agent A (makers) initiiert an B (auditors) → Relation lernt 1 Interaktion."""
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:a", "makers")
        reg.assign("agent:b", "auditors")

        c = Conductor()
        c.register(EchoAgent("agent:a", "A"))
        c.register(EchoAgent("agent:b", "B"))
        await c.start()
        try:
            res = await c.initiate("agent:a", "agent:b", "review this")
            assert res["state"] == "completed"
            rel = reg.relation("makers", "auditors")
            assert rel.interactions == pytest.approx(1.0)
        finally:
            await c.stop()


# ── InitiativeService ──────────────────────────────────────────────────────────

class TestInitiativeService:
    async def test_loop_initiates_at_least_once(self, monkeypatch):
        # Clamp für den Test auf 0.01s herabsetzen (sonst min. 5.0s Wartezeit).
        monkeypatch.setattr(InitiativeService, "MIN_INTERVAL", 0.01)

        conductor = AsyncMock()
        entries = [{"agent_id": "agent:a", "recipient": "agent:b",
                    "content": "tick", "every_sec": 0.01}]
        svc = InitiativeService(conductor, entries)
        await svc.start()
        await asyncio.sleep(0.05)
        await svc.stop()

        assert conductor.initiate.await_count >= 1
        conductor.initiate.assert_awaited_with("agent:a", "agent:b", "tick")

    async def test_disabled_entry_does_not_run(self, monkeypatch):
        monkeypatch.setattr(InitiativeService, "MIN_INTERVAL", 0.01)
        conductor = AsyncMock()
        entries = [{"agent_id": "agent:a", "recipient": "agent:b",
                    "content": "x", "every_sec": 0.01, "enabled": False}]
        svc = InitiativeService(conductor, entries)
        await svc.start()
        await asyncio.sleep(0.05)
        await svc.stop()
        assert conductor.initiate.await_count == 0

    async def test_single_call_error_keeps_loop_alive(self, monkeypatch):
        monkeypatch.setattr(InitiativeService, "MIN_INTERVAL", 0.01)
        conductor = AsyncMock()
        conductor.initiate.side_effect = RuntimeError("boom")
        entries = [{"agent_id": "agent:a", "recipient": "agent:b",
                    "content": "x", "every_sec": 0.01}]
        svc = InitiativeService(conductor, entries)
        await svc.start()
        await asyncio.sleep(0.05)
        await svc.stop()
        # Fehler beendet den Loop nicht → mehrere Versuche trotz Exception
        assert conductor.initiate.await_count >= 1

    async def test_stop_is_clean(self, monkeypatch):
        monkeypatch.setattr(InitiativeService, "MIN_INTERVAL", 0.01)
        conductor = AsyncMock()
        svc = InitiativeService(conductor, [
            {"agent_id": "agent:a", "recipient": "agent:b", "content": "x", "every_sec": 0.01},
        ])
        await svc.start()
        await svc.stop()
        # Nach stop() keine laufenden Tasks mehr
        assert svc.to_dict()["running"] is False

    def test_to_dict_clamps_interval(self):
        conductor = AsyncMock()
        svc = InitiativeService(conductor, [
            {"agent_id": "agent:a", "recipient": "agent:b", "content": "x", "every_sec": 0.01},
        ])
        d = svc.to_dict()
        assert d["entries"][0]["every_sec"] == InitiativeService.MIN_INTERVAL
