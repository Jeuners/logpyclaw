"""
services/m2m_service.py — Martin-to-Martin Peer-Netzwerk.
Extrahiert aus app.py: tick_m2m_peers(), _send_remote_task(), _m2m_send_callback().
"""
import logging
import uuid
import requests
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from storage.nodes import load_nodes, save_nodes, update_node_cache, mark_node_offline

if TYPE_CHECKING:
    from services.agent_service import AgentService
    from services.event_service import EventService

logger = logging.getLogger(__name__)


class M2MService:
    def __init__(self, agents: "AgentService", events: "EventService"):
        self._agents = agents
        self._events = events

    def tick_peers(self):
        """Remote-Nodes synchronisieren."""
        from storage.providers import load_providers
        providers = load_providers()
        m2m = providers.get("martin_m2m", {})
        if not m2m.get("enabled"):
            return
        nodes = load_nodes()
        for node in nodes:
            if not node.get("active", True):
                continue
            try:
                self._refresh_node_cache(node)
            except Exception as e:
                logger.warning("M2M-Sync fehlgeschlagen für %s: %s", node.get("name"), e)

    def _refresh_node_cache(self, node: dict):
        """Node-Agent-Cache aktualisieren."""
        url = node.get("url", "")
        if not url:
            return
        resp = requests.get(f"{url}/api/a2a/agents", timeout=5)
        if resp.ok:
            agents = resp.json()
            update_node_cache(node["id"], agents)
        else:
            mark_node_offline(node["id"])

    def list_nodes(self) -> list[dict]:
        """Alle Remote-Nodes abrufen."""
        return load_nodes()

    def add_node(self, data: dict) -> dict:
        """Neuen Remote-Node hinzufügen."""
        nodes = load_nodes()
        node = {
            "id": str(uuid.uuid4()),
            "name": data["name"],
            "url": data["url"],
            "active": True,
            "agent_cache": [],
            "last_seen": None,
        }
        nodes.append(node)
        save_nodes(nodes)
        return node

    def remove_node(self, node_id: str):
        """Remote-Node entfernen."""
        nodes = [n for n in load_nodes() if n["id"] != node_id]
        save_nodes(nodes)

    def dispatch_remote(self, node_id: str, task: dict) -> dict:
        """Task an Remote-Node senden."""
        nodes = load_nodes()
        node = next((n for n in nodes if n["id"] == node_id), None)
        if not node:
            raise ValueError(f"Node {node_id} nicht gefunden")
        url = node["url"]
        resp = requests.post(f"{url}/api/a2a/dispatch", json=task, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def auth_check(self, request_headers: dict) -> bool:
        """M2M-Auth prüfen."""
        from storage.providers import load_providers
        providers = load_providers()
        m2m = providers.get("martin_m2m", {})
        expected_token = m2m.get("token", "")
        if not expected_token:
            return True  # kein Token konfiguriert → offen
        provided = request_headers.get("X-Martin-Token", "")
        return provided == expected_token
