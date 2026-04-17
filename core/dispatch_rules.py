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

# Skills die parallel ausgeführt werden können (externe Queue, keine Kette nötig).
# Muss synchron zu ``services.task_service.PARALLEL_SAFE_SKILLS`` bleiben.
PARALLEL_SAFE_SKILLS: frozenset[str] = frozenset({"image_gen", "video_gen", "image_edit"})


def redirect_for_images(
    recipient_id: str,
    recipient_name: str,
    all_agents: list[dict],
    log_tag: str = "A2A",
) -> Optional[tuple[str, str]]:
    """
    Prüft ob der Empfänger ein bildfähiges Skill hat. Falls nicht und ein
    Agent mit ``image_edit`` existiert, wird dessen ``(id, name)`` zurückgegeben.

    Returns:
        ``(new_id, new_name)`` wenn umgeleitet werden soll, sonst ``None``.
    """
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
