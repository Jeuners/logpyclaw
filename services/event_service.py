"""
services/event_service.py — Event-System und Activity-Tracking.
Extrahiert aus app.py: emit_event, get_events_since, activity_start/step/end/cleanup.

Verbesserungen:
  - Buffer: 500 statt 100 Events (kein Verlust bei mehreren gleichzeitigen Agents)
  - Durability: Events werden in events.jsonl (append-only) persistiert
  - Replay: Nach Neustart können Events der letzten N Minuten wiederhergestellt werden
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from core.state import _ACTIVITY, _EVENTS, _activity_lock, _events_lock

logger = logging.getLogger(__name__)

_EVENT_VERSION = 0
_event_version_lock = threading.Lock()

# Persist-Konfiguration
_EVENT_BUFFER_SIZE = 500          # In-Memory-Buffer
_EVENT_PERSIST_MAX_AGE_MIN = 60   # Nur Events der letzten 60min persistieren
_event_log_lock = threading.Lock()

# Callback für WebSocket-Emits (Legacy-Kompatibilität)
_ws_emit_callback = None

def set_ws_emit_callback(fn):
    """WebSocket-Emit-Funktion registrieren."""
    global _ws_emit_callback
    _ws_emit_callback = fn


def _get_event_log_path() -> str:
    """Pfad zur Event-Log-Datei."""
    from core.config import BASE_DIR
    return os.path.join(BASE_DIR, "events.jsonl")


class EventService:
    def emit(self, event_type: str, data: dict = None):
        """Event emittieren, in Memory-Buffer + Disk-Log speichern."""
        global _EVENT_VERSION
        with _event_version_lock:
            _EVENT_VERSION += 1
            version = _EVENT_VERSION

        event = {
            "type": event_type,
            "data": data or {},
            "v": version,
            "ts": datetime.now().isoformat(),
        }

        with _events_lock:
            _EVENTS.append(event)
            if len(_EVENTS) > _EVENT_BUFFER_SIZE:
                _EVENTS[:] = _EVENTS[-_EVENT_BUFFER_SIZE:]

        # Asynchron in Disk-Log schreiben (non-blocking, best-effort)
        self._persist_async(event)

        if _ws_emit_callback:
            try:
                _ws_emit_callback(event_type, data or {})
            except Exception as e:
                logger.warning("WS emit Fehler: %s", e)

    def _persist_async(self, event: dict):
        """Event asynchron in JSONL-Datei schreiben (daemon thread)."""
        def _write():
            try:
                with _event_log_lock:
                    with open(_get_event_log_path(), "a", encoding="utf-8") as f:
                        f.write(json.dumps(event, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.debug("Event-Persist fehlgeschlagen: %s", e)
        t = threading.Thread(target=_write, daemon=True)
        t.start()

    def get_since(self, version: int) -> list:
        """Alle Events seit version abrufen (aus Memory-Buffer)."""
        with _events_lock:
            return [e for e in _EVENTS if e["v"] > version]

    def replay_from_disk(self, max_age_minutes: int = _EVENT_PERSIST_MAX_AGE_MIN):
        """
        Events der letzten N Minuten aus Disk-Log in Memory-Buffer laden.
        Wird beim Startup aufgerufen um Events nach Restart verfügbar zu machen.
        """
        global _EVENT_VERSION
        log_path = _get_event_log_path()
        if not os.path.exists(log_path):
            return
        cutoff = (datetime.now() - timedelta(minutes=max_age_minutes)).isoformat()
        loaded = 0
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                events = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        if e.get("ts", "") >= cutoff:
                            events.append(e)
                    except json.JSONDecodeError:
                        continue
            if events:
                with _events_lock:
                    _EVENTS.clear()
                    _EVENTS.extend(events[-_EVENT_BUFFER_SIZE:])
                with _event_version_lock:
                    if _EVENTS:
                        _EVENT_VERSION = max(e.get("v", 0) for e in _EVENTS)
                loaded = len(events)
                logger.info("Event-Replay: %d Events aus Disk geladen", loaded)
        except Exception as e:
            logger.warning("Event-Replay fehlgeschlagen: %s", e)

    def rotate_log(self, max_age_hours: int = 6):
        """
        Altes Event-Log rotieren — Events älter als N Stunden entfernen.
        Streaming-Implementierung: kein vollständiges RAM-Laden großer Files.
        """
        log_path = _get_event_log_path()
        if not os.path.exists(log_path):
            return
        if os.path.getsize(log_path) == 0:
            return
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        tmp = log_path + ".tmp"
        kept = 0
        total = 0
        try:
            with _event_log_lock:
                with open(log_path, "r", encoding="utf-8") as src, \
                     open(tmp, "w", encoding="utf-8") as dst:
                    for line in src:
                        total += 1
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            ts = json.loads(stripped).get("ts", "")
                            if ts >= cutoff:
                                dst.write(line)
                                kept += 1
                        except json.JSONDecodeError:
                            continue
                os.replace(tmp, log_path)
            if kept < total:
                logger.info("Event-Log rotiert: %d → %d Einträge", total, kept)
        except Exception as e:
            logger.warning("Event-Log-Rotation fehlgeschlagen: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def activity_start(self, agent_id: str, atype: str, label: str):
        """Activity für Agent starten."""
        with _activity_lock:
            _ACTIVITY[agent_id] = {
                "type": atype,
                "label": label,
                "since": datetime.now().isoformat(),
            }
        self.emit("activity", {"agent_id": agent_id, "type": atype, "label": label, "status": "started"})

    def activity_step(self, agent_id: str, label: str):
        """Activity-Step updaten."""
        with _activity_lock:
            if agent_id in _ACTIVITY:
                _ACTIVITY[agent_id]["label"] = label
        self.emit("activity", {"agent_id": agent_id, "label": label, "status": "step"})

    def activity_end(self, agent_id: str):
        """Activity für Agent beenden."""
        with _activity_lock:
            _ACTIVITY.pop(agent_id, None)
        self.emit("activity", {"agent_id": agent_id, "status": "ended"})

    def activity_cleanup(self):
        """Veraltete Activity-Einträge entfernen (älter als 10 Minuten)."""
        cutoff = (datetime.now() - timedelta(minutes=10)).isoformat()
        with _activity_lock:
            stale = [k for k, v in _ACTIVITY.items() if v.get("since", "") < cutoff]
            for k in stale:
                del _ACTIVITY[k]

    def get_all_activity(self) -> dict:
        """Alle aktuellen Activity-Einträge abrufen."""
        with _activity_lock:
            return dict(_ACTIVITY)

    def emit_task_result(self, task_id: str, agent_id: str, result_text, result_image, status: str, error=None):
        """Task-Ergebnis als Event emittieren."""
        self.emit("task_result", {
            "task_id": task_id,
            "agent_id": agent_id,
            "result_text": result_text,
            "result_image": result_image,
            "status": status,
            "error": error,
        })

    def emit_chat_message(self, agent_id: str, role: str, content: str, image=None):
        """Chat-Nachricht als Event emittieren."""
        self.emit("chat_message", {
            "agent_id": agent_id,
            "role": role,
            "content": content,
            "image": image,
            "ts": datetime.now().isoformat(),
        })

    def emit_a2a_dispatch(
        self,
        sender_agent_id: str,
        sender_name: str,
        recipient_name: str,
        task_text: str,
        task_id: str = "",
    ):
        """A2A-Delegation als eigenes Event emittieren (kein Chat-Bubble)."""
        self.emit("a2a_dispatch", {
            "sender_agent_id": sender_agent_id,
            "sender_name": sender_name,
            "recipient_name": recipient_name,
            "task_text": task_text,
            "task_id": task_id,
            "ts": datetime.now().isoformat(),
        })

    def emit_heartbeat_result(self, agent_id: str, result: str):
        """Heartbeat-Ergebnis emittieren."""
        self.emit("heartbeat_result", {
            "agent_id": agent_id,
            "result": result,
            "ts": datetime.now().isoformat(),
        })
