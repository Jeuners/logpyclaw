"""
api/web_bridge.py — Externer Web-Bridge (dillenberg.net über SSH-Tunnel auf c2).

Schmaler, Token-geschützter Mirror auf /api/* nur für externe Web-Calls.
Lokale UI bleibt unangetastet — diese Routes sind eine Surface speziell für
dillenberg.net's article-agent.

Endpoints (prefix /ext/dilles/v1):
- GET  /health             — no auth, returns {ok: true}
- POST /chat               — sync chat (proxy auf /api/chat)
- POST /chat/stream        — SSE stream (proxy auf /api/chat/stream POST)
- GET  /task/{task_id}     — task status
- GET  /skill/{name}/check — skill availability for offline-badges

Auth: X-AgentClaw-Token Header gegen WEB_BRIDGE_TOKEN env-var.
Logging: JSON-Lines nach logs/web_bridge_access.jsonl (eine Zeile pro Request).
"""
import json
import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.chat import (
    ChatRequest,
    ChatStreamRequest,
    chat as _chat_handler,
    _stream_response,
)
from api.tasks import get_task as _get_task
from services import get_services

logger = logging.getLogger(__name__)

_TOKEN_ENV = "WEB_BRIDGE_TOKEN"
_BASE_DIR = Path(__file__).resolve().parent.parent
_LOG_PATH = _BASE_DIR / "logs" / "web_bridge_access.jsonl"
_LOG_PATH.parent.mkdir(exist_ok=True)


def _expected_token() -> str:
    return (os.environ.get(_TOKEN_ENV) or "").strip()


def _log(request: Request, *, status: int, ms: int, denied: bool = False, extra: dict | None = None) -> None:
    try:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "method": request.method,
            "path": str(request.url.path),
            "status": status,
            "ms": ms,
            "denied": denied,
            "ua": request.headers.get("user-agent", "")[:120],
            "fwd": request.headers.get("x-forwarded-for", "")[:80],
        }
        if extra:
            entry.update(extra)
        with _LOG_PATH.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("web_bridge log write failed")


async def verify_token(
    request: Request,
    x_agentclaw_token: str | None = Header(default=None),
) -> None:
    expected = _expected_token()
    if not expected:
        _log(request, status=503, ms=0, denied=True, extra={"reason": "token_not_configured"})
        raise HTTPException(503, "web-bridge not configured")
    if not x_agentclaw_token or x_agentclaw_token != expected:
        _log(request, status=401, ms=0, denied=True)
        raise HTTPException(401, "invalid token")


# ─── Health (no auth) ─────────────────────────────────────────────────────────
health_router = APIRouter(prefix="/ext/dilles/v1", tags=["web-bridge-health"])


@health_router.get("/health")
def health(request: Request):
    has_token = bool(_expected_token())
    _log(request, status=200, ms=0, extra={"token_configured": has_token})
    return {"ok": True, "service": "agentclaw-web-bridge", "token_configured": has_token}


# ─── Authenticated routes ─────────────────────────────────────────────────────
router = APIRouter(
    prefix="/ext/dilles/v1",
    tags=["web-bridge"],
    dependencies=[Depends(verify_token)],
)


@router.post("/chat")
async def chat_sync(req: ChatRequest, request: Request):
    t0 = time.time()
    try:
        result = await _chat_handler(req)
        _log(request, status=200, ms=int((time.time() - t0) * 1000),
             extra={"agent_id": req.agent_id, "skill": getattr(result, "skill", None)})
        return result
    except HTTPException as e:
        _log(request, status=e.status_code, ms=int((time.time() - t0) * 1000),
             extra={"agent_id": req.agent_id, "error": str(e.detail)[:200]})
        raise


@router.post("/chat/stream")
async def chat_stream(req: ChatStreamRequest, request: Request):
    t0 = time.time()
    _log(request, status=200, ms=int((time.time() - t0) * 1000),
         extra={"agent_id": req.agent_id, "streaming": True})
    return await _stream_response(
        req.agent_id, req.message, req.think,
        images=req.images, audio=req.audio,
    )


@router.get("/task/{task_id}")
def task_status(task_id: str, request: Request):
    t0 = time.time()
    try:
        result = _get_task(task_id)
        _log(request, status=200, ms=int((time.time() - t0) * 1000),
             extra={"task_id": task_id})
        return result
    except HTTPException as e:
        _log(request, status=e.status_code, ms=int((time.time() - t0) * 1000),
             extra={"task_id": task_id})
        raise


@router.get("/skill/{name}/check")
def skill_check(name: str, request: Request):
    t0 = time.time()
    services = get_services()
    available = False
    try:
        skill = services.registry.get(name)
        if skill is not None:
            try:
                from storage.providers import load_providers
                available = bool(skill.is_available(load_providers()))
            except Exception:
                available = True  # Existiert, kann nicht geprüft → optimistisch
    except Exception:
        available = False
    _log(request, status=200, ms=int((time.time() - t0) * 1000),
         extra={"skill": name, "available": available})
    return {"skill": name, "available": available}


@router.get("/agents")
def list_agents_brief(request: Request):
    """Schlanke Liste {id, name, role} — kein soul/skills, nur für Discovery."""
    t0 = time.time()
    services = get_services()
    agents = services.agents.list_all()
    brief = [
        {"id": a["id"], "name": a.get("name"), "role": a.get("role")}
        for a in agents
    ]
    _log(request, status=200, ms=int((time.time() - t0) * 1000),
         extra={"count": len(brief)})
    return {"agents": brief}


# ─── Direct image generation (ComfyUI) ────────────────────────────────────────
from pydantic import BaseModel, Field as _Field


class _ImageRequest(BaseModel):
    prompt: str = _Field(..., min_length=1, max_length=2000)
    width: int = _Field(default=1024, ge=64, le=2048)
    height: int = _Field(default=1024, ge=64, le=2048)
    seed: int | None = None


@router.post("/generate-image")
async def generate_image(req: _ImageRequest, request: Request):
    """Synchrone ComfyUI-Generierung. Returns {image: data-url, filename}."""
    from api.comfyui import comfyui_generate, GenerateRequest as _GReq
    t0 = time.time()
    try:
        result = await comfyui_generate(_GReq(
            prompt=req.prompt, width=req.width, height=req.height, seed=req.seed
        ))
        _log(request, status=200, ms=int((time.time() - t0) * 1000),
             extra={"skill": "image_gen", "filename": result.get("filename")})
        return result
    except HTTPException as e:
        _log(request, status=e.status_code, ms=int((time.time() - t0) * 1000),
             extra={"skill": "image_gen", "error": str(e.detail)[:200]})
        raise
