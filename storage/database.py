"""
storage/database.py — SQLite-Datenbank via SQLModel.

Ersetzt schrittweise die JSON-Dateien agents.json, history.json und tasks.json.
Bietet Migrations-Hilfsfunktionen für den einmaligen Import der JSON-Daten.

Verwendung:
    from storage.database import get_session, init_db
    init_db()          # Einmalig beim Start aufrufen
    with get_session() as session:
        session.add(...)
        session.commit()
"""
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from sqlmodel import Field, Session, SQLModel, create_engine, select, JSON, Column

from core.config import BASE_DIR

logger = logging.getLogger(__name__)

# ── Datenbank-Konfiguration ────────────────────────────────────────────────────
# AGENTCLAW_DB_PATH überschreibt den Default — von tests/conftest.py genutzt,
# damit Pytest-Runs niemals in die Production-agentclaw.db schreiben.
DB_PATH = os.environ.get("AGENTCLAW_DB_PATH") or os.path.join(BASE_DIR, "agentclaw.db")
DB_URL  = f"sqlite:///{DB_PATH}"

# connect_args für SQLite Thread-Sicherheit
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


# ── SQLModel Tabellen-Definitionen ─────────────────────────────────────────────

class AgentDB(SQLModel, table=True):
    """Agenten-Stammdaten."""
    __tablename__ = "agents"

    id:           str           = Field(primary_key=True)
    name:         str           = Field(index=True)
    role:         str           = Field(default="")
    soul:         str           = Field(default="")
    model:        str           = Field(default="")
    provider:     str           = Field(default="ollama")
    avatar:       str           = Field(default="🤖")
    color:        str           = Field(default="#00e676")
    voice:        str           = Field(default="")
    max_tokens:   int           = Field(default=2048)
    web_search:   bool          = Field(default=False)
    # JSON-Felder
    skills_json:  str           = Field(default="[]")    # JSON-Array als String
    heartbeat_json: str         = Field(default="{}")    # JSON-Object als String
    inbox_json:   str           = Field(default="[]")    # JSON-Array als String
    # Metadaten
    created_at:   str           = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at:   str           = Field(default_factory=lambda: datetime.now().isoformat())
    favorite:     bool          = Field(default=False)

    def to_dict(self) -> dict:
        """Konvertiert DB-Objekt zurück ins alte agents.json-Format."""
        return {
            "id":         self.id,
            "name":       self.name,
            "role":       self.role,
            "soul":       self.soul,
            "model":      self.model,
            "provider":   self.provider,
            "avatar":     self.avatar,
            "color":      self.color,
            "voice":      self.voice,
            "max_tokens": self.max_tokens,
            "web_search": self.web_search,
            "skills":     json.loads(self.skills_json or "[]"),
            "heartbeat":  json.loads(self.heartbeat_json or "{}"),
            "inbox":      json.loads(self.inbox_json or "[]"),
            "favorite":   self.favorite,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentDB":
        """Erstellt DB-Objekt aus altem agents.json-Format."""
        return cls(
            id=d["id"],
            name=d.get("name", ""),
            role=d.get("role", ""),
            soul=d.get("soul", ""),
            model=d.get("model", ""),
            provider=d.get("provider", "ollama"),
            avatar=d.get("avatar", "🤖"),
            color=d.get("color", "#00e676"),
            voice=d.get("voice", ""),
            max_tokens=int(d.get("max_tokens") or 2048),
            web_search=bool(d.get("web_search", False)),
            skills_json=json.dumps(d.get("skills", []), ensure_ascii=False),
            heartbeat_json=json.dumps(d.get("heartbeat", {}), ensure_ascii=False),
            inbox_json=json.dumps(d.get("inbox", []), ensure_ascii=False),
            favorite=bool(d.get("favorite", False)),
        )


class MessageDB(SQLModel, table=True):
    """Chat-Verlauf pro Agent."""
    __tablename__ = "messages"

    id:         Optional[int]  = Field(default=None, primary_key=True)
    agent_id:   str            = Field(index=True)
    role:       str            = Field()          # "user" | "assistant" | "system"
    content:    str            = Field(default="")
    ts:         str            = Field(default_factory=lambda: datetime.now().isoformat())
    skill_used: str            = Field(default="")
    image:      str            = Field(default="")  # Base64-URL oder leer


class TaskDB(SQLModel, table=True):
    """A2A-Task-Queue."""
    __tablename__ = "tasks"

    id:                   str            = Field(primary_key=True)
    status:               str            = Field(default="queued", index=True)  # queued|running|completed|failed|canceled
    sender_agent_id:      str            = Field(default="")
    sender_agent_name:    str            = Field(default="")
    recipient_agent_id:   str            = Field(default="", index=True)
    recipient_agent_name: str            = Field(default="")
    message:              str            = Field(default="")
    result_text:          str            = Field(default="")
    result_image:         str            = Field(default="")
    error:                str            = Field(default="")
    skill_used:           str            = Field(default="")
    delegation_depth:     int            = Field(default=0)
    created_at:           str            = Field(default_factory=lambda: datetime.now().isoformat())
    started_at:           Optional[str]  = Field(default=None)
    completed_at:         Optional[str]  = Field(default=None)
    # Eigenzeit-Felder (Dillenberg, Time Dilation §4.3 — Conceptual → Implemented).
    # Alle nullable, damit Bestandsdaten unverändert lesbar bleiben.
    reference_now:         Optional[str]   = Field(default=None)
    parent_reference_now:  Optional[str]   = Field(default=None)
    dilation_factor:       Optional[float] = Field(default=None)
    frame_id:              Optional[str]   = Field(default=None, index=True)

    def to_dict(self) -> dict:
        return {
            "id":                   self.id,
            "status":               self.status,
            "sender_agent_id":      self.sender_agent_id,
            "sender_agent_name":    self.sender_agent_name,
            "recipient_agent_id":   self.recipient_agent_id,
            "recipient_agent_name": self.recipient_agent_name,
            "message":              self.message,
            "result_text":          self.result_text,
            "result_image":         self.result_image,
            "error":                self.error,
            "skill_used":           self.skill_used,
            "delegation_depth":     self.delegation_depth,
            "created_at":           self.created_at,
            "started_at":           self.started_at,
            "completed_at":         self.completed_at,
            "reference_now":        self.reference_now,
            "parent_reference_now": self.parent_reference_now,
            "dilation_factor":      self.dilation_factor,
            "frame_id":             self.frame_id,
        }


# ── Engine & Session ───────────────────────────────────────────────────────────

def init_db():
    """Erstellt alle Tabellen (falls nicht vorhanden). Beim Start aufrufen."""
    SQLModel.metadata.create_all(engine)
    _ensure_eigenzeit_columns()
    logger.info("SQLite DB initialisiert: %s", DB_PATH)


def _ensure_eigenzeit_columns() -> None:
    """ALTER TABLE für Eigenzeit-Felder (§4.3).

    SQLModel.create_all legt fehlende Tabellen an, ändert aber keine vorhandenen.
    Diese Funktion fügt für Bestands-DBs die nullable Eigenzeit-Spalten nach.
    Idempotent — vorhandene Spalten werden übersprungen.
    """
    expected: list[tuple[str, str]] = [
        ("reference_now",        "TEXT"),
        ("parent_reference_now", "TEXT"),
        ("dilation_factor",      "REAL"),
        ("frame_id",             "TEXT"),
    ]
    with engine.connect() as conn:
        from sqlalchemy import text
        existing = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tasks)").all()
        }
        for col, sql_type in expected:
            if col in existing:
                continue
            try:
                conn.exec_driver_sql(
                    f"ALTER TABLE tasks ADD COLUMN {col} {sql_type}"
                )
                logger.info("Migration: tasks.%s (%s) hinzugefügt", col, sql_type)
            except Exception as e:
                logger.warning("Migration tasks.%s übersprungen: %s", col, e)
        try:
            conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_tasks_frame_id ON tasks (frame_id)"
            )
        except Exception as e:
            logger.warning("Index ix_tasks_frame_id übersprungen: %s", e)
        conn.commit()


