"""
app.py — AgentClaw v2 Einstiegspunkt.
NiceGUI + FastAPI. Ersetzt app.py + templates/index.html.

Architektur:
- NiceGUI: Server-seitige reaktive UI (reagiert auf Server-Events via ui.timer)
- FastAPI: REST-API für A2A, M2M, externe Clients
- EventService: internes Event-Bus (ersetzt Flask-SocketIO)
- asyncio Scheduler: ersetzt threading-basierten scheduler_loop()
"""
import logging
import subprocess
import sys

# ── Dependency-Check ──────────────────────────────────────────────────────────
_REQUIRED = ["dotenv", "nicegui", "fastapi", "pydantic_settings", "httpx", "sqlmodel"]
_MISSING = []
for _pkg in _REQUIRED:
    try:
        __import__(_pkg)
    except ImportError:
        _MISSING.append(_pkg)

if _MISSING:
    print("=" * 60)
    print(f"Fehlende Pakete: {', '.join(_MISSING)}")
    print("Bitte einmalig ausführen:")
    print()
    print("  python -m venv .venv")
    print("  source .venv/bin/activate")
    print("  pip install -r requirements.txt")
    print("  python app.py")
    print("=" * 60)
    sys.exit(1)

# ── .env laden ────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env-Variablen werden dann aus der Umgebung gelesen

from config.logging_config import setup_logging
setup_logging()

logger = logging.getLogger(__name__)

from config.settings import settings
logger.info("AgentClaw v2 — Settings: HOST=%s PORT=%d", settings.HOST, settings.PORT)

# ── FastAPI + NiceGUI App ──────────────────────────────────────────────────────
from nicegui import app, ui
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import os
from core.config import BASE_DIR

# Static Files (uploads etc.)
_static_dir = os.path.join(BASE_DIR, "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

# ── Services initialisieren ────────────────────────────────────────────────────
# Wichtig: passiert am Import-Zeitpunkt, damit Router via get_services() Zugriff
# haben. Disk-IO (load_from_disk) gehört aber in lifespan — siehe unten.
# Cross-Service-Verdrahtung (set_task_service / set_dispatcher) geschieht in
# ServiceContainer.__init__ — hier nicht mehr duplizieren.
from services import init_services
logger.info("Initialisiere Services...")
container = init_services()
logger.info("Services OK — %d Skills registriert", len(container.registry.all()))

# ── FastAPI Router registrieren ────────────────────────────────────────────────
# Konvention: JEDER Router definiert seinen eigenen prefix (meist "/api").
# Ausnahme: api/m2m.py hat keinen Prefix (Root-Level /.well-known/...).
from api import (
    agents as _agents_api,
    chat as _chat_api,
    tasks as _tasks_api,
    skills as _skills_api,
    health as _health_api,
    m2m as _m2m_api,
    providers as _providers_api,
    backup as _backup_api,
    upload as _upload_api,
    inbox as _inbox_api,
    activity as _activity_api,
    content as _content_api,
    tts as _tts_api,
    transcribe as _transcribe_api,
    stats as _stats_api,
    watchdogs as _watchdogs_api,
    comfyui as _comfyui_api,
    themes as _themes_api,
    tools as _tools_api,
    chrome_ws as _chrome_ws_api,
    ltx_batch as _ltx_batch_api,
    temporal as _temporal_api,
    web_bridge as _web_bridge_api,
)
from lab.api import lab_router as _lab_api  # 🧪 Communication Lab — isoliert
from lab.api import dilation_demo_router as _dilation_demo_api  # 🧪 Time Dilation Demo

_API_MODULES = (
    _agents_api, _chat_api, _tasks_api, _skills_api, _health_api,
    _m2m_api,
    _providers_api, _backup_api, _upload_api, _inbox_api, _activity_api,
    _content_api, _tts_api, _transcribe_api, _stats_api, _watchdogs_api,
    _comfyui_api, _themes_api, _tools_api, _chrome_ws_api, _ltx_batch_api,
    _temporal_api,
    _lab_api,
    _dilation_demo_api,
)
for _mod in _API_MODULES:
    app.include_router(_mod.router)

# Web-Bridge (externer Token-geschützter Mirror für dillenberg.net)
app.include_router(_web_bridge_api.health_router)
app.include_router(_web_bridge_api.router)

logger.info("Alle FastAPI-Router registriert (%d Stück)", len(_API_MODULES))

# ── NiceGUI Pages registrieren ────────────────────────────────────────────────
import ui.pages.home        # noqa: F401 — registriert @ui.page("/")
import ui.pages.chat        # noqa: F401 — registriert @ui.page("/chat/{agent_id}")
import ui.pages.tasks       # noqa: F401 — registriert @ui.page("/tasks")
import ui.pages.settings    # noqa: F401 — registriert @ui.page("/settings")
import ui.pages.skills      # noqa: F401 — registriert @ui.page("/skills")
import ui.pages.agent_edit  # noqa: F401 — registriert /agent/edit/{id} + /agent/new
import ui.pages.backup      # noqa: F401 — registriert @ui.page("/backup")
import ui.pages.network     # noqa: F401 — registriert @ui.page("/network")
import ui.pages.insights    # noqa: F401 — registriert @ui.page("/insights")
import ui.pages.ltx_batch  # noqa: F401 — registriert @ui.page("/ltx-batch")
import ui.pages.temporal   # noqa: F401 — registriert @ui.page("/temporal")
import lab.ui.lab_page              # noqa: F401 — 🧪 registriert @ui.page("/lab")
import lab.ui.spacetime_page        # noqa: F401 — registriert @ui.page("/lab/spacetime")
# dilation-demo wird als plain FastAPI HTMLResponse serviert (kein NiceGUI)
from nicegui import ui    # Re-import: lokales ui/-Paket hat nicegui.ui überschrieben
logger.info("NiceGUI Pages registriert")

# ── Time Dilation Demo — standalone HTML, kein NiceGUI ───────────────────────
from fastapi.responses import HTMLResponse as _HTMLResponse
from lab.api.dilation_demo_router import _PAGE_HTML as _dil_html

@app.get("/dilation-demo", response_class=_HTMLResponse)
def dilation_demo_html():
    return _HTMLResponse(_dil_html)

# ── /chat Redirect (HTTP 302 statt NiceGUI-Page) ──────────────────────────────
from fastapi.responses import RedirectResponse

@app.get("/chat")
def chat_redirect_http():
    """Leitet /chat zum ersten Agenten weiter (HTTP 302)."""
    try:
        agents = container.agents.list_all()
        agents_sorted = sorted(
            agents,
            key=lambda a: (not a.get("favorite"), a.get("name", "").lower())
        )
        if agents_sorted:
            return RedirectResponse(url=f"/chat/{agents_sorted[0]['id']}", status_code=302)
    except Exception:
        pass
    return RedirectResponse(url="/", status_code=302)

# ── Error Handler ──────────────────────────────────────────────────────────────
from core.errors import AgentClawError


@app.exception_handler(AgentClawError)
async def handle_app_error(request: Request, exc: AgentClawError):
    logger.error("AgentClawError [%d]: %s", exc.status_code, exc.message)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.message, "details": getattr(exc, "details", None)},
    )


