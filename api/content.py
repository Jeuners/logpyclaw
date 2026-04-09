"""
api/content.py — Screenshot, Bildbearbeitung, Tagesschau, Hacker News.
"""
import base64
import logging
import re
import xml.etree.ElementTree as ET

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from skills.url_fetch import is_safe_url
from skills.comfyui import run_comfyui_edit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["content"])

TAGESSCHAU_FEEDS = {
    "top":          "https://www.tagesschau.de/index~rss2.xml",
    "inland":       "https://www.tagesschau.de/inland/index~rss2.xml",
    "ausland":      "https://www.tagesschau.de/ausland/index~rss2.xml",
    "wirtschaft":   "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "sport":        "https://www.tagesschau.de/sport/index~rss2.xml",
    "faktenfinder": "https://www.tagesschau.de/faktenfinder/index~rss2.xml",
    "investigativ": "https://www.tagesschau.de/investigativ/index~rss2.xml",
}


async def _fetch_tagesschau(category: str = "top", limit: int = 10) -> list:
    url = TAGESSCHAU_FEEDS.get(category, TAGESSCHAU_FEEDS["top"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        desc  = re.sub(r"<[^>]+>", "", (item.findtext("description") or "")).strip()
        link  = (item.findtext("link") or "").strip()
        pub   = (item.findtext("pubDate") or "").strip()
        items.append({"title": title, "description": desc, "link": link, "pubDate": pub})
    return items


class ScreenshotRequest(BaseModel):
    url: str


class ImageEditRequest(BaseModel):
    image_data: str
    prompt: str


@router.post("/screenshot")
async def take_screenshot(body: ScreenshotRequest):
    url = body.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Keine URL angegeben")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not is_safe_url(url):
        raise HTTPException(
            status_code=403,
            detail=f"Blocked: '{url}' targets a private or internal network address",
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Playwright nicht installiert. Führe aus: pip install playwright && playwright install chromium",
        )

    try:
        # Playwright ist sync — in threadpool ausführen
        import asyncio
        loop = asyncio.get_event_loop()

        def _capture():
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                ctx = browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(1500)
                img_bytes = page.screenshot(type="jpeg", quality=80, full_page=False)
                ctx.close()
                browser.close()
                return img_bytes

        img_bytes = await loop.run_in_executor(None, _capture)
        b64 = base64.b64encode(img_bytes).decode()
        logger.info("Screenshot %s — %d KB", url, len(img_bytes) // 1024)
        return {"image": f"data:image/jpeg;base64,{b64}", "url": url}

    except Exception as e:
        logger.exception("Screenshot failed for %s", url)
        raise HTTPException(status_code=500, detail=f"Screenshot fehlgeschlagen: {e}")


@router.post("/image/edit")
async def edit_image(body: ImageEditRequest):
    if not body.image_data:
        raise HTTPException(status_code=400, detail="Kein Bild angegeben")
    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Kein Prompt angegeben")
    try:
        result = run_comfyui_edit(body.image_data, body.prompt, use_lightning=True)
        return {"image": result}
    except Exception as e:
        logger.exception("Image edit failed")
        raise HTTPException(status_code=500, detail=f"Bearbeitung fehlgeschlagen: {e}")


@router.get("/tagesschau")
async def tagesschau_feed(category: str = "top", limit: int = 10):
    if category not in TAGESSCHAU_FEEDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unbekannte Kategorie: {category}",
        )
    limit = min(limit, 20)
    try:
        items = await _fetch_tagesschau(category, limit)
        return {"category": category, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/hackernews")
async def hackernews_feed(limit: int = 15):
    limit = min(limit, 30)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
            story_ids = r.json()[:limit]
            items = []
            for sid in story_ids:
                sr = await client.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                )
                story = sr.json()
                if story:
                    items.append({
                        "title": story.get("title", ""),
                        "url": story.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                        "score": story.get("score", 0),
                        "by": story.get("by", ""),
                        "time": story.get("time", 0),
                        "descendants": story.get("descendants", 0),
                        "id": sid,
                    })
        return {"items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
