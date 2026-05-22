"""
backend/skills/websearch.py — WebSearch Skill via DuckDuckGo.

Nutzt die DuckDuckGo Instant Answer API (kein API-Key nötig).
Fallback: liefert strukturierten Hinweis wenn API nichts zurückgibt.
"""

from __future__ import annotations

import httpx

from backend.skills import Skill

_DDG_URL = "https://api.duckduckgo.com/"
_TIMEOUT = 10.0


class WebSearchSkill(Skill):
    skill_id = "websearch"
    description = "Sucht im Web via DuckDuckGo Instant Answers (kein API-Key)"

    async def execute(self, query: str) -> str:
        try:
            return await self._ddg_instant(query)
        except Exception as e:
            return f"[WebSearch] Fehler: {e}"

    async def _ddg_instant(self, query: str) -> str:
        params = {
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
            "skip_disambig": "1",
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(_DDG_URL, params=params)
            r.raise_for_status()
            data = r.json()

        results: list[str] = []

        abstract = data.get("AbstractText", "").strip()
        if abstract:
            source = data.get("AbstractSource", "")
            results.append(f"**{source}**: {abstract}" if source else abstract)

        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text']}")

        answer = data.get("Answer", "").strip()
        if answer:
            results.insert(0, f"Direkte Antwort: {answer}")

        if not results:
            return f'[WebSearch] Keine Instant-Answers für "{query}". Versuche eine präzisere Suchanfrage.'

        return "\n".join(results)