@app.exception_handler(Exception)
async def handle_generic_error(request: Request, exc: Exception):
    # Bekannte FastAPI-Exceptions nicht abfangen
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
        raise exc
    logger.exception("Unerwarteter Fehler")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Interner Fehler",
            "message": str(exc),  # DEBUG: immer zeigen
        },
    )


# ── Lifecycle ──────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app):
    # Startup
    logger.info("=== AGENTCLAW v2 STARTUP ===")
    try:
        from storage.database import run_migrations
        run_migrations()
    except Exception as e:
        logger.warning("DB-Migration übersprungen: %s", e)
    try:
        container.tasks.load_from_disk()
    except Exception as e:
        logger.warning("Task-Load übersprungen: %s", e)
    try:
        container.events.replay_from_disk(max_age_minutes=60)
    except Exception as e:
        logger.warning("Event-Replay übersprungen: %s", e)
    from core.scheduler import start_scheduler
    await start_scheduler()
    logger.info("Scheduler gestartet. Bereit auf %s:%d", settings.HOST, settings.PORT)

    yield  # App läuft

    # Shutdown
    logger.info("=== AGENTCLAW v2 SHUTDOWN ===")
    try:
        from core.scheduler import stop_scheduler
        stop_scheduler()
    except Exception:
        pass
    try:
        from core.thread_pools import shutdown_all
        shutdown_all(wait=False)
    except Exception:
        pass
    container.tasks.save_pending()
    container.cleanup()
    logger.info("Shutdown abgeschlossen")


app.router.lifespan_context = lifespan


# ── Einfacher Health-Endpunkt (wird auch von health_api abgedeckt) ─────────────
@app.get("/ping")
async def ping():
    return {"pong": True}


# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Starte AgentClaw v2")
    logger.info("DEBUG=%s  NATIVE_MODE=%s", settings.DEBUG, settings.NATIVE_MODE)
    logger.info("=" * 60)

    try:
        ui.run(
            title="AgentClaw",
            host=settings.HOST,
            port=settings.PORT,
            dark=True,
            native=settings.NATIVE_MODE,
            reload=settings.DEBUG,
            storage_secret=settings.SECRET_KEY,
            show=False,
            favicon="🤖",
        )
    except KeyboardInterrupt:
        logger.info("Shutdown angefordert")
        sys.exit(0)
    except Exception:
        logger.exception("Kritischer Fehler beim Starten")
        sys.exit(1)
