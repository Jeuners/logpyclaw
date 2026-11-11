"""
backend/skills/gmail.py — GmailSkill (Stub + vollständige Dispatch-Logik).

Backend-Aufruf via CLI-Tool fehlt noch — alle anderen Teile (Intent-Erkennung,
Dispatch, Response-Formatting) sind implementiert.

Um den Skill zu aktivieren, muss ein Gmail-CLI-Tool verfügbar sein.
Empfehlung: `pip install gmail-cli` oder ähnliches und _call_backend() anpassen.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from enum import Enum

from backend.skills import Skill


class GmailAction(Enum):
    READ = "read"
    SEND = "send"
    SEARCH = "search"
    UNKNOWN = "unknown"


@dataclass
class GmailRequest:
    action: GmailAction
    # send
    to: str = ""
    subject: str = ""
    body: str = ""
    # read/search
    query: str = ""
    limit: int = 5
    # raw
    raw: str = ""


def _parse_intent(query: str) -> GmailRequest:
    """Erkennt Aktion und Parameter aus der Query."""
    q = query.strip()
    lower = q.lower()

    # ── SEND ──────────────────────────────────────────────────────────────────
    # "schreibe/sende/schick eine Mail an X betreff Y inhalt Z"
    send_match = re.search(
        r"(?:schreibe?|sende?|schick)\s+(?:eine?\s+)?(?:mail|e-?mail|nachricht)\s+an\s+([^\s,]+)",
        lower,
    )
    if send_match or re.search(r"\bsend\b.*\bto\b", lower):
        to_match = re.search(
            r"(?:an|to)\s+([\w.+-]+@[\w.-]+)", q, re.IGNORECASE
        )
        subj_match = re.search(
            r"(?:betreff|subject)\s*[:\-]?\s*(.+?)(?:\n|inhalt|body|$)",
            q,
            re.IGNORECASE,
        )
        body_match = re.search(
            r"(?:inhalt|body|text)\s*[:\-]?\s*(.+)$",
            q,
            re.IGNORECASE | re.DOTALL,
        )
        return GmailRequest(
            action=GmailAction.SEND,
            to=to_match.group(1) if to_match else "",
            subject=subj_match.group(1).strip() if subj_match else "",
            body=body_match.group(1).strip() if body_match else q,
            raw=q,
        )

    # ── SEARCH ────────────────────────────────────────────────────────────────
    search_kw = re.search(
        r"(?:such[e]?|search|find|filter|grep)\s+(?:nach\s+)?(.+)", q, re.IGNORECASE
    )
    if search_kw:
        return GmailRequest(
            action=GmailAction.SEARCH,
            query=search_kw.group(1).strip(),
            raw=q,
        )

    # ── READ ──────────────────────────────────────────────────────────────────
    read_kw = re.search(
        r"(?:lies?|lese?|zeige?|show|read|liste?|inbox|posteingang)",
        lower,
    )
    if read_kw:
        limit_match = re.search(r"(\d+)\s+(?:mails?|e-?mails?|nachrichten)", lower)
        return GmailRequest(
            action=GmailAction.READ,
            limit=int(limit_match.group(1)) if limit_match else 5,
            raw=q,
        )

    return GmailRequest(action=GmailAction.UNKNOWN, raw=q)


def _gmail_available() -> str | None:
    """Gibt den Pfad zum Gmail-CLI-Tool zurück, falls vorhanden."""
    for tool in ("gmail", "gm-cli", "mutt", "neomutt"):
        path = shutil.which(tool)
        if path:
            return path
    return None


class GmailSkill(Skill):
    skill_id = "gmail"
    description = (
        "Liest, sendet und durchsucht Gmail-Nachrichten. "
        "Unterstützt: 'lies meine Mails', 'sende Mail an X', 'suche nach Y'."
    )

    async def execute(self, query: str) -> str:
        req = _parse_intent(query)

        if req.action == GmailAction.SEND:
            return await self._handle_send(req)
        elif req.action == GmailAction.READ:
            return await self._handle_read(req)
        elif req.action == GmailAction.SEARCH:
            return await self._handle_search(req)
        else:
            return (
                "[GmailSkill] Aktion nicht erkannt. Beispiele:\n"
                "- 'lies meine letzten 5 Mails'\n"
                "- 'sende Mail an max@example.com betreff Hallo body Text'\n"
                "- 'suche nach Betreff Rechnung'"
            )

    # ── Handler ───────────────────────────────────────────────────────────────

    async def _handle_send(self, req: GmailRequest) -> str:
        if not req.to:
            return "[GmailSkill] Empfänger-Adresse fehlt. Bitte 'an <email>' angeben."
        result = await self._call_backend("send", req)
        return result

    async def _handle_read(self, req: GmailRequest) -> str:
        result = await self._call_backend("read", req)
        return result

    async def _handle_search(self, req: GmailRequest) -> str:
        if not req.query:
            return "[GmailSkill] Suchbegriff fehlt."
        result = await self._call_backend("search", req)
        return result

    # ── Backend-Aufruf (STUB) ─────────────────────────────────────────────────

    async def _call_backend(self, action: str, req: GmailRequest) -> str:
        """
        STUB — Backend-Aufruf nicht implementiert.

        Um den Skill zu aktivieren:
        1. Ein Gmail-CLI-Tool installieren (z.B. `pip install gmail-cli`)
        2. Diese Methode entsprechend befüllen.

        Dispatch-Beispiel für action == 'send':
            subprocess.run(["gmail", "send", "--to", req.to,
                            "--subject", req.subject, "--body", req.body])

        Für action == 'read':
            subprocess.run(["gmail", "list", "--limit", str(req.limit)])

        Für action == 'search':
            subprocess.run(["gmail", "search", req.query])
        """
        action_de = {"send": "Senden", "read": "Lesen", "search": "Suchen"}.get(
            action, action
        )
        detail = ""
        if action == "send":
            detail = f" (an: {req.to}, Betreff: {req.subject!r})"
        elif action == "search":
            detail = f" (Suchbegriff: {req.query!r})"
        elif action == "read":
            detail = f" (letzte {req.limit} Mails)"

        return (
            f"[GmailSkill] Gmail nicht konfiguriert.\n"
            f"Aktion erkannt: {action_de}{detail}\n\n"
            "Bitte ein Gmail-CLI-Tool installieren und "
            "GmailSkill._call_backend() in backend/skills/gmail.py befüllen.\n"
            "Empfehlung: `pip install gmail-cli` oder IMAP/SMTP via `imaplib`/`smtplib`."
        )
