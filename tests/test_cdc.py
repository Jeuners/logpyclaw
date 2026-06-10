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
        """Kausal geordnet (V) aber Eigenzeit τ läuft schneller beim Sender."""
        a = CausalDilationClock(vector={"alice": 1}, tau={"alice": 10.0})
        b = CausalDilationClock(vector={"alice": 3}, tau={"alice": 2.0})
        assert a.relate(b) == CDCRelation.CAUSAL_DRIFT

    def test_concurrent_drift(self):
        """Nebenläufig (beide haben eigene Komponenten) + Eigenzeit divergiert."""
        a = CausalDilationClock(vector={"alice": 5}, tau={"alice": 5.0})
        b = CausalDilationClock(vector={"bob": 5}, tau={"bob": 20.0})
        assert a.relate(b) == CDCRelation.CONCURRENT_DRIFT

    def test_inconsistent(self):
        """Gleicher Vektor aber unterschiedliche Eigenzeit τ → Inkonsistenz."""
        a = CausalDilationClock(vector={"alice": 3}, tau={"alice": 5.0})
        b = CausalDilationClock(vector={"alice": 3}, tau={"alice": 15.0})
        assert a.relate(b) == CDCRelation.INCONSISTENT

    def test_equal_is_ordered(self):
        a = make_clock("alice", 3)
        b = make_clock("alice", 3)
        assert a.relate(b) == CDCRelation.ORDERED

    def test_different_rates_same_tau_is_ordered(self):
        """Raten sind Beobachtungen, keine kausale Größe — gleiche Vectors
        + gleiche τ müssen ORDERED sein, egal wie die Raten divergieren."""
        a = CausalDilationClock(
            vector={"alice": 3}, dilation={"alice": 10.0}, tau={"alice": 3.0}
        )
        b = CausalDilationClock(
            vector={"alice": 3}, dilation={"alice": 0.1}, tau={"alice": 3.0}
        )
        assert a.relate(b) == CDCRelation.ORDERED


class TestMerge:
    def test_merge_vector_takes_max(self):
        a = CausalDilationClock(vector={"alice": 5, "bob": 2}, dilation={"alice": 5.0})
        b = CausalDilationClock(vector={"alice": 3, "bob": 7}, dilation={"alice": 8.0, "bob": 3.0})
        a.merge(b)
        assert a.vector["alice"] == 5
        assert a.vector["bob"] == 7
        # alice: eigener vector-Eintrag (5) ist aktueller als other (3) →
        # eigene Rate bleibt; bob ist neu → Rate von other
        assert a.dilation["alice"] == 5.0
        assert a.dilation["bob"] == 3.0

    def test_merge_rate_follows_higher_vector(self):
        """Die Rate des Clocks mit dem höheren vector-Eintrag gewinnt —
        kein Max über Raten (einmal schnell ≠ für immer schnell)."""
        a = CausalDilationClock(vector={"alice": 2}, dilation={"alice": 9.0})
        b = CausalDilationClock(vector={"alice": 6}, dilation={"alice": 0.5})
        a.merge(b)
        assert a.dilation["alice"] == 0.5  # b ist aktueller, obwohl langsamer

    def test_merge_rate_tie_takes_max(self):
        a = CausalDilationClock(vector={"alice": 3}, dilation={"alice": 1.0})
        b = CausalDilationClock(vector={"alice": 3}, dilation={"alice": 2.0})
        a.merge(b)
        assert a.dilation["alice"] == 2.0

    def test_merge_tau_takes_max(self):
        a = CausalDilationClock(vector={"alice": 2}, tau={"alice": 7.0})
        b = CausalDilationClock(vector={"alice": 6}, tau={"alice": 4.0})
        a.merge(b)
        assert a.tau["alice"] == pytest.approx(7.0)

    def test_merge_returns_self(self):
        a = CausalDilationClock()
        b = CausalDilationClock()
        b.tick("x")
        result = a.merge(b)
        assert result is a


class TestTick:
    def test_tick_accumulates_tau(self):
        c = CausalDilationClock()
        c.tick("alice", op_weight=2.0)
        c.tick("alice", op_weight=3.5)
        assert c.vector["alice"] == 2
        assert c.tau["alice"] == pytest.approx(5.5)
        assert "alice" not in c.dilation  # tick fasst die Rate nicht an


