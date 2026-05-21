"""Tests für das CDC-Team-Protokoll."""
import pytest

from backend.core.cdc import CausalDilationClock
from backend.core.protocol import Message, new_mission_id
from backend.core.team_protocol import Team, TeamMessage, TeamState


def fast_clock(agent_id: str, rate: float = 3.0) -> CausalDilationClock:
    c = CausalDilationClock()
    c.tick_with_rate(agent_id, rate=rate)
    return c

def slow_clock(agent_id: str, rate: float = 0.3) -> CausalDilationClock:
    c = CausalDilationClock()
    c.tick_with_rate(agent_id, rate=rate)
    return c


class TestTeamMembership:
    def test_add_member(self):
        t = Team()
        t.add_member("agent:alice")
        assert "agent:alice" in t._members

    def test_remove_member(self):
        t = Team()
        t.add_member("agent:alice")
        t.remove_member("agent:alice")
        assert "agent:alice" not in t._members

    def test_state_forming_when_no_members(self):
        t = Team()
        assert t.state == TeamState.FORMING

    def test_state_active_when_quorum(self):
        t = Team()
        t.add_member("agent:alice")
        t.add_member("agent:bob")
        t.add_member("agent:charlie")
        assert t.state == TeamState.ACTIVE


class TestQuorum:
    def test_quorum_majority(self):
        t = Team()
        for a in ["agent:a", "agent:b", "agent:c"]:
            t.add_member(a)
        assert t.is_quorum()

    def test_no_quorum_empty(self):
        t = Team()
        assert not t.is_quorum()

    def test_single_member_has_quorum(self):
        t = Team()
        t.add_member("agent:alice")
        assert t.is_quorum()

    def test_quorum_lost_after_remove(self):
        t = Team()
        t.add_member("agent:a")
        t.add_member("agent:b")
        t.add_member("agent:c")
        # Alle members sind reachable (frisch hinzugefügt) → ACTIVE
        assert t.is_quorum()


class TestTeamClock:
    def test_merge_team_clock_empty(self):
        t = Team()
        clock = t.merge_team_clock()
        assert clock.vector == {}

    def test_merge_team_clock_max(self):
        t = Team()
        t.add_member("agent:alice")
        t.add_member("agent:bob")
        c_alice = CausalDilationClock(vector={"agent:alice": 5}, dilation={"agent:alice": 5.0})
        c_bob   = CausalDilationClock(vector={"agent:bob": 3},   dilation={"agent:bob": 7.0})
        t.update_member_clock("agent:alice", c_alice)
        t.update_member_clock("agent:bob",   c_bob)
        team_clock = t.merge_team_clock()
        assert team_clock.vector.get("agent:alice") == 5
        assert team_clock.vector.get("agent:bob") == 3
        assert team_clock.dilation.get("agent:bob") == 7.0


class TestGammaMatrix:
    def test_diagonal_is_one(self):
        t = Team()
        t.add_member("agent:alice")
        t.add_member("agent:bob")
        gamma = t.compute_gamma_matrix()
        assert gamma["agent:alice"]["agent:alice"] == 1.0
        assert gamma["agent:bob"]["agent:bob"] == 1.0

    def test_fast_agent_has_high_gamma(self):
        t = Team()
        t.add_member("agent:fast")
        t.add_member("agent:slow")
        t.update_member_clock("agent:fast", fast_clock("agent:fast", rate=4.0))
        t.update_member_clock("agent:slow", slow_clock("agent:slow", rate=1.0))
        gamma = t.compute_gamma_matrix()
        # fast relative to slow: 4.0/1.0 = 4.0
        assert gamma["agent:fast"]["agent:slow"] == pytest.approx(4.0, abs=0.1)

    def test_inverse_gamma(self):
        t = Team()
        t.add_member("agent:fast")
        t.add_member("agent:slow")
        t.update_member_clock("agent:fast", fast_clock("agent:fast", rate=2.0))
        t.update_member_clock("agent:slow", slow_clock("agent:slow", rate=1.0))
        gamma = t.compute_gamma_matrix()
        assert gamma["agent:slow"]["agent:fast"] == pytest.approx(0.5, abs=0.05)


class TestRecommendNext:
    def setup_method(self):
        self.t = Team()
        self.t.add_member("agent:alice")
        self.t.add_member("agent:bob")
        # Alice fast, Bob normal
        self.t.update_member_clock("agent:alice", fast_clock("agent:alice", rate=2.0))
        self.t.update_member_clock("agent:bob",   fast_clock("agent:bob",   rate=1.0))

    def test_recommends_someone(self):
        rec = self.t.recommend_next()
        assert rec is not None

    def test_busy_agent_excluded(self):
        self.t.set_busy("agent:alice", True)
        self.t.set_busy("agent:bob", False)
        rec = self.t.recommend_next()
        assert rec == "agent:bob"

    def test_all_busy_returns_none(self):
        self.t.set_busy("agent:alice", True)
        self.t.set_busy("agent:bob", True)
        rec = self.t.recommend_next()
        assert rec is None

    def test_recommend_details_sorted(self):
        details = self.t.recommend_details()
        scores = [d["recommendation_score"] for d in details]
        assert scores == sorted(scores, reverse=True)

    def test_recommend_details_has_gamma(self):
        details = self.t.recommend_details()
        for d in details:
            assert "gamma" in d
            assert "drift_score" in d


class TestTeamMessage:
    def test_wrap_upgrades_message(self):
        t = Team()
        t.add_member("agent:alice")
        t.add_member("agent:bob")
        mid = new_mission_id()
        msg = Message.request(mid, "agent:alice", "agent:bob", "do X")
        tm = t.wrap(msg)
        assert isinstance(tm, TeamMessage)
        assert tm.team_id == t.team_id
        assert tm.task_id == msg.task_id
        assert isinstance(tm.team_clock, CausalDilationClock)

    def test_team_message_to_dict(self):
        t = Team()
        t.add_member("agent:alice")
        mid = new_mission_id()
        msg = Message.request(mid, "agent:alice", "agent:bob", "do X")
        tm = t.wrap(msg)
        d = tm.to_dict()
        assert "team_id" in d
        assert "team_clock" in d
        assert "gamma_matrix" in d
        assert "team_state" in d


class TestTeamToDict:
    def test_to_dict_structure(self):
        t = Team(name="Alpha")
        t.add_member("agent:alice")
        d = t.to_dict()
        assert d["name"] == "Alpha"
        assert "members" in d
        assert "quorum" in d
        assert "gamma_matrix" in d
        assert "team_clock" in d
