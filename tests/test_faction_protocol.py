"""Tests für das Fraktionssystem — FactionRegistry, Relations, Trust, γ."""

import time
from unittest.mock import AsyncMock

import pytest

from backend.agents.conductor import Conductor
from backend.core.cdc import CDCRelation
from backend.core.faction_protocol import (
    FactionArchetype,
    FactionCDCRelation,
    FactionEnvelope,
    FactionRegistry,
    FactionRelation,
    FactionStance,
    TempoProfile,
    classify_drift,
)
from backend.core.protocol import Message, MessageType, new_mission_id


@pytest.fixture(autouse=True)
def fresh_registry():
    """Jeder Test bekommt eine frische Registry."""
    FactionRegistry.reset()
    yield
    FactionRegistry.reset()


# ── Basis-Registry ────────────────────────────────────────────────────────────

class TestFactionRegistry:
    def test_singleton(self):
        a = FactionRegistry.get()
        b = FactionRegistry.get()
        assert a is b

    def test_reset_gives_new_instance(self):
        a = FactionRegistry.get()
        FactionRegistry.reset()
        b = FactionRegistry.get()
        assert a is not b

    def test_register_and_get(self):
        reg = FactionRegistry.load_defaults()
        f = reg.get_faction("operators")
        assert f is not None
        assert f.archetype == FactionArchetype.OPERATORS

    def test_list_factions_returns_all_defaults(self):
        reg = FactionRegistry.load_defaults()
        ids = {f.id for f in reg.list_factions()}
        assert ids == {"operators", "makers", "auditors", "gatherers", "guardians", "scribes"}


# ── Mitgliedschaft ────────────────────────────────────────────────────────────

