"""
backend/skills/file.py — Dateisystem-Skill für Agenten.

Operationen (automatisch erkannt):
  - write:    "schreibe nach ~/pfad/datei.html: <inhalt>" — Inhalt kann auch als
              ```fence``` oder als gechainter "[Vorherige Ergebnisse]"-Block kommen
  - ls/list:  "liste ~/Downloads" oder "ls /tmp"
  - read/cat: "lese ~/datei.txt" oder "zeige /pfad/code.py"
  - find:     "finde *.py in ~/Desktop"
  - tree:     "struktur von ~/projekt"
"""
from __future__ import annotations

import glob
import os
import re

from backend.skills import Skill, SkillConfigField

_BLOCKED = {"/etc/shadow", "/etc/passwd", "/proc", "/sys", "/dev"}
_MAX_READ  = 8_000
_MAX_LIST  = 100
_MAX_FIND  = 40
_MAX_WRITE = 2 * 1024 * 1024  # 2 MB

# Schreib-Befehl: "schreibe/speichere ... nach/als/in <pfad>". Der Pfad wird
# hier extrahiert; der Inhalt kann VOR dem Befehl stehen (Martin-Chaining legt
# "[Vorherige Ergebnisse]" an den Anfang), als ```fence``` oder nach ":".
_WRITE_CMD = re.compile(
    r"\b(?:schreib\w*|speicher\w*|write|save)\b[^\n]*?"
    r"(?:nach|als|in|unter|to|at)\s+(~[/\w.\-]*|/[/\w.\-]+)",
    re.I,
)
_FENCE = re.compile(r"```[\w]*\s*\n?(.*?)```", re.DOTALL)
_CHAIN_MARKER = "[Vorherige Ergebnisse]"

_EXT_TEXT = re.compile(
    r"\.(py|js|ts|jsx|tsx|html|css|md|txt|yaml|yml|json|toml|sh|env|"
    r"rs|go|c|cpp|h|java|rb|php|sql|xml|csv|log|conf|ini|cfg)$", re.I
)


def _safe(path: str) -> str:
    path = os.path.realpath(os.path.expanduser(path))
    for b in _BLOCKED:
        if path.startswith(b):
            raise PermissionError(f"Gesperrt: {path}")
    return path


def _fmt(n: int) -> str:
    if n < 1024:      return f"{n}B"
    if n < 1024**2:   return f"{n//1024}KB"
    return f"{n//1024//1024}MB"


