"""
skills/file_skill.py — Datei-Lesen/Schreiben

Unterstützt zwei Modi:
  1. Wiki-Modus: Agent hat wiki_dir gesetzt → arbeitet im konfigurierten Wiki-Verzeichnis
                 Subdirectories erlaubt (pages/, etc.)
  2. Downloads-Modus: Fallback → ~/Downloads/AgentClaw
                      Subdirectories erlaubt, Path-Traversal (..) blockiert.
"""
import os
import re
from datetime import datetime

DOWNLOADS_DIR = os.path.expanduser("~/Downloads/AgentClaw")


def _get_base_dir(agent: dict = None) -> tuple[str, bool]:
    """Gibt (base_dir, wiki_mode) zurück."""
    if agent:
        wiki_dir = agent.get("wiki_dir", "").strip()
        if wiki_dir:
            expanded = os.path.expanduser(wiki_dir)
            os.makedirs(expanded, exist_ok=True)
            return expanded, True
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    return DOWNLOADS_DIR, False


def _safe_path(base_dir: str, filename: str, wiki_mode: bool) -> str | None:
    """
    Gibt sicheren absoluten Pfad zurück, oder None bei Path-Traversal.
    Subdirectories sind in beiden Modi erlaubt (z.B. projects/site/index.html).
    Blockiert: absolute Pfade, `..`-Traversal, Pfade ausserhalb base_dir.
    """
    if not filename:
        return None
    # Tilde/absolute Pfade auf base_dir projizieren: Agent schreibt
    # "~/Downloads/AgentClaw/projects/foo/index.html" → "projects/foo/index.html"
    expanded = os.path.expanduser(filename)
    base_abs = os.path.abspath(base_dir)
    if os.path.isabs(expanded):
        exp_abs = os.path.abspath(expanded)
        if exp_abs.startswith(base_abs + os.sep) or exp_abs == base_abs:
            rel = os.path.relpath(exp_abs, base_abs)
        else:
            return None  # Absolut ausserhalb base_dir → ablehnen
    else:
        rel = expanded

    clean = os.path.normpath(rel).lstrip(os.sep)
    if not clean or clean == "." or clean.startswith(".."):
        return None
    # Kein einzelner ".."-Segment irgendwo im Pfad
    if ".." in clean.split(os.sep):
        return None
    full = os.path.join(base_dir, clean)
    # Final-Check: Realpath liegt innerhalb base_dir
    if not os.path.realpath(full).startswith(os.path.realpath(base_dir)):
        return None
    return full


def list_files(base_dir: str, wiki_mode: bool, subdir: str = None) -> str:
    """Listet Dateien im Verzeichnis (wiki: rekursiv, downloads: flach)."""
    target = os.path.join(base_dir, subdir) if subdir and wiki_mode else base_dir
    if not os.path.isdir(target):
        return f"📂 Verzeichnis nicht gefunden: `{target}`"
    try:
        if wiki_mode:
            lines = []
            for root, dirs, files in os.walk(target):
                dirs.sort()
                rel_root = os.path.relpath(root, base_dir)
                for f in sorted(files):
                    rel = os.path.join(rel_root, f) if rel_root != "." else f
                    fp = os.path.join(root, f)
                    size_kb = os.path.getsize(fp) / 1024
                    mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
                    lines.append(f"- `{rel}` ({size_kb:.1f} KB, {mtime})")
            if not lines:
                return f"📂 Wiki-Verzeichnis leer: `{base_dir}`"
            return f"📂 **Wiki `{base_dir}`:**\n\n" + "\n".join(lines[:100])
        else:
            files = []
            for f in sorted(os.listdir(target), key=lambda x: os.path.getmtime(os.path.join(target, x)), reverse=True):
                fp = os.path.join(target, f)
                if os.path.isfile(fp):
                    size_mb = os.path.getsize(fp) / 1024 / 1024
                    mtime = datetime.fromtimestamp(os.path.getmtime(fp)).strftime("%Y-%m-%d %H:%M")
                    files.append(f"- `{f}` ({size_mb:.1f} MB, {mtime})")
            if not files:
                return f"📂 Downloads-Ordner leer: `{target}`"
            return f"📂 **Dateien in `{target}`:**\n\n" + "\n".join(files[:50])
    except Exception as e:
        return f"❌ Fehler beim Lesen des Ordners: {e}"


