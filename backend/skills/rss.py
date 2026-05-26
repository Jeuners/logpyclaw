"""
backend/skills/rss.py — RSS-Feed-Skill für Agenten.

Trigger-Beispiele:
  "zeige hackernews"
  "was gibt es neu bei tagesschau"
  "dillenberg feed"
  "top news"
  "rss"
"""
from __future__ import annotations

import re

from backend.services.rss import FEEDS, fetch_feed, get_entries, is_stale
from backend.skills import Skill

_FEED_PATTERNS = {
    "hackernews": re.compile(r"\b(hack\w*\s*news|hn|hacker)\b", re.I),
    "tagesschau": re.compile(r"\b(tagesschau|tages|nachrichten|news|ard)\b", re.I),
    "dillenberg": re.compile(r"\b(dillenberg|blog|site)\b", re.I),
}


class RSSSkill(Skill):
    skill_id   = "rss"
    description = "Liest RSS-Feeds: Hacker News, Tagesschau, dillenberg.net"

    async def execute(self, query: str) -> str:
        feed_id = self._detect_feed(query)

        # Fetch wenn veraltet
        if feed_id:
            if is_stale(feed_id):
                try:
                    await fetch_feed(feed_id)
                except Exception as e:
                    return f"[RSS] Fetch-Fehler ({feed_id}): {e}"
            entries = get_entries(feed_id=feed_id, limit=10)
            meta    = FEEDS[feed_id]
            header  = f"{meta['icon']} **{meta['name']}** — Top {len(entries)}\n\n"
        else:
            # Alle Feeds zusammen
            for fid in FEEDS:
                if is_stale(fid):
                    try:
                        await fetch_feed(fid)
                    except Exception:
                        pass
            entries = get_entries(feed_id=None, limit=15)
            header  = "📡 **RSS — Alle Feeds** (neueste 15)\n\n"

        if not entries:
            return "[RSS] Keine Einträge im Cache. Versuche: 'rss fetch hackernews'"

        lines = []
        for i, e in enumerate(entries, 1):
            pub  = e.published[:10] if len(e.published) >= 10 else e.published
            snip = (e.summary[:120] + "…") if len(e.summary) > 120 else e.summary
            lines.append(
                f"**{i}. {e.title}**\n"
                f"   {e.feed_icon} {e.feed_name} · {pub}\n"
                f"   {snip}\n"
                f"   🔗 {e.link}"
            )

        return header + "\n\n".join(lines)

    @staticmethod
    def _detect_feed(query: str) -> str | None:
        for fid, rx in _FEED_PATTERNS.items():
            if rx.search(query):
                return fid
        return None
