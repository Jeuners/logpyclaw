"""
backend/skills/telegram.py — Telegram-Nachrichten/Bilder/Videos via Bot-API.

Funktionen:
  - senden:  Text, Bilder (sendPhoto) oder Videos (sendVideo) an chat_id
  - lesen:   Letzte ungelesene Updates via getUpdates (DMs an den Bot,
             oder alle Messages in Gruppen wo der Bot Privacy off hat)

Send-Syntax (natürliche Sprache oder strukturiert):
  sende "Hallo Welt" an telegram
  sende bild: <pfad> an telegram
  sende image: <pfad> caption: "Mein Bild" an telegram
  sende video: <pfad> caption: "Clip" an telegram

Config (agents.yaml oder ENV):
  token:   BOT_TOKEN  (TELEGRAM_BOT_TOKEN)
  chat_id: CHAT_ID    (TELEGRAM_CHAT_ID)
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from backend.core.logging import get_logger
from backend.skills import Skill, SkillConfigField


_log = get_logger("logpyclaw.skill.telegram")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_updates(token: str, offset: int | None = None, limit: int = 100, timeout: int = 10) -> list[dict]:
    """Holt Updates vom Bot. Bei offset werden nur Updates MIT ID >= offset
    zurückgegeben und ältere acknowledged (long-polling Konvention)."""
    params: dict = {"limit": limit, "timeout": 0}
    if offset is not None:
        params["offset"] = offset
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates", params=params)
        if r.status_code == 200 and r.json().get("ok"):
            return r.json().get("result", [])
    return []


def _extract_text(message: str) -> str:
    """Extrahiert den eigentlichen Text aus einer 'sende …'-Anweisung (ohne
    Bild-/Video-Spezifikationen)."""
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
    # "sende: <text>" / "send: <text>" am Anfang
    m = re.match(r"\s*(sende?|send|nachricht|message)\s*[:\-]\s*(.+)",
                 message, re.IGNORECASE | re.DOTALL)
    if m:
        return m.group(2).strip()
    return message.strip()


def _fmt_size(n: int) -> str:
    """Menschen-lesbare Dateigröße (B / KB / MB)."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def _parse_send(query: str) -> dict:
    """Parst einen Send-Befehl → {kind: 'text'|'photo'|'video', path, caption}.

    Reihenfolge der Erkennung:
      1. video: <pfad>   → kind='video'
      2. image/bild/photo: <pfad>  → kind='photo'
      3. sonst: kind='text', text=_extract_text(query)
    """
    # 1. Video (vor Image prüfen — "video: ..." darf nicht als "image: ..." gelesen werden)
    m = re.search(r"\bvideo\s*[:=]\s*(\S+)", query, re.IGNORECASE)
    if m:
        cap_m = re.search(r"caption\s*[:=]\s*(.+?)(?:\s*$|\s+an\s+|\s+bild\s*:|\s+image\s*:|\s+video\s*:)",
                          query, re.IGNORECASE)
        caption = cap_m.group(1).strip() if cap_m else ""
        # Caption aus Anführungszeichen extrahieren wenn vorhanden
        q_m = re.search(r'caption\s*[:=]\s*["„“]([^"”“]+)["”]', query, re.IGNORECASE)
        if q_m:
            caption = q_m.group(1).strip()
        return {"kind": "video", "path": m.group(1), "caption": caption}

    # 2. Bild
    m = re.search(r"\b(?:image|bild|photo|foto|pic)\s*[:=]\s*(\S+)", query, re.IGNORECASE)
    if m:
        cap_m = re.search(r"caption\s*[:=]\s*(.+?)(?:\s*$|\s+an\s+|\s+bild\s*:|\s+image\s*:|\s+video\s*:)",
                          query, re.IGNORECASE)
        caption = cap_m.group(1).strip() if cap_m else ""
        q_m = re.search(r'caption\s*[:=]\s*["„“]([^"”“]+)["”]', query, re.IGNORECASE)
        if q_m:
            caption = q_m.group(1).strip()
        return {"kind": "photo", "path": m.group(1), "caption": caption}

    # 3. Plain text
    return {"kind": "text", "text": _extract_text(query)}