def read_file(base_dir: str, filename: str, wiki_mode: bool) -> str:
    """Liest eine Datei."""
    filepath = _safe_path(base_dir, filename, wiki_mode)
    if not filepath:
        return f"❌ Ungültiger Dateipfad: `{filename}`"
    if not os.path.exists(filepath):
        return f"❌ Datei nicht gefunden: `{filepath}`"
    try:
        size_mb = os.path.getsize(filepath) / 1024 / 1024
        if size_mb > 10:
            return f"❌ Datei zu groß ({size_mb:.1f} MB, max 10 MB)"
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"📄 **{filename}**:\n\n{content[:12000]}"
    except Exception as e:
        return f"❌ Fehler beim Lesen: {e}"


def write_file(base_dir: str, filename: str, content: str, wiki_mode: bool) -> str:
    """Schreibt Inhalt in eine Datei. Erstellt Subdirectories im Wiki-Modus."""
    filepath = _safe_path(base_dir, filename, wiki_mode)
    if not filepath:
        return f"❌ Ungültiger Dateipfad: `{filename}`"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        size_kb = os.path.getsize(filepath) / 1024
        return f"💾 **Gespeichert:** `{filepath}` ({size_kb:.1f} KB)"
    except Exception as e:
        return f"❌ Fehler beim Schreiben: {e}"


def append_file(base_dir: str, filename: str, content: str, wiki_mode: bool) -> str:
    """Hängt Inhalt an eine bestehende Datei an (für Wiki-Log etc.)."""
    filepath = _safe_path(base_dir, filename, wiki_mode)
    if not filepath:
        return f"❌ Ungültiger Dateipfad: `{filename}`"
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(content)
        return f"📝 **Angehängt:** `{filepath}`"
    except Exception as e:
        return f"❌ Fehler beim Anhängen: {e}"


