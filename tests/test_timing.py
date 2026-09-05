"""Contracts for monotonic action timing and bounded routing evidence."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.core import timing
from backend.core.protocol import Message, MessageType


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def agent(model="qwen", endpoint="http://localhost:11434"):
    return SimpleNamespace(agent_id="agent:alice", provider="ollama", model=model,
                           ollama_url=endpoint, temperature=0.7, max_tokens=100)


def response():
    msg = Message.request(mission_id="mis_test", sender="ext:user",
                          recipient="agent:alice", content="test")
    return Message.response(msg, "ok")


@pytest.mark.asyncio
async def test_duration_segments_and_no_idle_pollution():
    clock = Clock()
    registry = timing.TimingRegistry(clock=clock)
    worker = agent()

    async def run():
        with timing.timing_span("model"):
            clock.advance(2)
        with timing.timing_span("delegation"):
            clock.advance(3)
        clock.advance(1)
        return response()

    clock.advance(4)
    reply = await registry.observe(worker, run, started=0)
    measured = reply.payload["_timing"]
    assert measured["total_s"] == 10
    assert measured["prepare_s"] == 4
    assert measured["handle_s"] == 6
    assert measured["model_request_s"] == 2
    assert measured["delegation_wait_s"] == 3
    assert measured["other_handle_s"] == 1
    clock.advance(10000)
    await registry.observe(worker, run)
    assert registry.summary(worker)["median_s"] == 6


@pytest.mark.asyncio
async def test_model_and_endpoint_changes_reset_evidence_and_hide_credentials():
    clock = Clock()
    registry = timing.TimingRegistry(clock=clock)
    worker = agent(endpoint="http://user:secret@host:11434/path?token=private")

    async def run():
        clock.advance(2)
        return response()

    for _ in range(3):
        await registry.observe(worker, run)
    summary = registry.summary(worker)
    assert summary["status"] == "ready"
    assert summary["samples"] == 3
    assert "secret" not in str(summary) and "private" not in str(summary)
    worker.model = "other"
    assert registry.summary(worker)["status"] == "unknown"
    worker.model = "qwen"
    assert registry.summary(worker)["status"] == "unknown"  # old epoch cannot return
    await registry.observe(worker, run)
    worker.ollama_url = "http://different:11434"
    assert registry.summary(worker)["samples"] == 0


@pytest.mark.asyncio
async def test_stale_insufficient_failures_and_bounded_window():
    clock = Clock()
    registry = timing.TimingRegistry(clock=clock, window=3, max_age_s=10, min_samples=2)
    worker = agent()

    async def run():
        clock.advance(1)
        return response()

    assert registry.summary(worker)["median_s"] is None
    await registry.observe(worker, run)
    assert registry.summary(worker)["status"] == "insufficient"
    for _ in range(5):
        await registry.observe(worker, run)
    assert registry.summary(worker)["samples"] == 3
    clock.advance(11)
    summary = registry.summary(worker)
    assert summary["status"] == "stale"
    assert summary["median_s"] is None

    async def fail():
        clock.advance(1)
        raise TimeoutError("test")

    with pytest.raises(TimeoutError):
        await registry.observe(worker, fail)
    assert registry.summary(worker)["samples"] == 0
    assert registry.summary(worker)["failures"] == 1


@pytest.mark.asyncio
async def test_error_responses_and_qc_failure_are_not_success_samples():
    registry = timing.TimingRegistry()
    worker = agent()

    async def fail():
        r = response()
        r.type = MessageType.ERROR
        return r

    await registry.observe(worker, fail)
    assert registry.summary(worker)["samples"] == 0
    assert registry.summary(worker)["failures"] == 1

    async def qc_fail():
        r = response()
        r.payload["_qc"] = {"passed": False}
        return r

    await registry.observe(worker, qc_fail)
    assert registry.summary(worker)["failures"] == 2


@pytest.mark.asyncio
async def test_cancellation_is_recorded_and_context_is_restored():
    registry = timing.TimingRegistry()

    async def cancel():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await registry.observe(agent(), cancel)
    assert registry.summary(agent())["failures"] == 1
    with timing.timing_span("model"):
        pass
    assert timing.current_timing() is None


@pytest.mark.asyncio
async def test_nested_calls_isolate_model_time_and_union_parallel_waits():
    clock = Clock()
    registry = timing.TimingRegistry(clock=clock)
    parent = agent(model="planner")
    child = agent(model="worker")
    child.agent_id = "agent:bob"

    async def child_run():
        with timing.timing_span("model"):
            clock.advance(2)
        return response()

    async def parent_run():
        with timing.timing_span("delegation"):
            await registry.observe(child, child_run)
        # Nested overlapping spans must count elapsed waiting only once.
        with timing.timing_span("delegation"):
            with timing.timing_span("delegation"):
                clock.advance(3)
        return response()

    r = await registry.observe(parent, parent_run)
    assert r.payload["_timing"]["model_request_s"] == 0
    assert r.payload["_timing"]["delegation_wait_s"] == 5
    assert registry.summary(child)["median_s"] == 2


@pytest.mark.asyncio
async def test_routing_context_is_bounded_explicit_and_switchable():
    clock = Clock()
    registry = timing.TimingRegistry(clock=clock)
    workers = [agent()]

    async def run():
        clock.advance(2)
        return response()

    for _ in range(3):
        await registry.observe(workers[0], run)
    block, evidence = timing.routing_context(registry, workers, enabled=True, max_chars=1600)
    assert "agent:alice" in block
    assert "Median" in block and "n=3" in block
    assert "qwen" in block and "Alter" in block
    assert "garant" in block.lower()
    assert evidence[0]["status"] == "ready"
    assert timing.routing_context(registry, workers, enabled=False) == ("", [])
    many = [SimpleNamespace(**{**vars(workers[0]), "agent_id": f"agent:{i}"}) for i in range(50)]
    block, evidence = timing.routing_context(registry, many, enabled=True, max_chars=800)
    assert len(block) <= 800
    assert "unbekannt" in block
    assert len(evidence) < 50
