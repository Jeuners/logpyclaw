"""
skills/coding_skill.py — Coding-Skill: Projekte erstellen, Code schreiben/ausführen.
Workspace: ~/Downloads/AgentClaw/projects/<projektname>/

Fähigkeiten:
- Projekt erstellen (Ordnerstruktur + Dateien)
- Code in Dateien schreiben (beliebige Sprache)
- Code ausführen (Python, Node, Bash — sandboxed im Workspace)
- Projektstruktur anzeigen
- Dateien lesen/bearbeiten
- Git init + commits
"""
import os
import re
import subprocess
import json
from datetime import datetime
from pathlib import Path

from skills.base import BaseSkill, SkillResult

PROJECTS_DIR = os.path.expanduser("~/Downloads/AgentClaw/projects")
MAX_EXEC_TIMEOUT = 30  # Sekunden
MAX_OUTPUT_LEN = 4000  # Zeichen


def _ensure_projects_dir() -> str:
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    return PROJECTS_DIR


def _safe_project_path(project_name: str) -> str | None:
    """Sicherer Pfad — kein Path-Traversal."""
    safe = re.sub(r"[^\w\-.]", "_", project_name.strip())
    if not safe or safe.startswith("."):
        return None
    path = os.path.join(_ensure_projects_dir(), safe)
    # Sicherstellen dass wir im Projects-Ordner bleiben
    if not os.path.realpath(path).startswith(os.path.realpath(PROJECTS_DIR)):
        return None
    return path


def _safe_file_path(project_path: str, filepath: str) -> str | None:
    """Sicherer Dateipfad innerhalb eines Projekts."""
    # Normalisieren, kein Path-Traversal
    clean = os.path.normpath(filepath).lstrip(os.sep)
    if ".." in clean.split(os.sep):
        return None
    full = os.path.join(project_path, clean)
    if not os.path.realpath(full).startswith(os.path.realpath(project_path)):
        return None
    return full


# ─── Projekt-Operationen ─────────────────────────────────────────────────────


def list_projects() -> str:
    """Alle Projekte auflisten."""
    base = _ensure_projects_dir()
    try:
        entries = []
        for d in sorted(os.listdir(base)):
            dp = os.path.join(base, d)
            if os.path.isdir(dp):
                file_count = sum(1 for _, _, files in os.walk(dp) for _ in files)
                mtime = datetime.fromtimestamp(os.path.getmtime(dp)).strftime("%Y-%m-%d %H:%M")
                entries.append(f"- **{d}/** ({file_count} Dateien, {mtime})")
        if not entries:
            return f"Keine Projekte vorhanden.\n\nWorkspace: `{base}`\n\nSag z.B. *'Erstelle Projekt mein-tool'* um loszulegen."
        return f"**Projekte in** `{base}`:\n\n" + "\n".join(entries)
    except Exception as e:
        return f"Fehler: {e}"


def create_project(name: str, files: dict[str, str] | None = None) -> str:
    """Neues Projekt mit optionalen Dateien erstellen."""
    path = _safe_project_path(name)
    if not path:
        return f"Ungültiger Projektname: '{name}'"
    os.makedirs(path, exist_ok=True)
    results = [f"**Projekt erstellt:** `{path}`"]

    if files:
        for filepath, content in files.items():
            fp = _safe_file_path(path, filepath)
            if not fp:
                results.append(f"  - ⚠️ Übersprungen (unsicherer Pfad): {filepath}")
                continue
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content)
            results.append(f"  - `{filepath}` ({len(content)} Bytes)")

    return "\n".join(results)


def show_tree(project_name: str) -> str:
    """Projektstruktur als Baum anzeigen."""
    path = _safe_project_path(project_name)
    if not path or not os.path.isdir(path):
        return f"Projekt nicht gefunden: '{project_name}'"

    lines = [f"**{project_name}/**"]
    for root, dirs, files in os.walk(path):
        # Skip hidden dirs
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        level = root.replace(path, "").count(os.sep)
        indent = "  " * level
        basename = os.path.basename(root)
        if root != path:
            lines.append(f"{indent}{basename}/")
        subindent = "  " * (level + 1)
        for f in sorted(files):
            if f.startswith("."):
                continue
            fp = os.path.join(root, f)
            size = os.path.getsize(fp)
            lines.append(f"{subindent}{f} ({size}B)")

    if len(lines) == 1:
        lines.append("  (leer)")
    return "\n".join(lines[:100])