class TestMembership:
    def test_assign_and_lookup(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:alice", "makers")
        assert reg.faction_of("agent:alice") == "makers"

    def test_reassign_moves_member(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:bob", "makers")
        reg.assign("agent:bob", "auditors")
        assert reg.faction_of("agent:bob") == "auditors"
        assert "agent:bob" not in reg.get_faction("makers").members

    def test_unknown_agent_returns_none(self):
        reg = FactionRegistry.load_defaults()
        assert reg.faction_of("agent:ghost") is None

    def test_member_in_faction_set(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:charlie", "scribes")
        assert "agent:charlie" in reg.get_faction("scribes").members


# ── Beziehungen ───────────────────────────────────────────────────────────────

class TestFactionRelations:
    def test_default_stance_is_neutral(self):
        reg = FactionRegistry.load_defaults()
        # Makers → Scribes ist nicht explizit gesetzt → neutral
        rel = reg.relation("makers", "scribes")
        assert rel.stance == FactionStance.NEUTRAL

    def test_auditors_skeptical_of_makers(self):
        reg = FactionRegistry.load_defaults()
        rel = reg.relation("auditors", "makers")
        assert rel.stance == FactionStance.SKEPTICAL

    def test_operators_allied_with_all(self):
        reg = FactionRegistry.load_defaults()
        for target in ["makers", "auditors", "gatherers", "guardians", "scribes"]:
            rel = reg.relation("operators", target)
            assert rel.stance == FactionStance.ALLIED, f"operators→{target} should be ALLIED"

    def test_set_stance(self):
        reg = FactionRegistry.load_defaults()
        reg.set_stance("makers", "scribes", FactionStance.ALLIED)
        assert reg.relation("makers", "scribes").stance == FactionStance.ALLIED

    def test_requires_bridge_adversarial(self):
        reg = FactionRegistry.load_defaults()
        reg.set_stance("makers", "guardians", FactionStance.ADVERSARIAL)
        rel = reg.relation("makers", "guardians")
        assert rel.requires_bridge()

    def test_requires_bridge_non_adversarial(self):
        reg = FactionRegistry.load_defaults()
        rel = reg.relation("operators", "makers")
        assert not rel.requires_bridge()


# ── Trust-Learning ────────────────────────────────────────────────────────────

class TestTrustLearning:
    def test_initial_trust_is_half(self):
        rel = FactionRelation(source="a", target="b")
        # Beta(1,1) prior: (0+1)/(0+2) = 0.5
        assert rel.trust == pytest.approx(0.5)

    def test_trust_rises_after_successes(self):
        rel = FactionRelation(source="a", target="b")
        for _ in range(10):
            rel.record_outcome(success=True)
        assert rel.trust > 0.9

    def test_trust_falls_after_failures(self):
        rel = FactionRelation(source="a", target="b")
        for _ in range(10):
            rel.record_outcome(success=False)
        assert rel.trust < 0.2

    def test_interaction_counter(self):
        rel = FactionRelation(source="a", target="b")
        rel.record_outcome(True)
        rel.record_outcome(False)
        assert abs(rel.interactions - 2) < 1e-6  # float: Evidenz altert (minimal) zwischen Calls
        assert abs(rel.successes - 1) < 1e-6
        assert abs(rel.failures - 1) < 1e-6

    def test_record_cross_faction_outcome(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:alice", "makers")
        reg.assign("agent:bob", "auditors")
        reg.record_cross_faction_outcome("agent:alice", "agent:bob", success=True)
        rel = reg.relation("makers", "auditors")
        assert rel.interactions == 1
        assert rel.successes == 1

    def test_same_faction_outcome_ignored(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:a", "makers")
        reg.assign("agent:b", "makers")
        reg.record_cross_faction_outcome("agent:a", "agent:b", success=True)
        # Keine Relations-Einträge für same-faction
        assert len(reg.all_relations()) == 0 or \
               reg.relation("makers", "makers").interactions == 0


# ── γ-Update ──────────────────────────────────────────────────────────────────

class TestGammaUpdate:
    def test_initial_gamma_is_one(self):
        rel = FactionRelation(source="a", target="b")
        assert rel.gamma == pytest.approx(1.0)

    def test_gamma_ewma_update(self):
        rel = FactionRelation(source="a", target="b")
        rel.update_gamma(2.0, alpha=0.5)   # (0.5 * 1.0) + (0.5 * 2.0) = 1.5
        assert rel.gamma == pytest.approx(1.5)

    def test_gamma_matrix_export(self):
        reg = FactionRegistry.load_defaults()
        # EWMA: (1-0.2)*1.0 + 0.2*1.5 = 1.1
        reg.relation("operators", "makers").update_gamma(1.5, alpha=0.2)
        gamma = reg.gamma_factions()
        assert gamma[("operators", "makers")] == pytest.approx(1.1, abs=0.01)


# ── TempoProfile ──────────────────────────────────────────────────────────────

class TestTempoProfile:
    def test_normal_rate(self):
        tp = TempoProfile(expected_rate=1.0, min_rate=0.5, max_rate=2.0)
        assert tp.is_normal(1.0)
        assert tp.is_normal(0.5)
        assert tp.is_normal(2.0)

    def test_slow_rate_is_not_normal(self):
        tp = TempoProfile(expected_rate=1.0, min_rate=0.5, max_rate=2.0)
        assert not tp.is_normal(0.1)

    def test_fast_rate_deviation(self):
        tp = TempoProfile(expected_rate=1.0, min_rate=0.5, max_rate=2.0)
        assert tp.deviation(3.0) > 0
        assert tp.deviation(1.0) == 0.0

    def test_guardians_faster_than_makers(self):
        reg = FactionRegistry.load_defaults()
        g = reg.get_faction("guardians")
        m = reg.get_faction("makers")
        assert g.tempo.expected_rate > m.tempo.expected_rate


# ── FactionEnvelope ───────────────────────────────────────────────────────────

class TestFactionEnvelope:
    def test_build_envelope_both_known(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:alice", "makers")
        reg.assign("agent:bob", "auditors")
        env = reg.build_envelope("agent:alice", "agent:bob")
        assert env is not None
        assert env.sender_faction == "makers"
        assert env.recipient_faction == "auditors"
        assert env.expected_drift  # cross-faction

    def test_build_envelope_unknown_agent_returns_none(self):
        reg = FactionRegistry.load_defaults()
        env = reg.build_envelope("agent:unknown", "agent:also_unknown")
        assert env is None

    def test_same_faction_no_expected_drift(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:a", "makers")
        reg.assign("agent:b", "makers")
        env = reg.build_envelope("agent:a", "agent:b")
        assert env is not None
        assert not env.expected_drift

    def test_adversarial_requires_bridge(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:maker", "makers")
        reg.assign("agent:guardian", "guardians")
        reg.set_stance("makers", "guardians", FactionStance.ADVERSARIAL)
        env = reg.build_envelope("agent:maker", "agent:guardian")
        assert env is not None
        assert env.requires_bridge

    def test_envelope_to_dict(self):
        env = FactionEnvelope(
            sender_faction="makers",
            recipient_faction="auditors",
            stance=FactionStance.SKEPTICAL,
            expected_drift=True,
        )
        d = env.to_dict()
        assert d["sender_faction"] == "makers"
        assert d["expected_drift"] is True


# ── FactionCDCRelation ────────────────────────────────────────────────────────

class TestFactionCDCRelation:
    def test_enum_values(self):
        assert FactionCDCRelation.EXPECTED_DRIFT == "expected_drift"
        assert FactionCDCRelation.FACTION_RACE == "faction_race"


# ── classify_drift ────────────────────────────────────────────────────────────

class TestClassifyDrift:
    def test_expected_drift_with_matching_ratio(self):
        reg = FactionRegistry.load_defaults()
        # γ default 1.0, makers-Toleranz 1.0 → ratio 1.2 liegt im Fenster
        label = classify_drift(
            CDCRelation.CAUSAL_DRIFT, "operators", "makers", 1.2, reg
        )
        assert label == FactionCDCRelation.EXPECTED_DRIFT.value

    def test_faction_race_with_matching_ratio(self):
        reg = FactionRegistry.load_defaults()
        label = classify_drift(
            CDCRelation.CONCURRENT_DRIFT, "operators", "makers", 1.2, reg
        )
        assert label == FactionCDCRelation.FACTION_RACE.value

    def test_same_faction_passes_through(self):
        reg = FactionRegistry.load_defaults()
        label = classify_drift(
            CDCRelation.CAUSAL_DRIFT, "makers", "makers", 1.0, reg
        )
        assert label == CDCRelation.CAUSAL_DRIFT.value

    def test_unknown_faction_passes_through(self):
        reg = FactionRegistry.load_defaults()
        label = classify_drift(CDCRelation.CAUSAL_DRIFT, None, "makers", 1.0, reg)
        assert label == CDCRelation.CAUSAL_DRIFT.value

    def test_ratio_outside_tolerance_passes_through(self):
        reg = FactionRegistry.load_defaults()
        # auditors-Toleranz 0.3, γ default 1.0 → ratio 5.0 ist weit draußen
        label = classify_drift(
            CDCRelation.CAUSAL_DRIFT, "operators", "auditors", 5.0, reg
        )
        assert label == CDCRelation.CAUSAL_DRIFT.value

    def test_zero_ratio_passes_through(self):
        reg = FactionRegistry.load_defaults()
        label = classify_drift(
            CDCRelation.CAUSAL_DRIFT, "operators", "makers", 0.0, reg
        )
        assert label == CDCRelation.CAUSAL_DRIFT.value

    def test_ordered_passes_through(self):
        reg = FactionRegistry.load_defaults()
        label = classify_drift(CDCRelation.ORDERED, "operators", "makers", 1.0, reg)
        assert label == CDCRelation.ORDERED.value


# ── Conductor-Verdrahtung (Envelope, Outcome-Learning, Bridge) ────────────────

class _StubAgent:
    """Minimaler Agent-Stub für Conductor.dispatch — handle als AsyncMock."""

    def __init__(self, agent_id: str, result: str = "ok"):
        self.agent_id = agent_id
        self.name = agent_id
        self.handle = AsyncMock(side_effect=lambda m: Message.response(m, result))

    async def start(self): pass

    async def stop(self): pass


class _QCFailAgent:
    """Martin-ähnlicher Stub: liefert eine RESPONSE, deren payload ein
    _qc-Feld mit passed=False trägt (endgültig gescheiterter QC)."""

    def __init__(self, agent_id: str, score: int = 3):
        self.agent_id = agent_id
        self.name = agent_id
        self.score = score

        def _respond(m: Message) -> Message:
            resp = Message.response(m, f"[QC failed after 3 attempts, best score {score}/10] x")
            resp.payload["_qc"] = {"checked": True, "score": score, "passed": False}
            return resp

        self.handle = AsyncMock(side_effect=_respond)

    async def start(self): pass

    async def stop(self): pass


class TestConductorFactionWiring:
    async def test_dispatch_builds_envelope(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:alice", "makers")
        reg.assign("agent:bob", "auditors")

        c = Conductor()
        c.register(_StubAgent("agent:bob"))

        msg = Message.request(new_mission_id(), "agent:alice", "agent:bob", "review this")
        resp = await c.dispatch(msg)
        assert resp.type == MessageType.RESPONSE
        env = msg.payload.get("_faction")
        assert env is not None
        assert env["sender_faction"] == "makers"
        assert env["recipient_faction"] == "auditors"
        assert env["expected_drift"] is True

    async def test_dispatch_no_envelope_for_external_sender(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:bob", "auditors")

        c = Conductor()
        c.register(_StubAgent("agent:bob"))

        msg = Message.request(new_mission_id(), "ext:user", "agent:bob", "review this")
        resp = await c.dispatch(msg)
        assert resp.type == MessageType.RESPONSE
        assert "_faction" not in msg.payload

    async def test_dispatch_records_outcome(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:alice", "makers")
        reg.assign("agent:bob", "auditors")

        c = Conductor()
        c.register(_StubAgent("agent:bob"))

        msg = Message.request(new_mission_id(), "agent:alice", "agent:bob", "review this")
        await c.dispatch(msg)
        rel = reg.relation("makers", "auditors")
        assert rel.interactions == 1
        assert rel.successes == 1

    async def test_adversarial_dispatch_redirects_to_martin(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:maker", "makers")
        reg.assign("agent:guardian", "guardians")
        reg.set_stance("makers", "guardians", FactionStance.ADVERSARIAL)

        c = Conductor()
        martin = _StubAgent("agent:martin", result="bridged")
        guardian = _StubAgent("agent:guardian")
        c.register(martin)
        c.register(guardian)

        msg = Message.request(new_mission_id(), "agent:maker", "agent:guardian", "let me in")
        resp = await c.dispatch(msg)
        # Message landet bei Martin, nicht beim Guardian
        martin.handle.assert_awaited_once()
        guardian.handle.assert_not_awaited()
        assert msg.recipient == "agent:martin"
        assert msg.payload["_bridged"] is True
        assert resp.type == MessageType.RESPONSE

    async def test_bridged_flag_prevents_second_redirect(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:maker", "makers")
        reg.assign("agent:guardian", "guardians")
        reg.set_stance("makers", "guardians", FactionStance.ADVERSARIAL)

        c = Conductor()
        martin = _StubAgent("agent:martin", result="bridged")
        guardian = _StubAgent("agent:guardian")
        c.register(martin)
        c.register(guardian)

        msg = Message.request(new_mission_id(), "agent:maker", "agent:guardian", "let me in")
        msg.payload["_bridged"] = True  # bereits gebridged → keine erneute Umleitung
        await c.dispatch(msg)
        guardian.handle.assert_awaited_once()
        martin.handle.assert_not_awaited()

    async def test_qc_fail_response_counts_as_failure(self):
        """Ende-zu-Ende: eine RESPONSE mit _qc.passed=False zählt als Misserfolg
        in der Cross-Faction-Relation (failures+1 statt successes+1)."""
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:martin", "operators")
        reg.assign("agent:maker", "makers")

        c = Conductor()
        c.register(_QCFailAgent("agent:maker", score=3))

        msg = Message.request(new_mission_id(), "agent:martin", "agent:maker", "build X")
        resp = await c.dispatch(msg)
        # Transport ist erfolgreich (RESPONSE), inhaltlich aber gescheitert
        assert resp.type == MessageType.RESPONSE
        rel = reg.relation("operators", "makers")
        assert rel.interactions == 1
        assert rel.failures == 1
        assert rel.successes == 0

    async def test_qc_pass_response_counts_as_success(self):
        """Gegenprobe: RESPONSE mit _qc.passed=True zählt als Erfolg."""
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:martin", "operators")
        reg.assign("agent:maker", "makers")

        class _QCPassAgent(_QCFailAgent):
            def __init__(self, agent_id: str):
                super().__init__(agent_id)

                def _respond(m: Message) -> Message:
                    resp = Message.response(m, "all good")
                    resp.payload["_qc"] = {"checked": True, "score": 9, "passed": True}
                    return resp

                self.handle = AsyncMock(side_effect=_respond)

        c = Conductor()
        c.register(_QCPassAgent("agent:maker"))

        msg = Message.request(new_mission_id(), "agent:martin", "agent:maker", "build X")
        await c.dispatch(msg)
        rel = reg.relation("operators", "makers")
        assert rel.interactions == 1
        assert rel.successes == 1
        assert rel.failures == 0


class TestTrustAging:
    """Evidenz-Halbwertszeit: Vertrauen verjährt statt zu versteifen."""

    def test_old_evidence_decays(self, monkeypatch):
        rel = FactionRelation(source="a", target="b")
        t0 = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        for _ in range(10):
            rel.record_outcome(True)
        trust_young = rel.trust  # 11/12 ≈ 0.917
        assert trust_young > 0.9

        # Zwei Halbwertszeiten später: ein einzelner Misserfolg
        monkeypatch.setattr(
            time, "time", lambda: t0 + 2 * FactionRelation.TRUST_HALF_LIFE_S
        )
        rel.record_outcome(False)
        # Alte 10 Erfolge zählen nur noch als 2.5 → Misserfolg wirkt stark
        assert rel.interactions < 4.0
        assert rel.trust < 0.75, "frische Evidenz muss altes Vertrauen bewegen können"

    def test_without_decay_trust_would_be_rigid(self, monkeypatch):
        # Kontrollrechnung: ohne Altern wäre trust nach 10+1 Outcomes 11/13 ≈ 0.846
        rel = FactionRelation(source="a", target="b")
        t0 = 1_000_000.0
        monkeypatch.setattr(time, "time", lambda: t0)
        for _ in range(10):
            rel.record_outcome(True)
        rel.record_outcome(False)  # gleicher Zeitpunkt → kein Decay
        assert abs(rel.trust - 11 / 13) < 1e-9

    def test_trust_bounded_and_starts_at_prior(self):
        rel = FactionRelation(source="a", target="b")
        assert rel.trust == 0.5
        for _ in range(50):
            rel.record_outcome(True)
        assert 0.0 < rel.trust < 1.0

    def test_trust_never_unlocks_bridge(self):
        # Sicherheitseigenschaft: Bridge hängt an stance, nicht an trust
        rel = FactionRelation(source="a", target="b", stance=FactionStance.ADVERSARIAL)
        for _ in range(100):
            rel.record_outcome(True)
        assert rel.trust > 0.9
        assert rel.requires_bridge() is True
