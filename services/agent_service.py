"""
services/agent_service.py — Agent CRUD und Verwaltung.
"""
import logging
import uuid
from datetime import datetime
from storage.agents import load_agents, save_agents, patch_agent_heartbeat
from storage.history import load_history, save_history
from storage.providers import load_providers
from core.errors import AgentNotFoundError, ValidationError

logger = logging.getLogger(__name__)


class AgentService:
    def get(self, agent_id: str) -> dict | None:
        """Agent by ID abrufen."""
        agents = load_agents()
        return next((a for a in agents if a["id"] == agent_id), None)

    def get_or_raise(self, agent_id: str) -> dict:
        """Agent by ID abrufen oder Exception werfen."""
        agent = self.get(agent_id)
        if not agent:
            raise AgentNotFoundError(f"Agent '{agent_id}' nicht gefunden")
        return agent

    def list_all(self) -> list[dict]:
        """Alle Agenten abrufen."""
        return load_agents()

    def list_favorites(self) -> list[dict]:
        """Nur Favoriten abrufen."""
        return [a for a in load_agents() if a.get("favorite")]

    def create(self, data: dict) -> dict:
        """Neuen Agent erstellen."""
        agents = load_agents()
        # Duplikat-Check
        if any(a["name"].lower() == data.get("name", "").lower() for a in agents):
            raise ValidationError(f"Agent mit Name '{data['name']}' existiert bereits")
        agent = {
            "id": str(uuid.uuid4()),
            "name": data["name"],
            "soul": data.get("soul", ""),
            "model": data.get("model", "llama3"),
            "provider": data.get("provider", "ollama"),
            "color": data.get("color", "#00e676"),
            "role": data.get("role", ""),
            "skills": data.get("skills", []),
            "heartbeat": data.get("heartbeat", {}),
            "dream": data.get("dream", {}),
            "max_tokens": data.get("max_tokens", 2048),
            "favorite": data.get("favorite", False),
            "voice": data.get("voice", ""),
            "web_search": data.get("web_search", False),
        }
        agents.append(agent)
        save_agents(agents)
        logger.info("Agent erstellt: %s (%s)", agent["name"], agent["id"])
        return agent

    def update(self, agent_id: str, data: dict) -> dict:
        """Agent aktualisieren."""
        agents = load_agents()
        for i, a in enumerate(agents):
            if a["id"] == agent_id:
                agents[i].update({k: v for k, v in data.items() if k != "id"})
                save_agents(agents)
                logger.info("Agent aktualisiert: %s", agent_id)
                return agents[i]
        raise AgentNotFoundError(f"Agent '{agent_id}' nicht gefunden")

    def delete(self, agent_id: str):
        """Agent löschen und seine History bereinigen."""
        agents = load_agents()
        original = len(agents)
        agents = [a for a in agents if a["id"] != agent_id]
        if len(agents) == original:
            raise AgentNotFoundError(f"Agent '{agent_id}' nicht gefunden")
        save_agents(agents)
        # History löschen
        history = load_history()
        history.pop(agent_id, None)
        save_history(history)
        logger.info("Agent gelöscht: %s", agent_id)

    def get_history(self, agent_id: str) -> list[dict]:
        """Chat-History für Agent abrufen."""
        history = load_history()
        return history.get(agent_id, [])

    def clear_history(self, agent_id: str):
        """Chat-History für Agent löschen."""
        history = load_history()
        history[agent_id] = []
        save_history(history)

    def append_history(self, agent_id: str, role: str, content: str, **extra):
        """Eintrag zur Chat-History hinzufügen."""
        history = load_history()
        if agent_id not in history:
            history[agent_id] = []
        entry = {"role": role, "content": content, "ts": datetime.now().isoformat(), **extra}
        history[agent_id].append(entry)
        save_history(history)

    def get_providers(self) -> dict:
        """API-Provider-Config abrufen."""
        return load_providers()

    def patch_heartbeat(self, agent_id: str, **fields):
        """Heartbeat-Felder patchen."""
        patch_agent_heartbeat(agent_id, **fields)
