"""WhatsApp Skill via wacli (https://github.com/steipete/wacli)."""
import json
import os
import re
import subprocess
import shutil
from skills.base import BaseSkill, SkillResult

WACLI = shutil.which("wacli") or "/Users/jeuner/bin/wacli"
LAUNCHD_LABEL = "com.agentclaw.wacli-sync"


WACLI_LOCK = os.path.expanduser("~/.wacli/LOCK")


def _release_lock(error_out: str) -> bool:
    """Sync-Prozess killen + LOCK-Datei entfernen."""
    import signal, time, os
    # LaunchAgent stoppen damit er nicht sofort neu startet
    subprocess.run(["launchctl", "stop", LAUNCHD_LABEL], capture_output=True)
    # PID aus Fehlermeldung oder LOCK-Datei lesen
    m = re.search(r'pid=(\d+)', error_out)
    if m:
        try:
            os.kill(int(m.group(1)), signal.SIGKILL)
        except Exception:
            pass
    time.sleep(0.5)
    # LOCK-Datei löschen
    try:
        if os.path.exists(WACLI_LOCK):
            os.remove(WACLI_LOCK)
    except Exception:
        pass
    time.sleep(0.3)
    return True


def _resume_sync():
    """Sync-LaunchAgent wieder starten."""
    subprocess.run(["launchctl", "start", LAUNCHD_LABEL], capture_output=True)


def _run(*args, timeout: int = 20) -> tuple[bool, str]:
    """wacli ausführen — pausiert Sync-LaunchAgent falls Store gesperrt."""
    def _exec():
        try:
            result = subprocess.run(
                [WACLI, "--json", *args],
                capture_output=True, text=True, timeout=timeout
            )
            out = result.stdout.strip() or result.stderr.strip()
            return result.returncode == 0, out
        except FileNotFoundError:
            return False, f"wacli nicht gefunden unter {WACLI}"
        except subprocess.TimeoutExpired:
            return False, "wacli Timeout"
        except Exception as e:
            return False, str(e)

    ok, out = _exec()
    # Store gesperrt → Lock freigeben und nochmal versuchen
    if not ok and "store is locked" in out:
        _release_lock(out)
        try:
            ok, out = _exec()
        finally:
            _resume_sync()
    return ok, out


def _resolve_recipient(recipient: str) -> str:
    """Empfänger-JID auflösen — sucht Kontakt per Name/Nummer, gibt @lid-JID zurück."""
    # @lid JID — direkt verwenden
    if "@lid" in recipient:
        return recipient
    # @s.whatsapp.net → direkt verwenden (bereits vollständig)
    if "@s.whatsapp.net" in recipient:
        return recipient
    # +49... → + entfernen für Kontaktsuche
    search_term = recipient.lstrip("+")
    ok, out = _run("contacts", "search", search_term, "--limit", "3")
    if ok:
        parsed = _parse_response(out)
        if parsed and isinstance(parsed, list) and parsed:
            # @lid bevorzugen
            for contact in parsed:
                jid = contact.get("JID", "")
                if "@lid" in jid:
                    return jid
            # Fallback: ersten Treffer nehmen
            jid = parsed[0].get("JID", "")
            if jid:
                return jid
    # Letzter Fallback: direkt verwenden
    return search_term


def _parse_response(raw: str) -> list | dict | None:
    """wacli JSON-Response parsen — unterstützt {success, data} und direkte Arrays."""
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "data" in data:
            inner = data["data"]
            # messages list
            if isinstance(inner, dict) and "messages" in inner:
                return inner["messages"]
            # chats list
            if isinstance(inner, dict) and "chats" in inner:
                return inner["chats"]
            if isinstance(inner, list):
                return inner
            return inner
        if isinstance(data, list):
            return data
        return data
    except Exception:
        return None


def _format_messages(raw: str, limit: int = 10) -> str:
    """JSON-Nachrichten lesbar formatieren."""
    parsed = _parse_response(raw)
    if not parsed:
        return "Keine Nachrichten gefunden."
    msgs = parsed[:limit] if isinstance(parsed, list) else [parsed]
    lines = []
    for m in msgs:
        chat = m.get("ChatName") or m.get("SenderJID") or "?"
        # Telefonnummer kürzen
        if "@" in chat:
            chat = chat.split("@")[0]
        text = m.get("Text") or m.get("DisplayText") or m.get("Snippet") or "(kein Text)"
        ts = (m.get("Timestamp") or "")[:16].replace("T", " ")
        from_me = "→" if m.get("FromMe") else "←"
        lines.append(f"{from_me} **{chat}** [{ts}]: {text}")
    return "\n".join(lines) if lines else "Keine Nachrichten gefunden."


