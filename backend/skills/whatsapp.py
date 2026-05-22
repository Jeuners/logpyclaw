"""
backend/skills/whatsapp.py — WhatsApp Skill via wacli CLI.

Unterstützt: Nachrichten senden, Kontakte suchen, Nachrichten lesen.
Erfordert: wacli installiert + einmalig `wacli auth` durchgeführt.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil

from backend.skills import Skill

_WACLI = shutil.which("wacli") or "/Users/jeuner/.local/bin/wacli"


class WhatsAppSkill(Skill):
    skill_id = "whatsapp"
    description = "WhatsApp: Nachrichten senden, lesen, Kontakte suchen via wacli"

    async def execute(self, query: str) -> str:
        if not _WACLI:
            return "[WhatsApp] wacli nicht gefunden"
        try:
            return await self._dispatch(query)
        except Exception as e:
            return f"[WhatsApp] Fehler: {e}"

    async def _dispatch(self, query: str) -> str:
        q = query.lower()

        # send text: "schick/sende [an] <kontakt> [:]  <nachricht>"
        m = re.search(
            r"(?:schick|sende?|send)\s+(?:an\s+)?([^\s:,]+)[:\s]+(.+)", query, re.IGNORECASE
        )
        if m:
            return await self._send_text(m.group(1).strip(), m.group(2).strip())

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

    async def _run(self, *args: str, timeout: float = 15.0) -> dict | list | str:
        cmd = [_WACLI, "--json", "--lock-wait", "15s"] + list(args)
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
