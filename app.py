"""
app_new.py — AgentClaw v2 Einstiegspunkt.
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
    print("  pip install -r requirements_new.txt")
    print("  python app_new.py")
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
from services import init_services
logger.info("Initialisiere Services...")
container = init_services()
logger.info("Services OK — %d Skills registriert", len(container.registry.all()))

container.tasks.load_from_disk()
container.chat.set_task_service(container.tasks)
container.heartbeat.set_task_service(container.tasks)

# ── FastAPI Router registrieren ────────────────────────────────────────────────
# Router ohne eigenen Prefix → werden unter /api/ eingebunden
from api import agents as agents_api
from api import chat as chat_api
from api import tasks as tasks_api
from api import skills as skills_api
from api import health as health_api

for _router in [agents_api.router, chat_api.router, tasks_api.router,
                skills_api.router, health_api.router]:
    app.include_router(_router, prefix="/api")

# M2M Discovery Endpoint (Root-Level: /.well-known/...)
from api import m2m as m2m_api
app.include_router(m2m_api.router)

# Router mit eigenem Prefix (direkt einbinden, kein extra prefix="/api")
from api import providers as providers_api
from api import backup as backup_api
from api import upload as upload_api
from api import inbox as inbox_api
from api import memory as memory_api
from api import content as content_api
from api import tts as tts_api
from api import stats as stats_api
from api import watchdogs as watchdogs_api
from api import comfyui as comfyui_api

for _router in [
    providers_api.router,
    backup_api.router,
    upload_api.router,
    inbox_api.router,
    memory_api.router,
    content_api.router,
    tts_api.router,
    stats_api.router,
    watchdogs_api.router,   # /api/watchdogs*, /api/watchdog/*
    comfyui_api.router,     # /api/comfyui/*
]:
    app.include_router(_router)

logger.info("Alle FastAPI-Router registriert (%d Stück)", 15)

# ── NiceGUI Pages registrieren ────────────────────────────────────────────────
import ui.pages.home      # noqa: F401 — registriert @ui.page("/")
import ui.pages.chat      # noqa: F401 — registriert @ui.page("/chat/{agent_id}")
import ui.pages.tasks     # noqa: F401 — registriert @ui.page("/tasks")
import ui.pages.settings  # noqa: F401 — registriert @ui.page("/settings")
from nicegui import ui    # Re-import: lokales ui/-Paket hat nicegui.ui überschrieben
logger.info("NiceGUI Pages registriert")

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
            "message": str(exc) if settings.DEBUG else "Interner Fehler",
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
            show=not settings.NATIVE_MODE,
            favicon="🤖",
        )
    except KeyboardInterrupt:
        logger.info("Shutdown angefordert")
        sys.exit(0)
    except Exception:
        logger.exception("Kritischer Fehler beim Starten")
        sys.exit(1)
