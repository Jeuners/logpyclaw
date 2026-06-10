"""Tests für Martin — Operator-Agent mit Routing, QC-Loop, Bridge."""

import asyncio

import pytest

# ── Hilfs-Agenten ─────────────────────────────────────────────────────────────
from backend.agents.base import AsyncAgent
from backend.agents.conductor import Conductor
from backend.agents.martin import DelegationStep, MartinAgent, QCConfig
from backend.core.faction_protocol import FactionRegistry, FactionStance
from backend.core.protocol import Message, MessageType, new_mission_id


class EchoAgent(AsyncAgent):
    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, f"echo:{msg.payload.get('content','')}", clock=clock)


class ScoreAgent(AsyncAgent):
    """Gibt immer Score 8 zurück (für QC-Tests)."""
    def __init__(self, agent_id, name, score=8):
        super().__init__(agent_id, name)
        self.score = score

    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, str(self.score), clock=clock)


class LowScoreAgent(AsyncAgent):
    """Gibt immer Score 3 zurück (QC scheitert)."""
    async def handle(self, msg: Message) -> Message:
        clock = self.advance_clock(msg.clock)
        return Message.response(msg, "3", clock=clock)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_registry():
    FactionRegistry.reset()
    yield
    FactionRegistry.reset()


@pytest.fixture
async def conductor_with_echo():
    c = Conductor()
    c.register(EchoAgent("agent:echo", "Echo"))
    await c.start()
    yield c
    await c.stop()


@pytest.fixture
async def martin_basic(conductor_with_echo):
    m = MartinAgent(
        conductor=conductor_with_echo,
        qc=QCConfig(enabled=False),
    )
    conductor_with_echo.register(m)
    await m.start()
    return m


# ── Basis-Routing ─────────────────────────────────────────────────────────────

class TestMartinRouting:
    @pytest.mark.asyncio
    async def test_mention_routing(self, martin_basic, conductor_with_echo):
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:echo do this")
        resp = await martin_basic.handle(msg)
        assert resp.type == MessageType.RESPONSE
        assert "echo:" in resp.payload["result"]

    @pytest.mark.asyncio
    async def test_faction_routing(self, conductor_with_echo):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:echo", "makers")
        m = MartinAgent(conductor=conductor_with_echo, qc=QCConfig(enabled=False), registry=reg)
        conductor_with_echo.register(m)
        await m.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "#faction:makers build X")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE

    @pytest.mark.asyncio
    async def test_fallback_routing(self, martin_basic, conductor_with_echo):
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "do something without mention")
        resp = await martin_basic.handle(msg)
        # Fällt auf ersten verfügbaren Nicht-Martin-Agent zurück
        assert resp.type in (MessageType.RESPONSE, MessageType.ERROR)

    @pytest.mark.asyncio
    async def test_fallback_prefers_high_trust_faction(self):
        """Trust-gewichteter Fallback: Agent mit höchstem operators→Fraktion-Trust gewinnt."""
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:trusted", "makers")
        rel = reg.relation("operators", "makers")
        for _ in range(10):
            rel.record_outcome(True)  # trust ≈ 0.92 > 0.5 (kein Fraktions-Prior)

        c = Conductor()
        c.register(EchoAgent("agent:plain", "Plain"))
        c.register(EchoAgent("agent:trusted", "Trusted"))
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), registry=reg)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "kein Prefix")
        target = await m._resolve_target("kein Prefix", msg)
        assert target == "agent:trusted"

    @pytest.mark.asyncio
    async def test_no_target_returns_response_not_error(self):
        # Martin ohne Conductor und ohne LLM-Router → kein Target
        m = MartinAgent(conductor=None, qc=QCConfig(enabled=False))
        await m.start()
        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "do X")
        resp = await m.handle(msg)
        # Ohne Conductor → Message.error
        assert resp.type == MessageType.ERROR or "No route" in str(resp.payload)


# ── QC-Loop ───────────────────────────────────────────────────────────────────

