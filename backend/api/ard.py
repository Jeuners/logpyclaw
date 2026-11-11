"""
backend/api/ard.py — ARD (Agentic Resource Discovery) Publisher-Endpoint.

Serviert das `ai-catalog.json`-Manifest unter `/.well-known/ai-catalog.json`
gemäß ARD-Spec v0.9 (https://github.com/ards-project/ard-spec).

Aktivierung:
  - ard_publisher_enabled=true in .env (Default: true)
  - ard_publisher_domain und ard_publisher_name in .env (Default siehe config.py)

Sicherheit: GET ist immer lesbar; das Manifest enthält keine Secrets, nur
öffentliche Agent-Beschreibungen + URLs.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.config import get_settings
from backend.core.ard_publisher import build_catalog


router = APIRouter()


@router.get("/.well-known/ai-catalog.json")
@router.get("/ard/catalog.json", include_in_schema=False)
async def ai_catalog(request: Request) -> JSONResponse:
    """ARD ai-catalog.json — Standard-Well-Known-Pfad + Alias."""
    cfg = get_settings()
    if not cfg.ard_publisher_enabled:
        return JSONResponse({"error": "ARD publisher disabled"}, status_code=404)

    conductor = request.app.state.conductor
    # Basis-URL aus dem Request ableiten (funktioniert auch hinter Reverse-Proxy,
    # solange Forwarded-Header korrekt gesetzt sind).
    base_url = str(request.base_url).rstrip("/")

    manifest = build_catalog(
        conductor=conductor,
        base_url=base_url,
        publisher_domain=cfg.ard_publisher_domain,
        publisher_name=cfg.ard_publisher_name,
    )
    # Cache-Control: kurz halten — neue Agenten tauchen nach Boot auf.
    return JSONResponse(
        manifest,
        headers={"Cache-Control": "public, max-age=30"},
        media_type="application/ai-catalog+json",
    )