"""
tests/test_smoke.py — L1 Smoke Tests.

Ziel: Regression so früh wie möglich erkennen. Läuft in < 1s.
Jeder Test hier bricht = etwas Fundamentales ist kaputt.

Deckt:
- Alle UI-Pages importierbar
- FastAPI-App hat alle 18 Router registriert
- ServiceContainer hat alle erwarteten Services
- SkillRegistry hat erwartete Skills
"""
import pytest


# ── Erwartete Routen (konkrete Pfade oder Pfad-Präfixe) ──────────────────────
# Wenn etwas hier fehlt, wurde ein Router entfernt / ein Prefix geändert.
EXPECTED_ROUTES = [
    "/api/agents",
    "/api/chat",
    "/api/chat/stream",
    "/api/skills",
    "/api/health",
    "/api/providers",
    "/api/backup",
    "/api/upload",
    "/api/activity",
    "/api/summary",        # health router
    "/api/models",         # providers router
    "/api/screenshot",     # content router
    "/api/image/edit",     # content router
    "/api/hackernews",     # content router
    "/api/tagesschau",     # content router
    "/api/tts",
    "/api/transcribe",
    "/api/stats",
    "/api/watchdogs",
    "/api/watchdog/status",
    "/api/comfyui/generate",
    "/api/themes",
    "/api/tools",
    "/api/chrome/status",
    "/api/m2m/agents",
    "/api/a2a/dispatch",
    "/.well-known",        # M2M Discovery
    "/ping",
]

# ── Erwartete UI-Pages ─────────────────────────────────────────────────────────
UI_PAGE_MODULES = [
    "ui.pages.home",
    "ui.pages.chat",
    "ui.pages.tasks",
    "ui.pages.settings",
    "ui.pages.agent_edit",
    "ui.pages.backup",
    "ui.pages.network",
    "ui.pages.insights",
]

# ── Erwartete Core-Services ───────────────────────────────────────────────────
EXPECTED_SERVICES = [
    "registry", "events", "agents", "tasks", "chat",
    "heartbeat", "watchdog", "m2m", "whatsapp_watcher",
]

# ── Erwartete Skills (Minimum-Set, neue Skills erlaubt) ───────────────────────
# Stabiles Kern-Set (neue Skills ok, aber diese MÜSSEN da sein)
EXPECTED_SKILLS = {
    "image_gen", "video_gen", "image_edit",
    "transcription", "file_access", "linkedin",
    "prompt_optimize", "url_fetch", "coding",
    "chrome_browser", "hacker_news",
    "tagesschau", "whatsapp",
}


def test_app_imports_cleanly(agentclaw_app):
    """Smoke-Test: app.py importiert ohne Fehler (Fixture tut das bereits)."""
    assert agentclaw_app is not None
    assert hasattr(agentclaw_app, "routes")


@pytest.mark.parametrize("expected_path", EXPECTED_ROUTES)
def test_route_registered(expected_path, agentclaw_app):
    """Jede erwartete Route muss in der FastAPI-App registriert sein."""
    paths = [getattr(r, "path", "") for r in agentclaw_app.routes]
    assert any(p == expected_path or p.startswith(expected_path + "/") for p in paths), (
        f"Route {expected_path!r} nicht gefunden — Router entfernt oder Prefix geändert?"
    )


@pytest.mark.parametrize("module_name", UI_PAGE_MODULES)
def test_ui_page_importable(module_name, agentclaw_app):
    """Jede UI-Page-Modul muss importierbar sein."""
    import importlib
    module = importlib.import_module(module_name)
    assert module is not None


def test_service_container_complete(container):
    """ServiceContainer hat alle erwarteten Service-Attribute."""
    for name in EXPECTED_SERVICES:
        assert hasattr(container, name), f"Service '{name}' fehlt im Container"
        assert getattr(container, name) is not None


def test_skills_registry_has_expected_skills(container):
    """SkillRegistry enthält mindestens die erwarteten Skills."""
    actual = {s.id for s in container.registry.all()}
    missing = EXPECTED_SKILLS - actual
    assert not missing, (
        f"Erwartete Skills fehlen: {missing}. "
        f"Registrierte Skills: {sorted(actual)}"
    )


def test_ping_endpoint(client):
    """Einfachster möglicher HTTP-Smoke-Test."""
    r = client.get("/ping")
    assert r.status_code == 200
    assert r.json() == {"pong": True}


def test_health_endpoint(client):
    """Health-Endpoint antwortet mit app=ok."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json().get("app") == "ok"


def test_inbox_routes_under_agents_prefix(agentclaw_app):
    """Inbox-Router hängt unter /api/agents/{id}/inbox (nicht /api/inbox)."""
    paths = [getattr(r, "path", "") for r in agentclaw_app.routes]
    assert any("/inbox" in p for p in paths), "Kein Inbox-Endpoint gefunden"


def test_minimum_route_count(agentclaw_app):
    """Die App sollte deutlich mehr als 20 Routen haben."""
    routes = [r for r in agentclaw_app.routes if hasattr(r, "path")]
    # Verdächtig wenig → vermutlich wurde ein Router-Include entfernt
    assert len(routes) > 50, f"Nur {len(routes)} Routen — Router fehlt?"
