"""
skills/wikipedia.py — Wikipedia-Suche + Summary via offizieller REST-API.

Strategie:
  1. Search-Endpoint: /w/api.php?action=opensearch  → erster Treffer-Title
  2. Summary-Endpoint: /api/rest_v1/page/summary/<title>  → Extract + URL

Sprache wird heuristisch gewählt:
  - Umlaute/deutsche Stopwords → de.wikipedia.org
  - sonst → en.wikipedia.org

Trigger:
    "wikipedia <topic>"
    "wiki[pedia] zu <topic>"
    "was sagt wikipedia zu <topic>"
"""
from __future__ import annotations

import logging
import re

import requests

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)


def _detect_lang(query: str) -> str:
    """Simple Sprach-Erkennung: Umlaute oder deutsche Funktionswörter → de, sonst en."""
    if re.search(r"[äöüÄÖÜß]", query):
        return "de"
    tokens = set(re.findall(r"[a-zäöüß]+", query.lower()))
    de_markers = {"der", "die", "das", "und", "ist", "ein", "eine", "von", "mit", "für"}
    if tokens & de_markers:
        return "de"
    return "en"


def _opensearch(query: str, lang: str) -> str | None:
    """Erstes Search-Ergebnis (Title) von Wikipedia holen."""
    try:
        resp = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": query,
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
            headers={"User-Agent": "AgentClaw/1.0 (contact: local)"},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        titles = data[1] if len(data) > 1 else []
        return titles[0] if titles else None
    except Exception as e:
        logger.warning("wikipedia opensearch failed: %s", e)
        return None


def _summary(title: str, lang: str) -> dict | None:
    """Summary-Block via REST-API."""
    try:
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title, safe='')}"
        resp = requests.get(
            url,
            headers={"User-Agent": "AgentClaw/1.0 (contact: local)"},
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("wikipedia summary failed: %s", e)
        return None


def wikipedia_lookup(query: str, lang: str | None = None) -> str:
    """Ein-Aufruf-Helper: Title suchen + Summary holen + formatieren."""
    lang = lang or _detect_lang(query)
    title = _opensearch(query, lang)
    # Fallback: wenn de nichts hat, en probieren (und umgekehrt)
    if not title:
        alt = "en" if lang == "de" else "de"
        title = _opensearch(query, alt)
        if title:
            lang = alt
    if not title:
        return f"📖 Wikipedia: kein Artikel zu **{query}** gefunden."

    data = _summary(title, lang)
    if not data:
        return f"📖 Wikipedia: Artikel zu **{title}** konnte nicht geladen werden."

    extract = data.get("extract") or "(kein Textauszug verfügbar)"
    page_url = (data.get("content_urls", {}).get("desktop", {}) or {}).get("page") or ""
    title_out = data.get("title", title)

    out = [f"📖 **Wikipedia — {title_out}** ({lang})"]
    if page_url:
        out.append(page_url)
    out.append("")
    out.append(extract)
    return "\n".join(out)


_QUERY_RX = re.compile(
    r"\bwiki(?:pedia)?\s+(?:zu\s+|über\s+|about\s+|on\s+)?(.+?)[\?\.!]*$",
    re.IGNORECASE,
)
_ASK_RX = re.compile(
    r"\bwas\s+(?:sagt|steht\s+(?:bei|in))\s+wiki(?:pedia)?\s+(?:zu|über)\s+(.+?)[\?\.!]*$",
    re.IGNORECASE,
)


def extract_query(message: str) -> str | None:
    m = _ASK_RX.search(message.strip())
    if m:
        return m.group(1).strip(" \t\"'")
    m = _QUERY_RX.search(message.strip())
    if m:
        q = m.group(1).strip(" \t\"'")
        # "wiki read <name>" / "wiki list" rausfiltern — das ist der andere Skill
        if re.match(r"^(?:read|lies|list|liste|zeige?|show|open|öffne)\b", q, re.IGNORECASE):
            return None
        return q if len(q) >= 2 else None
    return None


class WikipediaSkill(BaseSkill):
    id = "wikipedia"
    name = "Wikipedia"
    icon = "menu_book"
    description = (
        "Schlägt Begriffe bei Wikipedia nach (DE/EN automatisch) und gibt "
        "Titel, URL und Einleitungsabsatz zurück."
    )
    # Triggers: "wikipedia <topic>" oder "was sagt wikipedia zu ...".
    # Nicht auf generisches "wiki" matchen — das gehört wiki_read / Wiki-Agent.
    triggers = [
        r"\bwikipedia\b",
        r"\bwas\s+sagt\s+wiki(?:pedia)?\b",
    ]
    requires: list[str] = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        task_sep = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
        search_text = task_sep.group(1).strip() if task_sep else message

        query = extract_query(search_text)
        if not query:
            return SkillResult(
                text=None,
                skill_used=self.id,
                metadata={"passthrough": True},
            )
        return SkillResult(text=wikipedia_lookup(query), skill_used=self.id)
