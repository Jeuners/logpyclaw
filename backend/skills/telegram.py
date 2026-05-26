"""
backend/skills/telegram.py — Telegram-Nachrichten/Bilder senden via Bot-API.

Config (agents.yaml oder ENV):
  token:   BOT_TOKEN  (TELEGRAM_BOT_TOKEN)
  chat_id: CHAT_ID    (TELEGRAM_CHAT_ID)
"""
from __future__ import annotations

import re

import httpx

from backend.skills import Skill, SkillConfigField


def _extract_text(message: str) -> str:
    """Extrahiert den eigentlichen Text aus der Nachricht."""
    # Anführungszeichen
    m = re.search(r'["„“]([^"”“]{1,500})["”]', message)
    if m:
        return m.group(1).strip()
    # "an telegram:\nText"
    m = re.search(
        r"(?:telegram|tg).*?(?:kanal|channel|gruppe|group|chat)?[:\s]*\n+(.+)",
        message, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    # "sende X an telegram"
    m = re.search(
        r"sende?\s+(.+?)\s+an\s+(?:den\s+)?(?:telegram|tg)",
        message, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()
    return message.strip()


class TelegramSkill(Skill):
    skill_id = "telegram"
    description = "Sendet Nachrichten und Bilder via Telegram Bot-API."
    CONFIG_FIELDS = (
        SkillConfigField("token",   env="TELEGRAM_BOT_TOKEN",   required=True,  secret=True),
        SkillConfigField("chat_id", env="TELEGRAM_CHAT_ID",     required=True),
    )

    async def execute(self, query: str) -> str:
        token   = self.config.get("token", "")
        chat_id = self.config.get("chat_id", "")
        if not token or not chat_id:
            return (
                "[Telegram] Nicht konfiguriert.\n"
                "Bitte TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID in .env setzen."
            )
        text = _extract_text(query)[:4096]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                )
                r.raise_for_status()
            return f"[Telegram] ✓ Gesendet: {text[:80]}{'…' if len(text) > 80 else ''}"
        except Exception as e:
            return f"[Telegram] Fehler: {e}"
