"""
scripts/migrate_images_to_files.py — Einmalige Migration:
Base64-data-URIs in tasks.result_image und messages.image → Dateien unter
static/uploads/skills/. Spalten werden auf den /static/...-Pfad umgeschrieben.

Idempotent: Werte ohne 'data:' bleiben unangetastet.

Lauf: python -m scripts.migrate_images_to_files
"""
import logging
import os
import sys

# Repo-Root in sys.path damit core/, storage/ importierbar sind
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import select  # noqa: E402

from storage.database import MessageDB, TaskDB, get_session, init_db  # noqa: E402
from storage.files import is_data_uri, persist_data_uri  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("migrate_images")


def migrate_tasks() -> tuple[int, int]:
    converted = 0
    skipped   = 0
    with get_session() as session:
        tasks = session.exec(select(TaskDB).where(TaskDB.result_image != "")).all()
        log.info("Tasks mit result_image: %d", len(tasks))
        for t in tasks:
            if not is_data_uri(t.result_image):
                skipped += 1
                continue
            path = persist_data_uri(t.result_image, name_hint=t.id)
            if path:
                t.result_image = path
                session.add(t)
                converted += 1
            else:
                log.warning("Task %s: persist_data_uri lieferte None", t.id)
                skipped += 1
        session.commit()
    return converted, skipped


def migrate_messages() -> tuple[int, int]:
    converted = 0
    skipped   = 0
    with get_session() as session:
        msgs = session.exec(select(MessageDB).where(MessageDB.image != "")).all()
        log.info("Messages mit image: %d", len(msgs))
        for m in msgs:
            if not is_data_uri(m.image):
                skipped += 1
                continue
            hint = f"msg{m.id}" if m.id else "msg"
            path = persist_data_uri(m.image, name_hint=hint)
            if path:
                m.image = path
                session.add(m)
                converted += 1
            else:
                log.warning("Message %s: persist_data_uri lieferte None", m.id)
                skipped += 1
        session.commit()
    return converted, skipped


def main():
    init_db()
    log.info("--- TASKS ---")
    t_conv, t_skip = migrate_tasks()
    log.info("Tasks: %d konvertiert, %d übersprungen", t_conv, t_skip)
    log.info("--- MESSAGES ---")
    m_conv, m_skip = migrate_messages()
    log.info("Messages: %d konvertiert, %d übersprungen", m_conv, m_skip)
    log.info("FERTIG. Anschließend manuell: sqlite3 agentclaw.db 'VACUUM;'")


if __name__ == "__main__":
    main()
