"""
backend/skills/browser.py — BrowserSkill.

Fetcht URLs via httpx, extrahiert lesbaren Text aus HTML (stdlib html.parser),
und macht Screenshots via lokalem Chromium-Script.

Query-Syntax:
  screenshot <url>   → Screenshot via Chromium headless
  fetch <url>        → HTML → lesbarer Text (httpx)
  navigate <url>     → Alias für fetch
  <url>              → Automatisch: fetch
"""
from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import httpx

from backend.skills import Skill

_TIMEOUT = 15.0
_MAX_CHARS = 5000
_SCREENSHOT_SCRIPT = "/Users/jeuner/.claude/skills/screenshot/screenshot.sh"

_SKIP_TAGS = {"script", "style", "nav", "footer", "head", "noscript", "iframe"}


class _TextExtractor(HTMLParser):
    """Minimaler HTML→Text Extraktor via stdlib."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in _SKIP_TAGS:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _SKIP_TAGS:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            t = data.strip()
            if t:
                self.parts.append(t)

    def get_text(self) -> str:
        text = " ".join(self.parts)
        return re.sub(r"\s+", " ", text).strip()


def _html_to_text(html: str) -> str:
    extractor = _TextExtractor()
    extractor.feed(html)
    return extractor.get_text()


class BrowserSkill(Skill):
    skill_id = "browser"
    description = (
        "Browser-Skill: fetcht URLs, extrahiert lesbaren Text aus HTML, "
        "macht Screenshots via lokalem Chromium. "
        "Syntax: 'screenshot <url>', 'fetch <url>', 'navigate <url>', oder direkt eine URL."
    )

    async def execute(self, query: str) -> str:
        query = query.strip()

        # Screenshot-Modus
        if re.match(r"(?i)^screenshot\s+", query):
            url = query.split(None, 1)[1].strip()
            return await self._screenshot(url)

        # fetch / navigate Alias
        m = re.match(r"(?i)^(?:fetch|navigate|open|go to|goto)\s+(https?://\S+)", query)
        if m:
            return await self._fetch(m.group(1))

        # Direkte URL
        url_m = re.search(r"https?://\S+", query)
        if url_m:
            url = url_m.group(0).rstrip(".,;\"')}]")
            return await self._fetch(url)

        return "[BrowserSkill] Kein gültiger Befehl erkannt. Nutze: 'fetch <url>', 'screenshot <url>'."

    async def _fetch(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AgentClaw/3)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()
                ct = r.headers.get("content-type", "")
                if "html" in ct:
                    text = _html_to_text(r.text)
                elif "json" in ct:
                    import json
                    try:
                        text = json.dumps(r.json(), indent=2, ensure_ascii=False)
                    except Exception:
                        text = r.text
                else:
                    text = r.text
            if not text:
                return f"[BrowserSkill] Leere Antwort von {url}"
            return f"[{url}]\n\n{text[:_MAX_CHARS]}"
        except httpx.HTTPStatusError as e:
            return f"[BrowserSkill] HTTP {e.response.status_code} bei {url}"
        except Exception as e:
            return f"[BrowserSkill] Fehler beim Laden von {url}: {e}"

    async def _screenshot(self, url: str) -> str:
        script = Path(_SCREENSHOT_SCRIPT)
        if not script.exists():
            return f"[BrowserSkill] Screenshot-Script nicht gefunden: {_SCREENSHOT_SCRIPT}"
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            out_path = f.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(script), "--url", url, "--out", out_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            if proc.returncode != 0:
                err = stderr.decode(errors="replace")[:300]
                return f"[BrowserSkill] Screenshot fehlgeschlagen (exit {proc.returncode}): {err}"
            if Path(out_path).exists() and Path(out_path).stat().st_size > 0:
                return f"[BrowserSkill] Screenshot gespeichert: {out_path}"
            return f"[BrowserSkill] Screenshot erzeugt, aber Datei leer oder fehlt: {out_path}"
        except asyncio.TimeoutError:
            return f"[BrowserSkill] Screenshot Timeout für {url}"
        except Exception as e:
            return f"[BrowserSkill] Screenshot Fehler: {e}"