class TestTickWithRate:
    def test_rate_stored_and_tau_incremented(self):
        c = CausalDilationClock()
        c.tick_with_rate("alice", rate=2.5)
        assert c.vector["alice"] == 1
        assert c.dilation["alice"] == pytest.approx(2.5)
        assert c.tau["alice"] == pytest.approx(1.0)

    def test_rate_overwrites_not_accumulates(self):
        c = CausalDilationClock()
        c.tick_with_rate("alice", rate=2.5)
        c.tick_with_rate("alice", rate=0.8)
        assert c.dilation["alice"] == pytest.approx(0.8)  # Momentanrate, kein Σ
        assert c.tau["alice"] == pytest.approx(2.0)

    def test_zero_rate_ignored(self):
        c = CausalDilationClock()
        c.tick("alice", op_weight=3.0)
        c.tick_with_rate("alice", rate=0.0)
        assert "alice" not in c.dilation  # Rate 0 wird nicht gespeichert
        assert c.tau["alice"] == pytest.approx(4.0)  # 3.0 + 1.0 vom Rate-Tick


class TestLLMSummary:
    def test_summary_format(self):
        c = CausalDilationClock(
            vector={"alice": 4, "bob": 2},
            dilation={"alice": 2.6, "bob": 0.3},
            tau={"alice": 12.0, "bob": 2.5},
        )
        s = c.llm_summary()
        assert "alice:fast" in s
        assert "bob:slow" in s
        assert "ez=4" in s
        assert "tau=12.0" in s
        assert "tau=2.5" in s

    def test_empty_clock(self):
        assert CausalDilationClock().llm_summary() == "no temporal data"


class TestSerialization:
    def test_round_trip_dict(self):
        c = make_clock("alice", 3, rate=1.5)
        c.tick_with_rate("alice", rate=2.0)
        c2 = CausalDilationClock.from_dict(c.to_dict())
        assert c2.vector == c.vector
        assert c2.dilation == pytest.approx(c.dilation)
        assert c2.tau == pytest.approx(c.tau)

    def test_round_trip_json(self):
        c = make_clock("bob", 2)
        c2 = CausalDilationClock.from_json(c.to_json())
        assert c2.vector["bob"] == 2
        assert c2.tau["bob"] == pytest.approx(c.tau["bob"])

    def test_from_dict_without_tau_is_legacy_tolerant(self):
        """Alte Payloads (vor dem tau/rate-Split) haben keinen tau-Key."""
        c = CausalDilationClock.from_dict(
            {"vector": {"alice": 2}, "dilation": {"alice": 1.5}}
        )
        assert c.vector["alice"] == 2
        assert c.dilation["alice"] == pytest.approx(1.5)
        assert c.tau == {}

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
        assert c2.tau["alice"] == pytest.approx(3.0)  # copy nimmt tau mit, bleibt unabhängig


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


class TestEWMARate:
    """EWMA-Rate in AsyncAgent.advance_clock — kein Lifetime-Durchschnitt mehr."""

    def make_agent(self):
        from backend.agents.base import AsyncAgent
        from backend.core.protocol import Message

        class Dummy(AsyncAgent):
            async def handle(self, msg: Message) -> Message:  # pragma: no cover
                return Message.response(msg, "ok")

        return Dummy("agent:dummy", "Dummy")

    def test_rate_stays_in_bounds(self):
        a = self.make_agent()
        for _ in range(50):
            clk = a.advance_clock()
            rate = clk.dilation["agent:dummy"]
            assert 0.01 <= rate <= 100.0

    def test_first_op_uses_start_rate(self):
        a = self.make_agent()
        clk = a.advance_clock()
        assert clk.dilation["agent:dummy"] == pytest.approx(1.0)  # Startwert, kein dt-Bezug

    def test_ewma_smooths_instant_rate(self):
        """Back-to-back-Ops → inst_rate riesig, aber EWMA + Clamp halten die Rate ≤ 100."""
        a = self.make_agent()
        a.advance_clock()
        clk = a.advance_clock()
        assert clk.dilation["agent:dummy"] <= 100.0

    def test_tau_grows_with_each_op(self):
        a = self.make_agent()
        a.advance_clock()
        clk = a.advance_clock()
        assert clk.tau["agent:dummy"] == pytest.approx(2.0)
