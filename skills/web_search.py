"""
skills/web_search.py — Web-Suche via DuckDuckGo HTML-Endpoint.

Kein API-Key nötig. Scraped die HTML-Response von duckduckgo.com/html/
und gibt Top-N Treffer (Titel, URL, Snippet) als Markdown zurück.

Trigger:
    "suche/google/duckduckgo/such im web/web search: <query>"
    "recherchier <topic>"
"""
from __future__ import annotations

import logging
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import requests

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15"
)


class _DDGParser(HTMLParser):
    """Extrahiert Titel, URL und Snippet aus DDG HTML-Results."""

    def __init__(self):
        super().__init__()
        self.results: list[dict] = []
        self._current: dict | None = None
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._current = self._current or {}
            self._current["url"] = self._clean_url(a.get("href", ""))
            self._capture = "title"
            self._buffer = []
        elif tag == "a" and "result__snippet" in cls:
            self._capture = "snippet"
            self._buffer = []

    def handle_endtag(self, tag):
        if tag == "a" and self._capture == "title":
            self._current["title"] = unescape("".join(self._buffer).strip())
            self._capture = None
        elif tag == "a" and self._capture == "snippet":
            if self._current is None:
                self._current = {}
            self._current["snippet"] = unescape("".join(self._buffer).strip())
            self._capture = None
            if self._current.get("url") and self._current.get("title"):
                self.results.append(self._current)
            self._current = None

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)

    @staticmethod
    def _clean_url(href: str) -> str:
        """DDG wrapped URLs like /l/?uddg=https%3A%2F%2Fexample.com entpacken."""
        if not href:
            return ""
        if href.startswith("//"):
            href = "https:" + href
        try:
            p = urlparse(href)
            qs = parse_qs(p.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        except Exception:
            pass
        return href


def web_search(query: str, limit: int = 5) -> list[dict]:
    """Führt DDG-Suche aus und gibt Liste von {title, url, snippet} zurück."""
    try:
        resp = requests.post(
            _DDG_URL,
            data={"q": query, "kl": "de-de"},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("web_search: DDG request failed: %s", e)
        return []

    parser = _DDGParser()
    try:
        parser.feed(resp.text)
    except Exception as e:
        logger.warning("web_search: parse failed: %s", e)
        return []

    return parser.results[:limit]


def format_results(query: str, results: list[dict]) -> str:
    if not results:
        return f"🔍 Keine Treffer für **{query}**."
    lines = [f"🔍 **Web-Suche: {query}**\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "(kein Titel)")
        url = r.get("url", "")
        snippet = r.get("snippet", "")
        lines.append(f"{i}. **{title}**")
        lines.append(f"   {url}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")
    return "\n".join(lines).rstrip()


_QUERY_RX = re.compile(
    r"\b(?:suche?|such|search|google|duckduckgo|recherchier\w*|web[\s\-]?search)"
    r"(?:\s+(?:im\s+web|online|nach|für|mal))*\s*[:\-]?\s*(.+?)[\?\.!]*$",
    re.IGNORECASE,
)


def extract_query(message: str) -> str | None:
    m = _QUERY_RX.search(message.strip())
    if not m:
        return None
    q = m.group(1).strip(" \t\"'")
    if len(q) < 2:
        return None
    return q


class WebSearchSkill(BaseSkill):
    id = "web_search"
    name = "Web Search"
    icon = "travel_explore"
    description = (
        "Führt eine Web-Suche via DuckDuckGo durch und gibt die Top-Treffer "
        "(Titel, URL, Snippet) zurück. Kein API-Key nötig."
    )
    triggers = [
        r"\b(?:suche?|such|search|google|duckduckgo)\s+(?:im\s+web|online|nach)?",
        r"\brecherchier\w*\b",
        r"\bweb[\s\-]?search\b",
    ]
    requires: list[str] = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        # Bei A2A-Tasks nur den Teil nach dem Separator nehmen
        task_sep = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
        search_text = task_sep.group(1).strip() if task_sep else message

        query = extract_query(search_text)
        if not query:
            return SkillResult(
                text=None,
                skill_used=self.id,
                metadata={"passthrough": True},
            )

        # Limit aus Message extrahieren (optional)
        m = re.search(r"\btop\s*(\d+)\b", search_text, re.IGNORECASE)
        limit = int(m.group(1)) if m else 5
        limit = min(max(limit, 3), 10)

        results = web_search(query, limit=limit)
        return SkillResult(text=format_results(query, results), skill_used=self.id)
