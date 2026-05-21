"""
backend/app.py — AgentClaw v3 FastAPI entry point.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.agents.a2a_gateway import A2AGatewayAgent
from backend.agents.conductor import Conductor
from backend.agents.llm_agent import LLMAgent
from backend.api.a2a.gateway_router import router as a2a_router
from backend.api.agents import router as agents_router
from backend.api.chat import router as chat_router
from backend.api.missions import router as missions_router
from backend.api.web_bridge import router as web_bridge_router
from backend.i18n import locale_from_header

# ── Global instances ──────────────────────────────────────────────────────────

conductor = Conductor()


def _boot_agents() -> None:
    from backend.agents.base import AsyncAgent
    from backend.core.protocol import Message
    from backend.i18n import t

    class EchoAgent(AsyncAgent):
        async def handle(self, msg: Message) -> Message:
            clock = self.advance_clock(msg.clock)
            content = msg.payload.get("content", "")
            return Message.response(msg, f"[Echo] {content}", clock=clock)

    conductor.register(EchoAgent("agent:echo", "Echo"))

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    conductor.register(LLMAgent(
        agent_id="agent:alice",
        name="Alice",
        model=os.environ.get("OLLAMA_MODEL", "gemma4:e4b"),
        provider="ollama",
        soul=t("agent.default_soul"),
        ollama_url=ollama_url,
    ))

    gw = A2AGatewayAgent(
        agent_id="a2a:gateway",
        default_recipient="agent:alice",
        conductor=conductor,
    )
    conductor.register(gw)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _boot_agents()
    await conductor.start()
    yield
    await conductor.stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="AgentClaw v3", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def locale_middleware(request: Request, call_next):
    """Injects request.state.locale from Accept-Language header."""
    request.state.locale = locale_from_header(request.headers.get("accept-language"))
    return await call_next(request)


static_dir = Path(__file__).parent.parent / "frontend"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(agents_router,     prefix="/api")
app.include_router(chat_router,       prefix="/api")
app.include_router(missions_router,   prefix="/api")
app.include_router(a2a_router)
app.include_router(web_bridge_router)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "frontend" / "index.html"
    if index.exists():
        return index.read_text()
    return HTMLResponse("<h1>AgentClaw v3</h1>")


@app.get("/ping")
async def ping():
    return {"pong": True, "version": "3.0.0"}


@app.get("/api/status")
async def status():
    agents = [a.to_dict() for a in conductor.list_agents()]
    return {"agents": agents, "missions": len(conductor.store.list_missions())}


app.state.conductor = conductor
