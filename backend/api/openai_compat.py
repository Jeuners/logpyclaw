"""
backend/api/openai_compat.py — OpenAI-kompatibler Provider-Layer.

Stellt agentclaw als „LLM-Provider" bereit, damit externe Systeme (z. B.
dillenberg.net oder jedes OpenAI-SDK) die Agenten wie ein Modell ansprechen:

  POST /v1/chat/completions   { "model": "martin", "messages": [...] }
  GET  /v1/models

`model` ist die Agent-ID — Kurzform (`alice`, `claude`, `martin`) oder voll
(`agent:alice`, `skill:websearch`). `martin` gibt automatisches Routing + QC.

Auth: `Authorization: Bearer <WEB_BRIDGE_TOKEN>` (oder Header X-LogpyClaw-Token).
Ist kein Token konfiguriert, ist der Endpoint offen (nur für lokalen Dev).
"""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

from backend.api.agent_select import is_allowed, resolve_agent
from backend.config import get_settings
from backend.core.protocol import Message, external_ref, new_mission_id

router = APIRouter()


def _auth_ok(authorization: str | None, x_token: str | None) -> bool:
    expected = get_settings().web_bridge_token
    if not expected:
        return True  # kein Token gesetzt → offen (lokaler Dev)
    tok = None
    if authorization and authorization.lower().startswith("bearer "):
        tok = authorization[7:].strip()
    return tok == expected or x_token == expected


def _unauth():
    return JSONResponse(
        {"error": {"message": "Invalid or missing API key.", "type": "invalid_request_error", "code": "invalid_api_key"}},
        status_code=401,
    )


def _messages_to_prompt(messages: list) -> str:
    """Baut aus dem OpenAI-Message-Array einen Prompt.

    Agenten sind zustandslos → Verlauf wird als Text mitgegeben, damit
    Mehrfach-Turns Kontext behalten. Bei einer einzelnen User-Message:
    diese direkt (kein Rollen-Präfix).
    """
    if not messages:
        return ""
    system = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system" and m.get("content"))
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    users = [m for m in convo if m.get("role") == "user"]

    if len(convo) <= 1 or (len(users) == 1 and not any(m.get("role") == "assistant" for m in convo)):
        body = convo[-1].get("content", "") if convo else ""
    else:
        lines = []
        for m in convo:
            who = "User" if m.get("role") == "user" else "Assistant"
            lines.append(f"{who}: {m.get('content', '')}")
        body = "\n".join(lines)

    return f"{system}\n\n{body}".strip() if system else body


async def _run_agent(conductor, agent_id: str, content: str) -> str:
    mission_id = new_mission_id()
    conductor.store.register_mission(
        mission_id,
        {"mission_id": mission_id, "title": "openai-compat", "state": "running",
         "started_at": time.time(), "source": "openai"},
    )
    msg = Message.request(
        mission_id=mission_id,
        sender=external_ref("openai"),
        recipient=agent_id,
        content=content,
    )
    final = "completed"
    try:
        resp = await conductor.dispatch(msg)
        text = str(resp.payload.get("result", "")) if resp and resp.payload else ""
    except Exception as e:
        final = "failed"
        text = f"[agentclaw error] {e}"
    finally:
        conductor.store.update_mission(mission_id, state=final, finished_at=time.time())
    return text


def _approx_tokens(s: str) -> int:
    return max(1, len(s) // 4)


@router.get("/v1/models")
async def list_models(
    request: Request,
    authorization: str | None = Header(default=None),
    x_logpyclaw_token: str | None = Header(default=None),
):
    if not _auth_ok(authorization, x_logpyclaw_token):
        return _unauth()
    conductor = request.app.state.conductor
    now = int(time.time())
    data = [
        {"id": a.agent_id, "object": "model", "created": now, "owned_by": "agentclaw"}
        for a in conductor.list_agents()
        if a.agent_id != "a2a:gateway" and is_allowed(a.agent_id)
    ]
    return {"object": "list", "data": data}


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_logpyclaw_token: str | None = Header(default=None),
):
    if not _auth_ok(authorization, x_logpyclaw_token):
        return _unauth()

    conductor = request.app.state.conductor
    body = await request.json()
    model = body.get("model", "agent:alice")
    agent_id = resolve_agent(model, conductor)
    if not agent_id:
        return JSONResponse(
            {"error": {"message": f"The model '{model}' does not exist.",
                       "type": "invalid_request_error", "code": "model_not_found"}},
            status_code=404,
        )
    if not is_allowed(agent_id):
        return JSONResponse(
            {"error": {"message": f"The model '{model}' is not available via this provider.",
                       "type": "invalid_request_error", "code": "model_not_permitted"}},
            status_code=403,
        )

    content = _messages_to_prompt(body.get("messages", []))
    stream = bool(body.get("stream", False))
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    if stream:
        async def gen():
            head = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
            yield f"data: {json.dumps(head)}\n\n"
            text = await _run_agent(conductor, agent_id, content)
            chunk = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                     "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield f"data: {json.dumps(chunk)}\n\n"
            done = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield f"data: {json.dumps(done)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    text = await _run_agent(conductor, agent_id, content)
    pt, ct = _approx_tokens(content), _approx_tokens(text)
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
    }