def _format_chats(raw: str) -> str:
    parsed = _parse_response(raw)
    if not parsed:
        return "Keine Chats gefunden."
    chats = parsed if isinstance(parsed, list) else []
    lines = []
    for c in chats[:20]:
        name = c.get("Name") or c.get("name") or c.get("JID", "?")
        jid = c.get("JID") or c.get("jid", "")
        if "@" in jid:
            jid = jid.split("@")[0]
        lines.append(f"- **{name}** `{jid}`")
    return "\n".join(lines) if lines else "Keine Chats gefunden."


class WhatsAppSkill(BaseSkill):
    id = "whatsapp"
    name = "WhatsApp"
    icon = "chat"
    description = (
        "Liest und sendet WhatsApp-Nachrichten via wacli. "
        "Kann Nachrichten lesen, suchen, senden und Chats auflisten. "
        "Erfordert einmalige Authentifizierung via 'wacli auth'."
    )
    triggers = []  # nur via LLM-Entscheidung [whatsapp]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        msg = message.lower()

        # ── Antworten (antworte auf X mit Y) ─────────────────────────────────
        reply_match = re.search(
            r'antworte?\s+(?:auf|an)\s+(.+?)\s+mit\s+["\']?(.+?)["\']?\s*$',
            message, re.IGNORECASE
        )
        if reply_match:
            recipient = _resolve_recipient(reply_match.group(1).strip().rstrip("?!.,"))
            text = reply_match.group(2).strip().strip('"\'')
            ok, out = _run("send", "text", "--to", recipient, "--message", text)
            if ok:
                return SkillResult(
                    text=f"✅ Antwort an **{reply_match.group(1).strip()}** gesendet: *{text}*",
                    skill_used=self.id
                )
            return SkillResult(text=f"⚠ Senden fehlgeschlagen: {out}", skill_used=self.id)

        # ── Senden ────────────────────────────────────────────────────────────
        send_match = re.search(
            r'schick(?:e|en)?\s+(?:an\s+)?(.+?)\s*[:\-]\s*(.+)',
            message, re.IGNORECASE
        ) or re.search(
            r'send\s+(?:to\s+)?(.+?)\s*[:\-]\s*(.+)',
            message, re.IGNORECASE
        ) or re.search(
            r'nachricht\s+an\s+(.+?)\s*[:\-]\s*(.+)',
            message, re.IGNORECASE
        )
        if send_match:
            recipient = _resolve_recipient(send_match.group(1).strip())
            text = send_match.group(2).strip()
            ok, out = _run("send", "text", "--to", recipient, "--message", text)
            if ok:
                return SkillResult(
                    text=f"✅ Nachricht an **{send_match.group(1).strip()}** gesendet.",
                    skill_used=self.id
                )
            return SkillResult(
                text=f"⚠ Senden fehlgeschlagen: {out}",
                skill_used=self.id
            )

        # ── Suche ─────────────────────────────────────────────────────────────
        search_match = re.search(
            r'such(?:e|en)?\s+(?:nach\s+)?["\']?(.+?)["\']?\s*(?:in\s+whatsapp)?$',
            message, re.IGNORECASE
        )
        if "such" in msg and search_match:
            query = search_match.group(1).strip()
            ok, out = _run("messages", "search", query, "--limit", "10")
            if ok:
                return SkillResult(
                    text=f"### WhatsApp Suche: '{query}'\n\n{_format_messages(out)}",
                    skill_used=self.id
                )
            return SkillResult(text=f"⚠ Suche fehlgeschlagen: {out}", skill_used=self.id)

        # ── Chats auflisten ───────────────────────────────────────────────────
        if any(w in msg for w in ["chats", "gespräche", "kontakte", "liste"]):
            ok, out = _run("chats", "list", "--limit", "20")
            if ok:
                return SkillResult(
                    text=f"### WhatsApp Chats\n\n{_format_chats(out)}",
                    skill_used=self.id
                )
            return SkillResult(text=f"⚠ Chats laden fehlgeschlagen: {out}", skill_used=self.id)

        # ── Nachrichten lesen (Standard) ──────────────────────────────────────
        limit_match = re.search(r'\b(\d+)\b', message)
        limit = str(min(int(limit_match.group(1)), 30)) if limit_match else "10"

        ok, out = _run("messages", "list", "--limit", limit)
        if ok:
            return SkillResult(
                text=f"### WhatsApp — Letzte {limit} Nachrichten\n\n{_format_messages(out, int(limit))}",
                skill_used=self.id
            )
        # Auth-Fehler erkennen
        if "not authenticated" in out.lower() or "no such file" in out.lower():
            return SkillResult(
                text=(
                    "⚠ WhatsApp nicht authentifiziert.\n\n"
                    "Bitte einmalig im Terminal ausführen:\n"
                    "```\n~/bin/wacli auth\n```\n"
                    "QR-Code scannen → danach funktioniert der Skill."
                ),
                skill_used=self.id
            )
        return SkillResult(text=f"⚠ Fehler: {out}", skill_used=self.id)
