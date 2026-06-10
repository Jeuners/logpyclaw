"""
backend/skills/urlfetch.py — UrlFetchSkill.

Fetcht beliebige URLs via httpx (follow_redirects=True).
Content-Type-Erkennung:
  - HTML  → Text extrahiert (stdlib html.parser)
  - JSON  → pretty-printed
  - sonst → Rohtext

Max. 5000 Zeichen Rückgabe.
skill_id = "urlfetch"
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

import httpx

from backend.skills import Skill

_TIMEOUT = 15.0
_MAX_CHARS = 5000
_SKIP_TAGS = {"script", "style", "nav", "footer", "head", "noscript", "iframe"}


class _TextExtractor(HTMLParser):
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


class UrlFetchSkill(Skill):
    skill_id = "urlfetch"
    description = (
        "Fetcht eine URL und gibt den Inhalt zurück. "
        "Erkennt HTML (→ Text), JSON (→ pretty-print) und Plaintext automatisch. "
        "Max 5000 Zeichen. Direkte URL als Query übergeben."
    )

    async def execute(self, query: str) -> str:
        query = query.strip()
        url_m = re.search(r"https?://\S+", query)
        if not url_m:
            return "[UrlFetch] Keine URL in der Anfrage gefunden."
        url = url_m.group(0).rstrip(".,;\"')}]")
        return await self._fetch(url)

    async def _fetch(self, url: str) -> str:
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; LogpyClaw/3)"},
            ) as client:
                r = await client.get(url)
                r.raise_for_status()

            ct = r.headers.get("content-type", "").lower()

            if "html" in ct:
                extractor = _TextExtractor()
                extractor.feed(r.text)
                text = extractor.get_text()
                label = "HTML→Text"
            elif "json" in ct:
                try:
                    text = json.dumps(r.json(), indent=2, ensure_ascii=False)
                except Exception:
                    text = r.text
                label = "JSON"
            else:
                text = r.text
                label = "Plaintext"

            if not text.strip():
                return f"[UrlFetch] Leere Antwort von {url}"

            truncated = text[:_MAX_CHARS]
            suffix = f"\n… [auf {_MAX_CHARS} Zeichen gekürzt]" if len(text) > _MAX_CHARS else ""
            return f"[{url}] ({label})\n\n{truncated}{suffix}"

        except httpx.HTTPStatusError as e:
            return f"[UrlFetch] HTTP {e.response.status_code} bei {url}"
        except httpx.ConnectError:
            return f"[UrlFetch] Verbindung zu {url} fehlgeschlagen."
        except httpx.TimeoutException:
            return f"[UrlFetch] Timeout bei {url} (>{_TIMEOUT}s)."
        except Exception as e:
            return f"[UrlFetch] Fehler: {e}"
