"""
tests/conftest.py — Fixtures für AgentClaw Tests.

Isolation:
- `AGENTCLAW_DATA_DIR` wird VOR jedem Import auf ein tmp-Verzeichnis gesetzt,
  damit Tests niemals data/agents.json etc. der Produktion berühren.
- Der Import von `app.py` wird einmal pro Session ausgeführt und liefert die
  volle FastAPI-App mit allen 18 Routern → implizit L1-Smoke für jeden Import.
"""
import os
import shutil
import tempfile

# ── MUSS vor jedem anderen Import passieren ───────────────────────────────────
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="agentclaw_test_")
os.environ["AGENTCLAW_DATA_DIR"] = _TEST_DATA_DIR
# Alte Kompat-Variable aus v1 Tests
os.environ["AGENTCLAW_TEST_DATA_DIR"] = _TEST_DATA_DIR

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def tmp_data_dir():
    """Session-weites tmp-Datenverzeichnis; nach Tests aufgeräumt."""
    yield _TEST_DATA_DIR
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def agentclaw_app(tmp_data_dir):
    """Importiert die echte FastAPI-App aus app.py (alle Router + Services)."""
    import app as agentclaw_module  # noqa: F401 — Import hat Seiteneffekte (Router-Registrierung)

    # NiceGUI's 404-Handler braucht Werte aus add_run_config(), das sonst nur
    # ui.run() aufruft. Für Tests minimal konfigurieren.
    from nicegui import core as ng_core
    if not getattr(ng_core.app.config, "_has_run_config", False):
        ng_core.app.config.add_run_config(
            reload=False, title="test", viewport="width=device-width",
            favicon=None, dark=None, language="en",
            binding_refresh_interval=0.1, reconnect_timeout=3.0,
            message_history_length=0, tailwind=False, unocss=None,
            prod_js=True, show_welcome_message=False,
        )

    return agentclaw_module.app


@pytest.fixture(scope="session")
def client(agentclaw_app):
    """TestClient auf die echte App."""
    with TestClient(agentclaw_app) as c:
        yield c


@pytest.fixture(scope="session")
def container(agentclaw_app):
    """ServiceContainer (wird durch app-Import initialisiert)."""
    from services import get_services
    return get_services()


# ══════════════════════════════════════════════════════════════════════════════
# L2-Fixtures: Mocks für LLM + Background-Threads
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def clean_tasks():
    """Leert das globale _TASKS Dict vor UND nach dem Test."""
    from core.state import _TASKS, _tasks_lock
    with _tasks_lock:
        _TASKS.clear()
    yield
    with _tasks_lock:
        _TASKS.clear()


@pytest.fixture
def sync_spawn(monkeypatch):
    """
    Ersetzt `spawn_background` durch synchronen Aufruf.
    Tests laufen damit deterministisch ohne Threads.
    """
    def _sync(target, *args, **kwargs):
        try:
            target(*args, **kwargs)
        except Exception as e:
            # Simuliert Thread-Isolation: Fehler im Background nicht raisen
            import logging
            logging.getLogger(__name__).warning("sync_spawn swallowed: %s", e)
        return None

    # An BEIDEN Import-Orten patchen (core.config + services.task_service)
    monkeypatch.setattr("core.config.spawn_background", _sync)
    monkeypatch.setattr("services.task_service.spawn_background", _sync)
    return _sync


@pytest.fixture
def mock_llm(monkeypatch):
    """
    Mock-LLM mit programmierbarer Antwort.

    Verwendung:
        def test_foo(mock_llm, ...):
            mock_llm.set_reply("Hallo Welt")
            # oder:
            mock_llm.set_replies(["Antwort 1", "Antwort 2"])
            # oder Regex-basiert:
            mock_llm.on_prompt_contains("Tagesschau", "News-Text hier")

    Patched:
    - ChatService._call_llm (sync)
    - core.llm_stream.stream_llm (async generator)
    - core.llm.call_agent_text (sync, für Watchdog/Heartbeat)
    """
    state = {
        "replies": [],
        "default": "mock-reply",
        "patterns": [],  # list[(regex, reply)]
        "calls": [],     # list[str] — alle prompts
    }

    def set_reply(text: str):
        state["default"] = text
        state["replies"] = []

    def set_replies(texts: list[str]):
        state["replies"] = list(texts)

    def on_prompt_contains(pattern: str, reply: str):
        state["patterns"].append((pattern, reply))

    def _resolve_reply(prompt: str) -> str:
        state["calls"].append(prompt)
        for pat, reply in state["patterns"]:
            if pat.lower() in prompt.lower():
                return reply
        if state["replies"]:
            return state["replies"].pop(0)
        return state["default"]

    # 1. ChatService._call_llm (sync)
    def _fake_call_llm(self, agent, message, history, images, providers, **kwargs):
        return _resolve_reply(str(message))

    monkeypatch.setattr(
        "services.chat_service.ChatService._call_llm",
        _fake_call_llm,
        raising=True,
    )

    # 2. core.llm_stream.stream_llm (async generator)
    async def _fake_stream_llm(agent, messages, providers, think_override=None):
        prompt = ""
        for m in messages:
            if m.get("role") == "user":
                prompt = str(m.get("content", ""))
        reply = _resolve_reply(prompt)
        # Token-weise yield um echtes Streaming zu simulieren
        for i in range(0, len(reply), 20):
            yield reply[i:i + 20]

    monkeypatch.setattr("core.llm_stream.stream_llm", _fake_stream_llm)
    # ChatService importiert stream_llm lokal in stream_message — daher zusätzlich
    # als Attribut auf das Modul setzen reicht nicht, bei lokalem Import schon.

    # 3. core.llm.call_agent_text (für Watchdog/Heartbeat)
    def _fake_call_agent_text(agent, system_suffix, user_prompt, retries: int = 2):
        return _resolve_reply(str(user_prompt))

    monkeypatch.setattr("core.llm.call_agent_text", _fake_call_agent_text)

    # Public helper-Objekt (SimpleNamespace umgeht Class-Scope-Probleme)
    from types import SimpleNamespace
    return SimpleNamespace(
        set_reply=set_reply,
        set_replies=set_replies,
        on_prompt_contains=on_prompt_contains,
        calls=state["calls"],  # Live-Referenz, wird von _resolve_reply ergänzt
        _state=state,
    )


@pytest.fixture
def make_agent(container, agentclaw_app):
    """
    Factory: erstellt einen Agent über AgentService, löscht ihn am Testende.
    Kann mehrfach pro Test aufgerufen werden.
    """
    created_ids = []

    def _create(name: str = "TestBot", **overrides):
        import uuid
        unique_name = f"{name}_{uuid.uuid4().hex[:8]}"
        data = {
            "name": unique_name,
            "soul": overrides.get("soul", "Ich bin ein Test-Agent."),
            "model": overrides.get("model", "test-model"),
            "provider": overrides.get("provider", "ollama"),
            "skills": overrides.get("skills", []),
            "role": overrides.get("role", "tester"),
            "favorite": overrides.get("favorite", False),
            "max_tokens": overrides.get("max_tokens", 512),
        }
        agent = container.agents.create(data)
        created_ids.append(agent["id"])
        return agent

    yield _create

    for aid in created_ids:
        try:
            container.agents.delete(aid)
        except Exception:
            pass