@contextmanager
def get_session():
    """Context Manager für DB-Sessions."""
    with Session(engine) as session:
        yield session


# ── Migration: JSON → SQLite ───────────────────────────────────────────────────

def migrate_agents_json(agents_file: str) -> int:
    """
    Importiert agents.json einmalig in die SQLite-DB.
    Überspringt Agenten die bereits existieren (idempotent).
    Gibt Anzahl importierter Agenten zurück.
    """
    if not os.path.exists(agents_file):
        return 0

    with open(agents_file, encoding="utf-8") as f:
        agents = json.load(f)

    count = 0
    with get_session() as session:
        for a in agents:
            existing = session.get(AgentDB, a["id"])
            if existing:
                continue
            session.add(AgentDB.from_dict(a))
            count += 1
        session.commit()

    logger.info("Migration agents.json: %d Agenten importiert", count)
    return count


def migrate_history_json(history_file: str) -> int:
    """
    Importiert history.json einmalig in die SQLite-DB.
    Überspringt bereits vorhandene Einträge (idempotent via Timestamp-Check).
    Gibt Anzahl importierter Nachrichten zurück.
    """
    if not os.path.exists(history_file):
        return 0

    with open(history_file, encoding="utf-8") as f:
        history = json.load(f)

    count = 0
    with get_session() as session:
        for agent_id, messages in history.items():
            # Prüfe ob bereits Nachrichten für diesen Agenten vorhanden
            existing = session.exec(
                select(MessageDB).where(MessageDB.agent_id == agent_id).limit(1)
            ).first()
            if existing:
                continue

            for msg in messages:
                session.add(MessageDB(
                    agent_id=agent_id,
                    role=msg.get("role", "user"),
                    content=msg.get("content", ""),
                    ts=msg.get("ts", datetime.now().isoformat()),
                    skill_used=msg.get("skill_used", ""),
                    image=msg.get("image", ""),
                ))
                count += 1
        session.commit()

    logger.info("Migration history.json: %d Nachrichten importiert", count)
    return count