class FileSkill(Skill):
    skill_id   = "file"
    description = (
        "Liest, listet, durchsucht UND schreibt Dateien auf dem lokalen "
        "Dateisystem. Schreiben: 'schreibe nach <pfad>: <inhalt>' — Inhalt "
        "kann auch aus dem gechainten Vorschritt (depends_on) kommen."
    )
    CONFIG_FIELDS = (
        SkillConfigField("root_dir", env="FILE_SKILL_ROOT",
                         default=os.path.expanduser("~")),
    )

    async def execute(self, query: str) -> str:
        q = query.strip()
        try:
            # Write ZUERST prüfen — der zu schreibende Inhalt (HTML/Code) würde
            # sonst über Wörter wie "list"/"show" die Lese-Operationen triggern.
            m = _WRITE_CMD.search(q)
            if m:
                return self._write(m.group(1), q, m)

            if re.search(r"\b(ls|list|liste|dir|verzeichnis)\b", q, re.I):
                path = self._path(q) or self.config["root_dir"]
                return self._list(path)

            if re.search(r"\b(tree|struktur|baum)\b", q, re.I):
                path = self._path(q) or self.config["root_dir"]
                return self._tree(path)

            if re.search(r"\b(find|suche|finde|search|glob)\b", q, re.I):
                return self._find(q)

            if re.search(r"\b(cat|read|lese|zeige|inhalt|show|open)\b", q, re.I):
                path = self._path(q)
                return self._read(path) if path else "[File] Kein Pfad erkannt."

            # Pfad im Query → automatisch ls oder cat
            path = self._path(q)
            if path:
                if os.path.isdir(path):  return self._list(path)
                if os.path.isfile(path): return self._read(path)
                return f"[File] Nicht gefunden: {path}"

            return (
                "[File] Befehl nicht erkannt.\n"
                "Beispiele: `ls ~/Downloads`, `lese ~/datei.py`, `finde *.json in ~/projekt`"
            )
        except PermissionError as e:
            return f"[File] ⛔ {e}"
        except Exception as e:
            return f"[File] Fehler: {e}"

    # ── Operationen ───────────────────────────────────────────────────────────

    def _write(self, raw_path: str, query: str, cmd_match: re.Match) -> str:
        path = _safe(raw_path)
        home = os.path.realpath(os.path.expanduser("~"))
        if not path.startswith(home + os.sep):
            return f"[File] ⛔ Schreiben nur unterhalb von ~ erlaubt: {path}"

        content = self._extract_content(query, cmd_match)
        if content is None:
            return (
                "[File] Kein Inhalt zum Schreiben erkannt.\n"
                "Formate: 'schreibe nach ~/datei.txt: <inhalt>', Inhalt als "
                "```block``` oder via depends_on aus dem Vorschritt."
            )
        if len(content.encode("utf-8", errors="replace")) > _MAX_WRITE:
            return f"[File] Inhalt zu groß (max {_fmt(_MAX_WRITE)})."

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"📝 Geschrieben: {path} ({_fmt(os.path.getsize(path))})"

    @staticmethod
    def _extract_content(query: str, cmd_match: re.Match) -> str | None:
        """Ermittelt den zu schreibenden Inhalt — drei Quellen, in dieser Reihenfolge:
        1. größter ```fence```-Block irgendwo in der Query — der zuverlässigste
           Marker für echten (Coder-)Output; schlägt Doppelpunkt-Text, denn der
           ist beim LLM-Planning oft nur eine Beschreibung statt Inhalt
        2. expliziter Doppelpunkt nach dem Pfad ("… nach ~/x.txt: <inhalt>")
        3. gechainter "[Vorherige Ergebnisse]"-Block vor dem Schreib-Befehl
        """
        fences = _FENCE.findall(query)
        if fences:
            return max(fences, key=len).strip()

        after = query[cmd_match.end():]
        m = re.match(r"\s*:\s*(.+)", after, re.DOTALL)
        if m and m.group(1).strip():
            return m.group(1).strip()

        if _CHAIN_MARKER in query:
            start = query.index(_CHAIN_MARKER) + len(_CHAIN_MARKER)
            chained = query[start:cmd_match.start()].strip()
            if chained:
                return chained

        return None

    def _list(self, raw: str) -> str:
        path = _safe(raw)
        if not os.path.isdir(path):
            # "ls <datei>" / "gibts <datei>?" → Datei zeigen statt ablehnen
            if os.path.isfile(path):
                return self._read(raw)
            return f"[File] Nicht gefunden: {path}"
        try:
            entries = sorted(os.listdir(path),
                             key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        except PermissionError:
            return f"[File] ⛔ Kein Lese-Zugriff: {path}"

        lines = []
        for e in entries[:_MAX_LIST]:
            full = os.path.join(path, e)
            if os.path.isdir(full):
                lines.append(f"📁 {e}/")
            else:
                try:   size = _fmt(os.path.getsize(full))
                except Exception: size = "?"
                lines.append(f"📄 {e}  ({size})")

        out = "\n".join(lines)
        if len(entries) > _MAX_LIST:
            out += f"\n… (+{len(entries)-_MAX_LIST} weitere)"
        return f"📂 **{path}** — {len(entries)} Einträge\n\n{out}"

    def _read(self, raw: str) -> str:
        path = _safe(raw)
        if not os.path.exists(path):
            return f"[File] Nicht gefunden: {path}"
        if os.path.isdir(path):
            return self._list(raw)
        size = os.path.getsize(path)
        if size > 5 * 1024 * 1024:
            return f"[File] Zu groß ({_fmt(size)}) — max 5 MB."
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except Exception as e:
            return f"[File] Lesefehler: {e}"
        truncated = ""
        if len(text) > _MAX_READ:
            truncated = f"\n\n… ({len(text)-_MAX_READ} Zeichen abgeschnitten)"
            text = text[:_MAX_READ]
        lang = os.path.splitext(path)[1].lstrip(".") or ""
        return f"📄 **{os.path.basename(path)}** ({_fmt(size)})\n\n```{lang}\n{text}\n```{truncated}"

    def _tree(self, raw: str, depth: int = 3) -> str:
        path = _safe(raw)
        lines: list[str] = [f"📂 {path}"]
        self._walk(path, "", depth, lines)
        return "\n".join(lines[:80])

    def _walk(self, path: str, prefix: str, depth: int, out: list[str]) -> None:
        if depth == 0 or len(out) > 78:
            return
        try:
            entries = sorted(os.listdir(path),
                             key=lambda x: (not os.path.isdir(os.path.join(path, x)), x.lower()))
        except PermissionError:
            return
        for i, e in enumerate(entries[:20]):
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            full = os.path.join(path, e)
            if os.path.isdir(full):
                out.append(f"{prefix}{connector}📁 {e}/")
                self._walk(full, prefix + ("    " if last else "│   "), depth - 1, out)
            else:
                out.append(f"{prefix}{connector}📄 {e}")

    def _find(self, query: str) -> str:
        m = re.search(r"(\*[\w.*]+|\w+\*[\w.]*)", query)
        pattern = m.group(1) if m else "*.py"
        path = self._path(query) or self.config["root_dir"]
        path = _safe(path)
        try:
            hits = sorted(glob.glob(os.path.join(path, "**", pattern), recursive=True))[:_MAX_FIND]
        except Exception as e:
            return f"[File] Suchfehler: {e}"
        if not hits:
            return f"[File] Keine Treffer für `{pattern}` in {path}"
        return f"🔍 **{len(hits)} Treffer** — `{pattern}` in `{path}`\n\n" + "\n".join(hits)

    # ── Hilfe ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _path(q: str) -> str | None:
        m = re.search(r"(~[/\w.\-]*|/[/\w.\-]+)", q)
        if m:
            return os.path.expanduser(m.group(1))
        return None
