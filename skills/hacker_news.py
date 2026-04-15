"""Hacker News Skill — Top Stories via öffentliche HN Firebase API."""
import requests
from skills.base import BaseSkill, SkillResult


def fetch_hn_top(limit: int = 10) -> str:
    """Lädt Top-Stories von Hacker News und gibt formatierten Text zurück."""
    try:
        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            timeout=8,
        )
        resp.raise_for_status()
        ids = resp.json()[:limit]
    except Exception as e:
        return f"[Fehler beim Laden der HN Top-Stories: {e}]"

    stories = []
    for story_id in ids:
        try:
            r = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=5,
            )
            r.raise_for_status()
            item = r.json()
            if not item or item.get("type") != "story":
                continue
            title = item.get("title", "(kein Titel)")
            url = item.get("url", f"https://news.ycombinator.com/item?id={story_id}")
            score = item.get("score", 0)
            comments = item.get("descendants", 0)
            stories.append(f"- **{title}** ({score} pts, {comments} Kommentare)\n  {url}")
        except Exception:
            continue

    if not stories:
        return "[Keine Stories geladen]"

    return "\n".join(stories)


class HackerNewsSkill(BaseSkill):
    id = "hacker_news"
    name = "Hacker News"
    icon = "newspaper"
    description = "Lädt aktuelle Top-Stories von Hacker News."
    triggers = [
        r"\bhacker\s*news\b",
        r"\bhn\b.{0,20}\b(top|news|stories|aktuell)\b",
        r"\b(top|aktuell).{0,20}\bhacker\s*news\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        # Anzahl Stories aus Nachricht extrahieren (z.B. "top 15")
        import re
        m = re.search(r"\b(\d+)\s*(stories|artikel|einträge|top)\b", message, re.IGNORECASE)
        limit = int(m.group(1)) if m else 10
        limit = min(max(limit, 3), 30)

        text = fetch_hn_top(limit=limit)
        return SkillResult(
            text=f"### Hacker News — Top {limit} Stories\n\n{text}",
            skill_used=self.id,
        )
