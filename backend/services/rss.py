"""
backend/services/rss.py — RSS Feed Service.

Verwaltet eine Liste von Feeds, fetcht sie periodisch und cached
die Einträge in einer JSON-Datei (~/.agentclaw/rss_cache.json).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import feedparser

# ── Feed-Definitionen ─────────────────────────────────────────────────────────

FEEDS: dict[str, dict] = {
    "hackernews": {
        "name":  "Hacker News",
        "url":   "https://news.ycombinator.com/rss",
        "icon":  "🔶",
        "color": "#ff6600",
    },
    "tagesschau": {
        "name":  "Tagesschau",
        "url":   "https://www.tagesschau.de/xml/rss2/",
        "icon":  "📺",
        "color": "#003da5",
    },
    "dillenberg": {
        "name":  "dillenberg.net",
        "url":   "https://dillenberg.net/feed/",
        "icon":  "🖥",
        "color": "#00ff00",
    },
}

_CACHE_PATH = Path.home() / ".agentclaw" / "rss_cache.json"
_CACHE_PATH.parent.mkdir(exist_ok=True)

_TTL = 30 * 60  # 30 Minuten


@dataclass
class RSSEntry:
    id:        str
    feed_id:   str
    feed_name: str
    feed_icon: str
    title:     str
    link:      str
    summary:   str
    published: str   # ISO-String oder leer
    fetched:   float = field(default_factory=time.time)


# ── In-memory Cache ───────────────────────────────────────────────────────────

_cache: dict[str, list[RSSEntry]] = {}
_last_fetch: dict[str, float] = {}


def _load_cache() -> None:
    global _cache, _last_fetch
    if not _CACHE_PATH.exists():
        return
    try:
        raw = json.loads(_CACHE_PATH.read_text())
        _cache = {
            fid: [RSSEntry(**e) for e in entries]
            for fid, entries in raw.get("entries", {}).items()
        }
        _last_fetch = raw.get("last_fetch", {})
    except Exception:
        pass


def _save_cache() -> None:
    try:
        _CACHE_PATH.write_text(json.dumps({
            "entries":    {fid: [asdict(e) for e in entries] for fid, entries in _cache.items()},
            "last_fetch": _last_fetch,
        }, ensure_ascii=False, indent=2))
    except Exception:
        pass


_load_cache()


# ── Fetch ─────────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _parse_feed(feed_id: str, url: str) -> list[RSSEntry]:
    meta = FEEDS[feed_id]
    try:
        parsed = feedparser.parse(url, agent="agentclaw-v3/1.0")
    except Exception as e:
        raise RuntimeError(f"feedparser error: {e}") from e

    entries = []
    for e in parsed.entries[:30]:
        pub = ""
        if hasattr(e, "published"):
            pub = e.published
        elif hasattr(e, "updated"):
            pub = e.updated

        summary = _strip_html(getattr(e, "summary", "") or "")[:400]
        entry = RSSEntry(
            id=getattr(e, "id", e.link),
            feed_id=feed_id,
            feed_name=meta["name"],
            feed_icon=meta["icon"],
            title=getattr(e, "title", "(kein Titel)"),
            link=getattr(e, "link", ""),
            summary=summary,
            published=pub,
        )
        entries.append(entry)
    return entries


async def fetch_feed(feed_id: str) -> list[RSSEntry]:
    if feed_id not in FEEDS:
        raise ValueError(f"Unbekannter Feed: {feed_id}")
    url = FEEDS[feed_id]["url"]
    entries = await _fetch_async(feed_id, url)
    _cache[feed_id] = entries
    _last_fetch[feed_id] = time.time()
    _save_cache()
    return entries


async def _fetch_async(feed_id: str, url: str) -> list[RSSEntry]:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _parse_feed, feed_id, url)


async def fetch_all() -> dict[str, list[RSSEntry]]:
    import asyncio
    results = await asyncio.gather(
        *[fetch_feed(fid) for fid in FEEDS],
        return_exceptions=True,
    )
    return {fid: r for fid, r in zip(FEEDS, results) if isinstance(r, list)}


def get_entries(feed_id: str | None = None, limit: int = 20) -> list[RSSEntry]:
    """Gibt gecachte Einträge zurück (frisch fetchen wenn veraltet)."""
    if feed_id:
        return _cache.get(feed_id, [])[:limit]
    all_entries = []
    for entries in _cache.values():
        all_entries.extend(entries)
    all_entries.sort(key=lambda e: e.fetched, reverse=True)
    return all_entries[:limit]


def is_stale(feed_id: str) -> bool:
    return time.time() - _last_fetch.get(feed_id, 0) > _TTL
