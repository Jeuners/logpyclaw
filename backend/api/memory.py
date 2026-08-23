"""
backend/api/memory.py — REST für das semantische Gedächtnis.

  POST   /api/memory/remember   {text, kind, scope, meta}
  GET    /api/memory/recall?q=…&k=5&scope=agent:martin
  GET    /api/memory/stats
  DELETE /api/memory/{id}
  GET    /api/memory/enabled
  POST   /api/memory/enabled    {enabled}

`scope` beim Recall sucht im eigenen Scope + global (Hybrid); ohne scope → alle.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.core.memory import recall_scopes

router = APIRouter()


class RememberReq(BaseModel):
    text: str
    kind: str = "note"
    scope: str = "global"
    meta: dict | None = None


@router.post("/memory/remember")
async def remember(req: RememberReq, request: Request):
    rid = await request.app.state.memory.remember(req.text, req.kind, req.scope, req.meta)
    return {"id": rid, "scope": req.scope}


@router.get("/memory/recall")
async def recall(request: Request, q: str, k: int = 5, scope: str | None = None, min_score: float = 0.0):
    scopes = recall_scopes(scope) if scope else None
    res = await request.app.state.memory.recall(q, k, scopes, min_score)
    return {"query": q, "scopes": scopes, "results": res}


@router.get("/memory/stats")
async def stats(request: Request):
    return request.app.state.memory.stats()


@router.delete("/memory/{mem_id}")
async def forget(mem_id: int, request: Request):
    return {"removed": request.app.state.memory.forget(mem_id)}


class EnabledReq(BaseModel):
    enabled: bool


@router.get("/memory/enabled")
async def get_enabled(request: Request):
    return {"enabled": request.app.state.memory.enabled}


@router.post("/memory/enabled")
async def set_enabled(req: EnabledReq, request: Request):
    request.app.state.memory.enabled = req.enabled
    return {"enabled": request.app.state.memory.enabled}
