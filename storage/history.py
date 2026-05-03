"""
storage/history.py — Chat-History via SQLite (MessageDB).

API:
- get_messages(agent_id, limit=MAX_HISTORY_PER_AGENT)
- get_all_history() — dict[agent_id, list[msg]] (für Backup-/Reads die alles brauchen)
- append_message(agent_id, role, content, image="", skill_used="", ts=None)
- clear_messages(agent_id)

Truncation auf MAX_HISTORY_PER_AGENT erfolgt beim Schreiben.
Content > MAX_CONTENT_LENGTH wird mit "[…]"-Marker abgeschnitten.
data:image/...-Strings werden über storage.files.persist_image_field auf Disk geschrieben.
"""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlmodel import delete, select

from config.settings import settings
from core.state import _history_lock
from storage.database import MessageDB, get_session
from storage.files import persist_image_field

MAX_HISTORY_PER_AGENT = settings.MAX_HISTORY_PER_AGENT
MAX_CONTENT_LENGTH    = settings.MAX_CONTENT_LENGTH

logger = logging.getLogger(__name__)


def _row_to_dict(m: MessageDB) -> dict:
    d = {"role": m.role, "content": m.content, "ts": m.ts}
    if m.image:
        d["image"] = m.image
    if m.skill_used:
        d["skill_used"] = m.skill_used
    return d


def _truncate_content(s: str) -> str:
    if isinstance(s, str) and len(s) > MAX_CONTENT_LENGTH:
        return s[:MAX_CONTENT_LENGTH] + " […]"
    return s


def get_messages(agent_id: str, limit: Optional[int] = None) -> list[dict]:
    """Last-N messages für Agent in chronologischer Reihenfolge (älteste → neueste)."""
    if limit is None:
        limit = MAX_HISTORY_PER_AGENT
    with get_session() as session:
        # Order by id DESC, take limit, then reverse — id ist PK und monoton steigend.
        rows = session.exec(
            select(MessageDB)
            .where(MessageDB.agent_id == agent_id)
            .order_by(MessageDB.id.desc())
            .limit(limit)
        ).all()
    return [_row_to_dict(m) for m in reversed(rows)]


def get_all_history() -> dict:
    """Komplettes dict[agent_id, list[msg]] — nur für Backup/Export."""
    out: dict = {}
    with get_session() as session:
        rows = session.exec(select(MessageDB).order_by(MessageDB.id)).all()
    for m in rows:
        out.setdefault(m.agent_id, []).append(_row_to_dict(m))
    # Truncation pro Agent (für Backup-Konsistenz)
    for aid, msgs in list(out.items()):
        if len(msgs) > MAX_HISTORY_PER_AGENT:
            out[aid] = msgs[-MAX_HISTORY_PER_AGENT:]
    return out


def append_message(
    agent_id: str,
    role: str,
    content: str,
    *,
    image: str = "",
    skill_used: str = "",
    ts: Optional[str] = None,
) -> None:
    """Hängt eine Message an die History des Agenten an.

    - Truncation: Content gekürzt auf MAX_CONTENT_LENGTH.
    - Bilder: data-URIs werden zu /static/...-Pfad konvertiert.
    - Auto-Cleanup: Älteste Messages über MAX_HISTORY_PER_AGENT werden gelöscht.
    """
    content = _truncate_content(content or "")
    image_val = persist_image_field(image, name_hint=f"{agent_id}_{role}") if image else ""
    ts_val = ts or datetime.now().isoformat()

    with _history_lock, get_session() as session:
        session.add(MessageDB(
            agent_id=agent_id,
            role=role,
            content=content,
            ts=ts_val,
            skill_used=skill_used or "",
            image=image_val,
        ))
        session.commit()

        # Auto-Truncate: behalte nur die letzten MAX_HISTORY_PER_AGENT pro Agent.
        # Cheap: COUNT erst, nur wenn überschritten DELETE.
        count_stmt = select(func.count()).select_from(MessageDB).where(MessageDB.agent_id == agent_id)
        n = session.exec(count_stmt).one()
        if n > MAX_HISTORY_PER_AGENT:
            # IDs der ältesten (n - MAX) Einträge holen und löschen.
            overflow = n - MAX_HISTORY_PER_AGENT
            stale_ids = session.exec(
                select(MessageDB.id)
                .where(MessageDB.agent_id == agent_id)
                .order_by(MessageDB.id)
                .limit(overflow)
            ).all()
            if stale_ids:
                session.exec(
                    delete(MessageDB).where(MessageDB.id.in_(stale_ids))  # type: ignore[attr-defined]
                )
                session.commit()


def clear_messages(agent_id: str) -> None:
    """Alle Messages eines Agenten löschen."""
    with _history_lock, get_session() as session:
        session.exec(delete(MessageDB).where(MessageDB.agent_id == agent_id))  # type: ignore[attr-defined]
        session.commit()


# ── Kompat-Layer für noch-nicht-migrierte Aufrufer ────────────────────────────

def load_history() -> dict:
    """DEPRECATED: nutze get_messages(agent_id) oder get_all_history()."""
    return get_all_history()


def save_history(history: dict) -> None:
    """DEPRECATED: nutze append_message() / clear_messages().

    Diese Implementierung ist absichtlich kein No-Op, sondern macht einen Diff:
    Für jeden Agenten werden bestehende Messages gelöscht und der neue Stand
    geschrieben. Sehr teuer — nur als Übergangs-Sicherheitsnetz, alle echten
    Aufrufer sollten migriert werden.
    """
    logger.warning("save_history (Kompat-Pfad) aufgerufen — bitte append_message/clear_messages nutzen")
    with _history_lock, get_session() as session:
        for agent_id, msgs in history.items():
            session.exec(delete(MessageDB).where(MessageDB.agent_id == agent_id))  # type: ignore[attr-defined]
            for m in msgs[-MAX_HISTORY_PER_AGENT:]:
                content = _truncate_content(m.get("content", ""))
                img = m.get("image") or ""
                if img:
                    img = persist_image_field(img, name_hint=f"{agent_id}_{m.get('role','?')}")
                session.add(MessageDB(
                    agent_id=agent_id,
                    role=m.get("role", "user"),
                    content=content,
                    ts=m.get("ts", datetime.now().isoformat()),
                    skill_used=m.get("skill_used", "") or "",
                    image=img,
                ))
        session.commit()