class TestMartinQC:
    @pytest.mark.asyncio
    async def test_qc_passes_high_score(self):
        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        c.register(ScoreAgent("agent:auditor", "Auditor", score=9))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=1, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE
        # Kein "QC failed" im Ergebnis
        assert "QC failed" not in resp.payload.get("result", "")
        await c.stop()

    @pytest.mark.asyncio
    async def test_qc_retries_low_score(self):
        retry_count = {"n": 0}

        class CountingAgent(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                retry_count["n"] += 1
                clock = self.advance_clock(msg.clock)
                return Message.response(msg, f"attempt {retry_count['n']}", clock=clock)

        c = Conductor()
        c.register(CountingAgent("agent:maker", "Maker"))
        c.register(LowScoreAgent("agent:auditor", "LowScoreAuditor"))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=2, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        resp = await m.handle(msg)
        # max_retries=2 → 3 Maker-Aufrufe (1 original + 2 retries)
        assert retry_count["n"] == 3
        assert "QC failed" in resp.payload.get("result", "")
        await c.stop()

    @pytest.mark.asyncio
    async def test_qc_disabled_no_auditor_call(self):
        auditor_called = {"n": 0}

        class TrackingAuditor(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                auditor_called["n"] += 1
                return Message.response(msg, "10", clock=self.advance_clock(msg.clock))

        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        c.register(TrackingAuditor("agent:auditor", "TrackingAuditor"))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=False, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        await m.handle(msg)
        assert auditor_called["n"] == 0
        await c.stop()

    @pytest.mark.asyncio
    async def test_qc_prompt_contains_task_and_result(self):
        """Der Auditor-Prompt enthält Aufgabe UND Ergebnis."""
        captured = {"content": ""}

        class CapturingAuditor(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                captured["content"] = msg.payload.get("content", "")
                return Message.response(msg, "9", clock=self.advance_clock(msg.clock))

        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        c.register(CapturingAuditor("agent:auditor", "CapturingAuditor"))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=1, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        await m.handle(msg)
        # Neues Format: untrusted Content in <task>/<result>-Delimitern
        assert "<task>" in captured["content"]
        assert "@agent:maker do X" in captured["content"]
        assert "<result>" in captured["content"]
        assert "echo:" in captured["content"]
        assert "UNTRUSTED" in captured["content"]
        await c.stop()

    @pytest.mark.asyncio
    async def test_auditor_failure_no_retry(self):
        """Auditor-Ausfall → min_score wird durchgewunken, kein Retry."""
        maker_calls = {"n": 0}

        class CountingMaker(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                maker_calls["n"] += 1
                return Message.response(msg, "made it", clock=self.advance_clock(msg.clock))

        class BrokenAuditor(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                raise RuntimeError("auditor down")

        c = Conductor()
        c.register(CountingMaker("agent:maker", "Maker"))
        c.register(BrokenAuditor("agent:auditor", "BrokenAuditor"))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=2, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        resp = await m.handle(msg)
        # Nur 1 Delegations-Call — kein erzwungener Retry-Loop
        assert maker_calls["n"] == 1
        assert resp.type == MessageType.RESPONSE
        assert "QC failed" not in resp.payload.get("result", "")
        await c.stop()

    @pytest.mark.asyncio
    async def test_qc_pass_sets_qc_metadata(self):
        """QC besteht → _qc {checked: True, passed: True, score >= min}."""
        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        c.register(ScoreAgent("agent:auditor", "Auditor", score=9))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=1, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        resp = await m.handle(msg)
        qc = resp.payload.get("_qc")
        assert qc is not None
        assert qc["checked"] is True
        assert qc["passed"] is True
        assert qc["score"] >= 7
        await c.stop()

    @pytest.mark.asyncio
    async def test_qc_fail_sets_qc_metadata_passed_false(self):
        """QC scheitert endgültig → RESPONSE mit _qc.passed False, Score < min."""
        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        c.register(LowScoreAgent("agent:auditor", "LowScoreAuditor"))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=1, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:maker do X")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE
        qc = resp.payload.get("_qc")
        assert qc is not None
        assert qc["checked"] is True
        assert qc["passed"] is False
        assert qc["score"] < 7
        # Menschlicher Texthinweis bleibt zusätzlich erhalten
        assert "QC failed" in resp.payload.get("result", "")
        await c.stop()

    @pytest.mark.asyncio
    async def test_skill_delegation_has_no_qc_metadata(self):
        """Skill-Delegation → kein _qc-Feld (deterministisch, kein QC-Loop)."""
        c = Conductor()
        c.register(EchoAgent("skill:coding", "Coding"))
        c.register(ScoreAgent("agent:auditor", "Auditor", score=9))
        await c.start()

        m = MartinAgent(
            conductor=c,
            qc=QCConfig(enabled=True, min_score=7, max_retries=1, auditor_id="agent:auditor"),
        )
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@skill:coding write X")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE
        assert "_qc" not in resp.payload
        await c.stop()


# ── Plan-Ausführung (Wellen-Parallelisierung) ─────────────────────────────────

class TestMartinExecutePlan:
    @pytest.mark.asyncio
    async def test_dependent_steps_run_in_waves(self):
        """Step 1 läuft allein, Step 2+3 (hängen von 1 ab) laufen parallel."""
        events: list[tuple[str, str]] = []

        class RecordingAgent(AsyncAgent):
            async def handle(self, msg: Message) -> Message:
                tag = msg.payload.get("content", "").strip().split()[-1]
                events.append(("start", tag))
                await asyncio.sleep(0.05)
                events.append(("end", tag))
                return Message.response(msg, f"done:{tag}", clock=self.advance_clock(msg.clock))

        steps = [
            DelegationStep(agent_id="agent:worker", content="step s1"),
            DelegationStep(agent_id="agent:worker", content="step s2", depends_on=[0]),
            DelegationStep(agent_id="agent:worker", content="step s3", depends_on=[0]),
        ]

        async def planner(content: str):
            return steps

        c = Conductor()
        c.register(RecordingAgent("agent:worker", "Worker"))
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_planner_fn=planner)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "do plan")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE

        # Welle 1: s1 läuft allein (start + end bevor irgendetwas anderes startet)
        assert events[0] == ("start", "s1")
        assert events[1] == ("end", "s1")
        # Welle 2: s2 und s3 starten beide, bevor einer von ihnen endet → parallel
        assert {events[2], events[3]} == {("start", "s2"), ("start", "s3")}
        assert {events[4], events[5]} == {("end", "s2"), ("end", "s3")}

        # Ergebnis-Aggregation in stabiler Step-Reihenfolge
        combined = resp.payload.get("result", "")
        assert combined.index("Schritt 1/3") < combined.index("Schritt 2/3")
        assert combined.index("Schritt 2/3") < combined.index("Schritt 3/3")
        assert "done:s1" in combined
        await c.stop()

    @pytest.mark.asyncio
    async def test_cyclic_depends_on_marks_failed(self):
        """Zyklische depends_on → Steps werden failed markiert, keine Endlosschleife."""
        steps = [
            DelegationStep(agent_id="agent:worker", content="step a", depends_on=[1]),
            DelegationStep(agent_id="agent:worker", content="step b", depends_on=[0]),
        ]

        async def planner(content: str):
            return steps

        c = Conductor()
        c.register(EchoAgent("agent:worker", "Worker"))
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_planner_fn=planner)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "do plan")
        resp = await asyncio.wait_for(m.handle(msg), timeout=5.0)
        assert resp.type == MessageType.RESPONSE
        combined = resp.payload.get("result", "")
        assert "zyklische oder ungültige depends_on" in combined
        await c.stop()

    @pytest.mark.asyncio
    async def test_invalid_depends_on_index_marks_failed(self):
        """depends_on auf nicht-existenten Index → failed statt Hänger."""
        steps = [
            DelegationStep(agent_id="agent:worker", content="step a"),
            DelegationStep(agent_id="agent:worker", content="step b", depends_on=[99]),
        ]

        async def planner(content: str):
            return steps

        c = Conductor()
        c.register(EchoAgent("agent:worker", "Worker"))
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_planner_fn=planner)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "do plan")
        resp = await asyncio.wait_for(m.handle(msg), timeout=5.0)
        combined = resp.payload.get("result", "")
        # Schritt 1 läuft normal, Schritt 2 wird als failed markiert
        assert "echo:" in combined
        assert "zyklische oder ungültige depends_on" in combined
        await c.stop()


