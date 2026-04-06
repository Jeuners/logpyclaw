"""
mac_mail/skill.py — Apple Mail Steuerung via AppleScript.
Self-contained: nur stdlib + core.state (für _PENDING_MAIL_SORT).
"""
import os
import re
import subprocess
import json
import requests

from core.config import BASE_DIR
from core.state import _PENDING_MAIL_SORT


def _run_mac_mail(message: str, agent_id: str = "") -> str:
    """
    Steuert Apple Mail direkt via AppleScript.
    Kein externer Server nötig.
    """
    def _as(script: str) -> str:
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    def _get_messages(unread_only=False, limit=10, mailbox_name="INBOX") -> list:
        """Holt Mails aus INBOX aller Accounts oder aus einem benannten lokalen Ordner."""
        filt = "whose read status is false" if unread_only else ""
        script = f"""
tell application "Mail"
    set output to ""
    set counter to 0
    -- Zuerst Account-INBOXen durchsuchen
    repeat with acc in accounts
        try
            set mb to mailbox "{mailbox_name}" of acc
            set msgs to (messages of mb) {filt}
            repeat with m in msgs
                if counter >= {limit} then exit repeat
                set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "|" & ((date received of m) as string) & "|" & (read status of m) & "|" & (name of acc) & "\n"
                set counter to counter + 1
            end repeat
        end try
        if counter >= {limit} then exit repeat
    end repeat
    -- Falls kein Ergebnis: lokale Postfächer durchsuchen
    if counter = 0 then
        repeat with mb in mailboxes
            if (name of mb) is "{mailbox_name}" then
                set msgs to (messages of mb) {filt}
                repeat with m in msgs
                    if counter >= {limit} then exit repeat
                    set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "|" & ((date received of m) as string) & "|" & (read status of m) & "|Lokal\n"
                    set counter to counter + 1
                end repeat
            end if
        end repeat
    end if
    return output
end tell"""
        raw = _as(script)
        result = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            p = line.split("|")
            if len(p) >= 5:
                result.append({
                    "id": p[0], "subject": p[1], "sender": p[2],
                    "date": p[3], "read": p[4] == "true",
                    "account": p[5] if len(p) > 5 else ""
                })
        return result

    # Kategorisierungsregeln → lokale Ordner
    SORT_RULES = [
        ("Finanzen", [
            "rechnung", "invoice", "zahlung", "payment", "überweisung", "sepa",
            "kontoauszug", "steuer", "gebühr", "mahnung", "abbuchung", "paypal",
            "stripe", "lastschrift", "bankverbindung", "kreditkarte", "kosten",
            "betrag", "eur", "€", "quittung", "beleg", "abrechnung", "payslip",
        ]),
        ("Termine", [
            "termin", "kalender", "meeting", "einladung", "reminder", "erinnerung",
            "appointment", "schedule", "calendar", "zoom", "teams", "webinar",
            "konferenz", "veranstaltung", "event", "rsvp", "reservierung",
        ]),
        ("Sicherheit", [
            "passwort", "password", "sicherheit", "security", "verification",
            "bestätigung", "2fa", "login", "zugang", "alert", "warning",
            "authentifizierung", "code", "pin", "verify", "confirm", "access",
        ]),
        ("News", [
            "newsletter", "digest", "weekly", "monthly", "update", "ankündigung",
            "announcement", "roundup", "summary", "report", "tagesschau",
            "breaking", "headlines", "edition", "ausgabe",
        ]),
        ("Werbung", [
            "angebot", "sale", "rabatt", "discount", "promotion", "marketing",
            "werbung", "deal", "offer", "unsubscribe", "abmelden", "exklusiv",
            "limited", "nur heute", "jetzt kaufen", "% off", "promo",
            "newsletter abbestellen", "commercial", "advertis",
        ]),
    ]

    def _categorize(subject: str, sender: str) -> str:
        text = (subject + " " + sender).lower()
        for folder, keywords in SORT_RULES:
            if any(kw in text for kw in keywords):
                return folder
        return "Wichtig"

    try:
        _agent_sort_pending = _PENDING_MAIL_SORT.get(agent_id, False)
        msg = message.lower()

        def _fetch_inbox_batch(acc_name: str, batch_size: int = 50, offset: int = 1) -> list:
            end_idx = offset + batch_size - 1
            raw = _as(f"""tell application "Mail"
    set output to ""
    set mb to mailbox "INBOX" of account "{acc_name}"
    set total to count of messages of mb
    set endIdx to {end_idx}
    if endIdx > total then set endIdx to total
    if {offset} > total then return ""
    repeat with i from {offset} to endIdx
        set m to message i of mb
        set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "\n"
    end repeat
    return output
end tell""")
            result = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                p = line.split("|")
                if len(p) >= 3:
                    result.append({"id": p[0], "subject": p[1], "sender": p[2], "account": acc_name})
            return result

        def _move_batch(mails: list) -> tuple:
            moved = {}
            errors = 0
            for mail in mails:
                folder = _categorize(mail["subject"], mail["sender"])
                try:
                    _as(f"""tell application "Mail"
    set mb to mailbox "INBOX" of account "{mail['account']}"
    set theMsg to first message of mb whose message id is "{mail['id']}"
    move theMsg to mailbox "{folder}"
end tell""")
                    moved[folder] = moved.get(folder, 0) + 1
                except Exception:
                    errors += 1
            return moved, errors

        # Aufräumen / Einsortieren → Vorschau
        if re.search(r"\b(aufräum|sortier|einsortier|organis|kategorisier)\w*\b", msg, re.I):
            counts_raw = _as("""tell application "Mail"
    set output to ""
    repeat with acc in accounts
        try
            set mb to mailbox "INBOX" of acc
            set output to output & (name of acc) & "|" & (count of messages of mb) & "\n"
        end try
    end repeat
    return output
end tell""")
            accounts_with_mail = []
            total_count = 0
            for line in counts_raw.splitlines():
                if not line.strip():
                    continue
                p = line.split("|")
                if len(p) == 2 and p[1].isdigit() and int(p[1]) > 0:
                    accounts_with_mail.append((p[0], int(p[1])))
                    total_count += int(p[1])

            if total_count == 0:
                return "📬 Posteingang ist bereits leer."

            preview_mails = []
            for acc_name, count in accounts_with_mail:
                preview_mails += _fetch_inbox_batch(acc_name, batch_size=min(50, count))

            plan = {}
            for mail in preview_mails:
                folder = _categorize(mail["subject"], mail["sender"])
                plan.setdefault(folder, []).append(mail)

            lines = [f"📬 **{total_count} Mails** in {len(accounts_with_mail)} Account(s) — Sortierplan (Vorschau):\n"]
            for acc_name, count in accounts_with_mail:
                lines.append(f"  • {acc_name}: {count} Mails")
            lines.append("")
            for folder, items in sorted(plan.items()):
                lines.append(f"**📁 {folder}** (~{len(items)} Beispiele)")
                for item in items[:2]:
                    lines.append(f"  • {item['subject'][:55]}")
            lines.append(f"\n✋ Soll ich alle **{total_count} Mails** in Batches verschieben?")
            lines.append("Antworte mit **'ja'** oder **'go'**.")
            _PENDING_MAIL_SORT[agent_id] = True
            return "\n".join(lines)

        # Bestätigung → tatsächlich verschieben
        _explicit_confirm = bool(re.search(r"\bja[,.]?\s*(verschieb|sortier|mach|go|los|ok|weiter|bestätig)\w*\b", msg, re.I))
        _short_confirm = bool(_agent_sort_pending and re.search(
            r"^\s*[0-9]*\s*(go|los|ja|ok|yes|weiter|mach\s*mal?|start|run|tu\s*es?|bestätig\w*)\s*$",
            msg.strip(), re.I
        ))
        if _explicit_confirm or _short_confirm:
            counts_raw = _as("""tell application "Mail"
    set output to ""
    repeat with acc in accounts
        try
            set mb to mailbox "INBOX" of acc
            set output to output & (name of acc) & "|" & (count of messages of mb) & "\n"
        end try
    end repeat
    return output
end tell""")
            accounts_with_mail = []
            for line in counts_raw.splitlines():
                if not line.strip():
                    continue
                p = line.split("|")
                if len(p) == 2 and p[1].isdigit() and int(p[1]) > 0:
                    accounts_with_mail.append((p[0], int(p[1])))

            if not accounts_with_mail:
                return "📬 Keine Mails zum Verschieben."

            total_moved = {}
            total_errors = 0
            BATCH = 50
            for acc_name, total in accounts_with_mail:
                remaining = total
                while remaining > 0:
                    batch = _fetch_inbox_batch(acc_name, batch_size=min(BATCH, remaining))
                    if not batch:
                        break
                    moved, errors = _move_batch(batch)
                    for k, v in moved.items():
                        total_moved[k] = total_moved.get(k, 0) + v
                    total_errors += errors
                    remaining -= len(batch)

            _PENDING_MAIL_SORT[agent_id] = False
            lines = [f"✅ **{sum(total_moved.values())} Mails verschoben:**\n"]
            for folder, count in sorted(total_moved.items()):
                lines.append(f"  📁 {folder}: {count}")
            if total_errors:
                lines.append(f"\n⚠️ {total_errors} Mail(s) konnten nicht verschoben werden.")
            return "\n".join(lines)

        # Ordner anlegen
        if re.search(r"ordner\s+anlegen|create.*folder|new.*mailbox|erstell.*ordner", msg, re.I):
            m = re.search(r'(?:ordner|folder|mailbox)[:\s]+["\']?([^"\'.,\n]{2,40})["\']?', message, re.I)
            name = m.group(1).strip() if m else "Neu"
            _as(f'tell application "Mail" to make new mailbox with properties {{name:"{name}"}}')
            return f"📬 Ordner **{name}** angelegt ✅"

        # Nachricht verschieben
        if re.search(r"verschieb|move.*mail|archiviere?", msg, re.I):
            id_m = re.search(r'id[:\s]+([^\s,]+)', message, re.I)
            to_m = re.search(r'(?:nach|in|to|into)[:\s]+["\']?([^"\'.,\n]{2,40})["\']?', message, re.I)
            if id_m and to_m:
                mid = id_m.group(1)
                target = to_m.group(1).strip()
                _as(f"""tell application "Mail"
    set theMsg to first message of mailbox "INBOX" whose message id is "{mid}"
    move theMsg to mailbox "{target}"
end tell""")
                return f"📬 Nachricht verschoben nach **{target}** ✅"
            return "📬 Bitte angeben: welche Mail-ID und in welchen Ordner?"

        # Anhänge
        if re.search(r"anhang|attachment", msg, re.I):
            id_m = re.search(r'id[:\s]+([^\s,]+)', message, re.I)
            if id_m:
                mid = id_m.group(1)
                raw = _as(f"""tell application "Mail"
    set theMsg to missing value
    repeat with acc in accounts
        try
            set theMsg to first message of mailbox "INBOX" of acc whose message id is "{mid}"
            exit repeat
        end try
    end repeat
    if theMsg is missing value then return "not found"
    set output to ""
    repeat with att in mail attachments of theMsg
        set output to output & (name of att) & "|" & (file size of att) & "\n"
    end repeat
    return output
end tell""")
                atts = [l.split("|") for l in raw.splitlines() if l.strip()]
                if not atts:
                    return "📬 Keine Anhänge in dieser Nachricht."
                lines = [f"📎 **{len(atts)} Anhang/Anhänge:**\n"]
                for a in atts:
                    kb = int(a[1]) // 1024 if len(a) > 1 and a[1].isdigit() else 0
                    lines.append(f"• {a[0]} ({kb} KB)")
                return "\n".join(lines)
            return "📬 Bitte Nachrichten-ID angeben."

        # Suche
        if re.search(r"suche?|such|search|find", msg, re.I):
            q_m = re.search(r'(?:suche?|nach|search|find|von|from|betreff|subject)[:\s]+["\']?([^"\'.,\n]{2,60})["\']?', message, re.I)
            query = q_m.group(1).strip() if q_m else message[:40]
            raw = _as(f"""tell application "Mail"
    set output to ""
    set counter to 0
    set q to "{query}"
    repeat with mb in mailboxes
        try
            set found to (messages of mb) whose subject contains q
            repeat with m in found
                if counter >= 20 then exit repeat
                set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "|" & ((date received of m) as string) & "|" & (name of mb) & "\n"
                set counter to counter + 1
            end repeat
        end try
        if counter >= 20 then exit repeat
    end repeat
    if counter < 20 then
        repeat with acc in accounts
            try
                set mb to mailbox "INBOX" of acc
                set found to (messages of mb) whose subject contains q
                repeat with m in found
                    if counter >= 20 then exit repeat
                    set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "|" & ((date received of m) as string) & "|INBOX/" & (name of acc) & "\n"
                    set counter to counter + 1
                end repeat
            end try
            if counter >= 20 then exit repeat
        end repeat
    end if
    return output
end tell""")
            results = [l.split("|") for l in raw.splitlines() if l.strip()]
            if not results:
                return f"📬 Keine Mails gefunden für: **{query}**"
            lines = [f"📬 **{len(results)} Treffer** für '{query}':\n"]
            for r in results[:15]:
                folder = r[4][:30] if len(r) > 4 else ""
                lines.append(f"• **{r[1][:55]}**  \n  von {r[2]} — {r[3][:16]}  `{folder}`")
            return "\n".join(lines)

        # Ungelesene
        if re.search(r"ungelesen|neu(e|en)?\s+mail|new\s+mail|unread|posteingang", msg, re.I):
            msgs = _get_messages(unread_only=True, limit=10)
            if not msgs:
                return "📬 Keine ungelesenen Mails."
            lines = [f"📬 **{len(msgs)} ungelesene Mail(s):**\n"]
            for m2 in msgs:
                lines.append(f"📧 **{m2['subject']}**  \n  von {m2['sender']} — {m2['date'][:16]}")
            return "\n".join(lines)

        # Ordner auflisten
        if re.search(r"postfächer|ordner|mailbox|folder", msg, re.I):
            raw = _as("""tell application "Mail"
    set output to ""
    set output to output & "=== Lokal ===" & "\n"
    repeat with mb in mailboxes
        try
            set _ to account of mb
        on error
            set output to output & "  " & (name of mb) & "\n"
        end try
    end repeat
    repeat with acc in accounts
        set aname to name of acc
        set output to output & "=== " & aname & " ===" & "\n"
        repeat with mb in mailboxes of acc
            set output to output & "  " & (name of mb) & "\n"
        end repeat
    end repeat
    return output
end tell""")
            lines = ["📬 **Verfügbare Postfächer:**\n"]
            for line in raw.splitlines():
                if line.startswith("==="):
                    lines.append(f"\n**{line.strip('= ')}**")
                elif line.strip():
                    lines.append(f"  • {line.strip()}")
            return "\n".join(lines)

        # Älteste N Mails → MARTIN-Unterordner
        if re.search(r"\b(älteste?|oldest)\b.{0,30}\b(mail|mails?|nachricht)\b|\b(verschieb|move)\b.{0,40}\bmartin\b", msg, re.I):
            n_m = re.search(r"\b(\d+)\b", msg)
            n = int(n_m.group(1)) if n_m else 20
            raw = _as(f"""tell application "Mail"
    set output to ""
    set counter to 0
    repeat with acc in accounts
        try
            set mb to mailbox "INBOX" of acc
            set total to count of messages of mb
            if total > 0 then
                set startIdx to total - {n} + 1
                if startIdx < 1 then set startIdx to 1
                repeat with i from startIdx to total
                    set m to message i of mb
                    set output to output & (message id of m) & "|" & (subject of m) & "|" & (sender of m) & "|" & (name of acc) & "\n"
                    set counter to counter + 1
                    if counter >= {n} then exit repeat
                end repeat
            end if
        end try
        if counter >= {n} then exit repeat
    end repeat
    return output
end tell""")
            mails = []
            for line in raw.splitlines():
                if not line.strip():
                    continue
                p = line.split("|")
                if len(p) >= 4:
                    mails.append({"id": p[0], "subject": p[1], "sender": p[2], "account": p[3]})

            if not mails:
                return "📬 Keine Mails zum Verschieben gefunden."

            moved = {}
            errors = 0
            for mail in mails:
                folder = _categorize(mail["subject"], mail["sender"])
                target = f"MARTIN/{folder}"
                try:
                    _as(f"""tell application "Mail"
    set mb to mailbox "INBOX" of account "{mail['account']}"
    set theMsg to first message of mb whose message id is "{mail['id']}"
    move theMsg to mailbox "{target}"
end tell""")
                    moved[target] = moved.get(target, 0) + 1
                except Exception:
                    errors += 1

            lines = [f"✅ **{sum(moved.values())} älteste Mails → MARTIN-Unterordner:**\n"]
            for folder, count in sorted(moved.items()):
                lines.append(f"  📁 {folder}: {count}")
            if errors:
                lines.append(f"\n⚠️ {errors} konnten nicht verschoben werden.")
            return "\n".join(lines)

        # Triage: neue Mails per LLM bewerten
        if re.search(r"\b(triage|prüf|check|bewerт|analyse|wichtig|dringend|priorit)\w*\b", msg, re.I):
            try:
                with open(os.path.join(BASE_DIR, "providers.json"), encoding="utf-8") as _f:
                    _providers = json.load(_f)
                ollama_url = _providers.get("ollama", {}).get("url", "http://localhost:11434").rstrip("/")
            except Exception:
                ollama_url = "http://localhost:11434"

            unread_raw = _as("""tell application "Mail"
    set output to ""
    set counter to 0
    repeat with acc in accounts
        try
            set mb to mailbox "INBOX" of acc
            set msgs to (messages of mb) whose read status is false
            repeat with m in msgs
                if counter >= 15 then exit repeat
                set output to output & (message id of m) & "|||" & (subject of m) & "|||" & (sender of m) & "|||" & ((date received of m) as string) & "|||" & (name of acc) & "\n"
                set counter to counter + 1
            end repeat
        end try
        if counter >= 15 then exit repeat
    end repeat
    return output
end tell""")
            unread = []
            for line in unread_raw.splitlines():
                if not line.strip():
                    continue
                p = line.split("|||")
                if len(p) >= 4:
                    unread.append({"id": p[0], "subject": p[1], "sender": p[2], "date": p[3], "account": p[4] if len(p) > 4 else ""})

            if not unread:
                return "📬 Keine ungelesenen Mails — Posteingang ist sauber! ✅"

            for mail in unread:
                try:
                    body_raw = _as(f"""tell application "Mail"
    set mb to mailbox "INBOX" of account "{mail['account']}"
    set m to first message of mb whose message id is "{mail['id']}"
    set b to content of m
    if length of b > 400 then set b to text 1 thru 400 of b
    return b
end tell""")
                    mail["snippet"] = body_raw.strip().replace("\n", " ")
                except Exception:
                    mail["snippet"] = ""

            mail_block = ""
            for i, m in enumerate(unread, 1):
                mail_block += f"\n---\nMail {i}:\nVon: {m['sender']}\nBetreff: {m['subject']}\nDatum: {m['date'][:16]}\nInhalt: {m['snippet'][:300]}\n"

            llm_prompt = f"""Du bist ein E-Mail-Assistent. Bewerte folgende ungelesene Mails und gib für jede Mail aus:
- Priorität: 🔴 Hoch / 🟡 Mittel / 🟢 Niedrig
- 1-Satz-Zusammenfassung was die Mail will
- Empfohlene Aktion: Antworten / Lesen / Archivieren / Ignorieren / Löschen

Antworte kompakt, eine Mail pro Zeile im Format:
[Mail N] PRIORITÄT | ZUSAMMENFASSUNG | AKTION

Mails:{mail_block}"""

            try:
                resp = requests.post(
                    f"{ollama_url}/api/generate",
                    json={"model": "gemma3:latest", "prompt": llm_prompt, "stream": False},
                    timeout=60
                )
                resp.raise_for_status()
                llm_result = resp.json().get("response", "").strip()
            except Exception as e:
                llm_result = f"(LLM nicht erreichbar: {e})"

            lines = [f"📬 **Triage — {len(unread)} ungelesene Mail(s):**\n", llm_result, ""]
            lines.append("─" * 40)
            lines.append("**Mails:**")
            for i, m in enumerate(unread, 1):
                lines.append(f"  {i}. **{m['subject'][:55]}**  \n     von {m['sender']} — {m['date'][:16]}")
            return "\n".join(lines)

        # Standard: Inbox zeigen
        msgs = _get_messages(limit=10)
        if not msgs:
            return "📬 Posteingang ist leer."
        lines = [f"📬 **Posteingang** ({len(msgs)} Nachrichten):\n"]
        for m2 in msgs:
            icon = "📧" if not m2["read"] else "✉️"
            lines.append(f"{icon} **{m2['subject']}**  \n  von {m2['sender']} — {m2['date'][:16]}")
        return "\n".join(lines)

    except Exception as e:
        return f"❌ Mac Mail Fehler: {str(e)[:300]}"
