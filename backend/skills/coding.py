"""
backend/skills/coding.py — CodingSkill: Python-Code ausführen via subprocess.

Extrahiert Python-Code aus der Query (```python...``` oder direkt) und führt
ihn in einem isolierten subprocess aus. Gibt stdout + stderr zurück.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap

from backend.skills import Skill

_TIMEOUT = 30  # Sekunden


def _extract_code(query: str) -> str:
    """Extrahiert Python-Code aus Markdown-Codeblock oder nimmt Query direkt."""
    # ```python ... ``` oder ``` ... ```
    m = re.search(r"```(?:python)?\s*\n?(.*?)```", query, re.DOTALL | re.IGNORECASE)
    if m:
        return textwrap.dedent(m.group(1)).strip()

    # Schlüsselwörter wie "führe aus:", "execute:", "run:" → danach den Rest nehmen
    m2 = re.search(
        r"(?:führe?\s+aus|execute|run|ausführen)\s*:?\s*(.*)",
        query,
        re.IGNORECASE | re.DOTALL,
    )
    if m2:
        return textwrap.dedent(m2.group(1)).strip()

    return query.strip()


class CodingSkill(Skill):
    skill_id = "coding"
    description = (
        "Führt Python-Code aus. Query kann ```python...```-Block oder "
        "'führe aus: <code>' sein. Gibt stdout + stderr zurück."
    )

    async def execute(self, query: str) -> str:
        code = _extract_code(query)
        if not code:
            return "[CodingSkill] Kein Code gefunden. Bitte Python-Code angeben."

        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                cwd="/tmp",
            )
        except subprocess.TimeoutExpired:
            return f"[CodingSkill] Timeout nach {_TIMEOUT}s — Code abgebrochen."
        except Exception as e:
            return f"[CodingSkill] Fehler beim Starten des Subprozesses: {e}"

        parts: list[str] = []
        if result.stdout.strip():
            parts.append(result.stdout.rstrip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.rstrip()}")
        if result.returncode != 0 and not parts:
            parts.append(f"[exit {result.returncode}] (kein Output)")

        return "\n".join(parts) if parts else "(kein Output)"
