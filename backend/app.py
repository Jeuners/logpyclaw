"""
backend/app.py — AgentClaw v3 FastAPI Einstiegspunkt.
"""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from backend.agents.conductor import Conductor
from backend.agents.a2a_gateway import A2AGatewayAgent
from backend.agents.llm_agent import LLMAgent
from backend.api.agents import router as agents_router
from backend.api.chat import router as chat_router
from backend.api.missions import router as missions_router
from backend.api.a2a.gateway_router import router as a2a_router

# ── Globale Instanzen (DI ohne Framework) ────────────────────────────────────

conductor = Conductor()

def _boot_agents() -> None:
    """Startagenten registrieren. Wer keine API-Keys hat, bekommt einen Echo-Agent."""
    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434")

    # Default-Agent — funktioniert immer (kein LLM nötig)
    from backend.agents.base import AsyncAgent
    from backend.core.protocol import Message, MessageType

    class EchoAgent(AsyncAgent):
        async def handle(self, msg: Message) -> Message:
            clock = self.advance_clock(msg.clock)
            content = msg.payload.get("content", "")
            return Message.response(msg, f"[Echo] {content}", clock=clock)

    conductor.register(EchoAgent("agent:echo", "Echo"))

    # Alice — Ollama (wenn verfügbar)
    conductor.register(LLMAgent(
        agent_id="agent:alice",
        name="Alice",
        model=os.environ.get("OLLAMA_MODEL", "gemma4:e4b"),
        provider="ollama",
        soul="Du bist Alice, eine hilfreiche KI-Assistentin. Antworte präzise und freundlich.",
        ollama_url=ollama_url,
    ))

    # A2A-Gateway
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

# Statische Dateien
static_dir = Path(__file__).parent.parent / "frontend"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Router
app.include_router(agents_router,   prefix="/api")
app.include_router(chat_router,     prefix="/api")
app.include_router(missions_router, prefix="/api")
app.include_router(a2a_router)


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "frontend" / "index.html"
    if index.exists():
        return index.read_text()
    return HTMLResponse("<h1>AgentClaw v3</h1><p>Frontend not built yet.</p>")


@app.get("/ping")
async def ping():
    return {"pong": True, "version": "3.0.0"}


@app.get("/api/status")
async def status():
    agents = [a.to_dict() for a in conductor.list_agents()]
    return {"agents": agents, "missions": len(conductor.store.list_missions())}


# Conductor global zugänglich machen (für Router-Injection)
app.state.conductor = conductor
