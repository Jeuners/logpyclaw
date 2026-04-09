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


# ─── BaseSkill Wrapper ───────────────────────────────────────────────────────


class CodingSkill(BaseSkill):
    id = "coding"
    name = "Coding"
    icon = "code"
    description = "Creates projects, writes code, executes scripts in sandboxed workspace."
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
        Der Coding-Skill wird NICHT direkt per Regex dispatcht.
        Stattdessen wird er als Context-Info ans LLM übergeben.
        Das LLM entscheidet dann selbst welche Operationen es braucht.

        Für direkte Aufrufe (z.B. 'liste projekte'):
        """
        msg_lower = message.lower().strip()

        try:
            # Direkte Befehle
            if re.search(r"(liste|zeig\w*|list|show)\s+(alle\s+)?(projekte?|projects?)", msg_lower):
                return SkillResult(text=list_projects(), skill_used=self.id)

            # Alles andere: Return Skill-Info damit das LLM entscheiden kann
            # (Der ChatService übergibt dem LLM die Skill-Beschreibung als Tool-Beschreibung)
            return SkillResult(
                text=None,  # None = Skill hat nicht direkt geantwortet
                skill_used=self.id,
                metadata={"passthrough": True}  # Signal: LLM soll antworten
            )
        except Exception as e:
            return SkillResult(error=str(e), skill_used=self.id)
