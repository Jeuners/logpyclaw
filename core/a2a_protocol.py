"""
core/a2a_protocol.py — Strukturiertes A2A-Kommunikationsprotokoll.

Ersetzt fragiles Regex-Parsing in chat_service._dispatch_mentions().
Vorteile:
  - Alle @Mentions in einem Reply werden gefunden (nicht nur erste)
  - Task-Text wird korrekt extrahiert (bis zur nächsten Mention oder Ende)
  - Kein 500-Zeichen-Limit mehr
  - Fuzzy-Matching für Agent-Namen mit Umlauten/Leerzeichen
  - Selbstreferenz-Schutz (Agent kann sich nicht selbst dispatchen)
  - Optionale Qdrant-basierte Capability-Validation
"""
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import uuid

logger = logging.getLogger(__name__)

# Robustere Regex: Leerzeichen im Namen erlaubt, Trailing-Satzzeichen abschneiden
_MENTION_RX = re.compile(
    r"@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{0,39}?)(?=\s|$|[,.:!?;]|\n)",
    re.UNICODE,
)


@dataclass
class A2ADispatch:
    """Ein strukturierter A2A-Task-Dispatch."""
    recipient_id: str
    recipient_name: str
    task_text: str
    sender_id: str = ""
    sender_name: str = ""
    priority: int = 5           # 0=niedrig, 10=kritisch; User-Chat=8, Heartbeat=3
    delegation_depth: int = 1
    timeout_secs: int = 1210
    images: list = field(default_factory=list)        # base64-Bilder weitergeben
    audio: list = field(default_factory=list)          # base64-Audio (MP3) weitergeben
    attachment_path: str = ""                          # Dateipfad weitergeben
    metadata: dict = field(default_factory=dict)

    def to_task_dict(self) -> dict:
        """Konvertiert zu Task-Dict kompatibel mit TaskService.enqueue()."""
        now = datetime.now()
        d = {
            "id": str(uuid.uuid4()),
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
            "timeout_at": (now + timedelta(seconds=self.timeout_secs)).isoformat(),
            "delegation_depth": self.delegation_depth,
            "priority": self.priority,
        }
        if self.images:
            d["images"] = self.images
        if self.audio:
            d["audio"] = self.audio
        if self.attachment_path:
            d["attachment_path"] = self.attachment_path
        return d


def parse_a2a_dispatches(
    reply: str,
    sender_agent: dict,
    all_agents: list[dict],
    sender_delegation_depth: int = 0,
) -> list[A2ADispatch]:
    """
    Parsed alle @Mentions aus einem LLM-Reply zu strukturierten A2A-Dispatches.

    Verbesserungen gegenüber altem Regex-Parsing:
    - Findet ALLE Mentions im Reply (nicht nur erste)
    - Task-Text geht bis zur nächsten Mention (kein 500-char-Limit)
    - Fuzzy-Matching für Namen mit Umlauten/Sonderzeichen
    - Selbstreferenz wird ignoriert
    - Markdown-Tabellenzellen und Code-Blöcke werden IGNORIERT
    - Kein Fallback auf ganzen Reply wenn task_text zu kurz (→ Skip statt falscher Task)

    Args:
        reply: Vollständiger LLM-Reply-Text
        sender_agent: Der Agent der die Antwort gegeben hat
        all_agents: Liste aller bekannten Agenten
        sender_delegation_depth: Aktuelle Delegationstiefe des Senders

    Returns:
        Liste von A2ADispatch-Objekten (ohne Duplikate)
    """
    if not reply or not all_agents:
        return []

    # Tabellenzellen + Code-Blöcke maskieren, damit @Mentions darin nicht dispatcht werden
    safe_reply = _mask_table_and_code(reply)

    matches = list(_MENTION_RX.finditer(safe_reply))
    if not matches:
        return []

    # Name-Map aufbauen: lowercase name → agent dict
    name_map = _build_name_map(all_agents)
    sender_id = sender_agent.get("id", "")

    dispatches: list[A2ADispatch] = []
    seen_self_refs: set[str] = set()  # nur für Selbstreferenz-Schutz

    for i, m in enumerate(matches):
        raw_name = m.group(1).strip().rstrip(",.;:!?")
        target = _find_agent(raw_name, name_map)

        if not target:
            logger.debug("A2A: Kein Agent gefunden für '@%s'", raw_name)
            continue

        # Selbstreferenz ignorieren
        if target.get("id") == sender_id:
            logger.debug("A2A: Selbstreferenz ignoriert für '%s'", raw_name)
            continue

        # Task-Text: von nach dem @Mention bis vor der nächsten Mention oder Ende
        task_start = m.end()
        task_end = matches[i + 1].start() if i + 1 < len(matches) else len(safe_reply)
        task_text = safe_reply[task_start:task_end].strip()

        # Führende Satzzeichen/Bindestriche entfernen
        task_text = re.sub(r"^[\s:,\-–—|]+", "", task_text).strip()

        # KEIN Fallback auf ganzen Reply — zu kurzer Task-Text = falscher Trigger → überspringen
        if len(task_text) < 10:
            logger.debug(
                "A2A: Zu kurzer Task-Text (%d Zeichen) für '@%s' — kein Dispatch",
                len(task_text), raw_name,
            )
            continue

        dispatch = A2ADispatch(
            recipient_id=target["id"],
            recipient_name=target["name"],
            task_text=task_text,
            sender_id=sender_id,
            sender_name=sender_agent.get("name", ""),
            priority=5,
            delegation_depth=sender_delegation_depth + 1,
        )
        dispatches.append(dispatch)
        logger.info(
            "A2A-Dispatch geplant: @%s ← '%s...'",
            target["name"], task_text[:60]
        )

    return dispatches


