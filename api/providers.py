"""
api/providers.py — Provider-Konfiguration und Modell-Discovery.
"""
import logging

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from core.config import MISTRAL_VOICES_URL, OPENROUTER_BASE_URL
from storage.providers import load_providers, save_providers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["providers"])


class ProviderUpdate(BaseModel):
    data: dict


@router.get("/providers")
async def get_providers():
    providers = load_providers()
    result = {}
    for k, v in providers.items():
        entry = dict(v)
        if "api_key" in entry and entry["api_key"]:
            key = entry["api_key"]
            entry["api_key_masked"] = (
                key[:6] + "•" * max(0, len(key) - 10) + key[-4:]
                if len(key) > 10
                else "••••"
            )
        result[k] = entry
    return result


@router.post("/providers")
async def update_providers(body: dict):
    providers = load_providers()
    for key, val in body.items():
        if key in providers:
            providers[key].update(val)
        else:
            providers[key] = val
    save_providers(providers)
    return {"ok": True}


@router.get("/providers/status")
async def providers_status():
    providers = load_providers()
    status: dict = {}

    # Ollama
    try:
        url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{url}/api/tags")
        count = len(r.json().get("models", []))
        status["ollama"] = {"ok": True, "info": f"{count} Modelle"}
    except Exception:
        status["ollama"] = {"ok": False, "info": "Nicht erreichbar"}

    # Mistral
    mk = providers.get("mistral", {}).get("api_key", "")
    if mk:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    f"{MISTRAL_VOICES_URL}?page_size=1",
                    headers={"Authorization": f"Bearer {mk}"},
                )
            status["mistral"] = {
                "ok": r.is_success,
                "info": "API Key gültig" if r.is_success else f"Fehler {r.status_code}",
            }
        except Exception:
            status["mistral"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["mistral"] = {"ok": False, "info": "Kein API Key"}

    # OpenRouter
    ok_key = providers.get("openrouter", {}).get("api_key", "")
    if ok_key:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(
                    f"{OPENROUTER_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {ok_key}"},
                )
            count = len(r.json().get("data", []))
            status["openrouter"] = {
                "ok": r.is_success,
                "info": f"{count} Modelle verfügbar" if r.is_success else f"Fehler {r.status_code}",
            }
        except Exception:
            status["openrouter"] = {"ok": False, "info": "Nicht erreichbar"}
    else:
        status["openrouter"] = {"ok": False, "info": "Kein API Key"}

    # Qdrant
    try:
        qdrant_url = providers.get("qdrant", {}).get("url", "http://localhost:6333")
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{qdrant_url}/collections")
        count = len(r.json().get("result", {}).get("collections", []))
        status["qdrant"] = {"ok": True, "info": f"{count} Collections"}
    except Exception:
        status["qdrant"] = {"ok": False, "info": "Nicht erreichbar"}

    return status


@router.get("/models")
async def get_all_models():
    providers = load_providers()
    result: dict = {"ollama": [], "openrouter": []}

    # Ollama
    try:
        url = providers.get("ollama", {}).get("url", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{url}/api/tags")
        r.raise_for_status()
        result["ollama"] = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        result["ollama"] = []

    # OpenRouter
    or_key = providers.get("openrouter", {}).get("api_key", "")
    if or_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{OPENROUTER_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {or_key}"},
                )
            r.raise_for_status()
            models = r.json().get("data", [])
            parsed = [
                {
                    "id": m["id"],
                    "name": m.get("name", m["id"]),
                    "free": (
                        str(m.get("pricing", {}).get("prompt", "1")) == "0"
                        and str(m.get("pricing", {}).get("completion", "1")) == "0"
                    ) or m["id"].endswith(":free"),
                }
                for m in models
            ]
            result["openrouter"] = sorted(
                parsed, key=lambda x: (0 if x["free"] else 1, x.get("name", "").lower())
            )
        except Exception:
            result["openrouter"] = []

    return result
