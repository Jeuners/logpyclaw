"""
backend/api/rss.py — REST-Endpunkte für den RSS-Reader.

GET  /api/rss/feeds          → alle konfigurierten Feeds
GET  /api/rss/entries        → gecachte Einträge (?feed=hackernews&limit=20)
POST /api/rss/fetch          → manuell fetchen (?feed=all oder ?feed=hackernews)
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from backend.services.rss import FEEDS, fetch_all, fetch_feed, get_entries, is_stale

router = APIRouter()


@router.get("/api/rss/feeds")
async def list_feeds():
    return [
        {
            "id":       fid,
            "name":     meta["name"],
            "icon":     meta["icon"],
            "color":    meta["color"],
            "url":      meta["url"],
            "stale":    is_stale(fid),
        }
        for fid, meta in FEEDS.items()
    ]


@router.get("/api/rss/entries")
async def list_entries(
    feed: str | None = Query(default=None),
    limit: int = Query(default=25, le=100),
    refresh: bool = Query(default=False),
):
    if refresh or (feed and is_stale(feed)) or (not feed and any(is_stale(f) for f in FEEDS)):
        if feed and feed != "all":
            try:
                await fetch_feed(feed)
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": str(e)})
        else:
            await fetch_all()

    entries = get_entries(feed_id=feed, limit=limit)
    return [asdict(e) for e in entries]


@router.post("/api/rss/fetch")
async def trigger_fetch(feed: str = Query(default="all")):
    try:
        if feed == "all":
            result = await fetch_all()
            return {"fetched": {fid: len(entries) for fid, entries in result.items()}}
        else:
            entries = await fetch_feed(feed)
            return {"fetched": {feed: len(entries)}}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
