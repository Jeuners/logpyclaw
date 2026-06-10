"""
backend/skills/wikipedia.py — Wikipedia-Suche + Summary via REST-API.

Sprache wird automatisch erkannt (DE/EN). Fallback auf andere Sprache wenn kein Treffer.
"""
from __future__ import annotations

import re
import urllib.parse

import httpx

from backend.skills import Skill


def _detect_lang(query: str) -> str:
    if re.search(r"[äöüÄÖÜß]", query):
        return "de"
    tokens = set(re.findall(r"[a-zäöüß]+", query.lower()))
    if tokens & {"der", "die", "das", "und", "ist", "ein", "eine", "von", "mit", "für"}:
        return "de"
    return "en"


def _extract_query(text: str) -> str:
    """Entfernt Trigger-Wörter und gibt den Suchbegriff zurück."""
    text = text.strip()
    m = re.search(
        r"\bwas\s+(?:sagt|steht\s+(?:bei|in))\s+wiki(?:pedia)?\s+(?:zu|über)\s+(.+?)[\?\.!]*$",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" \t\"'")
    m = re.search(
        r"\bwiki(?:pedia)?\s+(?:zu\s+|über\s+|about\s+|on\s+|zu\s+)?(.+?)[\?\.!]*$",
        text, re.IGNORECASE,
    )
    if m:
        return m.group(1).strip(" \t\"'")
    return text


class WikipediaSkill(Skill):
    skill_id = "wikipedia"
    description = "Schlägt Begriffe bei Wikipedia nach (DE/EN automatisch) — Titel, URL, Einleitung."

    async def execute(self, query: str) -> str:
        topic = _extract_query(query)
        if not topic or len(topic) < 2:
            return "[Wikipedia] Bitte einen Suchbegriff angeben."
        try:
            return await self._lookup(topic)
        except Exception as e:
            return f"[Wikipedia] Fehler: {e}"

    async def _lookup(self, topic: str) -> str:
        lang = _detect_lang(topic)
        title = await self._search(topic, lang)
        if not title:
            alt = "en" if lang == "de" else "de"
            title = await self._search(topic, alt)
            if title:
                lang = alt
        if not title:
            return f"[Wikipedia] Kein Artikel zu **{topic}** gefunden."

        data = await self._summary(title, lang)
        if not data:
            return f"[Wikipedia] Artikel **{title}** konnte nicht geladen werden."

        extract  = data.get("extract") or "(kein Auszug)"
        page_url = data.get("content_urls", {}).get("desktop", {}).get("page", "")
        title_out = data.get("title", title)

        parts = [f"📖 **Wikipedia — {title_out}** ({lang})"]
        if page_url:
            parts.append(page_url)
        parts += ["", extract[:2000]]
        return "\n".join(parts)

    async def _search(self, query: str, lang: str) -> str | None:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "logpyclaw-v3/1.0"}) as c:
            r = await c.get(
                f"https://{lang}.wikipedia.org/w/api.php",
                params={"action": "opensearch", "search": query, "limit": 1,
                        "namespace": 0, "format": "json"},
            )
            r.raise_for_status()
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            return titles[0] if titles else None

    async def _summary(self, title: str, lang: str) -> dict | None:
        async with httpx.AsyncClient(timeout=8, headers={"User-Agent": "logpyclaw-v3/1.0"}) as c:
            r = await c.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title, safe='')}"
            )
            r.raise_for_status()
            return r.json()
