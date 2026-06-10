"""
backend/skills/whatsapp.py — WhatsApp Skill via wacli CLI.

Unterstützt: Nachrichten senden, Kontakte suchen, Nachrichten lesen.
Erfordert: wacli installiert + einmalig `wacli auth` durchgeführt.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil

from backend.skills import Skill, SkillConfigField

_WACLI        = shutil.which("wacli") or os.path.expanduser("~/bin/wacli")
_DEFAULT_GROUP = ""  # via WHATSAPP_DEFAULT_GROUP (.env) oder agents.yaml konfigurieren

_GROUP_WORDS = re.compile(
    r"\b(gruppe|group|chat|kanal|channel|die\s+gruppe|unsere\s+gruppe|"
    r"h\.?g\.?o\.?d\.?)\b", re.I
)


class WhatsAppSkill(Skill):
    skill_id = "whatsapp"
    description = "WhatsApp: Nachrichten senden, lesen, Kontakte suchen via wacli"
    CONFIG_FIELDS = (
        SkillConfigField("default_group", env="WHATSAPP_DEFAULT_GROUP",
                         default=_DEFAULT_GROUP),
    )

    async def execute(self, query: str) -> str:
        if not _WACLI:
            return "[WhatsApp] wacli nicht gefunden"
        try:
            return await self._dispatch(query)
        except Exception as e:
            return f"[WhatsApp] Fehler: {e}"

    def _resolve_to(self, raw: str) -> str:
        """Löst Gruppen-/Kontaktname zu JID auf. Fallback: default_group."""
        raw = raw.strip()
        # JID direkt übergeben
        if "@" in raw:
            return raw
        # Gruppen-Keywords → default_group
        if _GROUP_WORDS.search(raw) or not raw:
            return self.config.get("default_group") or _DEFAULT_GROUP
        return raw

    def _extract_recipient(self, query: str) -> str:
        """Sucht 'an <kontakt/gruppe>'-Muster. Gruppen-Keywords haben Vorrang."""
        if _GROUP_WORDS.search(query):
            return self.config.get("default_group") or _DEFAULT_GROUP
        m = re.search(r"\ban\s+(\S+)", query, re.I)
        if m:
            return self._resolve_to(m.group(1).strip())
        return ""

    @staticmethod
    def _strip_send_prefix(query: str) -> str:
        """Entfernt 'schicke eine whatsapp nachricht an unsere gruppe mit ...'-Vorsätze.

        Strategie: wenn ein 'wort: ' in den ersten 120 Zeichen steht,
        ist alles davor Routing-Metadaten — Text beginnt nach dem ersten Doppelpunkt.
        Sonst: bekannte Prefix-Wörter token-weise abschneiden.
        """
        # Erste ": " im ersten Teil = Trennung Metadaten/Inhalt (vor Time-Stamps wie 15:01)
        m = re.search(r"^.{0,120}?:\s+", query)
        if m:
            return query[m.end():].strip()
        # Fallback: Token-weise prefix abschneiden
        tokens = query.split()
        skip = {"schicke", "schick", "sende", "send", "eine", "ein", "an", "die", "unsere",
                "whatsapp", "nachricht", "warnung", "meldung", "info", "message",
                "gruppe", "group", "chat", "mit", "einer", "the", "a"}
        while tokens and tokens[0].lower().strip(":,.;") in skip:
            tokens.pop(0)
        return " ".join(tokens).lstrip(":").strip()

    async def _dispatch(self, query: str) -> str:
        q = query.lower()
        send_intent = bool(re.search(r"\b(schick\w*|send\w*|nachricht|warnung|meldung|info)\b", q, re.I))

        # Explizite Quote: "text" → Inhalt ist klar
        m = re.search(r'["„“]([^"“”]{1,1000})["”"]', query)
        if m:
            text = m.group(1).strip()
            to   = self._extract_recipient(query) or self._resolve_to("gruppe")
            return await self._send_text(to, text)

        # send text: "schick/sende [an] <kontakt/gruppe>:  <nachricht>"
        m = re.search(
            r"(?:schicke?|sende?|send)\s+(?:an\s+)?([^\s:,\"]+)\s*:\s*(.+)", query, re.IGNORECASE
        )
        if m:
            to   = self._resolve_to(m.group(1).strip())
            text = m.group(2).strip().strip('"')
            return await self._send_text(to, text)

        # Send-Intent ohne Quotes/Colon → Default-Gruppe, kompletter Text als Nachricht
        if send_intent and _GROUP_WORDS.search(query):
            text = self._strip_send_prefix(query)
            if text:
                to = self._resolve_to("gruppe")
                return await self._send_text(to, text)

        # search messages: "suche <query>"
        m = re.search(r"(?:suche?|search|finde?)\s+(.+)", query, re.IGNORECASE)
        if m:
            return await self._search(m.group(1).strip())

        # list chats
        if any(w in q for w in ["chats", "gespräche", "kontakte"]):
            return await self._list_chats()

        # status / doctor
        if "status" in q or "doctor" in q:
            return await self._doctor()

        return (
            "[WhatsApp] Befehle:\n"
            "- 'sende an <kontakt>: <text>'\n"
            "- 'suche <stichwort>'\n"
            "- 'chats' — letzte Gespräche\n"
            "- 'status' — Verbindungsstatus"
        )

    async def _run(self, *args: str, timeout: float = 30.0) -> dict | list | str:
        # LaunchAgent com.agentclaw.wacli-sync hält den Store-Lock permanent.
        # Kurz stoppen → Befehl ausführen → wieder starten.
        sync_label = "com.agentclaw.wacli-sync"
        stopped = await self._launchctl("stop", sync_label)
        if stopped:
            await asyncio.sleep(1.0)  # Lock freigeben lassen

        try:
            cmd = [_WACLI, "--json"] + list(args)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                raise TimeoutError(f"wacli timeout: {' '.join(args)}")

            if proc.returncode != 0:
                raise RuntimeError(err.decode().strip() or f"exit {proc.returncode}")

            text = out.decode().strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        finally:
            if stopped:
                await self._launchctl("start", sync_label)

    @staticmethod
    async def _launchctl(action: str, _label: str) -> bool:
        plist = os.path.expanduser(
            "~/Library/LaunchAgents/com.agentclaw.wacli-sync.plist"
        )
        if not os.path.exists(plist):
            return False
        # KeepAlive:true → stop restartet sofort → unload/load nötig
        verb = "unload" if action == "stop" else "load"
        try:
            proc = await asyncio.create_subprocess_exec(
                "launchctl", verb, plist,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
            return True
        except Exception:
            return False

    async def _send_text(self, to: str, text: str) -> str:
        await self._run("send", "text", "--to", to, "--message", text, timeout=30.0)
        return f"[WhatsApp] ✅ Nachricht an '{to}' gesendet: {text[:80]}"

    async def _search(self, query: str) -> str:
        data = await self._run("messages", "search", query, "--limit", "5")
        rows = self._unwrap(data)
        if not rows:
            return f"[WhatsApp] Keine Nachrichten gefunden für: {query}"
        if isinstance(rows, list):
            lines = [
                f"- {m.get('sender_name') or m.get('from', '?')}: {m.get('text', '')[:80]}"
                for m in rows[:5]
            ]
            return "[WhatsApp] Suchergebnisse:\n" + "\n".join(lines)
        return f"[WhatsApp] {rows}"

    async def _list_chats(self) -> str:
        data = await self._run("chats", "list", "--limit", "8")
        rows = self._unwrap(data)
        if isinstance(rows, list):
            lines = [
                f"- {c.get('name') or c.get('jid', '?')} ({c.get('kind', '?')})" for c in rows[:8]
            ]
            return "[WhatsApp] Letzte Chats:\n" + "\n".join(lines)
        return f"[WhatsApp] {rows}"

    async def _doctor(self) -> str:
        data = await self._run("doctor")
        d = self._unwrap(data)
        if isinstance(d, dict):
            lines = [f"  {k}: {v}" for k, v in d.items() if k not in ("lock_info",)]
            return "[WhatsApp] Status:\n" + "\n".join(lines)
        return f"[WhatsApp] {d}"

    @staticmethod
    def _unwrap(data) -> object:
        """Packt {'success':True,'data':[...]} aus."""
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data
