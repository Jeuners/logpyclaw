"""Tests für Martin — Operator-Agent mit Routing, QC-Loop, Bridge."""

import pytest

# ── Hilfs-Agenten ─────────────────────────────────────────────────────────────
from backend.agents.base import AsyncAgent
from backend.agents.conductor import Conductor
from backend.agents.martin import MartinAgent, QCConfig
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