def write_project_file(project_name: str, filepath: str, content: str) -> str:
    """Datei in einem Projekt erstellen/überschreiben."""
    path = _safe_project_path(project_name)
    if not path:
        return f"Ungültiger Projektname: '{project_name}'"
    os.makedirs(path, exist_ok=True)

    fp = _safe_file_path(path, filepath)
    if not fp:
        return f"Unsicherer Dateipfad: '{filepath}'"

    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)

    return f"**Geschrieben:** `{project_name}/{filepath}` ({len(content)} Bytes)"


def read_project_file(project_name: str, filepath: str) -> str:
    """Datei aus einem Projekt lesen."""
    path = _safe_project_path(project_name)
    if not path:
        return f"Ungültiger Projektname: '{project_name}'"

    fp = _safe_file_path(path, filepath)
    if not fp or not os.path.exists(fp):
        return f"Datei nicht gefunden: `{project_name}/{filepath}`"

    try:
        size = os.path.getsize(fp)
        if size > 500_000:
            return f"Datei zu groß ({size} Bytes). Max: 500 KB"
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return f"**{project_name}/{filepath}** ({size} Bytes):\n\n```\n{content[:MAX_OUTPUT_LEN]}\n```"
    except Exception as e:
        return f"Fehler beim Lesen: {e}"


# ─── Code-Ausführung ─────────────────────────────────────────────────────────


def execute_code(project_name: str, command: str) -> str:
    """Code im Projekt-Verzeichnis ausführen (sandboxed)."""
    path = _safe_project_path(project_name)
    if not path or not os.path.isdir(path):
        return f"Projekt nicht gefunden: '{project_name}'"

    # Gefährliche Befehle blockieren
    dangerous = ["rm -rf /", "rm -rf ~", "sudo", "chmod 777", "mkfs",
                  "dd if=", "> /dev/", "curl | sh", "wget | sh"]
    cmd_lower = command.lower()
    for d in dangerous:
        if d in cmd_lower:
            return f"**Blockiert:** Befehl enthält gefährliche Operation: `{d}`"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=path,
            capture_output=True,
            text=True,
            timeout=MAX_EXEC_TIMEOUT,
            env={
                **os.environ,
                "HOME": os.path.expanduser("~"),
                "PATH": os.environ.get("PATH", "/usr/bin:/usr/local/bin"),
            },
        )

        output_parts = []
        if result.stdout:
            stdout = result.stdout[:MAX_OUTPUT_LEN]
            output_parts.append(f"**stdout:**\n```\n{stdout}\n```")
        if result.stderr:
            stderr = result.stderr[:MAX_OUTPUT_LEN]
            output_parts.append(f"**stderr:**\n```\n{stderr}\n```")

        status = "✅ OK" if result.returncode == 0 else f"❌ Exit-Code: {result.returncode}"
        output = "\n\n".join(output_parts) if output_parts else "(keine Ausgabe)"

        return f"**Befehl:** `{command}`\n**Status:** {status}\n\n{output}"

    except subprocess.TimeoutExpired:
        return f"**Timeout!** Befehl hat {MAX_EXEC_TIMEOUT}s überschritten: `{command}`"
    except Exception as e:
        return f"**Fehler:** {e}"


def git_init(project_name: str) -> str:
    """Git-Repository im Projekt initialisieren."""
    path = _safe_project_path(project_name)
    if not path or not os.path.isdir(path):
        return f"Projekt nicht gefunden: '{project_name}'"

    result = execute_code(project_name, "git init && git add -A && git commit -m 'Initial commit' --allow-empty")
    return result


# ─── Multi-File Extraction ───────────────────────────────────────────────────

# Akzeptierte Datei-Extensions in Multi-File-Blocks
_ALLOWED_EXT = (
    "html|htm|css|js|mjs|ts|tsx|jsx|json|py|rb|go|rs|java|kt|swift|c|cc|cpp|h|hpp|"
    "sh|bash|zsh|ps1|sql|md|txt|yaml|yml|toml|ini|cfg|env|xml|svg|csv|dockerfile|"
    "makefile|gitignore|lock|conf"
)

