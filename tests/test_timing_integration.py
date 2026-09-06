"""Dispatcher, monotonic CDC rate and planner wiring without network calls."""
from __future__ import annotations

import asyncio
import importlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agents.base import AsyncAgent
from backend.agents.conductor import Conductor
from backend.agents.llm_agent import LLMAgent
from backend.agents.martin import MartinAgent, QCConfig
from backend.config import Settings
from backend.core.faction_protocol import FactionRegistry
from backend.core.protocol import Message, MessageType, new_mission_id
from backend.core.timing import TimingRegistry


class Echo(AsyncAgent):
    async def handle(self, msg):
        return Message.response(msg, "ok", clock=self.advance_clock(msg.clock))


@pytest.fixture(autouse=True)
def registry():
    FactionRegistry.reset()
    yield
    FactionRegistry.reset()


def test_cdc_rate_ignores_wall_clock_jumps(monkeypatch):
    agent = Echo("agent:echo", "Echo")
    ticks = iter([0.0, 1.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    walls = iter([100.0, -1000.0, 1e9])
    monkeypatch.setattr(time, "time", lambda: next(walls))
    for _ in range(3):
        agent.advance_clock()
    assert agent.rate_stats["rate"] == 1.0
    assert agent.clock.tau["agent:echo"] == 3


@pytest.mark.asyncio
async def test_dispatch_records_model_latency_and_preserves_signed_response(monkeypatch):
    conductor = Conductor()
    worker = LLMAgent("agent:alice", "Alice", model="test", provider="ollama")

    async def ollama(*args):
        await asyncio.sleep(0.002)
        return "model result"

    monkeypatch.setattr(worker, "_ollama", ollama)
    conductor.register(worker)
    result = await conductor.start_mission("test", "agent:alice", "hello")
    timing = result["result"]["_timing"]
    assert 0 < timing["model_request_s"] <= timing["handle_s"] <= timing["total_s"]
    assert timing["identity"]["model"] == "test"
    assert conductor.timings.summary(worker)["samples"] == 1
    conductor.unregister(worker.agent_id)
    assert conductor.timings.summary(worker)["samples"] == 0


@pytest.mark.asyncio
async def test_nested_delegation_is_wait_not_parent_model_time():
    conductor = Conductor()
    worker = Echo("agent:alice", "Alice")
    martin = MartinAgent(conductor=conductor, qc=QCConfig(enabled=False))
    conductor.register(worker)
    conductor.register(martin)
    result = await conductor.start_mission("test", "agent:martin", "@agent:alice hello")
    measured = result["result"]["_timing"]
    assert measured["delegation_wait_s"] > 0
    assert measured["model_request_s"] == 0
    assert measured["routing"]["mode"] == "explicit"
    assert measured["routing"]["selected_agents"] == ["agent:alice"]


@pytest.mark.asyncio
async def test_dispatch_timeout_records_failed_attempt_without_fast_sample():
    class Slow(Echo):
        async def handle(self, msg):
            await asyncio.sleep(10)
            return await super().handle(msg)

    conductor = Conductor()
    worker = Slow("agent:slow", "Slow")
    conductor.register(worker)
    mid = new_mission_id()
    conductor.store.register_mission(mid, {"mission_id": mid, "timeout_sec": 0.001})
    r = await conductor.dispatch(Message.request(mid, "ext:user", worker.agent_id, "test"))
    assert r.type == MessageType.ERROR
    assert conductor.timings.summary(worker)["failures"] == 1
    assert conductor.timings.summary(worker)["samples"] == 0


@pytest.fixture
def planner_app(monkeypatch, tmp_path):
    # Prevent tests from opening the user's databases at app import.
    from backend.core.memory import SemanticMemory
    monkeypatch.setattr(SemanticMemory, "__init__", lambda self: None)
    monkeypatch.setenv("DB_URL", "sqlite://")
    from backend.config import get_settings
    get_settings.cache_clear()
    app = importlib.import_module("backend.app")
    monkeypatch.setattr(app, "memory", SimpleNamespace(recall=AsyncMock(return_value=[])))
    monkeypatch.setattr(app, "conductor", Conductor())
    yield app
    get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_planner_receives_optional_context_and_audits_decision(planner_app, monkeypatch, enabled):
    app = planner_app
    worker = Echo("agent:alice", "Alice")
    app.conductor.register(worker)
    for _ in range(3):
        await app.conductor.start_mission("warmup", worker.agent_id, "test")
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, *, json, headers):
            captured.update(json)
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
                "message": {"content": '{"tasks":[{"agent":"agent:alice","content":"test"}]}'}})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    cfg = Settings(_env_file=None, martin_latency_context_enabled=enabled)
    planner = app._make_planner_fn(cfg, model="test", provider="ollama")
    martin = MartinAgent(conductor=app.conductor, llm_planner_fn=planner,
                         qc=QCConfig(enabled=False), model="test")
    app.conductor.register(martin)
    result = await app.conductor.start_mission("test", "agent:martin", "delegiere die Aufgabe")
    prompt = captured["messages"][0]["content"]
    assert ("Gemessene Aktionslatenzen" in prompt) is enabled
    audit = result["result"]["_timing"]["routing"]
    assert audit["latency_context_supplied"] is enabled
    assert audit["selected_agents"] == ["agent:alice"]
    assert len(audit["candidates"]) == (1 if enabled else 0)


@pytest.mark.asyncio
async def test_explicit_target_bypasses_planner():
    conductor = Conductor()
    worker = Echo("agent:alice", "Alice")
    planner = AsyncMock(side_effect=AssertionError("must not plan"))
    martin = MartinAgent(conductor=conductor, llm_planner_fn=planner, qc=QCConfig(enabled=False))
    conductor.register(worker)
    conductor.register(martin)
    result = await conductor.start_mission("test", "agent:martin", "@agent:alice hello")
    assert result["state"] == "completed"
    planner.assert_not_awaited()


def test_timing_settings_are_validated():
    with pytest.raises(ValueError):
        Settings(_env_file=None, latency_max_age_s=-1)
    with pytest.raises(ValueError):
        Settings(_env_file=None, martin_latency_context_max_chars=100)


@pytest.mark.asyncio
async def test_signed_child_error_is_not_mutated_when_returned_by_parent():
    class Failing(Echo):
        async def handle(self, msg):
            return Message.error(msg, "child failed", clock=self.advance_clock(msg.clock))

    conductor = Conductor()
    child = Failing("agent:alice", "Alice")
    martin = MartinAgent(conductor=conductor, qc=QCConfig(enabled=False))
    conductor.register(child)
    conductor.register(martin)
    result = await conductor.start_mission("test", martin.agent_id, "@agent:alice hello")
    trace = conductor.store.get_trace(result["mission_id"])
    errors = [m for m in trace if m.type == MessageType.ERROR]
    assert len(errors) == 2
    assert errors[0].msg_id != errors[1].msg_id
    assert errors[0].payload["_timing"]["identity"] != errors[1].payload["_timing"]["identity"]
    assert conductor.store.verify_chain(result["mission_id"])["valid"] is True


@pytest.mark.asyncio
async def test_total_mission_duration_includes_dispatch_and_finalisation():
    conductor = Conductor()
    conductor.register(Echo("agent:echo", "Echo"))
    result = await conductor.start_mission("test", "agent:echo", "hello")
    assert result["duration_s"] >= result["result"]["_timing"]["total_s"]
    assert conductor.store.get_mission(result["mission_id"])["duration_s"] == result["duration_s"]
