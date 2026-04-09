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
DB_PATH = os.path.join(BASE_DIR, "agentclaw.db")
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
        }


# ── Engine & Session ───────────────────────────────────────────────────────────

def init_db():
    """Erstellt alle Tabellen (falls nicht vorhanden). Beim Start aufrufen."""
    SQLModel.metadata.create_all(engine)
    logger.info("SQLite DB initialisiert: %s", DB_PATH)


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
