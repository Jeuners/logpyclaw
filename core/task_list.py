"""
core/task_list.py — Parser für den [tasklist]...[/tasklist] Tool-Aufruf.

Der LLM schreibt KEINE JSON — er benutzt eine einfache Zeilensyntax:

    [tasklist]
    Picasso: Butterfly Bild 1 — Monarchfalter, Flügel ausgebreitet...
    Picasso: Butterfly Bild 2 — gleicher Falter im Flug [after: 0]
    Jan: Referenzbilder suchen [parallel]
    [/tasklist]

Format pro Zeile:  AgentName: Task-Beschreibung [optionale flags]
Flags:  [after: N]      wartet auf Zeile N (0-basiert)
        [parallel]      startet sofort, kein auto-sequenziell
        [priority: N]   1–10, Standard 5

Die API unter GET /api/tools/tasklist beschreibt dieses Format selbst.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from core.time_provider import TimeProvider

logger = logging.getLogger(__name__)

# Findet den [tasklist]...[/tasklist] Block (case-insensitive)
_BLOCK_RX = re.compile(
    r"\[tasklist\]\s*([\s\S]*?)\s*\[/tasklist\]",
    re.IGNORECASE,
)

# Zeilen-Parser — akzeptiert beides:
#   "AgentName: Task-Text [flags]"   (mit Doppelpunkt, @-Präfix optional)
#   "@AgentName Task-Text [flags]"   (ohne Doppelpunkt, @-Präfix Pflicht)
_LINE_COLON_RX = re.compile(
    r"^@?([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{0,39}?)\s*:\s*(.+)$",
    re.UNICODE,
)
_LINE_AT_RX = re.compile(
    r"^@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\-]{0,39})\s+(.+)$",
    re.UNICODE,
)

# Flag-Parser innerhalb der Task-Beschreibung
# [after: N] oder [after: N,M,...] — eine oder mehrere Abhängigkeiten
_FLAG_AFTER_RX    = re.compile(r"\[after\s*:\s*(\d+(?:\s*,\s*\d+)*)\]", re.IGNORECASE)
_FLAG_PARALLEL_RX = re.compile(r"\[parallel\]", re.IGNORECASE)
_FLAG_PRIORITY_RX = re.compile(r"\[priority\s*:\s*(\d+)\]", re.IGNORECASE)


@dataclass
class TaskItem:
    """Ein einzelner Task aus einer [tasklist]."""
    line_index: int         # Position im Block (0-basiert, für [after: N])
    recipient_id: str
    recipient_name: str
    task_text: str
    sender_id: str = ""
    sender_name: str = ""
    after_lines: list[int] = field(default_factory=list)  # leer = kein explizites after
    priority: int = 5
    parallel: bool = False
    images: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    attachment_path: str = ""

    def to_task_dict(
        self,
        system_task_id: str = "",
        depends_on: list | None = None,
        time_provider: Optional[TimeProvider] = None,
    ) -> dict:
        """Konvertiert den Task in das Dispatch-Dict.

        Wenn ``time_provider`` übergeben wird, werden zusätzlich Eigenzeit-Felder
        (§4.3 — reference_now, parent_reference_now, dilation_factor, frame_id)
        gesetzt. Ohne Provider verhält sich die Methode wie bisher.
        """
        wall_now = datetime.now()
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
            "created_at": wall_now.isoformat(),
            "completed_at": None,
            "timeout_at": (wall_now + timedelta(seconds=1210)).isoformat(),
            "delegation_depth": 1,
            "priority": self.priority,
            "depends_on": depends_on or [],
        }
        if time_provider is not None:
            frame = time_provider.frame
            d["reference_now"] = time_provider.now().isoformat()
            d["parent_reference_now"] = (
                frame.parent_reference_now.isoformat()
                if frame.parent_reference_now is not None else None
            )
            d["dilation_factor"] = frame.dilation_factor
            d["frame_id"] = frame.frame_id
        if self.images:
            d["images"] = self.images
        if self.audio:
            d["audio"] = self.audio
        if self.attachment_path:
            d["attachment_path"] = self.attachment_path
        return d


def has_task_list(reply: str) -> bool:
    """Schnellprüfung ob ein Reply einen [tasklist]-Block enthält."""
    return bool(_BLOCK_RX.search(reply))


def strip_task_list(reply: str) -> str:
    """Entfernt den [tasklist]-Block aus dem Reply für saubere Chat-Anzeige."""
    cleaned = _BLOCK_RX.sub("", reply).strip()
    return re.sub(r"\n{3,}", "\n\n", cleaned)


def parse_task_list(
    reply: str,
    sender_agent: dict,
    all_agents: list[dict],
    delegation_depth: int = 0,
) -> list[TaskItem]:
    """
    Parsed einen [tasklist]-Block aus einem LLM-Reply.

    Format pro Zeile:
        AgentName: Task-Beschreibung [after: N] [parallel] [priority: N]

    Gibt geordnete Liste von TaskItem-Objekten zurück.
    Leere Liste wenn kein Block oder alle Zeilen ungültig.
    """
    m = _BLOCK_RX.search(reply)
    if not m:
        return []

    block = m.group(1)
    name_map = {a["name"].lower(): a for a in all_agents if a.get("name")}
    sender_id = sender_agent.get("id", "")

    items: list[TaskItem] = []
    line_index = 0

    # Tasks können mehrzeilig sein — ein Task startet an einer Zeile, die
    # mit "@Name" oder "Name:" beginnt, und umfasst alle Folgezeilen bis
    # zum nächsten solchen Start (oder Block-Ende).
    # Wir sammeln Chunks und verarbeiten sie dann gemeinsam.
    raw_chunks: list[str] = []
    current: list[str] = []
    lines = block.splitlines()

    def _looks_like_task_start(s: str) -> bool:
        t = s.strip()
        if not t or t.startswith("#"):
            return False
        # Zeilen wie "```" oder Markdown-Header ignorieren
        if t.startswith("```") or t.startswith("**") or t.startswith("*") or t.startswith("-"):
            return False
        # @Name ... ODER Name: ...
        if _LINE_AT_RX.match(t):
            return True
        m2 = _LINE_COLON_RX.match(t)
        if m2:
            # Name muss ein bekannter Agent sein — vermeidet False-Positives
            # bei Zeilen wie "Projekt-Pfad: ..." oder "Farbpalette: ..."
            name = m2.group(1).strip().lower()
            return name in name_map or _find_agent(name, name_map) is not None
        return False

    for raw_line in lines:
        if _looks_like_task_start(raw_line):
            if current:
                raw_chunks.append("\n".join(current).strip())
                current = []
            current.append(raw_line)
        else:
            if current:
                current.append(raw_line)
            # Wenn kein aktueller Chunk offen ist (Vorspann), ignorieren.
    if current:
        raw_chunks.append("\n".join(current).strip())

    for chunk in raw_chunks:
        # Erste Zeile enthält @Name / Name:, Rest ist Fortsetzung der Task
        first_line, _, rest = chunk.partition("\n")
        first_line = first_line.strip()

        # @-Prefix → @Name-Regex bevorzugen (Colon-Regex erlaubt Spaces im Namen
        # und würde sonst URLs wie "https://..." als Trenner missdeuten, weil
        # "Recon youtube download https" als Name + "://..." als Task durchgeht).
        if first_line.lstrip().startswith("@"):
            lm = _LINE_AT_RX.match(first_line) or _LINE_COLON_RX.match(first_line)
        else:
            lm = _LINE_COLON_RX.match(first_line) or _LINE_AT_RX.match(first_line)
        if not lm:
            logger.debug("TASKLIST: Chunk übersprungen: %r", first_line[:60])
            continue

        agent_name_raw = lm.group(1).strip()
        # Task-Text = Rest der ersten Zeile + alle Folgezeilen
        task_first = lm.group(2).strip()
        task_raw = (task_first + ("\n" + rest if rest else "")).strip()

        # Flags extrahieren
        after_m    = _FLAG_AFTER_RX.search(task_raw)
        prio_m     = _FLAG_PRIORITY_RX.search(task_raw)
        parallel   = bool(_FLAG_PARALLEL_RX.search(task_raw))
        after_lines: list[int] = []
        if after_m:
            after_lines = [int(x.strip()) for x in after_m.group(1).split(",") if x.strip().isdigit()]
        # Self-Referenz filtern: [after: N] auf der eigenen Zeile N ist Unsinn.
        # Das LLM nutzt die Flag manchmal als Label ihrer eigenen Zeile statt als Dep.
        after_lines = [a for a in after_lines if a != line_index]
        priority   = int(prio_m.group(1)) if prio_m else 5

        # Flags aus Task-Text entfernen
        task_text = task_raw
        task_text = _FLAG_AFTER_RX.sub("", task_text)
        task_text = _FLAG_PARALLEL_RX.sub("", task_text)
        task_text = _FLAG_PRIORITY_RX.sub("", task_text)
        task_text = task_text.strip().rstrip("—-–,;").strip()

        if len(task_text) < 10:
            logger.debug("TASKLIST: Zeile %d — Task zu kurz (%d Zeichen)", line_index, len(task_text))
            line_index += 1
            continue

        # Agent finden
        target = _find_agent(agent_name_raw, name_map)
        if not target:
            logger.warning("TASKLIST: Zeile %d — Agent '%s' nicht gefunden", line_index, agent_name_raw)
            line_index += 1
            continue

        # Selbstreferenz verhindern
        if target["id"] == sender_id:
            logger.debug("TASKLIST: Zeile %d — Selbstreferenz für '%s'", line_index, agent_name_raw)
            line_index += 1
            continue

        items.append(TaskItem(
            line_index=line_index,
            recipient_id=target["id"],
            recipient_name=target["name"],
            task_text=task_text,
            sender_id=sender_id,
            sender_name=sender_agent.get("name", ""),
            after_lines=after_lines,
            priority=max(1, min(10, priority)),
            parallel=parallel,
        ))
        logger.info("TASKLIST: Zeile %d → @%s: '%s...'", line_index, target["name"], task_text[:60])
        line_index += 1

    logger.info("TASKLIST: %d Tasks geparsed", len(items))
    return items


def _find_agent(raw_name: str, name_map: dict) -> dict | None:
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
