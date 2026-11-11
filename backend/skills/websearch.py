"""
backend/skills/websearch.py — WebSearch Skill via DuckDuckGo.

Zwei-stufige Suche:
1. Instant Answer API (Fakten/Definitionen, kein API-Key)
2. Falls leer → HTML-Endpoint Scrape (echte Suchergebnisse mit URLs)
"""

from __future__ import annotations

import re
import urllib.parse
from html.parser import HTMLParser

import httpx

from backend.skills import Skill

_DDG_API  = "https://api.duckduckgo.com/"
_DDG_HTML = "https://html.duckduckgo.com/html/"
_TIMEOUT  = 12.0
_UA       = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


class _DDGResultParser(HTMLParser):
    """Extrahiert title/href/snippet aus html.duckduckgo.com Result-HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self._current: dict | None = None
        self._mode: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._current = {"title": "", "url": "", "snippet": ""}
            self._mode = "title"
            self._buf = []
            href = a.get("href", "")
            # DDG verlinkt durch eigenen Redirector — uddg-Parameter extrahieren
            if href.startswith("//duckduckgo.com/l/?") or href.startswith("/l/?"):
                qs = urllib.parse.urlparse(href).query
                uddg = urllib.parse.parse_qs(qs).get("uddg", [""])[0]
                self._current["url"] = urllib.parse.unquote(uddg) if uddg else href
            else:
                self._current["url"] = href if href.startswith("http") else "https:" + href
        elif tag == "a" and "result__snippet" in cls and self._current is not None:
            self._mode = "snippet"
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "a" and self._mode == "title" and self._current:
            self._current["title"] = "".join(self._buf).strip()
            self._mode = None
        elif tag == "a" and self._mode == "snippet" and self._current:
            self._current["snippet"] = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            self._mode = None
            if self._current.get("url"):
                self.results.append(self._current)
            self._current = None

    def handle_data(self, data):
        if self._mode in ("title", "snippet"):
            self._buf.append(data)


class WebSearchSkill(Skill):
    skill_id = "websearch"
    description = "Sucht im Web via DuckDuckGo: Instant Answers + HTML-Fallback mit echten Result-URLs"

    async def execute(self, query: str) -> str:
        try:
            # 1. Instant Answer probieren
            instant = await self._ddg_instant(query)
            if instant:
                return instant
            # 2. Fallback: HTML-Suche
            return await self._ddg_html(query)
        except Exception as e:
            return f"[WebSearch] Fehler: {e}"

    async def _ddg_instant(self, query: str) -> str | None:
        params = {
            "q": query, "format": "json",
            "no_redirect": "1", "no_html": "1", "skip_disambig": "1",
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.get(_DDG_API, params=params, headers={"User-Agent": _UA})
            r.raise_for_status()
            data = r.json()

        results: list[str] = []
        abstract = data.get("AbstractText", "").strip()
        if abstract:
            source = data.get("AbstractSource", "")
            url = data.get("AbstractURL", "")
            head = f"**{source}**: {abstract}" if source else abstract
            if url: head += f"\n  → {url}"
            results.append(head)
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                t = topic["Text"]
                u = topic.get("FirstURL", "")
                results.append(f"- {t}" + (f"\n  → {u}" if u else ""))
        answer = data.get("Answer", "").strip()
        if answer:
            results.insert(0, f"Direkte Antwort: {answer}")
        return "\n".join(results) if results else None

    async def _ddg_html(self, query: str) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            r = await client.post(
                _DDG_HTML,
                data={"q": query, "kl": "wt-wt"},   # weltweit
                headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9,de;q=0.8"},
            )
            r.raise_for_status()
            html = r.text

        parser = _DDGResultParser()
        parser.feed(html)
        results = parser.results[:8]
        if not results:
            return f'[WebSearch] Keine Suchergebnisse für "{query}".'

        out = [f"🔍 **{len(results)} Treffer für** «{query}»\n"]
        for i, hit in enumerate(results, 1):
            title = hit["title"][:120]
            url = hit["url"]
            snippet = hit["snippet"][:180]
            out.append(f"**{i}. {title}**\n  → {url}" + (f"\n  {snippet}" if snippet else ""))
        return "\n\n".join(out)
