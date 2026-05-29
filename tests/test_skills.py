"""tests/test_skills.py — Tests für Skill-Interface und WebSearchSkill."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.agents.conductor import Conductor
from backend.agents.martin import MartinAgent, QCConfig
from backend.agents.skill_agent import SkillAgent
from backend.core.protocol import Message, external_ref, new_mission_id
from backend.skills import Skill
from backend.skills.websearch import WebSearchSkill

# ── Skill ABC ─────────────────────────────────────────────────────────────────

class DummySkill(Skill):
    skill_id = "dummy"
    description = "Test skill"

    async def execute(self, query: str) -> str:
        return f"dummy:{query}"


class TestSkillInterface:
    def test_to_dict(self):
        s = DummySkill()
        d = s.to_dict()
        assert d["skill_id"] == "dummy"
        assert "description" in d

    @pytest.mark.asyncio
    async def test_execute(self):
        s = DummySkill()
        result = await s.execute("hello")
        assert result == "dummy:hello"


# ── SkillAgent ────────────────────────────────────────────────────────────────

class TestSkillAgent:
    def test_agent_id(self):
        agent = SkillAgent(DummySkill())
        assert agent.agent_id == "skill:dummy"

    def test_to_dict_has_faction(self):
        agent = SkillAgent(DummySkill())
        d = agent.to_dict()
        assert d["faction"] == "gatherers"
        assert d["skill_id"] == "dummy"

    @pytest.mark.asyncio
    async def test_handle_returns_result(self):
        agent = SkillAgent(DummySkill())
        await agent.start()
        msg = Message.request(
            mission_id=new_mission_id(),
            sender=external_ref("test"),
            recipient="skill:dummy",
            content="test query",
        )
        response = await agent.handle(msg)
        assert response.payload.get("result") == "dummy:test query"
        await agent.stop()


# ── WebSearchSkill ────────────────────────────────────────────────────────────

def _mock_response(data: dict) -> MagicMock:
    """httpx Response ist sync — MagicMock statt AsyncMock."""
    resp = MagicMock()
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


class TestWebSearchSkill:
    @pytest.mark.asyncio
    async def test_abstract_result(self):
        skill = WebSearchSkill()
        data = {
            "AbstractText": "Berlin ist die Hauptstadt Deutschlands.",
            "AbstractSource": "Wikipedia",
            "RelatedTopics": [],
            "Answer": "",
        }
        import httpx
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as m:
            m.return_value = _mock_response(data)
            result = await skill.execute("Berlin")
        assert "Wikipedia" in result or "Hauptstadt" in result

    @pytest.mark.asyncio
    async def test_direct_answer(self):
        skill = WebSearchSkill()
        data = {"AbstractText": "", "AbstractSource": "", "RelatedTopics": [], "Answer": "42"}
        import httpx
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as m:
            m.return_value = _mock_response(data)
            result = await skill.execute("answer to life")
        assert "42" in result

    @pytest.mark.asyncio
    async def test_no_results_fallback(self):
        skill = WebSearchSkill()
        # Stufe 1 (Instant Answer) leer → Skill fällt auf HTML-Suche (POST) zurück.
        data = {"AbstractText": "", "AbstractSource": "", "RelatedTopics": [], "Answer": ""}
        html_resp = MagicMock()
        html_resp.text = "<html><body>no results</body></html>"  # Parser findet nichts
        html_resp.raise_for_status.return_value = None
        import httpx
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock) as m_get, \
             patch.object(httpx.AsyncClient, "post", new_callable=AsyncMock) as m_post:
            m_get.return_value = _mock_response(data)
            m_post.return_value = html_resp
            result = await skill.execute("xyzzy123nonsense")
        assert "Keine Suchergebnisse" in result

    @pytest.mark.asyncio
    async def test_error_handling(self):
        skill = WebSearchSkill()
        import httpx
        with patch.object(httpx.AsyncClient, "get", side_effect=httpx.ConnectError("offline")):
            result = await skill.execute("test")
        assert "[WebSearch]" in result and "Fehler" in result


# ── Martin → #skill:websearch Routing ────────────────────────────────────────

class TestMartinSkillRouting:
    @pytest.fixture
    def conductor_with_skill(self):
        c = Conductor()
        c.register(SkillAgent(DummySkill()))
        return c

    @pytest.mark.asyncio
    async def test_skill_routing(self, conductor_with_skill):
        martin = MartinAgent(
            conductor=conductor_with_skill,
            qc=QCConfig(enabled=False),
        )
        conductor_with_skill.register(martin)
        await conductor_with_skill.start()

        msg = Message.request(
            mission_id=new_mission_id(),
            sender=external_ref("test"),
            recipient=MartinAgent.AGENT_ID,
            content="#skill:dummy was ist das?",
        )
        response = await martin.handle(msg)
        assert "dummy" in response.payload.get("result", "").lower()

        await conductor_with_skill.stop()
