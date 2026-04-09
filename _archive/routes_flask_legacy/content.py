"""
routes/content.py — Screenshot, Bildbearbeitung, Tagesschau, Hacker News.
"""
import base64
import re

import requests
from flask import Blueprint, jsonify, request

from skills import _is_safe_url, _run_comfyui_edit

bp = Blueprint("content", __name__)

TAGESSCHAU_FEEDS = {
    "top": "https://www.tagesschau.de/index~rss2.xml",
    "inland": "https://www.tagesschau.de/inland/index~rss2.xml",
    "ausland": "https://www.tagesschau.de/ausland/index~rss2.xml",
    "wirtschaft": "https://www.tagesschau.de/wirtschaft/index~rss2.xml",
    "sport": "https://www.tagesschau.de/sport/index~rss2.xml",
    "faktenfinder": "https://www.tagesschau.de/faktenfinder/index~rss2.xml",
    "investigativ": "https://www.tagesschau.de/investigativ/index~rss2.xml",
}


def fetch_tagesschau(category="top", limit=10):
    import xml.etree.ElementTree as ET

    url = TAGESSCHAU_FEEDS.get(category, TAGESSCHAU_FEEDS["top"])
    r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:limit]:
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        desc = re.sub(r"<[^>]+>", "", desc).strip()
        items.append({"title": title, "description": desc, "link": link, "pubDate": pub})
    return items


@bp.route("/api/screenshot", methods=["POST"])
def take_screenshot():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "Keine URL angegeben"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    if not _is_safe_url(url):
        return jsonify({"error": f"Blocked: '{url}' targets a private or internal network address"}), 403

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return jsonify({"error": "Playwright nicht installiert. Führe aus: venv/bin/pip install playwright && venv/bin/playwright install chromium"}), 501

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(1500)
            img_bytes = page.screenshot(type="jpeg", quality=80, full_page=False)
            context.close()
            browser.close()
            browser = None
        b64 = base64.b64encode(img_bytes).decode()
        print(f"[Screenshot] {url} — {len(img_bytes) // 1024}KB", flush=True)
        return jsonify({"image": f"data:image/jpeg;base64,{b64}", "url": url})
    except Exception as e:
        print(f"[Screenshot] Error: {e}", flush=True)
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        return jsonify({"error": f"Screenshot fehlgeschlagen: {e}"}), 500


@bp.route("/api/image/edit", methods=["POST"])
def edit_image():
    data = request.json
    image_data = data.get("image_data", "")
    prompt = data.get("prompt", "").strip()

    if not image_data:
        return jsonify({"error": "Kein Bild angegeben"}), 400
    if not prompt:
        return jsonify({"error": "Kein Prompt angegeben"}), 400

    try:
        result = _run_comfyui_edit(image_data, prompt, use_lightning=True)
        return jsonify({"image": result})
    except Exception as e:
        print(f"[Image Edit] Error: {e}", flush=True)
        return jsonify({"error": f"Bearbeitung fehlgeschlagen: {e}"}), 500


@bp.route("/api/tagesschau", methods=["GET"])
def tagesschau_feed():
    category = request.args.get("category", "top")
    limit = min(int(request.args.get("limit", 10)), 20)
    if category not in TAGESSCHAU_FEEDS:
        return jsonify({"error": f"Unbekannte Kategorie: {category}", "categories": list(TAGESSCHAU_FEEDS.keys())}), 400
    try:
        items = fetch_tagesschau(category, limit)
        return jsonify({"category": category, "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/hackernews", methods=["GET"])
def hackernews_feed():
    limit = min(int(request.args.get("limit", 15)), 30)
    try:
        r = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=10)
        story_ids = r.json()[:limit]
        items = []
        for sid in story_ids:
            sr = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=5)
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
        return jsonify({"items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
