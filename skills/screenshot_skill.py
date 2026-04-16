"""
skills/screenshot_skill.py — Playwright-Screenshot einer URL.

Einfach, zuverlässig, kein Chrome-DevTools nötig.
Triggert auf: "screenshot https://...", "mach einen screenshot von ...", etc.
Optional: "screenshot https://... als dateiname.png" → speichert PNG zusätzlich auf Disk.
"""
import base64
import logging
import os
import re

import requests

from skills.base import BaseSkill, SkillResult

logger = logging.getLogger(__name__)

_SCREENSHOT_API = "http://localhost:5050/api/screenshot"
_TIMEOUT = 45


def _extract_url(message: str) -> str | None:
    # Bei A2A-Tasks: nur den Teil NACH dem Separator "Deine Aufgabe:" verwenden
    # damit Kontext-URLs aus vorherigen Schritten nicht fälschlich genutzt werden
    task_sep = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
    search_text = task_sep.group(1).strip() if task_sep else message

    m = re.search(r"https?://\S+", search_text)
    if m:
        return m.group(0).rstrip(".,;\"')}]")
    # Domain ohne Protokoll: "screenshot example.com"
    m = re.search(
        r"\b((?:[a-z0-9-]+\.)+(?:com|de|org|net|io|ai|app|dev|co|uk|ch|at|eu)\S*)",
        search_text, re.IGNORECASE
    )
    if m:
        return "https://" + m.group(1).rstrip(".,;\"')")
    return None


class ScreenshotSkill(BaseSkill):
    id = "screenshot"
    name = "Screenshot"
    icon = "photo_camera"
    description = "Erstellt einen Playwright-Screenshot einer Webseite. Syntax: 'screenshot https://...'"
    triggers = [
        r"\bscreenshot\b.{0,80}https?://",
        r"\bscreenshot\b.{0,60}\b(?:[a-z0-9-]+\.)+(?:com|de|org|net|io|ai|app|dev)\b",
        r"\bmach\w*\s+(?:einen?\s+)?screenshot\b",
        r"\bscreenshot\s+(?:von|of|der|die|das|the)\b",
    ]
    # Nicht triggern wenn chrome_browser-Befehl
    excludes = [r"chrome_browser\s*:"]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        # Bei A2A-Tasks nur den Teil nach dem Separator für URL+Dateiname verwenden
        task_sep = re.search(r"---\s*\nDeine Aufgabe:\s*(.+)", message, re.DOTALL)
        search_text = task_sep.group(1).strip() if task_sep else message

        url = _extract_url(message)
        if not url:
            return SkillResult(
                error="Keine URL für Screenshot erkannt. Beispiel: 'screenshot https://example.com'",
                skill_used=self.id,
            )

        # Optional: "als dateiname.png" → Screenshot auf Disk speichern
        save_m = re.search(
            r'\bals\s+([\w\-]+\.png)\b',
            search_text, re.IGNORECASE
        )
        save_filename = save_m.group(1) if save_m else None

        logger.info("Screenshot: %s%s", url, f" → {save_filename}" if save_filename else "")
        try:
            resp = requests.post(
                _SCREENSHOT_API,
                json={"url": url, "delay": 1500},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            image_b64 = data.get("image")
            if not image_b64:
                return SkillResult(error="Screenshot leer", skill_used=self.id)

            # Auf Disk speichern falls gewünscht
            saved_path = None
            if save_filename:
                try:
                    from skills.file_skill import _get_base_dir
                    base_dir, _ = _get_base_dir(agent)
                    filepath = os.path.join(base_dir, save_filename)
                    # base64 Data-URI → binary PNG
                    b64_data = image_b64.split(",", 1)[1] if "," in image_b64 else image_b64
                    with open(filepath, "wb") as f:
                        f.write(base64.b64decode(b64_data))
                    saved_path = filepath
                    logger.info("Screenshot gespeichert: %s", filepath)
                except Exception as e:
                    logger.warning("Screenshot-Speichern fehlgeschlagen: %s", e)

            result_text = f"Screenshot von {url}"
            if saved_path:
                result_text += f"\nGespeichert als: {save_filename}"

            return SkillResult(
                text=result_text,
                image=image_b64,
                skill_used=self.id,
                metadata={"saved_as": save_filename, "saved_path": saved_path},
            )
        except requests.exceptions.Timeout:
            return SkillResult(error=f"Timeout beim Screenshot von {url}", skill_used=self.id)
        except Exception as e:
            return SkillResult(error=f"Screenshot fehlgeschlagen: {e}", skill_used=self.id)
