"""
backend/core/ard_publisher.py — Generiert ARD ai-catalog.json aus dem Conductor.

ARD (Agentic Resource Discovery, https://github.com/ards-project/ard-spec) ist
ein Draft-Standard (v0.9) zur föderierten Discovery agentischer Ressourcen.
Dieser Publisher-Modus exposiert logpyclaws lokale Agenten + Skills als
statisches `ai-catalog.json` unter `/.well-known/ai-catalog.json`, damit
andere logpyclaw-Instanzen oder ARD-kompatible Clients sie finden können.

Publisher-Domain: settings.ard_publisher_domain (Default "logpyclaw.local")
— muss ein FQDN sein (ARD-Spec §4.2.1 verlangt das für die urn:air: NSS).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.conductor import Conductor


# ARD-Media-Types — Draft-Status, IANA-Registrierung steht aus (ard-spec §3.3).
TYPE_A2A_AGENT_CARD = "application/a2a-agent-card+json"
# Skills: text/markdown ist in ard-spec §4.4 explizit als Beispiel,
# das conformance-Tool erkennt den +profile-Suffix.
TYPE_AI_SKILL = "text/markdown; profile=\"urn:air:agent-skills\""
TYPE_AI_REGISTRY = "application/ai-registry+json"

# Diese Agent-Typen werden NICHT veröffentlicht — sie sind interne Infrastruktur,
# keine adressierbaren Capabilities für externe Caller.
_INTERNAL_AGENT_PREFIXES = ("agent:martin", "a2a:gateway", "agent:echo")


def _is_external_agent(agent_id: str) -> bool:
    return not any(agent_id.startswith(p) for p in _INTERNAL_AGENT_PREFIXES)


def _is_skill_agent(agent_id: str) -> bool:
    return agent_id.startswith("skill:")


def _agent_card_url(base_url: str, agent_id: str) -> str:
    """URL zur A2A-Agent-Card (Google A2A Discovery Format)."""
    return f"{base_url}/a2a/agents/{agent_id}/card"


def _urn(publisher: str, namespace: str, name: str) -> str:
    """Baut eine ARD-konforme urn:air:…-Identifikator (ard-spec §4.2.1)."""
    return f"urn:air:{publisher}:{namespace}:{name}"


def build_catalog(
    conductor: "Conductor",
    base_url: str,
    publisher_domain: str,
    publisher_name: str,
) -> dict:
    """Erzeugt das `ai-catalog.json`-Manifest aus dem aktuellen Conductor-State.

    Entries:
    - LLM-Agents (alice, coder, local, router, claude) → a2a-agent-card+json
    - Skills (skill:websearch, skill:comfyui, …)             → ai-skill+md
    - Martin / Gateway / Echo werden übersprungen (intern)

    Args:
        conductor: aktive Conductor-Instanz (liefert list_agents())
        base_url: externe Basis-URL der Instanz, z.B. "https://logpyclaw.local"
        publisher_domain: FQDN für urn:air:-NSS, z.B. "logpyclaw.local"
        publisher_name: Anzeige-Name für `host.displayName`
    """
    base = base_url.rstrip("/")
    entries: list[dict] = []

    for ag in conductor.list_agents():
        aid = ag.agent_id
        if not _is_external_agent(aid):
            continue

        name = ag.name
        description = (getattr(ag, "description", "") or "").strip()

        if _is_skill_agent(aid):
            # Skills: skill_id als URN-Komponente (z.B. "websearch")
            skill_id = aid[len("skill:"):]
            entries.append({
                "identifier": _urn(publisher_domain, "skill", skill_id),
                "displayName": name,
                "type": TYPE_AI_SKILL,
                "url": _agent_card_url(base, aid),
                "description": description,
                "tags": ["skill", skill_id],
            })
        else:
            # LLM-Agents: "agent:coder" → urn-Komponente "coder"
            short = aid[len("agent:"):] if aid.startswith("agent:") else aid
            tags = ["llm-agent", short]
            faction = getattr(ag, "faction", "") or ""
            if faction:
                tags.append(f"faction-{faction}")
            entry: dict = {
                "identifier": _urn(publisher_domain, "agent", short),
                "displayName": name,
                "type": TYPE_A2A_AGENT_CARD,
                "url": _agent_card_url(base, aid),
                "description": description or f"{name} — logpyclaw LLM agent",
                "tags": tags,
            }
            # representativeQueries: 2-5 Beispiele helfen ARD-Regisries beim
            # semantischen Matching (ard-spec §4.2: SHOULD 2-5, JSON Schema
            # verlangt minItems:2).
            queries: list[str] = []
            if description:
                first = description.split(".")[0].strip()
                if first:
                    queries.append(first[:120])
                # Zweite Query: aus zweitem Satz, falls vorhanden
                rest = description.split(".")[1].strip() if "." in description else ""
                if rest and rest not in queries:
                    queries.append(rest[:120])
            # Default-Fallback: solange auffüllen bis 2 erreicht sind.
            queries.append(f"delegate task to {name}")
            if len(queries) < 2:
                queries.append(f"what can {name} do?")
            entry["representativeQueries"] = queries[:5]
            entries.append(entry)

    return {
        "specVersion": "1.0",
        "host": {
            "displayName": publisher_name,
            "identifier": f"did:web:{publisher_domain}",
        },
        "entries": entries,
    }