def _mask_table_and_code(text: str) -> str:
    """
    Ersetzt Markdown-Tabellenzellen und Code-Block-Inhalte durch Leerzeichen
    gleicher Länge, damit @Mentions darin NICHT als Dispatches erkannt werden.
    Positionen bleiben erhalten (wichtig für m.start()/m.end() der Regex-Matches).
    """
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    in_code = False
    for line in lines:
        stripped = line.strip()
        # Code-Block toggle
        if stripped.startswith("```"):
            in_code = not in_code
            # Die ``` Zeile selbst maskieren (Länge beibehalten)
            eol = "\n" if line.endswith("\n") else ""
            result.append(" " * (len(line) - len(eol)) + eol)
            continue
        # Code-Block-Inhalt oder Markdown-Tabellenzelle → alles maskieren
        if in_code or stripped.startswith("|"):
            eol = "\n" if line.endswith("\n") else ""
            result.append(" " * (len(line) - len(eol)) + eol)
        else:
            result.append(line)
    return "".join(result)


def _build_name_map(agents: list[dict]) -> dict[str, dict]:
    """Erstellt eine Lookup-Map: lowercase_name → agent_dict."""
    return {a["name"].lower(): a for a in agents if a.get("name")}


def _find_agent(raw_name: str, name_map: dict[str, dict]) -> Optional[dict]:
    """
    Findet einen Agenten anhand seines Namens — mit mehreren Fallback-Strategien.

    Reihenfolge:
    1. Exakter Match (case-insensitive)
    2. Normalisierter Match (Umlaute → ae/oe/ue)
    3. Startswith-Match (Kurzname)
    4. Enthält-Match (Teilstring)
    """
    lower = raw_name.lower()

    # 1. Exakt
    if lower in name_map:
        return name_map[lower]

    # 2. Normalisiert (Umlaute)
    normalized = _normalize(lower)
    for k, v in name_map.items():
        if _normalize(k) == normalized:
            return v

    # 3. Startswith
    candidates = [v for k, v in name_map.items() if k.startswith(lower) or lower.startswith(k)]
    if len(candidates) == 1:
        return candidates[0]

    # 4. Enthält (nur wenn eindeutig)
    if len(lower) >= 3:
        contains = [v for k, v in name_map.items() if lower in k or k in lower]
        if len(contains) == 1:
            return contains[0]

    return None


def strip_a2a_for_display(reply: str) -> str:
    """
    Entfernt alle @Mention-Blöcke aus dem Reply für saubere Chat-Anzeige.

    Jeder Block geht von @AgentName bis zur nächsten @Mention (oder Ende).
    Gibt leeren String zurück wenn der gesamte Reply nur @Mentions war.

    Beispiel:
        "@Jan bitte lade das Video herunter"           → ""
        "OK ich helfe. @Jan lade das Video herunter"   → "OK ich helfe."
    """
    matches = list(_MENTION_RX.finditer(reply))
    if not matches:
        return reply

    # Baue maskierte Version: @Mention-Blöcke → leer
    result = list(reply)
    for i, m in enumerate(matches):
        block_start = m.start()
        block_end = matches[i + 1].start() if i + 1 < len(matches) else len(reply)
        for j in range(block_start, block_end):
            result[j] = ""

    # Leerzeilen/Whitespace bereinigen
    cleaned = " ".join("".join(result).split())
    return cleaned.strip()


def _normalize(name: str) -> str:
    """Normalisiert Umlaute und Sonderzeichen für Vergleiche."""
    return (
        name
        .replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("ß", "ss").replace("-", "").replace("_", "").replace(" ", "")
    )
