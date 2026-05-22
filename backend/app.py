"""
backend/app.py — LogpyClaw v3 FastAPI entry point.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.agents.a2a_gateway import A2AGatewayAgent
from backend.agents.conductor import Conductor
from backend.agents.llm_agent import LLMAgent
from backend.agents.martin import MartinAgent
from backend.agents.skill_agent import SkillAgent
from backend.api.a2a.gateway_router import router as a2a_router
from backend.api.agents import router as agents_router
from backend.api.chat import router as chat_router
from backend.api.factions import router as factions_router
from backend.api.missions import router as missions_router
from backend.api.teams import router as teams_router
from backend.api.logs import router as logs_router
from backend.api.web_bridge import router as web_bridge_router
from backend.config import get_settings
from backend.i18n import locale_from_header
from backend.skills.browser import BrowserSkill
from backend.skills.coding import CodingSkill
from backend.skills.comfyui import ComfyUISkill
from backend.skills.gmail import GmailSkill
from backend.skills.urlfetch import UrlFetchSkill
from backend.skills.websearch import WebSearchSkill
from backend.skills.whatsapp import WhatsAppSkill

# ── Global instances ──────────────────────────────────────────────────────────

conductor = Conductor(db_url=get_settings().db_url)


def _make_planner_fn(cfg):
    """Baut eine async Planner-Funktion für Martin.

    Gibt eine Liste von DelegationSteps zurück — ein Step für einfache
    Anfragen, mehrere Steps für Batch/Multi-Task-Anfragen.
    """
    import json as _json

    import httpx

    from backend.agents.martin import DelegationStep

    def _extract_json(raw: str) -> dict:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return _json.loads(raw[start:end])
        raise ValueError("kein JSON gefunden")

    def _validate_id(target: str, valid_ids: set[str]) -> str | None:
        if target in valid_ids:
            return target
        # LLM schreibt manchmal "skill:comfyui: Comfyui" → links davon nehmen
        parts = target.split(":")
        if len(parts) >= 2:
            candidate = parts[0] + ":" + parts[1].split(" ")[0]
            if candidate in valid_ids:
                return candidate
        for aid in valid_ids:
            if aid in target:
                return aid
        return None

    async def planner_fn(content: str) -> list[DelegationStep] | None:
        agents = [
            a for a in conductor.list_agents() if a.agent_id not in ("agent:martin", "a2a:gateway")
        ]
        valid_ids = {a.agent_id for a in agents}
        agent_list = "\n".join(f"- {a.agent_id}: {a.name}" for a in agents)

        prompt = (
            "Du bist ein Planungs-Agent. Analysiere die Anfrage und erstelle einen Ausführungsplan.\n\n"
            f"Verfügbare Agenten:\n{agent_list}\n\n"
            f"Anfrage: {content[:400]}\n\n"
            "Regeln:\n"
            "- Wenn die Anfrage MEHRERE gleichartige Aufgaben enthält (z.B. '5 Bilder', '3 Texte'), "
            "erstelle einen Task pro Einheit mit variierten Inhalten.\n"
            "- Wenn die Anfrage einen einzelnen Schritt erfordert, erstelle genau einen Task.\n"
            "- 'content' ist die konkrete Anweisung an den Agenten.\n\n"
            'Antworte NUR mit JSON: {"tasks": [{"agent": "<agent_id>", "content": "<anweisung>"}, ...]}'
        )

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{cfg.ollama_url}/api/chat",
                    json={
                        "model": cfg.ollama_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "stream": False,
                    },
                )
                r.raise_for_status()
                raw = r.json()["message"]["content"]

            data = _extract_json(raw)
            tasks = data.get("tasks", [])
            if not tasks:
                return None

            steps = []
            for t in tasks:
                aid = _validate_id(t.get("agent", ""), valid_ids)
                if aid:
                    steps.append(DelegationStep(agent_id=aid, content=t.get("content", content)))
            return steps or None

        except Exception:
            import traceback

            traceback.print_exc()
        return None

    return planner_fn


def _load_agents_yaml():
    import os

    import yaml
    from pydantic import ValidationError

    from backend.core.agent_config import AgentsFile

    yaml_path = Path(__file__).parent.parent / "agents.yaml"
    if not yaml_path.exists():
        raise SystemExit(f"[boot] agents.yaml nicht gefunden: {yaml_path}")
    raw = os.path.expandvars(yaml_path.read_text(encoding="utf-8"))
    try:
        data = yaml.safe_load(raw)
        return AgentsFile.model_validate(data)
    except yaml.YAMLError as e:
        raise SystemExit(f"[boot] YAML-Syntaxfehler in agents.yaml:\n{e}") from e
    except ValidationError as e:
        raise SystemExit(f"[boot] Ungültige agents.yaml:\n{e}") from e


def _boot_agents() -> None:
    from backend.agents.base import AsyncAgent
    from backend.agents.martin import QCConfig
    from backend.core.agent_config import (
        A2AGatewayConfig,
        EchoAgentConfig,
        LLMAgentConfig,
        MartinAgentConfig,
        SkillAgentConfig,
    )
    from backend.core.protocol import Message
    from backend.i18n import t

    cfg = get_settings()
    agents_file = _load_agents_yaml()

    skill_map = {
        "websearch": lambda c: WebSearchSkill(),
        "comfyui":   lambda c: ComfyUISkill(endpoint=c.get("endpoint") or cfg.comfyui_url),
        "whatsapp":  lambda c: WhatsAppSkill(),
        "coding":    lambda c: CodingSkill(),
        "gmail":     lambda c: GmailSkill(),
        "browser":   lambda c: BrowserSkill(),
        "urlfetch":  lambda c: UrlFetchSkill(),
    }

    class EchoAgent(AsyncAgent):
        async def handle(self, msg: Message) -> Message:
            clock = self.advance_clock(msg.clock)
            content = msg.payload.get("content", "")
            return Message.response(msg, f"[Echo] {content}", clock=clock)

    for entry in agents_file.agents:
        if isinstance(entry, EchoAgentConfig):
            conductor.register(EchoAgent(entry.id, entry.name))

        elif isinstance(entry, LLMAgentConfig):
            if not entry.enabled:
                continue
            conductor.register(LLMAgent(
                agent_id=entry.id,
                name=entry.name,
                model=entry.model or cfg.ollama_model,
                provider=entry.provider,
                soul=entry.soul or t("agent.default_soul"),
                ollama_url=cfg.ollama_url,
            ))

        elif isinstance(entry, MartinAgentConfig):
            qc = QCConfig(
                enabled=entry.qc.enabled,
                min_score=entry.qc.min_score,
                max_retries=entry.qc.max_retries,
                auditor_id=entry.qc.auditor_id or cfg.martin_qc_auditor_id,
            )
            conductor.register(MartinAgent(
                conductor=conductor,
                qc=qc,
                llm_planner_fn=_make_planner_fn(cfg),
                model=entry.model or cfg.ollama_model,
            ))

        elif isinstance(entry, SkillAgentConfig):
            if not entry.enabled:
                continue
            builder = skill_map.get(entry.skill_id)
            if not builder:
                raise SystemExit(f"[boot] Unbekannte skill_id: {entry.skill_id}")
            conductor.register(SkillAgent(builder(entry.config)))

        elif isinstance(entry, A2AGatewayConfig):
            conductor.register(A2AGatewayAgent(
                agent_id="a2a:gateway",
                default_recipient=entry.default_recipient,
                conductor=conductor,
            ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _boot_agents()
    await conductor.start()
    yield
    await conductor.stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="LogpyClaw v3", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def locale_middleware(request: Request, call_next):
    """Injects request.state.locale from Accept-Language header."""
    request.state.locale = locale_from_header(request.headers.get("accept-language"))
    return await call_next(request)


static_dir = Path(__file__).parent.parent / "frontend"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(agents_router, prefix="/api")
app.include_router(chat_router, prefix="/api")
app.include_router(missions_router, prefix="/api")
app.include_router(factions_router, prefix="/api")
app.include_router(teams_router, prefix="/api")
app.include_router(a2a_router)
app.include_router(web_bridge_router)
app.include_router(logs_router, prefix="/api")


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root():
    index = Path(__file__).parent.parent / "frontend" / "index.html"
    if index.exists():
        return index.read_text()
    return HTMLResponse("<h1>LogpyClaw v3</h1>")


@app.get("/ping")
async def ping():
    cfg = get_settings()
    return {"pong": True, "version": "3.0.0", "model": cfg.ollama_model}


@app.get("/api/status")
async def status():
    agents = [a.to_dict() for a in conductor.list_agents()]
    return {"agents": agents, "missions": len(conductor.store.list_missions())}


app.state.conductor = conductor