# ── Task-Persistenz ────────────────────────────────────────────────────────────

_TASK_FIELDS = {
    "id", "status", "sender_agent_id", "sender_agent_name",
    "recipient_agent_id", "recipient_agent_name", "message",
    "result_text", "result_image", "error", "skill_used",
    "delegation_depth", "created_at", "started_at", "completed_at",
    # Eigenzeit-Felder (§4.3) — nullable
    "reference_now", "parent_reference_now", "dilation_factor", "frame_id",
}
_TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


def _task_dict_to_row(t: dict) -> dict:
    """Projiziert ein in-memory Task-Dict auf die TaskDB-Spalten (None → '')."""
    row = {k: t.get(k) for k in _TASK_FIELDS}
    for key in ("sender_agent_id", "sender_agent_name", "recipient_agent_id",
                "recipient_agent_name", "message", "result_text",
                "result_image", "error", "skill_used"):
        if row.get(key) is None:
            row[key] = ""
    if row.get("status") is None:
        row["status"] = "queued"
    if row.get("delegation_depth") is None:
        row["delegation_depth"] = 0
    if not row.get("id"):
        raise ValueError("Task ohne id kann nicht persistiert werden")
    # Eigenzeit-Felder bleiben explizit nullable — None ist ein gültiger Wert
    # (= "Task aus Pre-Eigenzeit-Ära oder ohne Frame-Kontext").
    return row


def upsert_task(task: dict) -> None:
    """Einzelnen Task in SQLite speichern (insert oder update)."""
    row = _task_dict_to_row(task)
    with get_session() as session:
        existing = session.get(TaskDB, row["id"])
        if existing:
            for k, v in row.items():
                setattr(existing, k, v)
            session.add(existing)
        else:
            session.add(TaskDB(**row))
        session.commit()


def upsert_tasks_bulk(tasks: list[dict]) -> int:
    """Alle Tasks in einer Transaktion upserten. Gibt Anzahl zurück."""
    if not tasks:
        return 0
    count = 0
    with get_session() as session:
        for t in tasks:
            try:
                row = _task_dict_to_row(t)
            except ValueError:
                continue
            existing = session.get(TaskDB, row["id"])
            if existing:
                for k, v in row.items():
                    setattr(existing, k, v)
                session.add(existing)
            else:
                session.add(TaskDB(**row))
            count += 1
        session.commit()
    return count


def load_open_tasks() -> list[dict]:
    """Lädt alle nicht-terminalen Tasks aus SQLite."""
    with get_session() as session:
        stmt = select(TaskDB).where(TaskDB.status.not_in(_TERMINAL_STATES))
        return [t.to_dict() for t in session.exec(stmt).all()]


def delete_old_tasks(cutoff_iso: str) -> int:
    """Löscht alle terminalen Tasks deren ``completed_at`` älter als ``cutoff_iso`` ist.

    Wird vom periodischen TaskService-Cleanup aufgerufen. Idempotent.
    Liefert die Anzahl gelöschter Zeilen zurück.
    """
    with get_session() as session:
        stmt = select(TaskDB).where(
            TaskDB.status.in_(_TERMINAL_STATES),
            TaskDB.completed_at.is_not(None),
            TaskDB.completed_at < cutoff_iso,
        )
        rows = session.exec(stmt).all()
        for r in rows:
            session.delete(r)
        session.commit()
        return len(rows)


def delete_orphan_tasks() -> int:
    """Löscht Tasks deren recipient_agent_id nicht mehr in der agents-Tabelle existiert.

    Bereinigt Test-Pollution + Verweise auf gelöschte Agents. Liefert die
    Anzahl entfernter Zeilen zurück.
    """
    with get_session() as session:
        agent_ids = {a.id for a in session.exec(select(AgentDB)).all()}
        rows = session.exec(select(TaskDB)).all()
        removed = 0
        for r in rows:
            if r.recipient_agent_id not in agent_ids:
                session.delete(r)
                removed += 1
        session.commit()
        return removed


# ── Migrations ─────────────────────────────────────────────────────────────────

def run_migrations():
    """
    Führt alle Migrations-Schritte durch.
    Sicher mehrfach aufrufbar (idempotent).
    Beim App-Start nach init_db() aufrufen.
    """
    from core.config import AGENTS_FILE, HISTORY_FILE

    init_db()
    agents_count  = migrate_agents_json(AGENTS_FILE)
    history_count = migrate_history_json(HISTORY_FILE)

    if agents_count or history_count:
        logger.info(
            "Migration abgeschlossen: %d Agenten, %d Nachrichten",
            agents_count, history_count,
        )
    else:
        logger.debug("Keine Migration notwendig (Daten bereits in DB)")
