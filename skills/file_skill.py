"""
skills/file_skill.py — Datei-Lesen/Schreiben im Downloads-Ordner
Agenten können Dateien im ~/Downloads/AgentClaw Ordner lesen, schreiben und auflisten.
"""
import os
import re
from datetime import datetime

DOWNLOADS_DIR = os.path.expanduser("~/Downloads/AgentClaw")

FILE_TRIGGERS = re.compile(
    r"speichere?\s+(?:als?|in|die|den|das)\s+\S+|"
    r"schreib\w*\s+(?:in\s+)?datei|"
    r"als?\s+datei\s+speichern|"
    r"save\s+(?:as\s+)?\S+\.(?:md|txt|json|csv)|"
    r"write\s+to\s+file|"
    r"liste\s+(?:alle?\s+)?(?:dateien|files)|"
    r"zeig\w*\s+(?:alle?\s+)?(?:dateien|files)|"
    r"lese?\s+datei\s+\S+|"
    r"öffne?\s+datei\s+\S+",
    re.IGNORECASE,
)


def _get_downloads_dir() -> str:
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    return DOWNLOADS_DIR


def list_downloads() -> str:
    """Listet alle Dateien im AgentClaw Downloads-Ordner."""
    dl_dir = _get_downloads_dir()
    try:
        files = []
        for f in sorted(os.listdir(dl_dir), key=lambda x: os.path.getmtime(os.path.join(dl_dir, x)), reverse=True):
            fp = os.path.join(dl_dir, f)
            size_mb = os.path.getsize(fp) / 1024 / 1024
            mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
            files.append(f"- `{f}` ({size_mb:.1f} MB, {mtime})")
        if not files:
            return f"📂 Downloads-Ordner ist leer: `{dl_dir}`"
        return f"📂 **Dateien in `{dl_dir}`:**\n\n" + "\n".join(files[:50])
    except Exception as e:
        return f"❌ Fehler beim Lesen des Ordners: {e}"


def read_file(filename: str) -> str:
    """Liest eine Datei aus dem Downloads-Ordner."""
    dl_dir = _get_downloads_dir()
    # Sicherheitsprüfung: nur innerhalb des Downloads-Ordners
    filepath = os.path.join(dl_dir, os.path.basename(filename))
    if not os.path.exists(filepath):
        return f"❌ Datei nicht gefunden: `{filepath}`"
    try:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
        if size_mb > 10:
            return f"❌ Datei zu groß zum Lesen ({size_mb:.1f} MB). Max: 10 MB"
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"📄 **{filename}** ({size_mb:.2f} MB):\n\n```\n{content[:8000]}\n```"
    except Exception as e:
        return f"❌ Fehler beim Lesen: {e}"


def write_file(filename: str, content: str) -> str:
    """Schreibt Inhalt in eine Datei im Downloads-Ordner."""
    dl_dir = _get_downloads_dir()
    # Sicherheitsprüfung: nur innerhalb des Downloads-Ordners, kein Path-Traversal
    safe_name = os.path.basename(filename)
    if not safe_name:
        return "❌ Ungültiger Dateiname"
    filepath = os.path.join(dl_dir, safe_name)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        size_kb = os.path.getsize(filepath) / 1024
        return f"💾 **Gespeichert:** `{filepath}` ({size_kb:.1f} KB)"
    except Exception as e:
        return f"❌ Fehler beim Schreiben: {e}"


def run_file_access(message: str, content_to_save: str = None) -> str:
    """Hauptfunktion: Erkennt und führt Dateioperationen aus."""
    msg_lower = message.lower()

    # Liste anzeigen
    if re.search(r"liste|liste\s+dateien|zeig\w*\s+dateien|list\s+files", msg_lower):
        return list_downloads()

    # Datei lesen
    read_m = re.search(r"(?:lese?|öffne?|read|open)\s+datei\s+(\S+)", message, re.IGNORECASE)
    if read_m:
        return read_file(read_m.group(1))

    # Datei schreiben / speichern
    save_m = re.search(
        r"(?:speichere?|schreib\w*|save|write)\s+.*?(\w[\w.\-]+\.(?:md|txt|json|csv|log|html))",
        message, re.IGNORECASE,
    )
    if save_m and content_to_save:
        filename = save_m.group(1)
        return write_file(filename, content_to_save)

    if save_m and not content_to_save:
        return f"❓ Kein Inhalt zum Speichern gefunden. Zieldatei wäre: `{save_m.group(1)}`"

    return "❓ Keine erkannte Dateioperation. Beispiele:\n- `Liste alle Dateien`\n- `Speichere als result.md`\n- `Lese Datei mein_text.txt`"


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult


class FileAccessSkill(BaseSkill):
    id = "file_access"
    name = "File Access"
    icon = "folder_open"
    description = "Reads and writes files in the downloads directory."
    triggers = [
        r"\b(datei|file|lese|lies|öffne|open|schreibe|write|speichere|save)\b.{0,30}\b(datei|file|txt|pdf|csv|json)\b",
        r"\b(list|zeige|show)\b.{0,20}\b(dateien|files|downloads)\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        content_to_save = context.get("content_to_save")
        try:
            result = run_file_access(message, content_to_save=content_to_save)
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