# ── Operator-Bridge ───────────────────────────────────────────────────────────

class TestMartinBridge:
    @pytest.mark.asyncio
    async def test_bridge_translates_adversarial(self):
        reg = FactionRegistry.load_defaults()
        reg.assign("agent:maker", "makers")
        reg.assign("agent:guardian", "guardians")
        reg.set_stance("makers", "guardians", FactionStance.ADVERSARIAL)
        reg.assign("agent:echo", "operators")

        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        await c.start()

        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), registry=reg)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "agent:maker", MartinAgent.AGENT_ID, "Guardian, let me in")
        # Füge Bridge-Envelope in payload ein
        msg.payload["_faction"] = {
            "sender_faction": "makers",
            "recipient_faction": "guardians",
            "stance": "adversarial",
            "requires_bridge": True,
            "expected_drift": True,
        }
        resp = await m.handle(msg)
        assert resp.type in (MessageType.RESPONSE, MessageType.ERROR)
        await c.stop()


# ── CDC-Clock ─────────────────────────────────────────────────────────────────

class TestMartinCDC:
    @pytest.mark.asyncio
    async def test_clock_advances_on_handle(self):
        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False))
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:echo hi")
        before_ops = m._op_count
        await m.handle(msg)
        assert m._op_count > before_ops
        await c.stop()

    def test_cdc_context_returns_string(self):
        m = MartinAgent()
        s = m.cdc_context()
        assert isinstance(s, str)

    def test_to_dict_has_faction(self):
        m = MartinAgent()
        d = m.to_dict()
        assert d["faction"] == "operators"
        assert "qc" in d


