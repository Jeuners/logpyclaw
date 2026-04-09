"""
services/task_service.py — A2A Task-Lifecycle Management.
Extrahiert aus app.py: process_task(), _enqueue_task(), _cleanup_old_tasks(), _init_tasks().
"""
import logging
import re
import uuid
import threading
import json
import os
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from core.state import _TASKS, _tasks_lock, _TASK_TTL_SECONDS
from core.config import TASKS_FILE, spawn_background
from core.errors import AgentNotFoundError

if TYPE_CHECKING:
    from services.agent_service import AgentService
    from services.event_service import EventService

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 5

_MENTION_RX = re.compile(r"@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{1,40}?)(?=\s|$|[,.:!?])", re.UNICODE)

A2A_TASK_STATES = {
    "submitted": "Task received, waiting for processing",
    "working": "Task is actively being processed",
    "completed": "Task completed successfully",
    "failed": "Task failed with error",
    "canceled": "Task was canceled by client",
    "queued": "Task is queued (agent busy)",
}

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


class TaskService:
    def __init__(self, agents: "AgentService", events: "EventService"):
        self._agents = agents
        self._events = events
        self._dispatcher = None  # Wird von init_services gesetzt

    def set_dispatcher(self, dispatcher):
        """Dispatcher für Task-Verarbeitung registrieren."""
        self._dispatcher = dispatcher

    # ── Queue Management ──────────────────────────────────────────────────────

    def enqueue(self, task: dict) -> tuple[bool, int]:
        """
        Task einreihen. Gibt (queued, position) zurück.
        Setzt Default-Priority 5 wenn nicht angegeben.
        """
        agent_id = task["recipient_agent_id"]
        # Default-Priority sicherstellen
        task.setdefault("priority", 5)
        with _tasks_lock:
            busy = any(
                t.get("recipient_agent_id") == agent_id
                and t.get("status") in ("submitted", "working", "queued")
                for t in _TASKS.values()
            )
            if busy:
                task["status"] = "queued"
                task["queued_at"] = datetime.now().isoformat()
                # Queue-Position nach Priorität
                queue_pos = sum(
                    1 for t in _TASKS.values()
                    if t.get("recipient_agent_id") == agent_id
                    and t.get("status") == "queued"
                    and t.get("priority", 5) >= task["priority"]
                )
            else:
                task["status"] = "submitted"
                queue_pos = 0
            _TASKS[task["id"]] = task
        self._save()
        if not busy:
            spawn_background(self.process, task["id"])
            return (False, 0)
        return (True, queue_pos + 1)

    def tick_queue(self):
        """
        Queued Tasks starten wenn Agent frei wird.
        Sortiert nach: priority DESC (höher = wichtiger), dann queued_at ASC (FIFO).
        Prioritäten: User-Chat=8, A2A-User=5, Heartbeat=3
        """
        with _tasks_lock:
            agents_working = {
                t["recipient_agent_id"]
                for t in _TASKS.values()
                if t.get("status") in ("submitted", "working")
            }
            to_start = []
            # Priority-Queue: höhere priority zuerst, bei Gleichstand FIFO
            queued = [t for t in _TASKS.values() if t.get("status") == "queued"]
            queued.sort(key=lambda x: (-x.get("priority", 5), x.get("queued_at", "")))
            for task in queued:
                if task["recipient_agent_id"] not in agents_working:
                    task["status"] = "submitted"
                    agents_working.add(task["recipient_agent_id"])
                    to_start.append(task["id"])
        for tid in to_start:
            spawn_background(self.process, tid)

    def cancel(self, task_id: str) -> bool:
        """Task canceln wenn nicht schon terminal."""
        with _tasks_lock:
            task = _TASKS.get(task_id)
            if not task:
                return False
            if task.get("status") in TERMINAL_STATES:
                return False
            task["status"] = "canceled"
            task["completed_at"] = datetime.now().isoformat()
        self._save()
        return True

    def get(self, task_id: str) -> dict | None:
        """Task by ID abrufen."""
        with _tasks_lock:
            return _TASKS.get(task_id)

    def list_all(self) -> list[dict]:
        """Alle Tasks abrufen."""
        with _tasks_lock:
            return list(_TASKS.values())

    def list_active(self) -> list[dict]:
        """Nur aktive Tasks abrufen."""
        with _tasks_lock:
            return [t for t in _TASKS.values() if t.get("status") not in TERMINAL_STATES]

    # ── Task Processing ───────────────────────────────────────────────────────

    def process(self, task_id: str):
        """Haupt-Task-Worker. Wird im Background-Thread ausgeführt."""
        with _tasks_lock:
            task = _TASKS.get(task_id)
        if not task:
            return

        # Timeout-Check
        if datetime.now().isoformat() > task.get("timeout_at", "9999"):
            task["status"] = "failed"
            task["error"] = "Timeout vor Ausführungsstart"
            task["completed_at"] = datetime.now().isoformat()
            self._save()
            return

        task["status"] = "working"
        self._save()
        logger.info("Task %s gestartet: %s", task_id, task["message"][:60])

        agent = self._agents.get(task["recipient_agent_id"])
        if not agent:
            self._fail(task, f"Agent '{task['recipient_agent_name']}' nicht gefunden")
            return

        self._events.activity_start(
            task["recipient_agent_id"], "task",
            f"Task von @{task['sender_agent_name']}: {task['message'][:50]}"
        )

        try:
            if self._dispatcher:
                result = self._dispatcher.execute(agent, task)
                self._apply_result(task, result)
            else:
                # Fallback: LLM direkt
                from core.llm import call_agent_text
                reply = call_agent_text(agent, "[Task]", task["message"])
                task["result_text"] = reply
                task["skill_used"] = "llm"

            self._complete(task)

        except Exception as e:
            import traceback
            logger.exception("Task %s fehlgeschlagen", task_id)
            self._fail(task, str(e))
        finally:
            self._events.activity_end(task["recipient_agent_id"])
            self._save()

        self._events.emit_task_result(
            task["id"], task["recipient_agent_id"],
            task.get("result_text"), task.get("result_image"),
            task["status"], task.get("error")
        )

        # Callback-URL: Ergebnis aktiv zurück zum Sender senden (optional)
        self._maybe_callback(task)

    def _apply_result(self, task, result):
        """Skill-Result auf Task anwenden."""
        from skills.base import SkillResult
        if isinstance(result, SkillResult):
            if result.error:
                task["error"] = result.error
            else:
                task["result_text"] = result.text
                task["result_image"] = result.image
                task["skill_used"] = result.skill_used
        elif isinstance(result, dict):
            task.update(result)

    def _complete(self, task):
        """Task als completed markieren."""
        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        logger.info("Task %s abgeschlossen via %s", task["id"], task.get("skill_used"))
        # History-Eintrag
        if not task.get("chat_mode"):
            self._save_to_history(task)

    def _fail(self, task, error: str):
        """Task als failed markieren."""
        task["status"] = "failed"
        task["error"] = error
        task["completed_at"] = datetime.now().isoformat()
        logger.error("Task %s fehlgeschlagen: %s", task["id"], error)
        self._save()
        self._events.emit_task_result(
            task["id"], task["recipient_agent_id"],
            None, None, "failed", error
        )

    def _save_to_history(self, task):
        """Task-Ergebnis in Chat-History beider Agenten speichern."""
        try:
            ts = datetime.now().isoformat()
            recipient_id = task["recipient_agent_id"]
            sender_id = task.get("sender_agent_id", "")
            result_text = task.get("result_text", "")
            if result_text:
                self._events.emit_chat_message(
                    recipient_id, "system",
                    f"✅ **Done** (von @{task['sender_agent_name']}): {result_text[:300]}"
                )
                if sender_id and sender_id not in ("system", "inbox", "user", ""):
                    self._events.emit_chat_message(
                        sender_id, "system",
                        f"📬 **@{task['recipient_agent_name']}** → {result_text[:300]}"
                    )
        except Exception as e:
            logger.warning("History-Save fehlgeschlagen: %s", e)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self):
        """Tasks zu Disk speichern."""
        tmp = TASKS_FILE + ".tmp"
        with _tasks_lock:
            tasks_list = list(_TASKS.values())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(tasks_list, f, ensure_ascii=False, indent=2)
            os.replace(tmp, TASKS_FILE)
        except Exception as e:
            logger.error("Tasks-Save fehlgeschlagen: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass

    def load_from_disk(self):
        """Beim Start: offene Tasks von Disk laden."""
        if not os.path.exists(TASKS_FILE):
            return
        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                tasks = json.load(f)
            recovered = 0
            with _tasks_lock:
                for t in (tasks if isinstance(tasks, list) else tasks.values()):
                    if t.get("status") == "working":
                        t["status"] = "failed"
                        t["error"] = "Neustart während Ausführung"
                    if t.get("status") not in TERMINAL_STATES:
                        _TASKS[t["id"]] = t
                        recovered += 1
            if recovered:
                logger.info("%d offene Tasks von Disk geladen", recovered)
        except Exception as e:
            logger.error("Task-Load fehlgeschlagen: %s", e)

    def cleanup_old(self):
        """Tasks älter als TTL aus Memory entfernen."""
        cutoff = (datetime.now() - timedelta(seconds=_TASK_TTL_SECONDS)).isoformat()
        with _tasks_lock:
            to_remove = [
                tid for tid, t in _TASKS.items()
                if t.get("status") in ("completed", "failed", "cancelled")
                and (t.get("completed_at") or "9999") < cutoff
            ]
            for tid in to_remove:
                del _TASKS[tid]
        if to_remove:
            logger.info("Cleanup: %d alte Tasks entfernt", len(to_remove))
            self._save()

    def save_pending(self):
        """Beim Shutdown: alle Tasks speichern."""
        self._save()

    def _maybe_callback(self, task: dict):
        """
        Sendet Task-Resultat zum Sender-Agenten zurück (A2A Wait-Semantik).
        Zwei Modi:
          1. callback_url gesetzt → HTTP POST an Remote-Node
          2. sender_agent_id lokal → Chat-History + Event für den Sender
        """
        result_text = task.get("result_text")
        status = task.get("status")

        if status not in ("completed", "failed"):
            return

        # Modus 1: HTTP Callback (M2M Remote)
        callback_url = task.get("callback_url")
        if callback_url:
            try:
                import requests as req
                payload = {
                    "task_id": task["id"],
                    "origin_task_id": task.get("origin_task_id", task["id"]),
                    "status": status,
                    "result_text": result_text,
                    "result_image": task.get("result_image"),
                    "error": task.get("error"),
                    "agent_id": task["recipient_agent_id"],
                    "agent_name": task["recipient_agent_name"],
                }
                req.post(callback_url, json=payload, timeout=15)
                logger.info("Callback gesendet an %s für Task %s", callback_url, task["id"])
            except Exception as e:
                logger.warning("Callback fehlgeschlagen: %s", e)
            return

        # Modus 2: Lokaler Sender-Agent bekommt Ergebnis in seine History
        sender_id = task.get("sender_agent_id", "")
        if not sender_id or sender_id in ("user", "system", "inbox", ""):
            return
        if sender_id.startswith("remote::"):
            return  # Remote-Sender → Callback hätte greifen sollen

        if result_text and self._agents.get(sender_id):
            summary = f"📬 **@{task['recipient_agent_name']}** antwortete:\n\n{result_text[:500]}"
            if len(result_text) > 500:
                summary += "\n\n_[...]_"
            self._events.emit_chat_message(sender_id, "system", summary)
            logger.info(
                "A2A-Resultat weitergeleitet: @%s → @%s",
                task["recipient_agent_name"], task.get("sender_agent_name", sender_id)
            )