def _is_read_intent(query: str) -> bool:
    """Erkennt ob der User Updates lesen will (statt senden)."""
    q = query.lower().strip()
    # Strikt: erstes Wort muss ein Read-Kommando sein
    first = q.split()[0] if q.split() else ""
    if first in ("lies", "lese", "list", "liste", "zeige", "zeig"):
        return True
    # Phrasen mit Telegram-Kontext (höhere Präzision)
    telegram_phrases = (
        "telegram updates", "tg updates", "telegram nachrichten",
        "nachrichten auf telegram", "auf telegram",
        "was kam auf telegram", "was kam im telegram",
        "chats lesen", "telegram chats",
    )
    return any(t in q for t in telegram_phrases)


def _extract_chat_filter(query: str) -> str | None:
    """Optional: 'chat: <id>' oder 'chat: <name>' aus Query extrahieren."""
    m = re.search(r"chat\s*[:=]\s*([^\s]+)", query, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _format_update(upd: dict) -> str | None:
    """Formatiert ein einzelnes Update als kompakte Zeile. Gibt None zurück
    wenn das Update kein message/edited_message/channel_post enthält."""
    msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
    if not msg:
        return None

    chat = msg.get("chat", {})
    chat_title = chat.get("title") or chat.get("username") or chat.get("first_name") or str(chat.get("id", "?"))
    chat_type = chat.get("type", "?")

    sender = msg.get("from", {}) or {}
    sender_name = sender.get("first_name") or sender.get("username") or ""
    if not sender_name and chat_type in ("group", "supergroup"):
        sender_name = msg.get("author_signature", "") or "(group)"

    # Zeit (UTC → lokal HH:MM)
    ts = msg.get("date")
    time_str = ""
    if ts:
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
            time_str = dt.strftime("%H:%M")
        except (ValueError, OSError):
            time_str = ""

    text = msg.get("text") or msg.get("caption") or ""
    if not text and msg.get("photo"):
        text = "[Foto]"
    elif not text and msg.get("document"):
        text = f"[Datei: {msg['document'].get('file_name', '?')}]"
    elif not text and msg.get("sticker"):
        text = "[Sticker]"
    elif not text and msg.get("voice"):
        text = "[Voice]"
    elif not text:
        text = f"[{msg.get('content_type', '?')}-Nachricht]"
    # Auf eine Zeile bringen, trailing whitespace weg
    text = text.replace("\n", " ").strip()

    prefix = f"[{time_str}] " if time_str else ""
    who = f"{sender_name} " if sender_name and chat_type not in ("private",) else ""
    return f"{prefix}💬 {chat_title} ({chat_type}) {who}→ {text}"


# ── Skill ─────────────────────────────────────────────────────────────────────

class TelegramSkill(Skill):  # noqa: E302
    async def _auto_resolve_chat_id(self, token: str) -> str:
        """Holt die Chat-ID aus dem letzten Update (User → Bot)."""
        try:
            updates = await _get_updates(token, limit=10)
            if not updates:
                return ""
            for upd in reversed(updates):
                chat = upd.get("message", {}).get("chat", {})
                if chat.get("id"):
                    return str(chat["id"])
        except Exception:
            pass
        return ""


    skill_id = "telegram"
    description = (
        "Sendet und liest Telegram-Nachrichten via Bot-API. "
        "Senden: 'sende <text> an telegram'. Lesen: 'lies telegram updates', "
        "'zeige nachrichten', 'was kam auf telegram'."
    )
    CONFIG_FIELDS = (
        SkillConfigField("token",   env="TELEGRAM_BOT_TOKEN",   required=True,  secret=True),
        SkillConfigField("chat_id", env="TELEGRAM_CHAT_ID",     required=False),
    )

    async def execute(self, query: str) -> str:
        token   = self.config.get("token", "")
        chat_id = self.config.get("chat_id", "")
        if not token:
            return (
                "[Telegram] Kein Token konfiguriert.\n"
                "Bitte TELEGRAM_BOT_TOKEN in .env setzen."
            )

        # ── Read-Modus ─────────────────────────────────────────────────────
        if _is_read_intent(query):
            _log.info("read intent detected: %r", query[:80])
            return await self._read(token, chat_id, query)

        # ── Send-Modus ─────────────────────────────────────────────────────
        if not chat_id:
            chat_id = await self._auto_resolve_chat_id(token)
            if not chat_id:
                return (
                    "[Telegram] Keine Chat-ID konfiguriert und keine via getUpdates "
                    "gefunden. Bitte schicke dem Bot @DillesContactAgent_bot "
                    "einmal eine Nachricht, dann nochmal versuchen — oder "
                    "TELEGRAM_CHAT_ID in .env setzen."
                )
            self.config["chat_id"] = chat_id

        text = _extract_text(query)[:4096]
        send = _parse_send(query)
        _log.info("send kind=%s chat_id=%s path=%s", send["kind"], chat_id, send.get("path", "<text>"))

        if send["kind"] == "text":
            text = send["text"][:4096]
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

        # Bild oder Video: Datei muss lokal existieren
        path = send["path"].strip("\"'")
        p = Path(path).expanduser()
        if not p.exists():
            return f"[Telegram] Datei nicht gefunden: {path}"

        endpoint = (
            "sendPhoto" if send["kind"] == "photo" else "sendVideo"
        )
        kind_label = "Bild" if send["kind"] == "photo" else "Video"
        caption = (send.get("caption") or "")[:1024]
        try:
            with p.open("rb") as f:
                async with httpx.AsyncClient(timeout=120) as client:
                    r = await client.post(
                        f"https://api.telegram.org/bot{token}/{endpoint}",
                        data={"chat_id": chat_id, "caption": caption},
                        files={send["kind"]: (p.name, f, "application/octet-stream")},
                    )
                    r.raise_for_status()
            return (
                f"[Telegram] ✓ {kind_label} gesendet: {p.name}\n"
                f"Größe: {_fmt_size(p.stat().st_size)}"
                + (f"\nCaption: {caption}" if caption else "")
            )
        except Exception as e:
            return f"[Telegram] Fehler: {e}"

    # ── Read-Logik ─────────────────────────────────────────────────────────

    async def _read(self, token: str, chat_id: str, query: str) -> str:
        chat_filter = _extract_chat_filter(query)
        # Limit aus Query: "lies letzte 5", "zeige 10 nachrichten"
        m = re.search(r"(?:letzte|last|die\s+letzten?)\s+(\d+)", query, re.IGNORECASE)
        limit = max(1, min(int(m.group(1)), 100)) if m else 100

        updates = await _get_updates(token, limit=limit)
        if not updates:
            return (
                "[Telegram] Keine neuen Nachrichten. Der Bot empfängt nur Messages, "
                "die ihm geschickt wurden (DMs oder Gruppen, in denen er hinzugefügt "
                "wurde). Für Updates: schreib dem Bot erst etwas."
            )

        # Filtern
        lines: list[str] = []
        ack_offset = 0  # höchste gesehenen update_id → danach acknowledgen
        for upd in updates:
            uid = upd.get("update_id", 0)
            if uid >= ack_offset:
                ack_offset = uid + 1

            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
            if not msg:
                continue

            # Chat-Filter
            if chat_filter:
                this_chat = msg.get("chat", {})
                this_id = str(this_chat.get("id", ""))
                this_title = (this_chat.get("title") or this_chat.get("username")
                              or this_chat.get("first_name") or "")
                if chat_filter != this_id and chat_filter.lower() not in this_title.lower():
                    continue

            line = _format_update(upd)
            if line:
                lines.append(line)

        # Updates acknowledgen — verhindert dass dieselben Messages beim
        # nächsten Aufruf nochmal kommen.
        if ack_offset:
            await _get_updates(token, offset=ack_offset, limit=1)

        if not lines:
            filter_note = f" (Filter: {chat_filter})" if chat_filter else ""
            return f"[Telegram] Keine passenden Nachrichten{filter_note}."

        header = f"[Telegram] {len(lines)} Nachricht(en)"
        if chat_filter:
            header += f" · Filter: {chat_filter}"
        return header + "\n" + "\n".join(lines)