"""
Execution-Intent-Detektion für Task-Messages.

Zweck: Erkennen, wenn ein Task den Agent zur *Ausführung* eines Tools/Skripts
auffordert (nicht nur zum Lesen/Beschreiben). Wird vom chat_service als Guard
genutzt, damit Agenten ohne Execution-Skill nicht flüssig halluzinieren
("Ich habe das Skript ausgeführt, hier ist der Output..."), sondern strukturell
ehrlich antworten.

Stateless. Kein LLM-Aufruf. Reine Heuristik + Regex.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

IntentKind = Literal["shell", "python", "read", "write", "unknown"]


@dataclass(frozen=True)
class ExecutionIntent:
    kind: IntentKind
    target: Optional[str]     # z.B. Pfad zum Skript
    confidence: float         # 0.0 – 1.0
    matched_phrase: str       # was die Erkennung ausgelöst hat (fürs Log)


# ── Execution-Verben (DE + EN) ────────────────────────────────────────────
_EXEC_VERBS_DE = [
    r"führ(?:e|t)?\s+(?:das|den|die)?\s*(?:skript|script|tool|programm|code|kommando|befehl)",
    r"führ(?:e|t)?\s+.{0,40}\s*aus\b",
    r"starte?\s+(?:das|den|die)?\s*(?:skript|script|tool|programm|server|prozess)",
    r"lass(?:\s+mich)?\s+.{0,40}\s*laufen\b",
    r"exekutier(?:e|t)",
    r"ruf(?:e|t)?\s+.{0,40}\s*auf\b",
]
_EXEC_VERBS_EN = [
    r"\bexecute\s+(?:the\s+)?(?:script|tool|command|file)",
    r"\brun\s+(?:the\s+)?(?:script|tool|command|file|code)",
    r"\binvoke\s+",
    r"\blaunch\s+(?:the\s+)?",
]

# ── Explizite Python-Aufrufe ──────────────────────────────────────────────
_PYTHON_PATTERNS = [
    r"python3?\s+\S+\.py",
    r"\.py\b.{0,20}(?:aus|execute|run|start)",
]

# ── Explizite Shell-Kommandos im Fence ────────────────────────────────────
_SHELL_FENCE = re.compile(
    r"```(?:bash|sh|shell|zsh|console)\s*\n(.+?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# ── Lese-Absicht (soll NICHT als Execution gelten) ────────────────────────
_READ_VERBS = [
    r"\blies\b", r"\bles(?:e|t)\b", r"\bzeig(?:e|t)?\b",
    r"\bread\b", r"\bshow\b", r"\bdisplay\b", r"\binspect\b",
    r"\banalysier(?:e|t)", r"\banalyz(?:e|s)\b",
    r"\bbeschreib(?:e|t)", r"\bdescribe\b",
]

# ── Pfad-Heuristik ────────────────────────────────────────────────────────
_PATH_RE = re.compile(
    r"(?:[~/]|\.{1,2}/)[\w\-./]+\.\w{1,6}"   # ~/x.py, /tmp/a.sh, ./foo.py
    r"|`([^`]+\.\w{1,6})`"                    # `foo.py` in Backticks
)


def _extract_path(text: str) -> Optional[str]:
    m = _PATH_RE.search(text)
    if not m:
        return None
    return m.group(1) or m.group(0)


def _any_match(patterns: list[str], text: str) -> Optional[str]:
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def detect_execution_intent(task_text: str) -> ExecutionIntent:
    """
    Analysiert Task-Text auf Execution-Absicht.

    Rückgabe: ExecutionIntent mit kind/target/confidence.
      kind="unknown" + confidence=0 → keine klare Execution-Absicht erkannt.

    Strategie (von spezifisch nach grob):
      1. ```shell/bash Fence → shell (0.95)
      2. "python X.py" / ".py ausführen" → python (0.9)
      3. Execution-Verb + Pfad → shell/python je nach Endung (0.85)
      4. Execution-Verb ohne Pfad → unknown mit moderater Confidence (0.5)
      5. Reines Lese-Verb → kind="read" (explizit, damit Guard durchlässt)
    """
    if not task_text or not isinstance(task_text, str):
        return ExecutionIntent("unknown", None, 0.0, "")

    text = task_text.strip()

    # 1. Shell-Fence
    fence = _SHELL_FENCE.search(text)
    if fence:
        return ExecutionIntent(
            kind="shell",
            target=None,
            confidence=0.95,
            matched_phrase=fence.group(0)[:80],
        )

    # 2. Explizites "python X.py"
    py_match = _any_match(_PYTHON_PATTERNS, text)
    if py_match:
        return ExecutionIntent(
            kind="python",
            target=_extract_path(text),
            confidence=0.9,
            matched_phrase=py_match,
        )

    # 3. Execution-Verb + Pfad
    exec_match = _any_match(_EXEC_VERBS_DE + _EXEC_VERBS_EN, text)
    if exec_match:
        target = _extract_path(text)
        if target:
            kind: IntentKind = "python" if target.endswith(".py") else "shell"
            return ExecutionIntent(
                kind=kind,
                target=target,
                confidence=0.85,
                matched_phrase=exec_match,
            )
        # Execution-Verb ohne Pfad → schwache Confidence
        return ExecutionIntent(
            kind="shell",
            target=None,
            confidence=0.5,
            matched_phrase=exec_match,
        )

    # 4. Lese-Verb (explizit durchlassen)
    read_match = _any_match(_READ_VERBS, text)
    if read_match:
        return ExecutionIntent(
            kind="read",
            target=_extract_path(text),
            confidence=0.7,
            matched_phrase=read_match,
        )

    return ExecutionIntent("unknown", None, 0.0, "")


def is_execution_intent(task_text: str, threshold: float = 0.7) -> bool:
    """
    Convenience: True wenn Execution-Absicht (shell/python) mit Confidence >= threshold.
    "read" zählt nicht als Execution (wird durchgelassen).
    """
    intent = detect_execution_intent(task_text)
    return intent.kind in ("shell", "python") and intent.confidence >= threshold