# ── LLM-Router ────────────────────────────────────────────────────────────────

class TestMartinLLMRouter:
    @pytest.mark.asyncio
    async def test_llm_router_called_when_no_prefix(self):
        """Router-Fn wird aufgerufen wenn kein @/# Prefix vorhanden."""
        router_called = {"n": 0}

        async def fake_router(content: str) -> str | None:
            router_called["n"] += 1
            return "agent:echo"

        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        await c.start()

        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_router_fn=fake_router)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "Erkläre was CDC ist")
        resp = await m.handle(msg)
        assert router_called["n"] == 1
        assert "echo" in resp.payload.get("result", "").lower()
        await c.stop()

    @pytest.mark.asyncio
    async def test_llm_router_not_called_with_at_prefix(self):
        """@-Prefix überspringt den LLM-Router."""
        router_called = {"n": 0}

        async def fake_router(content: str) -> str | None:
            router_called["n"] += 1
            return "agent:echo"

        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        await c.start()

        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_router_fn=fake_router)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "@agent:echo direkt")
        await m.handle(msg)
        assert router_called["n"] == 0
        await c.stop()

    @pytest.mark.asyncio
    async def test_llm_router_fallback_when_returns_none(self):
        """Wenn Router None zurückgibt, greift der Fallback auf ersten Agenten."""
        async def null_router(content: str) -> str | None:
            return None

        c = Conductor()
        c.register(EchoAgent("agent:echo", "Echo"))
        await c.start()

        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_router_fn=null_router)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "keine Präferenz")
        resp = await m.handle(msg)
        # Fallback greift, Antwort kommt (nicht leer)
        assert resp.payload.get("result") is not None
        await c.stop()


class TestExplicitAddressingBeatsPlanner:
    """Regression: Planner darf explizite @agent:-Adressierung nicht überstimmen.

    Bug: Martins LLM-Planner zerlegte auch explizit adressierte Tasks in
    eigene Steps und ersetzte dabei die Original-Spezifikation."""

    async def test_explicit_agent_skips_planner(self):
        planner_called = {"n": 0}

        async def planner(content):
            planner_called["n"] += 1
            return [DelegationStep(agent_id="skill:coding", content="umgeschrieben")]

        c = Conductor()
        maker = EchoAgent("agent:maker", "Maker")
        c.register(maker)
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_planner_fn=planner)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        original = "@agent:maker baue exakt DIESE Spezifikation"
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, original)
        resp = await m.handle(msg)

        assert planner_called["n"] == 0, "Planner darf bei expliziter Adressierung nicht laufen"
        assert resp.type == MessageType.RESPONSE
        # EchoAgent gibt den Content zurück — Original-Spezifikation unverändert
        assert "DIESE Spezifikation" in str(resp.payload.get("result", ""))
        await c.stop()

    async def test_no_explicit_syntax_still_uses_planner(self):
        async def planner(content):
            return [DelegationStep(agent_id="agent:maker", content=content)]

        c = Conductor()
        c.register(EchoAgent("agent:maker", "Maker"))
        await c.start()
        m = MartinAgent(conductor=c, qc=QCConfig(enabled=False), llm_planner_fn=planner)
        c.register(m)
        await m.start()

        mid = new_mission_id()
        msg = Message.request(mid, "ext:user", MartinAgent.AGENT_ID, "freier Text ohne Syntax")
        resp = await m.handle(msg)
        assert resp.type == MessageType.RESPONSE
        await c.stop()
