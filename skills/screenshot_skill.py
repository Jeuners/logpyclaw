"""
skills/screenshot_skill.py — Screenshot-Skill: Webpage-Screenshots via Playwright.

Delegiert an den existierenden POST /api/screenshot Endpoint.
Keine Playwright-Logik hier — der Endpoint übernimmt alles inklusive
SSRF-Schutz, Playwright-Import-Check und Fehlerbehandlung.
"""
import re
import logging

import requests

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

_SCREENSHOT_API = "http://localhost:5050/api/screenshot"
_TIMEOUT = 30  # Sekunden (Playwright braucht Zeit)

# Regex zum Extrahieren einer URL aus der Nachricht
_URL_RE = re.compile(
    r"(https?://[^\s]+)"
    r"|([a-zA-Z0-9][a-zA-Z0-9\-]+\.[a-zA-Z]{2,}[^\s]*)",
    re.IGNORECASE,
)


class ScreenshotSkill(BaseSkill):
    id = "screenshot"
    name = "Screenshot"
    icon = "📸"
    description = "Macht einen Screenshot einer Webseite via Playwright."
    triggers = [
        r"\bscreenshot\b",
        r"\bscreenshot\s+(von|of)\b",
        r"\bseite\s+(knipsen|screenshot)\b",
        r"\bbild\s+von\s+.*seite\b",
        r"\bcapture\s+screen\b",
        r"\btake\s+a\s+screenshot\b",
        r"\bwebseite\s+(zeig|schau|öffne|screenshot)\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        # URL aus der Nachricht extrahieren
        match = _URL_RE.search(message)
        if not match:
            return SkillResult(
                error="Keine URL gefunden. Bitte gib eine URL an, z.B. 'Screenshot von https://example.com'",
                skill_used=self.id,
            )

        url = match.group(0).strip().rstrip(".,;!?")

        # https:// prefix hinzufügen wenn fehlt
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        logger.info("ScreenshotSkill: Capturing %s", url)

        try:
            resp = requests.post(
                _SCREENSHOT_API,
                json={"url": url},
                timeout=_TIMEOUT,
            )
        except requests.exceptions.Timeout:
            return SkillResult(
                error=f"Timeout nach {_TIMEOUT}s beim Screenshot von {url}",
                skill_used=self.id,
            )
        except requests.exceptions.ConnectionError:
            return SkillResult(
                error="AgentClaw API nicht erreichbar (localhost:5050). Läuft die App?",
                skill_used=self.id,
            )
        except Exception as e:
            return SkillResult(
                error=f"Fehler beim Screenshot-Request: {e}",
                skill_used=self.id,
            )

        if resp.status_code == 501:
            return SkillResult(
                error="Playwright nicht installiert. Führe aus: pip install playwright && playwright install chromium",
                skill_used=self.id,
            )
        if resp.status_code == 403:
            detail = resp.json().get("detail", "URL geblockt (SSRF-Schutz)")
            return SkillResult(error=detail, skill_used=self.id)
        if not resp.ok:
            detail = ""
            try:
                detail = resp.json().get("detail", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            return SkillResult(
                error=f"Screenshot fehlgeschlagen (HTTP {resp.status_code}): {detail}",
                skill_used=self.id,
            )

        data = resp.json()
        image_data_uri = data.get("image")
        if not image_data_uri:
            return SkillResult(
                error="API-Antwort enthält kein Bild",
                skill_used=self.id,
            )

        return SkillResult(
            text=f"Screenshot von {url}",
            image=image_data_uri,
            skill_used=self.id,
            metadata={"url": url},
        )