# Header-Formate die einen Dateinamen vor einem Code-Fence markieren:
#   ### 1. index.html           ### index.html          ## styles.css
#   **index.html**              **index.html:**         __index.html__
#   `index.html`                filename: index.html    # index.html
#   --- index.html ---          // File: index.html
_FILE_HEADER_RX = re.compile(
    r"""(?imx)
    ^\s*
    (?:
        \#{1,6}\s*\d*\.?\s* |        # ### 1.  oder  #
        [-=]{2,}\s* |                # ---  ===
        //\s*(?:file|datei)\s*[:=]\s* |
        \#\s*(?:file|datei)\s*[:=]\s* |
        (?:file|datei|filename|pfad|path)\s*[:=]\s*
    )?
    (?:\*\*|__|`)?                    # optional bold/code wrapper start
    (?P<fname>[\w\-./]+?\.(?:""" + _ALLOWED_EXT + r"""))
    \s*:?\s*                          # optional trailing colon (before or after bold close)
    (?:\*\*|__|`)?                    # optional bold/code wrapper close
    \s*:?\s*(?:---)?\s*$              # optional trailing colon + dashes
    """,
)

# Fence-Start:  ```lang   oder  ~~~lang
_FENCE_START_RX = re.compile(r"^\s*(```|~~~)\s*([\w+.-]*)\s*$")


def _extract_files_from_markdown(text: str) -> dict[str, str]:
    """Extrahiert `{filename: content}` aus Markdown mit Datei-Headern + Code-Fences.

    Erkennt:
        ### 1. index.html
        ```html
        <html>...</html>
        ```

    Heuristik: ein Header matcht nur, wenn innerhalb der nächsten ~5 nicht-leeren
    Zeilen ein Code-Fence startet. Sonst ignoriert (könnte Prosa sein).
    """
    if not text:
        return {}

    lines = text.splitlines()
    files: dict[str, str] = {}
    i = 0
    n = len(lines)
    while i < n:
        m = _FILE_HEADER_RX.match(lines[i])
        if not m:
            i += 1
            continue
        fname = m.group("fname")

        # Schaue bis zu 6 Zeilen voraus nach einem Code-Fence
        j = i + 1
        skipped = 0
        while j < n and skipped < 6:
            if _FENCE_START_RX.match(lines[j]):
                break
            if lines[j].strip():
                skipped += 1
            j += 1
        else:
            i += 1
            continue
        if j >= n or not _FENCE_START_RX.match(lines[j]):
            i += 1
            continue

        fence_m = _FENCE_START_RX.match(lines[j])
        fence_marker = fence_m.group(1)
        # Sammle Inhalt bis matching Fence-Close
        content_lines = []
        k = j + 1
        while k < n:
            if lines[k].lstrip().startswith(fence_marker):
                break
            content_lines.append(lines[k])
            k += 1
        if k >= n:
            # unclosed fence — trotzdem akzeptieren
            pass
        content = "\n".join(content_lines)
        # Nur akzeptieren wenn Inhalt nicht-trivial
        if content.strip():
            files[fname] = content
        i = k + 1

    return files


_STOPWORDS = {
    "in", "im", "am", "an", "auf", "bei", "zu", "und", "oder", "the", "a", "an",
    "für", "for", "mit", "with", "of", "ein", "eine", "einer", "einem", "einen",
    "der", "die", "das", "den", "dem", "des", "unter", "ordner", "folder",
    "name", "namens", "called", "namen", "pfad", "path", "dir", "directory",
}


