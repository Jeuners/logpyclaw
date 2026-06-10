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
import os
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

from backend.skills import Skill

_TIMEOUT = 15.0
_MAX_CHARS = 5000
_SCREENSHOT_SCRIPT = os.path.expanduser("~/.claude/skills/screenshot/screenshot.sh")

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

        # Modus erkennen: screenshot vs. fetch
        wants_screenshot = bool(re.search(r"\b(screenshot|snap|capture|shot|bild|aufnahme)\b", query, re.I))

        # 1. Explizites screenshot mit URL: "screenshot https://..."
        m = re.match(r"(?i)^screenshot\s+(https?://\S+)", query)
        if m:
            return await self._screenshot(m.group(1).rstrip(".,;\"')}]"))

        # 2. fetch/navigate Alias
        m = re.match(r"(?i)^(?:fetch|navigate|open|go to|goto)\s+(https?://\S+)", query)
        if m:
            return await self._fetch(m.group(1))

        # 3. URLs aus dem Query/Kontext extrahieren (auch wenn Vorgänger-Step
        #    eine Liste von URLs als [Vorherige Ergebnisse] reingab)
        urls = self._extract_urls(query)
        if urls:
            if wants_screenshot:
                # Screenshot je URL — bei vielen URLs: nur erste 5
                limited = urls[:5]
                results = []
                for url in limited:
                    res = await self._screenshot(url)
                    results.append(res)
                more = f"\n\n_(+ {len(urls) - len(limited)} weitere URLs übersprungen)_" if len(urls) > len(limited) else ""
                return "\n\n".join(results) + more
            # Default: erste URL fetchen
            return await self._fetch(urls[0])

        return (
            "[BrowserSkill] Keine URL gefunden im Input.\n"
            "Nutze: 'fetch <url>', 'screenshot <url>', oder reiche URL via "
            "Vorgänger-Step durch."
        )

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        """Extrahiert HTTP(S)-URLs aus beliebigem Text — dedupliziert."""
        urls = re.findall(r"https?://[^\s<>\"'\\]+", text)
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            u = u.rstrip(".,;:\"')}]>")
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    async def _fetch(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LogpyClaw/3)"},
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

        # In frontend/screenshots/ ablegen — wird via /static/screenshots/ ausgeliefert
        import time as _time
        screenshots_dir = Path(__file__).resolve().parent.parent.parent / "frontend" / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        filename = f"shot-{int(_time.time()*1000)}.png"
        out_path = str(screenshots_dir / filename)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", str(script), "--url", url, "--out", out_path, "--wait", "3000",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45)
                if proc.returncode != 0:
                    err = stderr.decode(errors="replace")[:300]
                    return f"[BrowserSkill] Screenshot fehlgeschlagen (exit {proc.returncode}): {err}"
            except TimeoutError:
                # Chrome hängt manchmal im Cleanup obwohl PNG schon geschrieben ist
                proc.kill()
                await asyncio.sleep(0.5)
                if not (Path(out_path).exists() and Path(out_path).stat().st_size > 0):
                    return f"[BrowserSkill] Screenshot Timeout für {url}"
                # Datei existiert → akzeptieren
            if Path(out_path).exists() and Path(out_path).stat().st_size > 0:
                # Public-URL (Static-Mount in app.py: /static → frontend/)
                http_url = f"/static/screenshots/{filename}"
                size_kb = Path(out_path).stat().st_size // 1024
                return (
                    f"[BrowserSkill] 📸 Screenshot von {url} ({size_kb}KB)\n"
                    f"{http_url}"
                )
            return f"[BrowserSkill] Screenshot erzeugt, aber Datei leer oder fehlt: {out_path}"
        except TimeoutError:
            return f"[BrowserSkill] Screenshot Timeout für {url}"
        except Exception as e:
            return f"[BrowserSkill] Screenshot Fehler: {e}"
