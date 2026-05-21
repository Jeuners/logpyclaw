"""Tests für CausalDilationClock — alle 4 Relationen + Serialisierung."""
import pytest
from backend.core.cdc import CausalDilationClock, CDCRelation


def make_clock(agent: str, ticks: int, rate: float = 1.0) -> CausalDilationClock:
    c = CausalDilationClock()
    for _ in range(ticks):
        c.tick(agent, op_weight=rate)
    return c


class TestRelations:
    def test_ordered(self):
        a = make_clock("alice", 3)
        b = make_clock("alice", 5)
        assert a.relate(b) == CDCRelation.ORDERED

    def test_causal_drift(self):
        """Kausal geordnet (V) aber Eigenzeit läuft schneller beim Sender."""
        a = CausalDilationClock(vector={"alice": 1}, dilation={"alice": 10.0})
        b = CausalDilationClock(vector={"alice": 3}, dilation={"alice": 2.0})
        assert a.relate(b) == CDCRelation.CAUSAL_DRIFT

    def test_concurrent_drift(self):
        """Nebenläufig (beide haben eigene Komponenten) + Dilation divergiert."""
        a = CausalDilationClock(vector={"alice": 5}, dilation={"alice": 5.0})
        b = CausalDilationClock(vector={"bob": 5}, dilation={"bob": 20.0})
        assert a.relate(b) == CDCRelation.CONCURRENT_DRIFT

    def test_inconsistent(self):
        """Gleicher Vektor aber unterschiedliche Dilation → Inkonsistenz."""
        a = CausalDilationClock(vector={"alice": 3}, dilation={"alice": 5.0})
        b = CausalDilationClock(vector={"alice": 3}, dilation={"alice": 15.0})
        assert a.relate(b) == CDCRelation.INCONSISTENT

    def test_equal_is_ordered(self):
        a = make_clock("alice", 3)
        b = make_clock("alice", 3)
        assert a.relate(b) == CDCRelation.ORDERED


class TestMerge:
    def test_merge_takes_max(self):
        a = CausalDilationClock(vector={"alice": 5, "bob": 2}, dilation={"alice": 5.0})
        b = CausalDilationClock(vector={"alice": 3, "bob": 7}, dilation={"alice": 8.0, "bob": 3.0})
        a.merge(b)
        assert a.vector["alice"] == 5
        assert a.vector["bob"] == 7
        assert a.dilation["alice"] == 8.0

    def test_merge_returns_self(self):
        a = CausalDilationClock()
        b = CausalDilationClock()
        b.tick("x")
        result = a.merge(b)
        assert result is a


class TestTickWithRate:
    def test_rate_stored(self):
        c = CausalDilationClock()
        c.tick_with_rate("alice", rate=2.5)
        assert c.vector["alice"] == 1
        assert abs(c.dilation["alice"] - 2.5) < 0.01

    def test_zero_rate_ignored(self):
        c = CausalDilationClock()
        c.tick("alice", op_weight=3.0)
        c.tick_with_rate("alice", rate=0.0)
        assert c.dilation["alice"] == pytest.approx(4.0)


class TestLLMSummary:
    def test_summary_format(self):
        c = CausalDilationClock(
            vector={"alice": 4, "bob": 2},
            dilation={"alice": 2.6, "bob": 0.3},
        )
        s = c.llm_summary()
        assert "alice:fast" in s
        assert "bob:slow" in s
        assert "ez=4" in s

    def test_empty_clock(self):
        assert CausalDilationClock().llm_summary() == "no temporal data"


class TestSerialization:
    def test_round_trip_dict(self):
        c = make_clock("alice", 3, rate=1.5)
        c2 = CausalDilationClock.from_dict(c.to_dict())
        assert c2.vector == c.vector
        assert c2.dilation == pytest.approx(c.dilation)

    def test_round_trip_json(self):
        c = make_clock("bob", 2)
        c2 = CausalDilationClock.from_json(c.to_json())
        assert c2.vector["bob"] == 2

    def test_to_dict_has_wall_ts(self):
        c = make_clock("alice", 1)
        d = c.to_dict()
        assert "wall_ts" in d
        assert d["wall_ts"] > 0

    def test_copy_is_independent(self):
        c = make_clock("alice", 3)
        c2 = c.copy()
        c.tick("alice")
        assert c2.vector["alice"] == 3


class TestTransform:
    def test_same_agent_identity(self):
        result = CausalDilationClock.transform(5.0, "alice", "alice", {})
        assert result == 5.0

    def test_gamma_applied(self):
        gamma = {("alice", "bob"): 2.0}
        result = CausalDilationClock.transform(3.0, "alice", "bob", gamma)
        assert result == pytest.approx(6.0)

    def test_missing_gamma_defaults_to_one(self):
        result = CausalDilationClock.transform(4.0, "alice", "charlie", {})
        assert result == pytest.approx(4.0)
