"""
backend/skills/file.py — Dateisystem-Skill für Agenten.

Operationen (automatisch erkannt):
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
    description = "Liest, listet und durchsucht Dateien auf dem lokalen Dateisystem."
    CONFIG_FIELDS = (
        SkillConfigField("root_dir", env="FILE_SKILL_ROOT",
                         default=os.path.expanduser("~")),
    )

    async def execute(self, query: str) -> str:
        q = query.strip()
        try:
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

    def _list(self, raw: str) -> str:
        path = _safe(raw)
        if not os.path.isdir(path):
            return f"[File] Kein Verzeichnis: {path}"
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
