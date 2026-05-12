"""Tests für core/causal_dilation_clock.py — §3.4 / §3.5.

Akzeptanzkriterien:
- tick erhöht V und D pro Agent monoton
- merge nimmt komponentenweise das Maximum
- relate klassifiziert die vier §3.4-Relationen
- JSON-Roundtrip verlustfrei
"""
import pytest

from core.causal_dilation_clock import CausalDilationClock, CDCRelation


# ── tick / merge ──────────────────────────────────────────────────────────────

def test_tick_increments_vector_and_dilation():
    c = CausalDilationClock()
    c.tick("a", op_weight=1.0)
    c.tick("a", op_weight=2.5)
    c.tick("b")
    assert c.vector == {"a": 2, "b": 1}
    assert c.dilation == pytest.approx({"a": 3.5, "b": 1.0})


def test_tick_rejects_empty_agent_id():
    c = CausalDilationClock()
    with pytest.raises(ValueError):
        c.tick("")


def test_tick_rejects_negative_weight():
    c = CausalDilationClock()
    with pytest.raises(ValueError):
        c.tick("a", op_weight=-1.0)


def test_merge_takes_componentwise_max():
    a = CausalDilationClock(
        vector={"x": 3, "y": 1},
        dilation={"x": 4.0, "y": 2.0},
    )
    b = CausalDilationClock(
        vector={"x": 2, "y": 4, "z": 1},
        dilation={"x": 1.5, "y": 5.0, "z": 0.5},
    )
    a.merge(b)
    assert a.vector == {"x": 3, "y": 4, "z": 1}
    assert a.dilation == pytest.approx({"x": 4.0, "y": 5.0, "z": 0.5})


# ── relate (4-Relations-Klassifikator) ────────────────────────────────────────

def test_relate_ordered_when_self_before_other():
    a = CausalDilationClock(vector={"x": 1}, dilation={"x": 1.0})
    b = CausalDilationClock(vector={"x": 2}, dilation={"x": 2.0})
    assert a.relate(b) == CDCRelation.ORDERED


def test_relate_causal_drift_when_dilation_diverges():
    # V ordered (a < b), aber D widerspricht (a hat MEHR τ als b)
    a = CausalDilationClock(vector={"x": 1}, dilation={"x": 99.0})
    b = CausalDilationClock(vector={"x": 2}, dilation={"x": 2.0})
    assert a.relate(b) == CDCRelation.CAUSAL_DRIFT


def test_relate_concurrent_drift_when_vector_concurrent():
    # x von a fortgeschritten, y von b — concurrent
    a = CausalDilationClock(vector={"x": 3, "y": 0}, dilation={"x": 3.0, "y": 0.0})
    b = CausalDilationClock(vector={"x": 0, "y": 3}, dilation={"x": 0.0, "y": 30.0})
    assert a.relate(b) == CDCRelation.CONCURRENT_DRIFT


def test_relate_ordered_handles_equal_clocks():
    a = CausalDilationClock(vector={"x": 1}, dilation={"x": 1.0})
    b = CausalDilationClock(vector={"x": 1}, dilation={"x": 1.0})
    assert a.relate(b) == CDCRelation.ORDERED


def test_relate_inconsistent_when_v_eq_but_d_diverges():
    """§3.4 Relation 4: V identisch, aber D widerspricht → vermutete Korruption."""
    a = CausalDilationClock(vector={"x": 1}, dilation={"x": 1.0})
    b = CausalDilationClock(vector={"x": 1}, dilation={"x": 100.0})
    assert a.relate(b) == CDCRelation.INCONSISTENT


# ── transform (γ) ─────────────────────────────────────────────────────────────

def test_transform_identity_for_same_agent():
    assert CausalDilationClock.transform(5.0, "a", "a", {}) == 5.0


def test_transform_applies_gamma():
    gamma = {("small", "frontier"): 0.025}
    out = CausalDilationClock.transform(1.0, "small", "frontier", gamma)
    assert out == pytest.approx(0.025)


def test_transform_default_one_when_no_entry():
    out = CausalDilationClock.transform(7.0, "a", "b", {})
    assert out == 7.0


# ── Serialisierung ────────────────────────────────────────────────────────────

def test_json_roundtrip():
    c = CausalDilationClock(
        vector={"x": 1, "y": 5},
        dilation={"x": 2.5, "y": 9.0},
    )
    s = c.to_json()
    c2 = CausalDilationClock.from_json(s)
    assert c2.vector == c.vector
    assert c2.dilation == pytest.approx(c.dilation)


def test_copy_does_not_alias_state():
    c = CausalDilationClock(vector={"x": 1}, dilation={"x": 1.0})
    c2 = c.copy()
    c2.tick("x")
    assert c.vector == {"x": 1}
    assert c2.vector == {"x": 2}
