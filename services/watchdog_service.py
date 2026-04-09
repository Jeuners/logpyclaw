"""
services/watchdog_service.py — URL-Monitoring (Watchdogs).
Extrahiert aus app.py: run_watchdog(), tick_watchdogs(), send_watchdog_alert().
"""
import logging
import hashlib
import requests
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from storage.watchdogs import load_watchdogs, save_watchdogs, update_watchdog_field
from core.config import spawn_background

if TYPE_CHECKING:
    from services.agent_service import AgentService
    from services.event_service import EventService

logger = logging.getLogger(__name__)


class WatchdogService:
    def __init__(self, agents: "AgentService", events: "EventService"):
        self._agents = agents
        self._events = events

    def tick(self):
        """Alle aktiven Watchdogs prüfen."""
        watchdogs = load_watchdogs()
        now = datetime.now().isoformat()
        for wd in watchdogs:
            if not wd.get("active"):
                continue
            next_check = wd.get("next_check", "")
            if next_check and now < next_check:
                continue
            spawn_background(self.run, wd["id"])

    def run(self, watchdog_id: str):
        """Einen Watchdog ausführen."""
        watchdogs = load_watchdogs()
        wd = next((w for w in watchdogs if w["id"] == watchdog_id), None)
        if not wd:
            return

        url = wd.get("url", "")
        if not url:
            return

        logger.info("Watchdog check: %s", url[:60])
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "AgentClaw-Watchdog/1.0"})
            content = resp.text
            current_hash = hashlib.sha256(content.encode()).hexdigest()
            last_hash = wd.get("last_hash", "")

            interval_min = wd.get("interval_min", 60)
            next_check = (datetime.now() + timedelta(minutes=interval_min)).isoformat()
            update_watchdog_field(watchdog_id, last_check=datetime.now().isoformat(),
                                  last_hash=current_hash, next_check=next_check)

            if last_hash and current_hash != last_hash:
                logger.info("Watchdog: Änderung erkannt bei %s", url[:60])
                self._send_alert(wd, content[:500])

        except Exception as e:
            logger.error("Watchdog Fehler für %s: %s", url[:60], e)
            update_watchdog_field(watchdog_id, last_error=str(e),
                                  last_check=datetime.now().isoformat())

    def _send_alert(self, wd: dict, changed_content: str):
        """Alert an den zugeordneten Agenten senden."""
        agent_id = wd.get("agent_id")
        if not agent_id:
            return
        agent = self._agents.get(agent_id)
        if not agent:
            return
        message = f"🔔 **Watchdog-Alert**: {wd['url']}\n\nÄnderung erkannt:\n{changed_content[:300]}"
        self._events.emit_chat_message(agent_id, "system", message)
        logger.info("Watchdog-Alert gesendet an Agent %s", agent_id)

    def list(self) -> list[dict]:
        """Alle Watchdogs abrufen."""
        return load_watchdogs()

    def create(self, data: dict) -> dict:
        """Neuen Watchdog erstellen."""
        import uuid
        watchdogs = load_watchdogs()
        wd = {
            "id": str(uuid.uuid4()),
            "url": data["url"],
            "agent_id": data.get("agent_id", ""),
            "interval_min": data.get("interval_min", 60),
            "active": data.get("active", True),
            "last_hash": "",
            "last_check": "",
            "next_check": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
        }
        watchdogs.append(wd)
        save_watchdogs(watchdogs)
        return wd

    def update(self, watchdog_id: str, data: dict) -> dict | None:
        """Watchdog aktualisieren."""
        watchdogs = load_watchdogs()
        for i, wd in enumerate(watchdogs):
            if wd["id"] == watchdog_id:
                watchdogs[i].update({k: v for k, v in data.items() if k != "id"})
                save_watchdogs(watchdogs)
                return watchdogs[i]
        return None

    def delete(self, watchdog_id: str):
        """Watchdog löschen."""
        watchdogs = [w for w in load_watchdogs() if w["id"] != watchdog_id]
        save_watchdogs(watchdogs)

    def toggle(self, watchdog_id: str) -> bool:
        """Watchdog aktivieren/deaktivieren."""
        watchdogs = load_watchdogs()
        for wd in watchdogs:
            if wd["id"] == watchdog_id:
                wd["active"] = not wd.get("active", False)
                save_watchdogs(watchdogs)
                return wd["active"]
        return False
