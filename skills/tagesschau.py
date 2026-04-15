"""Tagesschau Skill — Aktuelle Nachrichten via öffentlicher API."""
import requests
from skills.base import BaseSkill, SkillResult


def fetch_tagesschau(limit: int = 8) -> str:
    """Lädt aktuelle Schlagzeilen von der Tagesschau API."""
    try:
        resp = requests.get(
            "https://www.tagesschau.de/api2u/homepage/",
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"[Fehler beim Laden der Tagesschau-Daten: {e}]"

    news = data.get("news", [])
    items = []
    for article in news[:limit]:
        title = article.get("title", "").strip()
        if not title:
            continue
        teaser = article.get("firstSentence", "") or article.get("teaserText", "")
        teaser = teaser.strip()
        url = article.get("shareURL", "") or article.get("detailsweb", "")
        if not isinstance(url, str):
            url = ""

        line = f"- **{title}**"
        if teaser:
            line += f"\n  {teaser}"
        if url:
            line += f"\n  {url}"
        items.append(line)

    if not items:
        return "[Keine Nachrichten geladen]"

    return "\n\n".join(items)


class TagesschauSkill(BaseSkill):
    id = "tagesschau"
    name = "Tagesschau"
    icon = "article"
    description = "Lädt aktuelle Nachrichten von tagesschau.de."
    triggers = [
        r"\btagesschau\b",
        r"\b(ard|tages.?schau).{0,20}\b(news|nachrichten|schlagzeilen|aktuell)\b",
        r"\b(nachrichten|schlagzeilen).{0,20}\btagesschau\b",
        # Allgemeine Nachrichten-Anfragen (A2A-kompatibel)
        r"\b(aktuelle?n?|heutige?n?)\s+(nachrichten|news|meldungen|schlagzeilen)\b",
        r"\b(nachrichten|news|meldungen).{0,30}\b(tag|heute|aktuell)\b",
        r"\bwas\b.{0,20}\b(passiert|los|neu)\b.{0,20}\b(heute|gerade|aktuell)\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        import re
        m = re.search(r"\b(\d+)\s*(artikel|nachrichten|meldungen|schlagzeilen)\b", message, re.IGNORECASE)
        limit = int(m.group(1)) if m else 8
        limit = min(max(limit, 3), 20)

        text = fetch_tagesschau(limit=limit)
        return SkillResult(
            text=f"### Tagesschau — Aktuelle Nachrichten\n\n{text}",
            skill_used=self.id,
        )
