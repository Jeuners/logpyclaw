"""
core/dispatch_rules.py — Shared A2A dispatch rules.

Zentralisiert Routing-Entscheidungen die sowohl bei @Mention- als auch bei
TASKLIST-Dispatch greifen müssen. Bislang waren diese Regeln 2× dupliziert
in ``ChatService._dispatch_mentions`` und ``ChatService._dispatch_task_list``.
"""
from __future__ import annotations
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Skills die Bilder als Input konsumieren können.
# Hat der Ziel-Agent keines davon, wird ein A2A-Dispatch mit Bild an einen
# Agent mit ``image_edit`` umgeleitet.
IMAGE_CAPABLE_SKILLS: frozenset[str] = frozenset(
    {"image_edit", "talking_video", "video_gen", "image_gen"}
)

# Skills die parallel ausgeführt werden können (externe Queue oder kein shared state).
# Muss synchron zu ``services.task_service.PARALLEL_SAFE_SKILLS`` bleiben.
PARALLEL_SAFE_SKILLS: frozenset[str] = frozenset({
    "image_gen",    # ComfyUI externe Queue
    "video_gen",    # ComfyUI externe Queue
    "image_edit",   # ComfyUI externe Queue
    "file_access",  # reiner Disk-I/O, kein shared state
})


def redirect_for_images(
    recipient_id: str,
    recipient_name: str,
    all_agents: list[dict],
    log_tag: str = "A2A",
    message: str = "",
) -> Optional[tuple[str, str]]:
    """
    Prüft ob der Empfänger ein bildfähiges Skill hat. Falls nicht und ein
    Agent mit ``image_edit`` existiert, wird dessen ``(id, name)`` zurückgegeben.

    Guard (Bug C, 2026-05-14): Nur umleiten wenn die Task-Message tatsächlich
    Bild-Bezug hat. Ohne diesen Guard wurde jeder Dispatch mit Carry-Bild
    (auch Code/Text/WhatsApp-Aufgaben) blind zu @Image-Agent umgeleitet,
    sobald irgendein result_image vom Vor-Task durchgereicht wurde.

    Returns:
        ``(new_id, new_name)`` wenn umgeleitet werden soll, sonst ``None``.
    """
    if message:
        # Action-Verb DIREKT vor/nach Image-Noun gefordert. Sonst werden
        # Code-Aufgaben wie "HTML die das Bild anzeigt" oder "verbessere
        # das Design" fälschlich umgeleitet (Bug C-False-Positive).
        import re as _re
        _IMG_INTENT = _re.compile(
            r"\b(generier\w*|erzeug\w*|render\w*|mal\w*|zeichn\w*|illustrier\w*|"
            r"erstell\w*\s+(?:ein|einen|eine)?\s*(?:bild|foto|image|porträt|szene)|"
            r"generate|draw|paint|create\s+an?\s+(?:image|picture|photo)|"
            r"edit\w*|bearbei\w*|modifizier\w*|verwandle\w*|"
            r"upscale\w*|hochskalier\w*|vergröße?r\w*|enlarge\w*|"
            r"verbessere\s+(?:die|das)\s+(?:auflösung|qualität|bild|foto))"
            r".{0,80}\b(bild\w*|foto\w*|image|picture|photo\w*|porträt\w*|szene\w*|illustration|render|gemälde\w*|illustration|wallpaper|artwork|portrait)\b|"
            r"\b(bild\w*|foto\w*|image|picture|photo\w*|porträt\w*|szene\w*|portrait)\b.{0,80}\b"
            r"(generier\w*|erzeug\w*|render\w*|mal\w*|zeichn\w*|illustrier\w*|"
            r"edit\w*|bearbei\w*|modifizier\w*|upscale\w*|hochskalier\w*|"
            r"generate|draw|paint|edit|modify|upscale|enlarge)",
            _re.IGNORECASE,
        )
        if not _IMG_INTENT.search(message):
            return None

    recipient = next((a for a in all_agents if a.get("id") == recipient_id), {})
    if IMAGE_CAPABLE_SKILLS & set(recipient.get("skills", [])):
        return None

    edit_agent = next(
        (a for a in all_agents
         if "image_edit" in a.get("skills", []) and a["id"] != recipient_id),
        None,
    )
    if not edit_agent:
        return None

    logger.info(
        "%s Bild-Redirect: @%s hat kein image-fähiges Skill → umgeleitet zu @%s",
        log_tag, recipient_name, edit_agent["name"],
    )
    return edit_agent["id"], edit_agent["name"]


def is_parallel_safe(recipient_id: str, all_agents: list[dict], *, strict: bool = False) -> bool:
    """
    Prüft ob der Empfänger parallel dispatched werden darf.

    - ``strict=False`` (Default, @Mention-Dispatch): ein einziges parallel-safes Skill genügt.
    - ``strict=True`` (TASKLIST-Dispatch): alle Skills müssen parallel-safe sein.

    Die zwei Modi spiegeln das bestehende Verhalten wider (Multi-Skill-Agents mit
    nur einem parallel-safen Skill brauchen bei TASKLIST-Ketten weiter Reihenfolge).
    """
    recipient = next((a for a in all_agents if a.get("id") == recipient_id), {})
    skills = set(recipient.get("skills", []))
    if not skills:
        return False
    if strict:
        return skills.issubset(PARALLEL_SAFE_SKILLS)
    return bool(skills & PARALLEL_SAFE_SKILLS)
