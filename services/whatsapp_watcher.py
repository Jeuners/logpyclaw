"""
WhatsApp Watcher Service.
Beobachtet ~/.wacli/store.db auf Änderungen (mtime) — reagiert sofort wenn
wacli sync neue Nachrichten schreibt, ohne ständiges Polling.
Injiziert eingehende Nachrichten in MARTINs Chat-History + SSE-Push.
"""
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

WACLI_STORE_DB     = Path.home() / ".wacli" / "wacli.db"
WACLI_STORE_DB_WAL = Path.home() / ".wacli" / "wacli.db-wal"
LAST_TS_FILE   = Path.home() / ".wacli" / ".agentclaw_last_ts"

# SSE-Subscribers: jeder verbundene Client bekommt eine Queue
_subscribers: list[queue.SimpleQueue] = []
_subscribers_lock = threading.Lock()


def add_subscriber(q: "queue.SimpleQueue"):
    with _subscribers_lock:
        _subscribers.append(q)


def remove_subscriber(q: "queue.SimpleQueue"):
    with _subscribers_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict):
    with _subscribers_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                pass


def _find_martin_id() -> str | None:
    """MARTIN-Agent per Name suchen."""
    try:
        from storage.agents import load_agents
        for agent in load_agents():
            if agent.get("name", "").upper() == "MARTIN":
                return agent["id"]
    except Exception:
        pass
    return None


class WhatsAppWatcherService:
    def __init__(self):
        self._last_ts: str | None = self._load_last_ts()
        self._martin_id: str | None = None
        self._running = False

    # ── Persistenz ───────────────────────────────────────────────────────────

    def _load_last_ts(self) -> str | None:
        try:
            return LAST_TS_FILE.read_text().strip() or None
        except Exception:
            return None

    def _save_last_ts(self, ts: str):
        try:
            LAST_TS_FILE.write_text(ts)
        except Exception:
            pass

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        self._running = True
        t = threading.Thread(target=self._watch_loop, daemon=True, name="wa-watcher")
        t.start()
        logger.info("WhatsApp Watcher gestartet — beobachte %s", WACLI_STORE_DB)

    def stop(self):
        self._running = False

    # ── Haupt-Loop ───────────────────────────────────────────────────────────

    def _watch_loop(self):
        last_mtime = 0.0
        initialized = False

        while self._running:
            try:
                # WAL-Datei bevorzugen (SQLite schreibt neue Daten dorthin zuerst)
                db_path = WACLI_STORE_DB_WAL if WACLI_STORE_DB_WAL.exists() else WACLI_STORE_DB
                if db_path.exists():
                    mtime = db_path.stat().st_mtime
                    if not initialized:
                        last_mtime = mtime
                        initialized = True
                        if not self._last_ts:
                            self._init_last_ts()
                        else:
                            # Beim Start sofort prüfen ob neue Nachrichten seit last_ts
                            self._check_new_messages()
                    elif mtime != last_mtime:
                        last_mtime = mtime
                        self._check_new_messages()
            except Exception as e:
                logger.debug("WhatsApp Watcher loop Fehler: %s", e)
            time.sleep(2)

    def _init_last_ts(self):
        """Beim Start: neuesten Timestamp merken, keine alten Nachrichten anzeigen."""
        try:
            from skills.whatsapp import _run, _parse_response
            ok, out = _run("messages", "list", "--limit", "1")
            if ok:
                parsed = _parse_response(out)
                if parsed and isinstance(parsed, list) and parsed:
                    ts = parsed[0].get("Timestamp", "")
                    if ts:
                        self._last_ts = ts
                        self._save_last_ts(ts)
        except Exception:
            pass

    def _check_new_messages(self):
        try:
            from skills.whatsapp import _run, _parse_response
            ok, out = _run("messages", "list", "--limit", "20")
            if not ok:
                return
            parsed = _parse_response(out)
            if not parsed or not isinstance(parsed, list):
                return

            new_msgs = []
            latest_ts = self._last_ts

            # Chronologisch (älteste zuerst)
            for msg in reversed(parsed):
                ts = msg.get("Timestamp", "")
                if self._last_ts and ts <= self._last_ts:
                    continue
                new_msgs.append(msg)
                if not latest_ts or ts > latest_ts:
                    latest_ts = ts

            for msg in new_msgs:
                self._deliver(msg)

            if new_msgs and latest_ts and latest_ts != self._last_ts:
                self._last_ts = latest_ts
                self._save_last_ts(latest_ts)

        except Exception as e:
            logger.warning("WhatsApp Nachrichten-Check Fehler: %s", e)

    # ── Delivery ─────────────────────────────────────────────────────────────

    def _deliver(self, msg: dict):
        sender = msg.get("ChatName") or msg.get("SenderJID", "?").split("@")[0]
        text   = msg.get("Text") or msg.get("Snippet") or "(Medieninhalt)"
        ts     = (msg.get("Timestamp") or "")[:16].replace("T", " ")
        jid    = msg.get("ChatJID", "")

        event = {
            "type": "whatsapp_incoming",
            "sender": sender,
            "text": text,
            "ts": ts,
            "jid": jid,
        }

        logger.info("📱 WhatsApp eingehend von %s: %s", sender, text[:60])

        # SSE an alle Browser-Clients
        _broadcast(event)

        # In MARTINs Chat-History schreiben
        self._inject_to_martin(sender, text, ts)

    def _inject_to_martin(self, sender: str, text: str, ts: str):
        try:
            if not self._martin_id:
                self._martin_id = _find_martin_id()
            if not self._martin_id:
                return

            from storage.history import append_message
            agent_id = self._martin_id
            append_message(
                agent_id, "assistant",
                f"📱 **WhatsApp** von **{sender}** [{ts}]:\n{text}",
                skill_used="whatsapp",
            )
        except Exception as e:
            logger.warning("WhatsApp inject Fehler: %s", e)
