"""
Mac Mail MCP Server
Gibt Agenten Zugriff auf Apple Mail via AppleScript.
10 Tools: Lesen, Anhänge, Verschieben, Ordner anlegen.

Starten: venv/bin/python3 mac_mail_mcp.py
Port:    5051 (HTTP/SSE)
"""

import subprocess
import json
import base64
import os
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Mac Mail", port=5051)


# ── AppleScript helper ────────────────────────────────────────────────────────

def _run_applescript(script: str) -> str:
    """Run an AppleScript and return stdout. Raises on error."""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def _run_js(script: str) -> str:
    """Run a JXA (JavaScript for Automation) script."""
    result = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"JXA error: {result.stderr.strip()}")
    return result.stdout.strip()


# ── Tool 1: Accounts auflisten ────────────────────────────────────────────────

@mcp.tool()
def mail_list_accounts() -> str:
    """Listet alle konfigurierten Mail-Accounts auf."""
    script = """
tell application "Mail"
    set output to ""
    repeat with acc in accounts
        set output to output & name of acc & "\n"
    end repeat
    return output
end tell
"""
    result = _run_applescript(script)
    accounts = [a for a in result.splitlines() if a.strip()]
    return json.dumps({"accounts": accounts})


# ── Tool 2: Postfächer auflisten ─────────────────────────────────────────────

@mcp.tool()
def mail_list_mailboxes(account_name: str = "") -> str:
    """
    Listet alle Postfächer (Ordner) auf.
    account_name: optional — leer = alle Accounts
    """
    if account_name:
        script = f"""
tell application "Mail"
    set output to ""
    set acc to account "{account_name}"
    repeat with mb in mailboxes of acc
        set output to output & name of mb & "\n"
    end repeat
    return output
end tell
"""
    else:
        script = """
tell application "Mail"
    set output to ""
    repeat with acc in accounts
        repeat with mb in mailboxes of acc
            set output to output & (name of acc) & "/" & (name of mb) & "\n"
        end repeat
    end repeat
    return output
end tell
"""
    result = _run_applescript(script)
    mailboxes = [m for m in result.splitlines() if m.strip()]
    return json.dumps({"mailboxes": mailboxes})


# ── Tool 3: Nachrichten in einem Postfach abrufen ────────────────────────────

@mcp.tool()
def mail_get_messages(mailbox: str, account_name: str = "", limit: int = 20, unread_only: bool = False) -> str:
    """
    Holt Nachrichten aus einem Postfach.
    mailbox: Name des Postfachs z.B. "INBOX"
    account_name: optional
    limit: max. Anzahl (default 20)
    unread_only: nur ungelesene
    """
    unread_filter = "whose read status is false" if unread_only else ""
    if account_name:
        script = f"""
tell application "Mail"
    set mb to mailbox "{mailbox}" of account "{account_name}"
    set msgs to messages {unread_filter} of mb
    set output to ""
    set counter to 0
    repeat with m in msgs
        if counter >= {limit} then exit repeat
        set mid to message id of m
        set msubj to subject of m
        set msender to sender of m
        set mdate to date received of m as string
        set mread to read status of m
        set output to output & mid & "|" & msubj & "|" & msender & "|" & mdate & "|" & mread & "\n"
        set counter to counter + 1
    end repeat
    return output
end tell
"""
    else:
        script = f"""
tell application "Mail"
    set mb to mailbox "{mailbox}"
    set msgs to messages {unread_filter} of mb
    set output to ""
    set counter to 0
    repeat with m in msgs
        if counter >= {limit} then exit repeat
        set mid to message id of m
        set msubj to subject of m
        set msender to sender of m
        set mdate to date received of m as string
        set mread to read status of m
        set output to output & mid & "|" & msubj & "|" & msender & "|" & mdate & "|" & mread & "\n"
        set counter to counter + 1
    end repeat
    return output
end tell
"""
    result = _run_applescript(script)
    messages = []
    for line in result.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 5:
            messages.append({
                "id": parts[0],
                "subject": parts[1],
                "sender": parts[2],
                "date": parts[3],
                "read": parts[4] == "true",
            })
    return json.dumps({"messages": messages, "count": len(messages)})


# ── Tool 4: Nachricht vollständig lesen ───────────────────────────────────────