def run_file_access(message: str, content_to_save: str = None, agent: dict = None) -> str | None:
    """Hauptfunktion: Erkennt und führt Dateioperationen aus."""
    base_dir, wiki_mode = _get_base_dir(agent)

    # Multi-File-Fallback: wenn content_to_save mehrere Datei-Blocks enthält
    # (### filename.ext + code fence), delegiere an coding.create_project()
    # Agents die file_access statt coding aufrufen, schreiben so trotzdem Projekte korrekt.
    if content_to_save and not wiki_mode:
        try:
            from skills.coding_skill import (
                _extract_files_from_markdown,
                _derive_project_name,
                create_project,
                _safe_project_path,
            )
            files = _extract_files_from_markdown(content_to_save)
            if len(files) >= 2:
                project_name = _derive_project_name(message, content_to_save)
                result = create_project(project_name, files)
                path = _safe_project_path(project_name)
                if path:
                    result += f"\n\nProjektpfad: `{path}`"
                return result
        except Exception:
            pass  # Fallback auf Single-File-Logik

    # Datei lesen
    read_m = re.search(
        r"(?:lese?|lies|öffne?|read|open|zeige?|show|cat)\s+"
        r"(?:die\s+|den\s+|das\s+|the\s+)?(?:datei\s+|file\s+|seite\s+|page\s+)?"
        r"([\w\-./]+\.(?:md|txt|json|csv|log|html|py|js|ts|yaml|yml|toml|ini|cfg))",
        message, re.IGNORECASE,
    )
    if read_m:
        return read_file(base_dir, read_m.group(1), wiki_mode)

    # Datei anhängen
    append_m = re.search(
        r"(?:append|hänge?\s+an|füge?\s+(?:an|hinzu))\s+"
        r"[^.\n]{0,60}?([\w\-./]+\.(?:md|txt|log|json))",
        message, re.IGNORECASE,
    )
    if append_m and content_to_save:
        return append_file(base_dir, append_m.group(1), content_to_save, wiki_mode)

    # Datei schreiben / speichern
    save_rx = re.compile(
        r"(?:speichere?|schreib\w*|save|write|erstell\w*|create|als?|unter|in)\s+"
        r"[^.\n]{0,80}?([\w\-./]+\.(?:md|txt|json|csv|log|html|py|js|ts|yaml|yml))",
        re.IGNORECASE,
    )
    save_m = save_rx.search(message) or (save_rx.search(content_to_save) if content_to_save else None)
    if save_m and content_to_save:
        return write_file(base_dir, save_m.group(1), content_to_save, wiki_mode)
    if save_m and not content_to_save:
        # Kein Inhalt → passthrough: LLM soll erst Inhalt generieren
        return None

    # Generisches Speichern (als markdown/datei ohne Namen)
    generic_save = re.search(
        r"(?:als|in|zu)\s+(?:eine[r]?\s+)?(md|markdown|txt|text|datei)\b|"
        r"schreib\w*\s+(?:die\s+)?datei|"
        r"(?:speichere?|save)\s+(?:das|die|den|the\s+file)\b",
        message, re.IGNORECASE,
    )
    if generic_save and content_to_save:
        ext_map = {"md": "md", "markdown": "md", "txt": "txt", "text": "txt", "datei": "md"}
        ext = ext_map.get((generic_save.group(1) or "md").lower(), "md")
        title_m = re.search(r"^\s*#+\s*(.+)$", content_to_save, re.MULTILINE)
        title = title_m.group(1) if title_m else next(
            (ln.strip() for ln in content_to_save.splitlines() if ln.strip()), "datei"
        )
        slug = re.sub(r"[^\w\s-]", "", title.lower())
        slug = re.sub(r"\s+", "-", slug).strip("-")[:50] or "datei"
        filename = f"pages/{slug}.{ext}" if wiki_mode else f"{slug}.{ext}"
        return write_file(base_dir, filename, content_to_save, wiki_mode)

    # Verzeichnis auflisten
    if re.search(
        r"\b(liste[nt]?|list|zeig\w*|show|was|welche|which|display|anzeig\w*|ls)\b"
        r".{0,40}\b(datei|file|download|ordner|folder|verzeichnis|directory|inhalt|wiki)\w*\b",
        message, re.IGNORECASE,
    ):
        return list_files(base_dir, wiki_mode)

    return None


# ── BaseSkill Wrapper ─────────────────────────────────────────────────────────
from skills.base import BaseSkill, SkillResult


class FileAccessSkill(BaseSkill):
    id = "file_access"
    name = "File Access"
    icon = "folder_open"
    description = (
        "Liest und schreibt Dateien. Im Wiki-Modus: arbeitet im konfigurierten wiki_dir "
        "mit Subdirectory-Support (pages/, etc.). Fallback: ~/Downloads/AgentClaw."
    )
    triggers = [
        r"\b(?:datei|file|lese|lies|öffne|open|schreibe|write|speichere|save|append|hänge\s+an)\b"
        r".{0,30}\b(?:datei|file|txt|md|csv|json|log)\b",
        r"\bliste[nt]?\s+(?:dateien|files|downloads|wiki|alle)\b",
        r"\b(?:zeige|show|ls)\b.{0,20}\b(?:dateien|files|downloads|wiki)\b",
        r"\bspeichere?\s+(?:als?|in|die|den)\b",
        r"\bschreibe?\s+(?:die\s+)?(?:datei|seite|page)\b",
        r"\berstelle?\s+(?:eine?\s+)?(?:datei|seite|page)\b",
        r"\b(?:lese?|lies|read|open)\s+(?:index|log|pages/[\w\-]+)\.md\b",
        r"\bindex\.md\b",
        r"\bpages/[\w\-]+\.md\b",
        r"\blog\.md\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        content_to_save = context.get("content_to_save")
        try:
            result = run_file_access(message, content_to_save=content_to_save, agent=agent)
            if result is None:
                return SkillResult(text=None, skill_used=self.id, metadata={"passthrough": True})
            return SkillResult(text=result, skill_used=self.id)
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
