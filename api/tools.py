"""
api/tools.py — Selbstbeschreibende Tool-API für Agenten.

Agenten können diese Endpunkte abfragen um zu verstehen welche Tools
verfügbar sind und wie sie benutzt werden.

GET /api/tools              → alle verfügbaren Tools (kurz)
GET /api/tools/{name}       → ein Tool detailliert (Syntax + Beispiel)
GET /api/tools/{name}/schema → maschinenlesbare Feldbeschreibung
"""
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["tools"])

# ─── Tool-Definitionen ────────────────────────────────────────────────────────
# Jedes Tool beschreibt sich selbst: Syntax, Felder, Beispiel.
# Die Syntax-Beschreibung wird in den LLM-System-Prompt injiziert.

TOOLS: dict[str, dict] = {
    "tasklist": {
        "name": "tasklist",
        "description": (
            "Erstelle mehrere Tasks für Agenten — sequenziell oder parallel. "
            "Nutze dieses Tool wenn du Aufgaben an mehrere Agenten oder "
            "denselben Agenten in mehreren Schritten delegieren willst."
        ),
        "syntax": (
            "[tasklist]\n"
            "AgentName: Vollständige Task-Beschreibung\n"
            "AgentName: Nächster Task [after: 0]\n"
            "AgentName: Paralleler Task [parallel]\n"
            "[/tasklist]"
        ),
        "fields": {
            "AgentName": "Name des Ziel-Agenten (Pflicht)",
            "Task-Beschreibung": (
                "Vollständige, selbstständige Aufgabenbeschreibung — "
                "IMMER alle nötigen Details angeben, nie auf vorherige Tasks verweisen"
            ),
            "[after: N]": "Wartet bis Zeile N (0-basiert) abgeschlossen ist",
            "[parallel]": "Startet sofort, wartet nicht auf vorherigen Task desselben Agenten",
            "[priority: N]": "Priorität 1–10, Standard 5",
        },
        "rules": [
            "Jede Zeile ist genau ein Task",
            "Tasks ohne [after:] zum selben Agenten laufen automatisch sequenziell",
            "Jeder Task muss vollständig sein — kein 'wie zuvor' oder 'siehe oben'",
            "Für Bildserien: Charakter-/Stil-Beschreibung in JEDEM Task wiederholen",
        ],
        "example": (
            "[tasklist]\n"
            "Picasso: Schmetterling Bild 1 — Monarchfalter, Flügel ausgebreitet, "
            "sitzt auf roter Blume, sonnige Wiese, fotorealistisch\n"
            "Picasso: Schmetterling Bild 2 — gleicher Monarchfalter im Flug, "
            "Flügel in Bewegung, Bokeh-Hintergrund [after: 0]\n"
            "Jan: Suche Referenzbilder 'Monarchfalter Fotografie' [parallel]\n"
            "[/tasklist]"
        ),
    },
}


# ─── Endpunkte ────────────────────────────────────────────────────────────────

@router.get("/api/tools")
def list_tools():
    """Alle verfügbaren Tools — kurze Übersicht für Agenten."""
    return {
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "syntax_hint": t["syntax"].split("\n")[0],  # nur erste Zeile
                "url": f"/api/tools/{t['name']}",
            }
            for t in TOOLS.values()
        ]
    }


@router.get("/api/tools/{name}")
def describe_tool(name: str):
    """Vollständige Beschreibung eines Tools — Syntax, Felder, Beispiel."""
    tool = TOOLS.get(name.lower())
    if not tool:
        raise HTTPException(404, f"Tool '{name}' nicht gefunden. Verfügbar: {list(TOOLS.keys())}")
    return tool


@router.get("/api/tools/{name}/schema")
def tool_schema(name: str):
    """Maschinenlesbare Feldbeschreibung (für dynamische System-Prompt-Generierung)."""
    tool = TOOLS.get(name.lower())
    if not tool:
        raise HTTPException(404, f"Tool '{name}' nicht gefunden")
    return {
        "name": tool["name"],
        "fields": tool["fields"],
        "syntax": tool["syntax"],
    }


def get_tool_prompt_block() -> str:
    """
    Gibt den System-Prompt-Block für alle Tools zurück.
    Wird in _build_messages() injiziert — Agenten kennen so automatisch
    alle verfügbaren Tools und deren Syntax.
    """
    lines = ["--- TOOLS ---"]
    for tool in TOOLS.values():
        lines.append(f"\nTOOL: {tool['name']}")
        lines.append(f"Beschreibung: {tool['description']}")
        lines.append(f"Syntax:\n{tool['syntax']}")
        if tool.get("rules"):
            lines.append("Regeln:")
            for r in tool["rules"]:
                lines.append(f"  • {r}")
        lines.append(f"Beispiel:\n{tool['example']}")
    lines.append("\n--- END TOOLS ---")
    return "\n".join(lines)
