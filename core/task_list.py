"""
core/task_list.py — Strukturierte Aufgabenliste für Agenten.

Agenten können einen [TASKLIST]...[/TASKLIST]-Block in ihre Antwort einbetten.
Das System parst ihn und erstellt korrekt verkettete Tasks — ohne fragiles
@Mention-Parsing.

Format:
    [TASKLIST]
    [
      {"to": "Picasso", "task": "Generiere Bild 1: ...", "id": "b1"},
      {"to": "Picasso", "task": "Generiere Bild 2: ...", "after": "b1"},
      {"to": "Jan",     "task": "Suche Referenzen",     "parallel": true},
      {"to": "Picasso", "task": "Generiere Bild 3: ...", "after": "b2", "priority": 7}
    ]
    [/TASKLIST]

Felder:
    to        (str, Pflicht)  Agent-Name (fuzzy matching wie @Mentions)
    task      (str, Pflicht)  Aufgabenbeschreibung (mind. 10 Zeichen)
    id        (str, opt.)     Lokale ID zum Referenzieren via "after"
    after     (str, opt.)     Lokale ID des Tasks, auf den gewartet wird
    priority  (int, opt.)     1-10, default 5
    parallel  (bool, opt.)    true = kein automatisches Warten auf vorherigen
                              Task desselben Agenten (sonst immer sequenziell)
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Regex zum Finden des TASKLIST-Blocks
_TASKLIST_RX = re.compile(
    r"\[TASKLIST\]\s*([\s\S]*?)\s*\[/TASKLIST\]",
    re.IGNORECASE,
)


@dataclass
class TaskItem:
    """Ein einzelner Task aus einer TASKLIST."""
    local_id: str           # Lokale ID (aus JSON oder auto-generiert)
    recipient_id: str
    recipient_name: str
    task_text: str
    sender_id: str = ""
    sender_name: str = ""
    after: str = ""         # Lokale ID des Vorgängers
    priority: int = 5
    parallel: bool = False
    images: list = field(default_factory=list)
    attachment_path: str = ""

    def to_task_dict(self, system_task_id: str = "", depends_on: list | None = None) -> dict:
        """Konvertiert zu Task-Dict für TaskService.enqueue()."""
        now = datetime.now()
        d = {
            "id": system_task_id or str(uuid.uuid4()),
            "sender_agent_id": self.sender_id,
            "sender_agent_name": self.sender_name,
            "recipient_agent_id": self.recipient_id,
            "recipient_agent_name": self.recipient_name,
            "message": self.task_text,
            "skill_used": None,
            "result_text": None,
            "result_image": None,
            "error": None,
            "created_at": now.isoformat(),
            "completed_at": None,
            "timeout_at": (now + timedelta(seconds=1210)).isoformat(),
            "delegation_depth": 1,
            "priority": self.priority,
            "depends_on": depends_on or [],
        }
        if self.images:
            d["images"] = self.images
        if self.attachment_path:
            d["attachment_path"] = self.attachment_path
        return d


def parse_task_list(
    reply: str,
    sender_agent: dict,
    all_agents: list[dict],
    delegation_depth: int = 0,
) -> list[TaskItem]:
    """
    Parsed einen [TASKLIST]-Block aus einem LLM-Reply.

    Gibt eine geordnete Liste von TaskItem-Objekten zurück.
    Liefert [] wenn kein Block gefunden oder der Block leer/ungültig ist.
    """
    m = _TASKLIST_RX.search(reply)
    if not m:
        return []

    raw_json = m.group(1).strip()
    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("TASKLIST: Ungültiges JSON — %s\n%s", e, raw_json[:200])
        return []

    if not isinstance(items, list):
        logger.warning("TASKLIST: Erwartet JSON-Array, bekam %s", type(items).__name__)
        return []

    # Name-Map für Agent-Lookup
    name_map = {a["name"].lower(): a for a in all_agents if a.get("name")}
    sender_id = sender_agent.get("id", "")
    circuit_depth = delegation_depth + 1

    result: list[TaskItem] = []

    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            logger.debug("TASKLIST: Item %d kein dict — übersprungen", i)
            continue

        to_name = str(raw.get("to", "")).strip()
        task_text = str(raw.get("task", "")).strip()
        local_id = str(raw.get("id", f"_t{i}")).strip()
        after = str(raw.get("after", "")).strip()
        priority = int(raw.get("priority", 5))
        parallel = bool(raw.get("parallel", False))

        if not to_name or not task_text:
            logger.debug("TASKLIST: Item %d fehlt 'to' oder 'task' — übersprungen", i)
            continue

        if len(task_text) < 10:
            logger.debug("TASKLIST: Item %d task zu kurz (%d Zeichen) — übersprungen",
                         i, len(task_text))
            continue

        # Agent finden (fuzzy)
        target = _find_agent(to_name, name_map)
        if not target:
            logger.warning("TASKLIST: Agent '%s' nicht gefunden — übersprungen", to_name)
            continue

        # Selbstreferenz verhindern
        if target["id"] == sender_id:
            logger.debug("TASKLIST: Selbstreferenz für '%s' — übersprungen", to_name)
            continue

        result.append(TaskItem(
            local_id=local_id,
            recipient_id=target["id"],
            recipient_name=target["name"],
            task_text=task_text,
            sender_id=sender_id,
            sender_name=sender_agent.get("name", ""),
            after=after,
            priority=max(1, min(10, priority)),
            parallel=parallel,
        ))
        logger.info("TASKLIST: Item %d → @%s: '%s...'", i, target["name"], task_text[:60])

    logger.info("TASKLIST: %d von %d Items valide", len(result), len(items))
    return result


def strip_task_list(reply: str) -> str:
    """Entfernt den [TASKLIST]-Block aus dem Reply für saubere Chat-Anzeige."""
    cleaned = _TASKLIST_RX.sub("", reply).strip()
    # Mehrfach-Leerzeilen bereinigen
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def has_task_list(reply: str) -> bool:
    """Schnellprüfung ob ein Reply einen TASKLIST-Block enthält."""
    return bool(_TASKLIST_RX.search(reply))


def _find_agent(raw_name: str, name_map: dict) -> dict | None:
    """Findet einen Agenten per Name (exakt, normalisiert, startswith, contains)."""
    lower = raw_name.lower()
    if lower in name_map:
        return name_map[lower]
    normalized = _normalize(lower)
    for k, v in name_map.items():
        if _normalize(k) == normalized:
            return v
    candidates = [v for k, v in name_map.items() if k.startswith(lower) or lower.startswith(k)]
    if len(candidates) == 1:
        return candidates[0]
    if len(lower) >= 3:
        contains = [v for k, v in name_map.items() if lower in k or k in lower]
        if len(contains) == 1:
            return contains[0]
    return None


def _normalize(name: str) -> str:
    return (
        name.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("ß", "ss").replace("-", "").replace("_", "").replace(" ", "")
    )