@mcp.tool()
def mail_read_message(message_id: str, mailbox: str = "INBOX", account_name: str = "") -> str:
    """
    Liest den vollständigen Inhalt einer Nachricht.
    message_id: die ID aus mail_get_messages
    """
    if account_name:
        scope = f'mailbox "{mailbox}" of account "{account_name}"'
    else:
        scope = f'mailbox "{mailbox}"'

    script = f"""
tell application "Mail"
    set mb to {scope}
    set theMsg to first message of mb whose message id is "{message_id}"
    set msubj to subject of theMsg
    set msender to sender of theMsg
    set mdate to date received of theMsg as string
    set mbody to content of theMsg
    set mread to read status of theMsg
    -- mark as read
    set read status of theMsg to true
    return msubj & "|||" & msender & "|||" & mdate & "|||" & mread & "|||" & mbody
end tell
"""
    result = _run_applescript(script)
    parts = result.split("|||", 4)
    if len(parts) < 5:
        return json.dumps({"error": "Nachricht nicht gefunden", "raw": result})
    return json.dumps({
        "subject": parts[0],
        "sender": parts[1],
        "date": parts[2],
        "was_read": parts[3] == "true",
        "body": parts[4][:8000],  # max 8k chars
    })


# ── Tool 5: Anhänge einer Nachricht auflisten ─────────────────────────────────

@mcp.tool()
def mail_list_attachments(message_id: str, mailbox: str = "INBOX", account_name: str = "") -> str:
    """
    Listet alle Anhänge einer Nachricht auf (Name, Größe).
    """
    if account_name:
        scope = f'mailbox "{mailbox}" of account "{account_name}"'
    else:
        scope = f'mailbox "{mailbox}"'

    script = f"""
tell application "Mail"
    set mb to {scope}
    set theMsg to first message of mb whose message id is "{message_id}"
    set output to ""
    set attList to mail attachments of theMsg
    repeat with att in attList
        set aname to name of att
        set asize to file size of att
        set output to output & aname & "|" & asize & "\n"
    end repeat
    return output
end tell
"""
    result = _run_applescript(script)
    attachments = []
    for line in result.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        attachments.append({
            "name": parts[0] if parts else line,
            "size_bytes": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0,
        })
    return json.dumps({"attachments": attachments, "count": len(attachments)})


# ── Tool 6: Anhang herunterladen ──────────────────────────────────────────────

