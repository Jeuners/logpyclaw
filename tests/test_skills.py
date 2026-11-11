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


# ── CodingSkill ───────────────────────────────────────────────────────────────

from backend.core.protocol import MessageType
from backend.skills.coding import CodingSkill, _extract_code


class TestCodingExtraction:
    def test_fenced_block(self):
        assert _extract_code("```python\nprint(1)\n```") == "print(1)"

    def test_fuehre_aus_prefix(self):
        assert _extract_code("führe aus: print(1)") == "print(1)"

    def test_bare_valid_code(self):
        assert _extract_code("x = 1\nprint(x)") == "x = 1\nprint(x)"

    def test_run_mid_sentence_is_not_code(self):
        # Regression: das englische Wort "run" mitten in Prosa darf nicht
        # den Rest des Satzes als Code extrahieren
        assert _extract_code(
            "please run these commands and report the FULL output of these "
            "commands (one by one, don't skip any — adjust per OS):"
        ) is None

    def test_english_prose_is_not_code(self):
        # Regression: Provisioning-Prompt-Fragmente, die via python -c
        # SyntaxErrors produzierten
        assert _extract_code(
            "STEP 1 — Build candidate list. Pick from Ollama's library based "
            "on available VRAM/unified memory (use ~75% of GPU VRAM, or ~60% "
            "of total unified memory on Apple Silicon as the budget)."
        ) is None
        assert _extract_code("anytime to re-verify the setup is healthy") is None

    def test_single_word_is_not_code(self):
        assert _extract_code("anytime") is None


class TestCodingSkill:
    @pytest.mark.asyncio
    async def test_executes_code(self):
        out = await CodingSkill().execute("```python\nprint('ok')\n```")
        assert "ok" in out

    @pytest.mark.asyncio
    async def test_prose_rejected_with_hint(self):
        out = await CodingSkill().execute(
            "Set up a working local LLM for me with tool-calling support."
        )
        assert "Kein ausführbarer Python-Code" in out
        assert "agent:coder" in out

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self):
        # Exit ≠ 0 → Exception, damit der SkillAgent eine ERROR-Message baut
        with pytest.raises(RuntimeError, match="Exit 1"):
            await CodingSkill().execute("```python\nimport sys\nsys.exit(1)\n```")

    @pytest.mark.asyncio
    async def test_syntax_error_in_fenced_block_raises(self):
        with pytest.raises(RuntimeError, match="SyntaxError"):
            await CodingSkill().execute("```python\ndef kaputt(\n```")

    @pytest.mark.asyncio
    async def test_skill_agent_marks_failure_as_error(self):
        agent = SkillAgent(CodingSkill())
        await agent.start()
        msg = Message.request(
            mission_id=new_mission_id(),
            sender=external_ref("test"),
            recipient="skill:coding",
            content="```python\nimport sys\nsys.exit(2)\n```",
        )
        resp = await agent.handle(msg)
        assert resp.type == MessageType.ERROR
        await agent.stop()


# ── FileSkill write ───────────────────────────────────────────────────────────

import os
from backend.skills.file import FileSkill


class TestFileSkillWrite:
    @pytest.mark.asyncio
    async def test_write_explicit_colon(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "notiz.txt"
        out = await FileSkill().execute(f"schreibe nach {target}: hallo welt")
        assert "📝" in out
        assert target.read_text() == "hallo welt"

    @pytest.mark.asyncio
    async def test_write_fenced_block(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "seite.html"
        q = f"speichere als {target}:\n```html\n<h1>Zeitung</h1>\n```"
        out = await FileSkill().execute(q)
        assert "📝" in out
        assert target.read_text() == "<h1>Zeitung</h1>"

    @pytest.mark.asyncio
    async def test_write_chained_previous_results(self, tmp_path, monkeypatch):
        # Regression: Martin-Chaining legt den Inhalt VOR den Befehl
        # ("[Vorherige Ergebnisse]\n<output>\n\n<step content>")
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "zeitung.html"
        q = (
            "[Vorherige Ergebnisse]\n"
            "```html\n<!DOCTYPE html><h1>HN Top 10</h1>\n```\n\n"
            f"schreibe das Ergebnis nach {target}"
        )
        out = await FileSkill().execute(q)
        assert "📝" in out
        assert "HN Top 10" in target.read_text()

    @pytest.mark.asyncio
    async def test_write_chained_plain_text(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "liste.txt"
        q = f"[Vorherige Ergebnisse]\n1. Eins\n2. Zwei\n\nspeichere unter {target}"
        out = await FileSkill().execute(q)
        assert "📝" in out
        assert "2. Zwei" in target.read_text()

    @pytest.mark.asyncio
    async def test_write_outside_home_blocked(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        out = await FileSkill().execute("schreibe nach /tmp/boese.txt: x")
        assert "⛔" in out
        assert not os.path.exists("/tmp/boese.txt") or open("/tmp/boese.txt").read() != "x"

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "neu" / "tief" / "datei.txt"
        out = await FileSkill().execute(f"schreibe nach {target}: ok")
        assert "📝" in out
        assert target.read_text() == "ok"

    @pytest.mark.asyncio
    async def test_write_without_content_hint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        out = await FileSkill().execute(f"schreibe nach {tmp_path}/leer.txt")
        assert "Kein Inhalt" in out

    @pytest.mark.asyncio
    async def test_read_ops_still_work(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "a.txt").write_text("inhalt-a")
        out = await FileSkill(root_dir=str(tmp_path)).execute(f"lese {tmp_path}/a.txt")
        assert "inhalt-a" in out
