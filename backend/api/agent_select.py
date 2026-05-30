"""
backend/api/agent_select.py — geteilte Agent-Auswahl + Allowlist.

Eine Quelle der Wahrheit für externe Endpunkte (web_bridge, /v1-Provider):
- resolve_agent(): mappt eine Kurz-/Voll-ID ("alice", "agent:alice") auf eine echte Agent-ID
- allowed_agents()/is_allowed(): Allowlist aus Settings.provider_models (Default nur alice/claude/martin)
- build_content(): hängt einen optionalen Inject-/System-Prompt vor die Nachricht
"""

from __future__ import annotations

from backend.config import get_settings


def allowed_agents() -> set[str] | None:
    """Erlaubte Agent-IDs für externe Endpunkte. None = alle (nur lokaler Dev)."""
    raw = get_settings().provider_models.strip()
    if not raw:
        return None
    return {x.strip() for x in raw.split(",") if x.strip()}


def is_allowed(agent_id: str) -> bool:
    a = allowed_agents()
    return a is None or agent_id in a


def resolve_agent(model: str | None, conductor) -> str | None:
    """Mappt das `agent`/`model`-Feld auf eine existierende Agent-ID (oder None)."""
    m = (model or "").strip()
    if not m:
        return None
    if conductor.get_agent(m):
        return m
    for cand in (f"agent:{m}", f"skill:{m}"):
        if conductor.get_agent(cand):
            return cand
    return None


def build_content(message: str, inject: str | None) -> str:
    """Stellt einen optionalen Inject-/System-Prompt der Nachricht voran."""
    message = message or ""
    if inject and inject.strip():
        return f"{inject.strip()}\n\n---\n\n{message}"
    return message
