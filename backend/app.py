"""
backend/app.py — LogpyClaw v3 FastAPI entry point.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Logging früh initialisieren: BroadcastHandler an Root anhängen,
# damit Live-Log alle module-level logger erfasst.
from backend.core.logging import get_logger  # noqa: E402

get_logger("logpyclaw.boot").info("LogpyClaw v3 boot")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.agents.a2a_gateway import A2AGatewayAgent
from backend.agents.claude_agent import ClaudeSSHAgent
from backend.agents.conductor import Conductor
from backend.agents.llm_agent import LLMAgent
from backend.agents.martin import MartinAgent
from backend.agents.skill_agent import SkillAgent
from backend.api.a2a.gateway_router import router as a2a_router
from backend.api.agents import router as agents_router
from backend.api.chat import router as chat_router
from backend.api.chrome_ws import router as chrome_ws_router
from backend.api.deploys import router as deploys_router
from backend.api.dreams import router as dreams_router
from backend.api.factions import router as factions_router
from backend.api.files import router as files_router
from backend.api.keys import router as keys_router
from backend.api.logs import router as logs_router
from backend.api.memory import router as memory_router
from backend.api.missions import router as missions_router
from backend.api.openai_compat import router as openai_router
from backend.api.rss import router as rss_router
from backend.api.teams import router as teams_router
from backend.api.web_bridge import router as web_bridge_router
from backend.config import get_settings
from backend.core.memory import SemanticMemory
from backend.i18n import locale_from_header
from backend.skills.browser import BrowserSkill
from backend.skills.chrome_browser import ChromeBrowserSkill
from backend.skills.coding import CodingSkill
from backend.skills.comfyui import ComfyUISkill
from backend.skills.deploy import DeploySkill
from backend.skills.file import FileSkill
from backend.skills.gmail import GmailSkill
from backend.skills.linkedin import LinkedInSkill
from backend.skills.ltxvideo import LTXVideoSkill
from backend.skills.physorg import PhysOrgSkill
from backend.skills.rss import RSSSkill
from backend.skills.telegram import TelegramSkill
from backend.skills.transcription import TranscriptionSkill
from backend.skills.urlfetch import UrlFetchSkill
from backend.skills.websearch import WebSearchSkill
from backend.skills.whatsapp import WhatsAppSkill
from backend.skills.wikipedia import WikipediaSkill
from backend.skills.youtube import YouTubeSkill

# ── Global instances ──────────────────────────────────────────────────────────

conductor = Conductor(db_url=get_settings().db_url)
memory = SemanticMemory()  # semantisches Langzeit-Gedächtnis (RAG, sqlite-vec)


_DEFAULT_MARTIN_PERSONA = (
    "Du bist Martin, der persönliche Assistent des Nutzers. Du bist hilfsbereit, "
    "direkt und sprichst per Du auf Deutsch. Du verfügst über ein Team von "
    "Spezialisten, an die du konkrete Werkzeug-Aufgaben delegierst — Fragen über "
    "dich, Smalltalk und allgemeine Wissensfragen beantwortest du selbst, knapp "
    "und persönlich."
)


def _make_planner_fn(cfg, temperature: float = 0.3, persona: str = ""):
    """Baut Martins async Front-Desk-Funktion.

    Martin entscheidet pro Nachricht: entweder er antwortet SELBST in seiner
    Persona (Smalltalk, Identität, allgemeine Fragen) — Rückgabe als str —,
    oder er erstellt einen Delegations-Plan (eine/mehrere DelegationSteps) für
    echte Werkzeug-Aufgaben. None = kein verwertbares Ergebnis.
    """
    persona = persona.strip() or _DEFAULT_MARTIN_PERSONA
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

    async def planner_fn(content: str) -> str | list[DelegationStep] | None:
        agents = [
            a for a in conductor.list_agents() if a.agent_id not in ("agent:martin", "a2a:gateway")
        ]
        valid_ids = {a.agent_id for a in agents}

        # Agentenliste mit Beschreibungen für besseres Routing
        def _agent_desc(a) -> str:
            desc = getattr(a, "description", "") or getattr(getattr(a, "_skill", None), "description", "")
            name = a.name
            return f"- {a.agent_id}: {name}" + (f" — {desc}" if desc else "")

        agent_list = "\n".join(_agent_desc(a) for a in agents)

        prompt = (
            f"{persona}\n\n"
            "Du bekommst eine Nachricht vom Nutzer. Entscheide:\n\n"
            "A) SELBST ANTWORTEN — wenn es Smalltalk ist, eine Frage über dich oder "
            "deine Fähigkeiten, oder eine allgemeine Wissens-/Gesprächsfrage, die du "
            "ohne Werkzeug beantworten kannst. Antworte direkt und persönlich.\n"
            '   → {"reply": "<deine Antwort an den Nutzer>"}\n\n'
            "B) DELEGIEREN — wenn es eine konkrete Werkzeug-Aufgabe ist (Bild "
            "generieren, Web durchsuchen, Datei lesen/schreiben, Code ausführen, "
            "Nachricht senden, deployen, transkribieren, YouTube …). Erstelle einen "
            "Routing-Plan an die passenden Spezialisten.\n"
            '   → {"tasks": [{"agent": "<agent_id>", "content": "<anweisung>", "depends_on": [<idx>]}, ...]}\n\n'
            f"Verfügbare Spezialisten:\n{agent_list}\n\n"
            f"Nachricht des Nutzers: {content[:600]}\n\n"
            "Routing-Regeln für Fall B (höchste Priorität zuerst):\n"
            "- 'linkedin' im Text → skill:linkedin\n"
            "- 'whatsapp', 'sende nachricht' → skill:whatsapp\n"
            "- 'telegram' → skill:telegram\n"
            "- 'bild', 'generiere', 'comfyui', 'zeichne' → skill:comfyui\n"
            "- 'video', 'ltx', 'animier' → skill:ltxvideo\n"
            "- 'suche', 'search', 'web', 'google' → skill:websearch\n"
            "- 'wikipedia', 'wiki' → skill:wikipedia\n"
            "- 'youtube', 'video herunterladen' → skill:youtube\n"
            "- 'rss', 'news', 'feed', 'hackernews', 'tagesschau' → skill:rss\n"
            "- 'datei', 'verzeichnis', 'ls', 'cat', 'lese datei' → skill:file\n"
            "- 'code', 'programmier', 'python', 'skript' → skill:coding oder agent:coder\n"
            "- 'transkrib', 'audio', 'video transkript' → skill:transcription\n"
            "- 'deploy', 'publish', 'publiziere', 'online stellen', 'list deploys', 'undeploy' → skill:deploy\n"
            "- 'claude', 'frontier', 'komplex', 'schreib', 'essay', 'analyse', 'refactor', 'architektur' → agent:claude\n"
            "- Längere Texte/Analysen, die du bewusst an einen Spezialisten abgeben willst → agent:alice\n"
            "  (allgemeine Wissens-/Gesprächsfragen beantwortest du dagegen SELBST via Fall A)\n"
            "- Mehrere gleichartige Tasks (z.B. '5 Bilder') → einen Task pro Einheit\n\n"
            "CHAINING: Wenn ein Task das Ergebnis eines vorherigen braucht (z.B. Bild → Video),\n"
            'setze "depends_on": [<index>] mit dem 0-basierten Index des vorherigen Steps.\n'
            "Der Folge-Task bekommt den Output (inkl. Dateinamen) automatisch in seinem Kontext.\n\n"
            "Beispiel: 'Bild von Katze, dann Video draus':\n"
            '  {"tasks": [\n'
            '    {"agent": "skill:comfyui", "content": "cat sitting in garden, photorealistic"},\n'
            '    {"agent": "skill:ltxvideo", "content": "prompt: cat slowly looks around, gentle breeze", "depends_on": [0]}\n'
            "  ]}\n\n"
            'Antworte NUR mit JSON — ENTWEDER {"reply": "..."} (Fall A) ODER '
            '{"tasks": [{"agent": "<agent_id>", "content": "<anweisung>", "depends_on": [<idx>]}, ...]} (Fall B).'
        )

        try:
            from backend.core.key_pool import get_groq_key
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {get_groq_key()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "llama-3.3-70b-versatile",
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                    },
                )
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]

            data = _extract_json(raw)

            # Fall A: Martin antwortet selbst in Persona
            reply = data.get("reply")
            if isinstance(reply, str) and reply.strip():
                return reply.strip()

            # Fall B: Delegations-Plan
            tasks = data.get("tasks", [])
            if not tasks:
                return None

            steps = []
            for t in tasks:
                aid = _validate_id(t.get("agent", ""), valid_ids)
                if aid:
                    deps = t.get("depends_on") or []
                    if not isinstance(deps, list):
                        deps = [int(deps)] if str(deps).isdigit() else []
                    steps.append(DelegationStep(
                        agent_id=aid,
                        content=t.get("content", content),
                        depends_on=[int(d) for d in deps if str(d).strip().lstrip("-").isdigit()],
                    ))
            return steps or None

        except Exception:
            get_logger("logpyclaw.planner").exception("Planner-Fehler")
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


def _load_initiatives() -> list[dict]:
    """Optionaler Top-Level-Key `initiatives:` aus agents.yaml lesen.

    Fehlt der Key (Normalfall), wird eine leere Liste zurückgegeben — keine
    Tasks, kein Log-Spam. AgentsFile ignoriert den Extra-Key, deshalb hier
    additiv direkt aus dem rohen YAML gelesen.
    """
    import os

    import yaml

    yaml_path = Path(__file__).parent.parent / "agents.yaml"
    if not yaml_path.exists():
        return []
    try:
        raw = os.path.expandvars(yaml_path.read_text(encoding="utf-8"))
        data = yaml.safe_load(raw) or {}
        return data.get("initiatives") or []
    except yaml.YAMLError:
        return []


def _boot_agents() -> None:
    from backend.agents.base import AsyncAgent
    from backend.agents.martin import QCConfig
    from backend.core.agent_config import (
        A2AGatewayConfig,
        ClaudeAgentConfig,
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
        "websearch":     lambda c: WebSearchSkill(),
        "comfyui":       lambda c: ComfyUISkill(endpoint=c.get("endpoint") or cfg.comfyui_url),
        "ltxvideo":      lambda c: LTXVideoSkill(endpoint=c.get("endpoint") or cfg.comfyui_url),
        "whatsapp":      lambda c: WhatsAppSkill(),
        "coding":        lambda c: CodingSkill(),
        "gmail":         lambda c: GmailSkill(),
        "browser":       lambda c: BrowserSkill(),
        "chrome_browser": lambda c: ChromeBrowserSkill(),
        "deploy":        lambda c: DeploySkill(**c),
        "urlfetch":      lambda c: UrlFetchSkill(),
        "file":          lambda c: FileSkill(**c),
        "physorg":       lambda c: PhysOrgSkill(),
        "rss":           lambda c: RSSSkill(),
        "linkedin":      lambda c: LinkedInSkill(**c),
        "telegram":      lambda c: TelegramSkill(**c),
        "wikipedia":     lambda c: WikipediaSkill(),
        "youtube":       lambda c: YouTubeSkill(),
        "transcription": lambda c: TranscriptionSkill(**c),
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
                faction=entry.faction,
                ollama_url=cfg.ollama_url,
                temperature=entry.temperature,
                max_tokens=entry.max_tokens,
                conductor=conductor,
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
                llm_planner_fn=_make_planner_fn(cfg, entry.temperature, entry.persona),
                model=entry.model or cfg.ollama_model,
                temperature=entry.temperature,
            ))

        elif isinstance(entry, SkillAgentConfig):
            if not entry.enabled:
                continue
            builder = skill_map.get(entry.skill_id)
            if not builder:
                raise SystemExit(f"[boot] Unbekannte skill_id: {entry.skill_id}")
            conductor.register(SkillAgent(builder(entry.config)))

        elif isinstance(entry, ClaudeAgentConfig):
            if not entry.enabled:
                continue
            conductor.register(ClaudeSSHAgent(
                agent_id=entry.id,
                name=entry.name,
                claude_bin=entry.claude_bin,
                model=entry.model,
                goal=entry.goal,
                faction=entry.faction,
                timeout=entry.timeout,
            ))

        elif isinstance(entry, A2AGatewayConfig):
            conductor.register(A2AGatewayAgent(
                agent_id="a2a:gateway",
                default_recipient=entry.default_recipient,
                conductor=conductor,
            ))


@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from backend.core.faction_protocol import FactionRegistry
    from backend.services.dream import run_dream_cycle
    from backend.services.rss import fetch_all as rss_fetch_all

    # Standard-Faktionen (operators/makers/gatherers/auditors/scribes/guardians)
    FactionRegistry.load_defaults()

    _boot_agents()
    await conductor.start()

    cfg = get_settings()
    scheduler = AsyncIOScheduler()
    # täglich um 3:00 Uhr nachts
    scheduler.add_job(
        run_dream_cycle,
        "cron", hour=3, minute=0,
        args=[conductor, cfg.comfyui_url],
    )
    # RSS alle 30 Minuten
    scheduler.add_job(rss_fetch_all, "interval", minutes=30, id="rss_fetch")
    scheduler.start()

    # Initialer RSS-Fetch beim Start
    import asyncio as _asyncio
    _asyncio.create_task(rss_fetch_all())

    # Optionaler Initiative-Loop: nur wenn agents.yaml einen initiatives:-Key hat.
    # Fehlt er (Normalfall), passiert nichts — Default-Verhalten unverändert.
    app.state.initiative = None
    initiatives = _load_initiatives()
    if initiatives:
        from backend.services.initiative import InitiativeService

        app.state.initiative = InitiativeService(conductor, initiatives)
        await app.state.initiative.start()

    yield

    if app.state.initiative is not None:
        await app.state.initiative.stop()
    scheduler.shutdown(wait=False)
    await conductor.stop()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="LogpyClaw v3", version="3.0.0", lifespan=lifespan)
_cors_origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_cors_origins, allow_methods=["*"], allow_headers=["*"])


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
app.include_router(openai_router)  # OpenAI-kompatibel: /v1/chat/completions, /v1/models
app.include_router(chrome_ws_router)
app.include_router(keys_router)
app.include_router(deploys_router)
app.include_router(files_router)
app.include_router(rss_router)
app.include_router(logs_router, prefix="/api")
app.include_router(dreams_router, prefix="/api")
app.include_router(memory_router, prefix="/api")


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
app.state.memory = memory
