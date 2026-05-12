"""Tests für core/time_provider.py + Eigenzeit-Felder in Task-Dicts.

Bezug: Dillenberg, Time Dilation in LLM Agent Systems §3 + §4.3.
"""
from datetime import datetime

import pytest

from core.time_provider import (
    Frame,
    TimeProvider,
    WallClockProvider,
    get_default_provider,
    set_default_provider,
)


# ── WallClockProvider ─────────────────────────────────────────────────────────

def test_wallclock_now_is_datetime():
    p = WallClockProvider()
    n = p.now()
    assert isinstance(n, datetime)
    assert isinstance(p.wall_now(), datetime)


def test_wallclock_default_dilation_is_one():
    p = WallClockProvider()
    assert p.dilation() == 1.0


def test_wallclock_explicit_dilation():
    p = WallClockProvider(dilation_factor=0.025)  # frontier ↔ small-local
    assert p.dilation() == pytest.approx(0.025)


def test_frame_is_self_describing():
    p = WallClockProvider(agent_id="aria")
    f = p.frame
    assert isinstance(f, Frame)
    assert f.agent_id == "aria"
    assert f.parent_frame_id is None
    assert f.parent_reference_now is None
    assert f.dilation_factor == 1.0
    assert f.frame_id  # nicht leer


def test_frame_to_dict_serialisiert_datetime():
    p = WallClockProvider(
        agent_id="child",
        parent_reference_now=datetime(2026, 5, 6, 12, 0, 0),
        parent_frame_id="parent-fr",
        dilation_factor=2.0,
    )
    d = p.frame.to_dict()
    assert d["agent_id"] == "child"
    assert d["parent_frame_id"] == "parent-fr"
    assert d["dilation_factor"] == 2.0
    assert d["parent_reference_now"] == "2026-05-06T12:00:00"


# ── tau / tick ────────────────────────────────────────────────────────────────

def test_tau_starts_at_zero_and_advances_monotonically():
    p = WallClockProvider()
    assert p.tau == 0.0
    p.tick()
    p.tick()
    assert p.tau == 2.0


def test_tick_accepts_weight():
    p = WallClockProvider()
    p.tick(0.5)
    p.tick(2.0)
    assert p.tau == pytest.approx(2.5)


def test_tick_rejects_negative_weight():
    p = WallClockProvider()
    with pytest.raises(ValueError):
        p.tick(-1.0)


# ── fork (Frame-Vererbung) ────────────────────────────────────────────────────

def test_fork_inherits_parent_reference_now():
    parent = WallClockProvider(agent_id="orchestrator")
    parent.tick(3.0)
    child = parent.fork(agent_id="picasso")
    assert child.frame.agent_id == "picasso"
    assert child.frame.parent_frame_id == parent.frame.frame_id
    assert child.frame.parent_reference_now is not None
    # Child startet mit τ = 0 (Frame-Lokalität, §3.2 P2)
    assert child.tau == 0.0
    # Parent-τ unberührt
    assert parent.tau == 3.0


def test_fork_inherits_dilation_by_default():
    parent = WallClockProvider(dilation_factor=0.5)
    child = parent.fork(agent_id="sub")
    assert child.dilation() == 0.5


def test_fork_can_override_dilation():
    parent = WallClockProvider(dilation_factor=1.0)
    child = parent.fork(agent_id="sub", dilation_factor=0.025)
    assert child.dilation() == pytest.approx(0.025)


def test_two_forks_get_distinct_frame_ids():
    parent = WallClockProvider()
    a = parent.fork(agent_id="a")
    b = parent.fork(agent_id="b")
    assert a.frame.frame_id != b.frame.frame_id
    assert a.frame.parent_frame_id == b.frame.parent_frame_id == parent.frame.frame_id


# ── Default-Provider ──────────────────────────────────────────────────────────

def test_default_provider_is_wallclock_singleton():
    p1 = get_default_provider()
    p2 = get_default_provider()
    assert p1 is p2
    assert isinstance(p1, WallClockProvider)


def test_set_default_provider_for_tests():
    fake = WallClockProvider(agent_id="injected", dilation_factor=42.0)
    set_default_provider(fake)
    try:
        assert get_default_provider() is fake
        assert get_default_provider().dilation() == 42.0
    finally:
        # Reset auf einen frischen Default, damit andere Tests nicht beeinflusst
        # werden (get_default_provider legt lazy einen neuen an).
        from core import time_provider as _tp
        _tp._default_provider = None


# ── Interface-Form ────────────────────────────────────────────────────────────

def test_wallclockprovider_is_timeprovider():
    p = WallClockProvider()
    assert isinstance(p, TimeProvider)