def _derive_project_name(message: str, content: str) -> str:
    """Leitet einen kebab-case Projektnamen aus Message oder Content ab.

    Priorität:
      1. Pfad `projects/<name>/` in Content oder Message
      2. "Projektname: xyz" / "project: xyz" (mit echtem `:` oder `=`)
      3. Slug aus dem Nachrichteninhalt (erste sinnvolle Wörter)
    """
    def _clean(n: str) -> str:
        return re.sub(r"_", "-", n.strip("-_. ").lower())

    # 1) Pfad mit projects/<name>/ — stärkster Signal
    for src in (content, message):
        if not src:
            continue
        m = re.search(r"projects?/([a-zA-Z][\w\-]{2,50})(?:/|\s|$|`|\"|')", src)
        if m:
            name = _clean(m.group(1))
            if name and name not in _STOPWORDS and len(name) >= 3:
                return name

    # 2) Explizit: "Projektname: xyz" (benötigt echtes Trennzeichen)
    for src in (content, message):
        if not src:
            continue
        m = re.search(
            r"(?:projekt(?:name)?|project(?:name)?|projekt[\s\-]*pfad)"
            r"\s*[:=]\s*[`\"']?"
            r"([a-zA-Z][\w\-]{2,50})",
            src, re.IGNORECASE,
        )
        if m:
            name = _clean(m.group(1))
            if name and name not in _STOPWORDS and len(name) >= 3:
                return name

    # 3) Slug aus Message (erste 5 Content-Wörter)
    base = (message or "").strip()
    base = re.sub(r"---\s*\nDeine Aufgabe:\s*", "", base)
    # Technischen Boilerplate wegschneiden
    base = re.sub(r"\[Ergebnisse.*?\]:", "", base, flags=re.DOTALL)
    base = re.sub(r"[^\w\s-]", " ", base.lower())
    words = [w for w in base.split() if w not in _STOPWORDS and len(w) >= 3][:5]
    slug = "-".join(words)[:40].strip("-")
    if not slug or len(slug) < 3:
        slug = f"project-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return slug


# ─── BaseSkill Wrapper ───────────────────────────────────────────────────────


class CodingSkill(BaseSkill):
    id = "coding"
    name = "Coding"
    icon = "code"
    description = (
        "Creates projects, writes multiple files, executes scripts in sandboxed "
        "workspace (~/Downloads/AgentClaw/projects/<name>/). Extracts files from "
        "markdown reply (### filename.ext + code fence)."
    )
    triggers = [
        r"\b(erstelle?|create|neues?)\b.{0,20}\b(projekt|project|app|tool)\b",
        r"\b(code|script|programm|implement|debug|refactor)\b",
        r"\b(python|javascript|typescript|node|bash|html|css|rust|go)\b",
        r"\b(führe?\s+aus|execute|run|starte?)\b.{0,20}\b(code|script|test|server)\b",
        r"\b(zeig\w*|show|list)\b.{0,20}\b(projekt|project|tree|struktur)\b",
        r"\b(schreib\w*|write)\b.{0,20}\b(datei|file|code|class|function)\b",
        r"\b(baue?|build|compile|install|npm|pip|cargo)\b",
    ]
    requires = []

    def execute(self, agent: dict, message: str, **context) -> SkillResult:
        """
        Zwei Modi:
          1. Direkter Aufruf mit content_to_save (LLM hat Reply + [coding] geschrieben)
             → Multi-File-Extraktion + create_project()
          2. Direkte Befehle wie "liste projekte"
          3. Sonst: passthrough (LLM entscheidet selbst)
        """
        msg_lower = message.lower().strip()
        content = context.get("content_to_save") or context.get("llm_reply") or ""

        try:
            # Direkter Listen-Befehl
            if re.search(r"(liste|zeig\w*|list|show)\s+(alle\s+)?(projekte?|projects?)", msg_lower):
                return SkillResult(text=list_projects(), skill_used=self.id)

            # Multi-File-Extraktion aus LLM-Reply
            if content:
                files = _extract_files_from_markdown(content)
                if files:
                    project_name = _derive_project_name(message, content)
                    result_text = create_project(project_name, files)
                    path = _safe_project_path(project_name)
                    if path:
                        result_text += f"\n\nProjektpfad: `{path}`"
                    return SkillResult(
                        text=result_text,
                        skill_used=self.id,
                        metadata={"project_name": project_name, "files_written": len(files)},
                    )

            # Passthrough: Skill wurde getriggert, aber kein File-Output produziert
            # (kein content_to_save mit Markdown-Code-Blocks, kein list-Befehl).
            # → Status muss halted_no_exec werden, sonst meldet der Operator-Loop
            # „done" obwohl die versprochene Datei nicht existiert.
            # Beobachteter Bug: CodeCraft schrieb „[coding]" als Annotation,
            # CodingSkill returned passthrough → Task completed → Martin glaubt
            # an Erfolg, aber data/exports/wild/index.html wurde nie erstellt.
            return SkillResult(
                text=None,
                skill_used=self.id,
                metadata={"passthrough": True, "executed": False},
            )
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
