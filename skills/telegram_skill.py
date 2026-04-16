"""Telegram outgoing message/image skill."""
import re
import base64
import json
import os

import requests


def _load_providers() -> dict:
    try:
        from core.config import PROVIDERS_FILE
        with open(PROVIDERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def run_telegram(message: str, image_base64: str = None) -> str:
    """Send message or image to Telegram."""
    providers = _load_providers()
    tg = providers.get("telegram", {})
    token = tg.get("bot_token", "")
    chat_id = tg.get("chat_id", "")

    if not token or not chat_id:
        return "❌ Telegram nicht konfiguriert. Bitte Bot-Token und Chat-ID in den Provider-Einstellungen eintragen."

    # Extract the actual text to send
    # Priority 1: quoted content e.g. "Hallo Welt" or 'Hallo Welt'
    quoted = re.search(r'["\u201e\u201c]([^"\u201d\u201c]{1,500})["\u201d]', message)
    if quoted:
        text = quoted.group(1).strip()
    else:
        # Priority 2: content after ":\n" separator (agent passes trigger + newlines + actual content)
        # e.g. "Sende an Telegram Kanal:\n\nHier ist der Bericht..."
        colon_split = re.search(
            r"(?:telegram|tg).*?(?:kanal|channel|gruppe|group|chat)?[:\s]*\n+(.+)",
            message, re.IGNORECASE | re.DOTALL
        )
        if colon_split:
            text = colon_split.group(1).strip()
        else:
            # Priority 3: pattern-based extraction for inline text
            text = ""
            patterns = [
                r"schick.*(das\s*)?(bild|foto|photo|image).*telegram[:\s]*(.*)",
                r"schick.*telegram[:\s]*(.*)",
                r"sende?\s*(?:die\s*)?(?:nachricht|message|text)[:\s]+(.+?)(?:\s+an\s+(?:den\s*)?telegram.*)?$",
                # "sende X an telegram" → capture X before "an telegram"
                r"sende?\s+(.+?)\s+an\s+(?:den\s+)?(?:telegram|tg)(?:\s+(?:kanal|channel|gruppe|group|chat))?",
                r"send\s+(.+?)\s+to\s+telegram",
                r"post.*telegram[:\s]*(.*)",
            ]
            for p in patterns:
                m = re.search(p, message, re.IGNORECASE | re.DOTALL)
                if m:
                    # Take last non-empty group
                    for grp in reversed(m.groups()):
                        if grp and grp.strip():
                            text = grp.strip()
                            break
                    if text:
                        break
            if not text:
                text = message  # fallback: send full message

    if image_base64 and "," in image_base64:
        b64_data = image_base64.split(",", 1)[1]
        img_bytes = base64.b64decode(b64_data)
        files = {"photo": ("agentclaw.jpg", img_bytes, "image/jpeg")}
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        data = {"chat_id": chat_id, "caption": text[:1024]}
        try:
            resp = requests.post(url, data=data, files=files, timeout=30)
            if resp.ok:
                return f"✅ Bild an Telegram gesendet: {text}"
            else:
                return f"❌ Telegram-Fehler: {resp.json().get('description', resp.text[:100])}"
        except Exception as e:
            return f"❌ Telegram-Fehler: {str(e)[:100]}"
    else:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {"chat_id": chat_id, "text": text[:4096]}
        try:
            resp = requests.post(url, json=data, timeout=30)
            if resp.ok:
                return f"✅ Nachricht an Telegram gesendet: {text}"
            else:
                return f"❌ Telegram-Fehler: {resp.json().get('description', resp.text[:100])}"
        except Exception as e:
            return f"❌ Telegram-Fehler: {str(e)[:100]}"


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult


class TelegramSkill(BaseSkill):
    id = "telegram"
    name = "Telegram"
    icon = "send"
    description = "Sends messages and images via Telegram."
    triggers = [
        r"\b(telegram|send|sende|schicke|nachricht)\b.{0,30}\b(telegram|message|nachricht)\b",
        r"\b(sende|schick|send)\b.{0,50}\b(telegram)\b",
    ]
    requires = ["telegram"]

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        image_b64 = context.get("image_b64")
        try:
            result = run_telegram(message, image_b64)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
