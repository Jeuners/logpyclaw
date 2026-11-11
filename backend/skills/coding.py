"""
backend/skills/coding.py — CodingSkill: Python-Code ausführen via subprocess.

Extrahiert Python-Code aus der Query und führt ihn in einem isolierten
subprocess aus. Gibt stdout + stderr zurück; Exit-Code ≠ 0 → Exception,
damit der Step im UI als failed erscheint statt mit grünem Häkchen.

Akzeptiert wird NUR:
  - ```python ... ```-Codeblöcke (explizit — Syntaxfehler laufen durch und
    werden als stderr gemeldet)
  - "führe aus: <code>" / "execute: <code>" / "run: <code>" am Anfang
  - nackter Code, der tatsächlich als Python parst (ast.parse)
Prosa/Aufgabenbeschreibungen werden abgelehnt — die gehören zu agent:coder
oder agent:claude, nicht hierher.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap

from backend.skills import Skill

_TIMEOUT = 30  # Sekunden

# Mindest-Indikatoren für echten Code — verhindert, dass ein einzelnes Wort
# ("anytime"), das als Name-Expression parst, als Programm durchgeht.
_PY_INDICATORS = re.compile(
    r"(\bimport\s|\bdef\s|\bclass\s|\bprint\s*\(|\bfor\s|\bwhile\s|\bif\s|"
    r"\breturn\b|=|\braise\s|\bassert\s|\blambda\b|\bwith\s)"
)


def _parses_as_python(text: str) -> bool:
    try:
        ast.parse(text)
        return True
    except SyntaxError:
        return False


def _extract_code(query: str) -> str | None:
    """Extrahiert Python-Code. Gibt None zurück wenn kein expliziter Code erkannt."""
    # ```python ... ``` oder ``` ... ``` — explizit, läuft ohne Parse-Check
    m = re.search(r"```(?:python)?\s*\n?(.*?)```", query, re.DOTALL | re.IGNORECASE)
    if m:
        return textwrap.dedent(m.group(1)).strip() or None

    # "führe aus: <code>" — nur am ANFANG der Query, mit Doppelpunkt.
    # (Ungeankert matchte das englische Wort "run" mitten in Prosa, und der
    # gesamte Rest des Satzes wurde als "Code" ausgeführt.)
    m2 = re.match(
        r"\s*(?:führe?\s+aus|execute|run|ausführen)\s*:\s*(.+)",
        query,
        re.IGNORECASE | re.DOTALL,
    )
    if m2:
        code = textwrap.dedent(m2.group(1)).strip()
        return code or None

    # Nackte Query: nur akzeptieren, wenn sie WIRKLICH als Python parst UND
    # nach Code aussieht — englische Prosa fällt hier durch, statt via
    # `python -c` einen SyntaxError zu produzieren.
    stripped = query.strip()
    if (
        len(stripped) > 4
        and _PY_INDICATORS.search(stripped)
        and _parses_as_python(stripped)
    ):
        return stripped

    return None  # kein Code erkennbar


class CodingSkill(Skill):
    skill_id = "coding"
    description = (
        "Führt FERTIGEN Python-Code aus (```python...```-Block oder "
        "'führe aus: <code>'). Versteht KEINE Aufgabenbeschreibungen — "
        "Code schreiben lassen → agent:coder. Gibt stdout + stderr zurück."
    )

    async def execute(self, query: str) -> str:
        code = _extract_code(query)
        if not code:
            return (
                "[CodingSkill] Kein ausführbarer Python-Code erkannt — das liest "
                "sich wie eine Aufgabenbeschreibung. Ich führe nur fertigen Code "
                "aus; Code schreiben kann agent:coder.\n"
                "Formate: ```python\\ncode\\n``` oder 'führe aus: <code>'"
            )

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

        output = "\n".join(parts) if parts else "(kein Output)"

        # Exit ≠ 0 → als Fehler signalisieren. Der SkillAgent macht daraus
        # eine ERROR-Message, der Plan-Step erscheint als failed statt ✓.
        if result.returncode != 0:
            raise RuntimeError(f"Exit {result.returncode}\n{output}")

        return output