@mcp.tool()
def mail_get_attachment(message_id: str, attachment_name: str, mailbox: str = "INBOX", account_name: str = "") -> str:
    """
    Lädt einen Anhang herunter und gibt ihn als base64 zurück.
    Nur für Anhänge < 5 MB.
    """
    save_path = f"/tmp/agentclaw_att_{attachment_name}"

    if account_name:
        scope = f'mailbox "{mailbox}" of account "{account_name}"'
    else:
        scope = f'mailbox "{mailbox}"'

    script = f"""
tell application "Mail"
    set mb to {scope}
    set theMsg to first message of mb whose message id is "{message_id}"
    set attList to mail attachments of theMsg
    repeat with att in attList
        if name of att is "{attachment_name}" then
            save att in POSIX file "{save_path}"
            return "ok"
        end if
    end repeat
    return "not found"
end tell
"""
    result = _run_applescript(script)
    if result != "ok":
        return json.dumps({"error": f"Anhang '{attachment_name}' nicht gefunden"})

    if not os.path.exists(save_path):
        return json.dumps({"error": "Speichern fehlgeschlagen"})

    size = os.path.getsize(save_path)
    if size > 5 * 1024 * 1024:
        os.remove(save_path)
        return json.dumps({"error": f"Anhang zu groß ({size // 1024} KB) — max 5 MB"})

    with open(save_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    os.remove(save_path)

    ext = attachment_name.rsplit(".", 1)[-1].lower() if "." in attachment_name else "bin"
    mime_map = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "txt": "text/plain", "csv": "text/csv",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    mime = mime_map.get(ext, "application/octet-stream")

    return json.dumps({
        "name": attachment_name,
        "size_bytes": size,
        "mime": mime,
        "data_base64": b64,
    })


# ── Tool 7: Nachrichten suchen ────────────────────────────────────────────────

@mcp.tool()
def mail_search(query: str, mailbox: str = "INBOX", account_name: str = "", limit: int = 10) -> str:
    """
    Sucht Nachrichten nach Betreff oder Absender.
    query: Suchbegriff
    """
    if account_name:
        scope = f'mailbox "{mailbox}" of account "{account_name}"'
    else:
        scope = f'mailbox "{mailbox}"'

    script = f"""
tell application "Mail"
    set mb to {scope}
    set output to ""
    set counter to 0
    repeat with m in (messages of mb)
        if counter >= {limit} then exit repeat
        set msubj to subject of m
        set msender to sender of m
        if msubj contains "{query}" or msender contains "{query}" then
            set mid to message id of m
            set mdate to date received of m as string
            set output to output & mid & "|" & msubj & "|" & msender & "|" & mdate & "\n"
            set counter to counter + 1
        end if
    end repeat
    return output
end tell
"""
    result = _run_applescript(script)
    messages = []
    for line in result.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            messages.append({
                "id": parts[0],
                "subject": parts[1],
                "sender": parts[2],
                "date": parts[3],
            })
    return json.dumps({"results": messages, "count": len(messages), "query": query})


# ── Tool 8: Nachricht verschieben ─────────────────────────────────────────────

@mcp.tool()
def mail_move_message(message_id: str, from_mailbox: str, to_mailbox: str, account_name: str = "") -> str:
    """
    Verschiebt eine Nachricht in ein anderes Postfach.
    from_mailbox: Quell-Ordner z.B. "INBOX"
    to_mailbox:   Ziel-Ordner  z.B. "Archiv"
    """
    if account_name:
        from_scope = f'mailbox "{from_mailbox}" of account "{account_name}"'
        to_scope = f'mailbox "{to_mailbox}" of account "{account_name}"'
    else:
        from_scope = f'mailbox "{from_mailbox}"'
        to_scope = f'mailbox "{to_mailbox}"'

    script = f"""
tell application "Mail"
    set srcBox to {from_scope}
    set dstBox to {to_scope}
    set theMsg to first message of srcBox whose message id is "{message_id}"
    move theMsg to dstBox
    return "ok"
end tell
"""
    result = _run_applescript(script)
    if "ok" in result:
        return json.dumps({"success": True, "moved_to": to_mailbox})
    return json.dumps({"success": False, "error": result})


# ── Tool 9: Ordner anlegen ────────────────────────────────────────────────────

@mcp.tool()
def mail_create_mailbox(mailbox_name: str, account_name: str = "") -> str:
    """
    Legt einen neuen Ordner (Postfach) an.
    mailbox_name: Name des neuen Ordners
    account_name: optional — welcher Account
    """
    if account_name:
        script = f"""
tell application "Mail"
    make new mailbox with properties {{name:"{mailbox_name}"}} at account "{account_name}"
    return "ok"
end tell
"""
    else:
        script = f"""
tell application "Mail"
    make new mailbox with properties {{name:"{mailbox_name}"}}
    return "ok"
end tell
"""
    result = _run_applescript(script)
    if "ok" in result or mailbox_name in result:
        return json.dumps({"success": True, "created": mailbox_name})
    return json.dumps({"success": False, "error": result})


# ── Tool 10: Nachricht als gelesen/ungelesen/markiert setzen ─────────────────

@mcp.tool()
def mail_mark_message(message_id: str, mailbox: str = "INBOX", account_name: str = "",
                      read: bool = None, flagged: bool = None) -> str:
    """
    Setzt den Status einer Nachricht.
    read:    True = gelesen, False = ungelesen
    flagged: True = markiert (Fähnchen), False = Markierung entfernen
    """
    if account_name:
        scope = f'mailbox "{mailbox}" of account "{account_name}"'
    else:
        scope = f'mailbox "{mailbox}"'

    changes = []
    if read is not None:
        changes.append(f"set read status of theMsg to {'true' if read else 'false'}")
    if flagged is not None:
        changes.append(f"set flagged status of theMsg to {'true' if flagged else 'false'}")

    if not changes:
        return json.dumps({"error": "Kein Status angegeben (read oder flagged)"})

    changes_str = "\n        ".join(changes)
    script = f"""
tell application "Mail"
    set mb to {scope}
    set theMsg to first message of mb whose message id is "{message_id}"
    {changes_str}
    return "ok"
end tell
"""
    result = _run_applescript(script)
    return json.dumps({"success": "ok" in result, "message_id": message_id})


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("[Mac Mail MCP] Server startet auf http://localhost:5051", flush=True)
    print("[Mac Mail MCP] Tools: list_accounts, list_mailboxes, get_messages,", flush=True)
    print("               read_message, list_attachments, get_attachment,", flush=True)
    print("               search, move_message, create_mailbox, mark_message", flush=True)
    mcp.run(transport="sse")
