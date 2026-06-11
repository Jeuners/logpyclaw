"""
backend/skills/memory.py — Gedächtnis-Skill für das semantische Langzeit-Gedächtnis.

Gibt Martin (und jedem anderen Agenten) expliziten Zugriff auf SemanticMemory
(sqlite-vec). Operationen (automatisch erkannt):
  - stats:    "memory stats" / "statistik"
  - forget:   "vergiss eintrag 12"
  - remember: "merke dir: Dilles mag kurze Antworten"
  - recall:   alles andere → semantische Suche, z.B. "was weißt du über logpy?"
"""
from __future__ import annotations

import re

from backend.core.memory import GLOBAL, SemanticMemory
from backend.skills import Skill, SkillConfigField

# Floskeln am Anfang entfernen, damit nur der eigentliche Suchbegriff
# embedded wird ("schaue im memory nach logpy" → "logpy")
_LEAD_IN = re.compile(
    r"^(?:schau(?:e)?\s+(?:im|ins)\s+(?:memory|gedächtnis)(?:\s+nach)?"
    r"|(?:durch)?such(?:e)?\s+(?:im|das|dein)?\s*(?:memory|gedächtnis)(?:\s+nach)?"
    r"|was\s+weißt\s+du\s+(?:über|zu|von)"
    r"|erinnerst\s+du\s+dich\s+an"
    r"|recall)\s*[:,]?\s*",
    re.I,
)


class MemorySkill(Skill):
    skill_id = "memory"
    description = (
        "Semantisches Langzeit-Gedächtnis: durchsucht Erinnerungen über den "
        "Nutzer und seine Projekte (recall), speichert neue ('merke dir: …') "
        "und vergisst Einträge ('vergiss eintrag <id>')."
    )
    CONFIG_FIELDS = (
        SkillConfigField("scope", env="MEMORY_SKILL_SCOPE", default="agent:martin"),
    )

    def __init__(self, memory: SemanticMemory | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._memory = memory or SemanticMemory()

    async def execute(self, query: str) -> str:
        q = query.strip()
        if not q:
            return "[Memory] Leere Anfrage."
        try:
            if re.search(r"\b(stats|statistik|umfang|überblick)\b", q, re.I):
                return self._stats()

            m = re.search(r"\b(vergiss|lösche|forget|delete)\b\D*?(\d+)", q, re.I)
            if m:
                mem_id = int(m.group(2))
                ok = self._memory.forget(mem_id)
                return f"[Memory] Eintrag #{mem_id} " + ("vergessen." if ok else "nicht gefunden.")

            m = re.match(r"\s*(?:merke?\s+dir|speichere|notiere|remember)\b[:\s]+(.+)", q, re.I | re.S)
            if m:
                text = m.group(1).strip()
                scope = self.config["scope"] or GLOBAL
                rid = await self._memory.remember(text, kind="note", scope=scope)
                return f"[Memory] Gemerkt (#{rid}, {scope}): {text[:160]}"

            return await self._recall(q)
        except Exception as e:
            return f"[Memory] Fehler: {e}"

    async def _recall(self, q: str) -> str:
        query = _LEAD_IN.sub("", q).strip() or q
        scope = self.config["scope"] or GLOBAL
        scopes = [scope, GLOBAL] if scope != GLOBAL else [GLOBAL]
        hits = await self._memory.recall(query, k=5, scopes=scopes)
        if not hits:
            return f"[Memory] Keine Erinnerungen zu „{query}“ gefunden."
        lines = []
        for h in hits:
            text = h["text"].replace("\n", " ").strip()
            lines.append(f"- #{h['id']} (Score {h['score']:.2f}, {h['scope']}): {text[:300]}")
        return f"🧠 **{len(hits)} Erinnerungen** zu „{query}“\n\n" + "\n".join(lines)

    def _stats(self) -> str:
        s = self._memory.stats()
        scopes = ", ".join(f"{k}: {v}" for k, v in sorted(s["scopes"].items()))
        return (
            f"🧠 **Memory-Stats** — {s['count']} Einträge "
            f"({scopes}) · Modell {s['model']} · {s['db']}"
        )
