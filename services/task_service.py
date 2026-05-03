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

# Operator-Supervisor-Loop: max. Anzahl Re-Entry-Turns beim Orchestrator,
# bevor wir ihn zwangsweise zum Abschluss zwingen. Schutz gegen Endlos-Schleifen.
MAX_SUPERVISOR_TURNS = 5

# Skills die parallel laufen dürfen — externe Queue (ComfyUI) verwaltet Reihenfolge selbst.
# Source of Truth: core.dispatch_rules.PARALLEL_SAFE_SKILLS. Hier nur Re-Export.
from core.dispatch_rules import PARALLEL_SAFE_SKILLS  # noqa: E402,F401

# Separater Lock für Disk-Save — verhindert Race Condition beim Schreiben von tasks.json.tmp
_save_lock = threading.Lock()

_MENTION_RX = re.compile(r"@([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_\- ]{1,40}?)(?=\s|$|[,.:!?])", re.UNICODE)

A2A_TASK_STATES = {
    "submitted": "Task received, waiting for processing",
    "working": "Task is actively being processed",
    "completed": "Task completed successfully",
    "failed": "Task failed with error",
    "canceled": "Task was canceled by client",
    "queued": "Task is queued (agent busy)",
    "waiting": "Task waiting for dependencies to complete",
    "halted_no_exec": "Execution-Intent detected but no execution-skill fired (possible hallucination)",
}

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected", "halted_no_exec"}


class TaskService:
    def __init__(self, agents: "AgentService", events: "EventService"):
        self._agents = agents
        self._events = events
        self._dispatcher = None  # Wird von init_services gesetzt
        self._chat_service = None  # Wird von init_services gesetzt (Supervisor-Callback)
        # Welche Dispatch-Gruppen haben bereits einen Supervisor-Callback gefeuert.
        # Schützt gegen Race-Condition wenn mehrere Tasks der Gruppe fast
        # gleichzeitig completen und jeder _maybe_supervisor_callback auslöst.
        self._fired_supervisor_dispatches: set[str] = set()
        self._supervisor_lock = threading.Lock()

    def set_dispatcher(self, dispatcher):
        """Dispatcher für Task-Verarbeitung registrieren."""
        self._dispatcher = dispatcher

    def set_chat_service(self, chat_service):
        """ChatService registrieren für Supervisor-Callback (Operator-Re-Entry)."""
        self._chat_service = chat_service

    # ── Queue Management ──────────────────────────────────────────────────────

    def enqueue(self, task: dict) -> tuple[bool, int]:
        """
        Task einreihen. Gibt (queued, position) zurück.
        Setzt Default-Priority 5 wenn nicht angegeben.
        Tasks mit `depends_on` warten bis alle Abhängigkeiten completed sind.
        """
        agent_id = task["recipient_agent_id"]
        # Default-Priority sicherstellen
        task.setdefault("priority", 5)
        # Circuit Breaker: Delegation-Tiefe prüfen
        if task.get("delegation_depth", 0) >= MAX_DELEGATION_DEPTH:
            task["status"] = "rejected"
            task["error"] = f"Max delegation depth ({MAX_DELEGATION_DEPTH}) erreicht"
            task["completed_at"] = datetime.now().isoformat()
            with _tasks_lock:
                _TASKS[task["id"]] = task
            self._save()
            logger.warning(
                "Task %s abgelehnt: Delegation-Tiefe %d >= %d",
                task.get("id", "?")[:8], task["delegation_depth"], MAX_DELEGATION_DEPTH
            )
            return (False, 0)
        is_waiting = False
        start_immediately = False
        queue_pos = 0
        with _tasks_lock:
            # Dependency-Check: Falls Abhängigkeiten vorhanden und noch nicht alle completed
            depends_on = task.get("depends_on", [])
            if depends_on:
                all_done = all(
                    _TASKS.get(dep_id, {}).get("status") == "completed"
                    for dep_id in depends_on
                )
                if not all_done:
                    task["status"] = "waiting"
                    _TASKS[task["id"]] = task
                    is_waiting = True

            if not is_waiting:
                # Prüfe ob Agent parallel-safe Skills hat (ComfyUI etc.)
                agent_data = self._agents.get(agent_id) or {}
                agent_skills = set(agent_data.get("skills", []))
                is_parallel_safe = bool(agent_skills) and agent_skills.issubset(PARALLEL_SAFE_SKILLS)

                busy = any(
                    t.get("recipient_agent_id") == agent_id
                    and t.get("status") in ("submitted", "working", "queued")
                    for t in _TASKS.values()
                )
                if busy and not is_parallel_safe:
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
                    start_immediately = True
                _TASKS[task["id"]] = task

        # _save() IMMER außerhalb des Locks aufrufen — sonst Deadlock!
        self._save()

        if is_waiting:
            logger.info("Task %s wartet auf %d Abhängigkeiten", task["id"][:8], len(depends_on))
            return (False, 0)

        if start_immediately:
            spawn_background(self.process, task["id"])
            return (False, 0)
        return (True, queue_pos + 1)

    def tick_queue(self):
        """
        Queued + Waiting Tasks starten wenn Agent frei wird / Abhängigkeiten erfüllt.
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

            # Waiting Tasks: prüfen ob Abhängigkeiten jetzt erfüllt sind
            for task in list(_TASKS.values()):
                if task.get("status") != "waiting":
                    continue
                depends_on = task.get("depends_on", [])
                # Cascade-Fail: Wenn eine Dependency failed/canceled → Task auch failen
                any_failed = any(
                    _TASKS.get(dep_id, {}).get("status") in ("failed", "canceled", "rejected")
                    for dep_id in depends_on
                )
                if any_failed:
                    task["status"] = "failed"
                    task["error"] = "Abhängiger Task fehlgeschlagen"
                    task["completed_at"] = datetime.now().isoformat()
                    logger.warning("Task %s failed (dependency failed)", task["id"][:8])
                    continue
                all_done = all(
                    _TASKS.get(dep_id, {}).get("status") == "completed"
                    for dep_id in depends_on
                )
                if not all_done:
                    continue
                # Alle Deps completed: Kumulativen Kontext aller Schritte aufbauen
                if depends_on:
                    last_dep = _TASKS.get(depends_on[-1], {})
                    prev_chain = last_dep.get("context_chain", [])
                    ctx_text = last_dep.get("result_text") or ""
                    ctx_image = last_dep.get("result_image")
                    ctx_agent = last_dep.get("recipient_agent_name", "")
                    new_chain = list(prev_chain)
                    if ctx_text or ctx_image:
                        new_chain.append({
                            "text": ctx_text,
                            "image": ctx_image,
                            "agent_name": ctx_agent,
                        })
                    if new_chain:
                        task["context_chain"] = new_chain
                        task["context_from_prev"] = new_chain[-1]  # Rückwärtskompatibilität
                # Timeout neu setzen — Task lag in "waiting", alter timeout könnte abgelaufen sein
                task["timeout_at"] = (datetime.now() + timedelta(seconds=1800)).isoformat()
                # Normalen Queue-Flow fortsetzen
                agent_id = task["recipient_agent_id"]
                if agent_id not in agents_working:
                    task["status"] = "submitted"
                    agents_working.add(agent_id)
                    to_start.append(task["id"])
                else:
                    task["status"] = "queued"
                    task["queued_at"] = datetime.now().isoformat()

            # Queued Tasks: Priority-Queue, höhere priority zuerst, FIFO
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
        try:
            timeout_at = datetime.fromisoformat(task.get("timeout_at", "9999-12-31T23:59:59"))
            timed_out = datetime.now() > timeout_at
        except (ValueError, TypeError):
            timed_out = True  # Ungültiges Format → sicherheitshalber failen
        if timed_out:
            task["status"] = "failed"
            task["error"] = "Timeout vor Ausführungsstart"
            task["completed_at"] = datetime.now().isoformat()
            self._save()
            return

        task["status"] = "working"
        self._save()

        # Kumulativen Kontext aller vorherigen Chain-Schritte injizieren
        context_chain = task.get("context_chain", [])
        if context_chain:
            chain_parts = []
            for entry in context_chain:
                if entry.get("text"):
                    chain_parts.append(f"[@{entry.get('agent_name', '?')}]:\n{entry['text']}")
            if chain_parts:
                task["message"] = (
                    f"[Ergebnisse vorheriger Schritte]:\n"
                    + "\n\n".join(chain_parts)
                    + f"\n\n---\nDeine Aufgabe: {task['message']}"
                )
            last_ctx = context_chain[-1]
            if last_ctx.get("image") and not task.get("images"):
                task["context_image"] = last_ctx["image"]
        elif task.get("context_from_prev"):
            # Fallback (Rückwärtskompatibilität für ältere Tasks)
            ctx = task["context_from_prev"]
            ctx_text = ctx.get("text", "")
            ctx_agent = ctx.get("agent_name", "vorheriger Schritt")
            if ctx_text:
                task["message"] = (
                    f"[Ergebnis von @{ctx_agent}]:\n{ctx_text}\n\n"
                    f"---\nDeine Aufgabe: {task['message']}"
                )
            if ctx.get("image") and not task.get("images"):
                task["context_image"] = ctx["image"]

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

        # Bild/Ergebnis als chat_message für den SENDER emittieren
        # → Chat-UI des Senders zeigt das Bild automatisch an
        # AUSNAHME: Chain-Tasks (chain_index gesetzt) werden via Chain-Karte im UI angezeigt
        #           → kein separates emit, sonst erscheint jedes Ergebnis doppelt
        is_chain_task = "chain_index" in task
        if task["status"] in ("completed", "halted_no_exec") and not is_chain_task:
            sender_id = task.get("sender_agent_id")
            result_image = task.get("result_image")
            result_text = task.get("result_text") or ""
            recipient_name = task.get("recipient_agent_name", "Agent")
            if sender_id and (result_image or result_text):
                prefix = f"[@{recipient_name}]:"
                display_text = f"{prefix} {result_text}".strip() if result_text else prefix
                self._events.emit_chat_message(
                    sender_id, "assistant", display_text, image=result_image
                )

        # Callback-URL: Ergebnis aktiv zurück zum Sender senden (optional)
        self._maybe_callback(task)

        # Operator-Supervisor-Loop: Wenn dieser Task zu einer Operator-Dispatch-Gruppe
        # gehört (parent_dispatch_id gesetzt) und jetzt ALLE Tasks dieser Gruppe terminal
        # sind → synthetischen Chat-Turn beim Operator auslösen.
        self._maybe_supervisor_callback(task)

    def _apply_result(self, task, result):
        """Skill-Result auf Task anwenden."""
        from skills.base import SkillResult
        from storage.files import persist_image_field
        if isinstance(result, SkillResult):
            if result.error:
                logger.error("Skill-Fehler in Task %s: %s", task["id"], result.error)
                raise RuntimeError(result.error)   # → landet in _fail()
            task["result_text"] = result.text
            # Base64-data-URIs werden als Datei abgelegt; DB hält nur den /static/...-Pfad.
            task["result_image"] = persist_image_field(result.image, name_hint=task["id"])
            task["skill_used"] = result.skill_used
            if getattr(result, "metadata", None):
                task["metadata"] = result.metadata
        elif isinstance(result, dict):
            task.update(result)

    def _complete(self, task):
        """Task als completed markieren. Triggert Dependency-Queue für Folgetasks.

        Audit: Wenn metadata.executed==False → Status 'halted_no_exec' statt 'completed'.
        Das signalisiert Supervisor/UI, dass die Antwort kein tatsächlicher Tool-Output ist
        (siehe core.intent_detector + chat_service Guard).
        """
        metadata = task.get("metadata") or {}
        if metadata.get("executed") is False:
            task["status"] = "halted_no_exec"
            task["completed_at"] = datetime.now().isoformat()
            logger.warning(
                "Task %s HALTED_NO_EXEC via %s — intent=%s target=%s "
                "(kein Execution-Skill gefeuert, Reply möglicherweise halluziniert)",
                task["id"], task.get("skill_used"),
                metadata.get("intent_kind"), metadata.get("intent_target"),
            )
            return

        task["status"] = "completed"
        task["completed_at"] = datetime.now().isoformat()
        logger.info("Task %s abgeschlossen via %s", task["id"], task.get("skill_used"))
        # History-Eintrag
        if not task.get("chat_mode"):
            self._save_to_history(task)
        # Folgetasks aus "waiting" in Queue promoten
        self._save()
        self.tick_queue()

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
            from storage.history import append_message
            ts = datetime.now().isoformat()
            recipient_id  = task["recipient_agent_id"]
            sender_id     = task.get("sender_agent_id", "")
            result_text   = task.get("result_text") or ""
            result_image  = task.get("result_image") or ""   # /static/...-Pfad oder leer
            recipient_name = task.get("recipient_agent_name", "Agent")
            sender_name    = task.get("sender_agent_name", "?")
            skill_used     = task.get("skill_used") or ""

            if not result_text and not result_image:
                return

            # Beim Empfänger-Agenten speichern
            append_message(
                recipient_id, "assistant",
                result_text or f"[Task von @{sender_name} abgeschlossen]",
                image=result_image, skill_used=skill_used, ts=ts,
            )

            # Beim Sender-Agenten speichern (nur wenn echter Agent, nicht User/System)
            if sender_id and sender_id not in ("system", "inbox", "user", ""):
                append_message(
                    sender_id, "assistant",
                    result_text or f"[@{recipient_name} hat den Task abgeschlossen]",
                    image=result_image, skill_used=skill_used, ts=ts,
                )

        except Exception as e:
            logger.warning("History-Save fehlgeschlagen: %s", e)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self):
        """
        Tasks persistent speichern.
        1. SQLite (primary — crash-sicher, transaktional)
        2. tasks.json (Backup — human-readable)
        Beide Operationen sind außerhalb des _tasks_lock.
        """
        with _tasks_lock:
            tasks_list = list(_TASKS.values())

        # SQLite: primary source
        self._save_to_sqlite(tasks_list)

        # JSON: backup
        self._save_json_backup(tasks_list)

    def _save_to_sqlite(self, tasks_list: list):
        """Write-Through aller Tasks in SQLite."""
        try:
            from storage.database import upsert_tasks_bulk
            upsert_tasks_bulk(tasks_list)
        except Exception as e:
            logger.error("SQLite tasks-save fehlgeschlagen: %s", e)

    def _save_json_backup(self, tasks_list: list):
        """JSON-Backup (nicht-kritisch)."""
        with _save_lock:
            tmp = TASKS_FILE + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(tasks_list, f, ensure_ascii=False, indent=2)
                os.replace(tmp, TASKS_FILE)
            except Exception as e:
                logger.warning("tasks.json Backup fehlgeschlagen (nicht kritisch): %s", e)
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def load_from_disk(self):
        """
        Beim Start: offene Tasks wiederherstellen.
        1. SQLite (primary — vollständig und transaktional)
        2. tasks.json Fallback (falls SQLite leer)
        """
        recovered = self._load_from_sqlite()
        if not recovered:
            recovered = self._load_from_json()
        if recovered:
            logger.info("%d offene Tasks wiederhergestellt", recovered)

    def _load_from_sqlite(self) -> int:
        """Lädt offene Tasks aus SQLite. Gibt Anzahl geladener Tasks zurück."""
        try:
            from storage.database import load_open_tasks
            open_tasks = load_open_tasks()
            if not open_tasks:
                return 0
            recovered = 0
            with _tasks_lock:
                for t in open_tasks:
                    if t.get("status") == "working":
                        t["status"] = "failed"
                        t["error"] = "Neustart während Ausführung"
                        # Status in SQLite aktualisieren
                        try:
                            from storage.database import upsert_task
                            upsert_task(t)
                        except Exception:
                            pass
                    if t.get("status") not in TERMINAL_STATES:
                        _TASKS[t["id"]] = t
                        recovered += 1
            return recovered
        except Exception as e:
            logger.error("SQLite task-load fehlgeschlagen: %s", e)
            return 0

    def _load_from_json(self) -> int:
        """Fallback: Tasks aus tasks.json laden (falls SQLite leer)."""
        if not os.path.exists(TASKS_FILE):
            return 0
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
                logger.info("tasks.json Fallback: %d Tasks geladen", recovered)
                # Direkt in SQLite schreiben
                self._save_to_sqlite(list(_TASKS.values()))
            return recovered
        except Exception as e:
            logger.error("tasks.json load fehlgeschlagen: %s", e)
            return 0

    def cleanup_old(self):
        """Tasks älter als TTL aus Memory + SQLite entfernen + Hard-Limit 500."""
        cutoff = (datetime.now() - timedelta(seconds=_TASK_TTL_SECONDS)).isoformat()
        with _tasks_lock:
            # 1. Abgelaufene Terminal-Tasks aus Memory entfernen
            to_remove = [
                tid for tid, t in _TASKS.items()
                if t.get("status") in TERMINAL_STATES
                and (t.get("completed_at") or "9999") < cutoff
            ]
            for tid in to_remove:
                del _TASKS[tid]

            # 2. Hard-Limit: max 500 Tasks, älteste abgeschlossene zuerst entfernen
            if len(_TASKS) > 500:
                terminal = [
                    (tid, t.get("completed_at") or "")
                    for tid, t in _TASKS.items()
                    if t.get("status") in TERMINAL_STATES
                ]
                terminal.sort(key=lambda x: x[1])
                excess = len(_TASKS) - 500
                for tid, _ in terminal[:excess]:
                    del _TASKS[tid]
                    to_remove.append(tid)

        if to_remove:
            logger.info("Cleanup: %d alte Tasks aus Memory entfernt", len(to_remove))
            # SQLite bereinigen
            try:
                from storage.database import delete_old_tasks
                db_removed = delete_old_tasks(cutoff)
                if db_removed:
                    logger.debug("Cleanup SQLite: %d alte Tasks gelöscht", db_removed)
            except Exception as e:
                logger.warning("SQLite task-cleanup fehlgeschlagen: %s", e)
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

        if status not in ("completed", "failed", "halted_no_exec"):
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
        # Skip wenn dieser Task zu einer Operator-Dispatch-Gruppe gehört —
        # der Supervisor-Callback liefert das aggregierte Ergebnis, einzelne
        # Chat-Events würden nur duplizieren.
        if task.get("parent_dispatch_id"):
            return

        if result_text and self._agents.get(sender_id):
            summary = f"📬 **@{task['recipient_agent_name']}** antwortete:\n\n{result_text[:500]}"
            if len(result_text) > 500:
                summary += "\n\n_[...]_"
            self._events.emit_chat_message(sender_id, "system", summary)
            logger.info(
                "A2A-Resultat weitergeleitet: @%s → @%s",
                task["recipient_agent_name"], task.get("sender_agent_name", sender_id)
            )

    # ── Operator-Supervisor-Loop ──────────────────────────────────────────────

    def _maybe_supervisor_callback(self, task: dict):
        """
        Prüft ob dieser Task Teil einer Operator-Dispatch-Gruppe ist und ob alle
        Tasks dieser Gruppe jetzt terminal sind. Wenn ja → Callback auslösen.
        """
        dispatch_id = task.get("parent_dispatch_id")
        if not dispatch_id:
            return
        if not self._chat_service:
            logger.debug("Supervisor-Callback: ChatService nicht registriert")
            return

        # Double-fire-Schutz: ein Dispatch wird nur einmal abgefeuert.
        with self._supervisor_lock:
            if dispatch_id in self._fired_supervisor_dispatches:
                return

            with _tasks_lock:
                group = [
                    t for t in _TASKS.values()
                    if t.get("parent_dispatch_id") == dispatch_id
                ]

            if not group:
                return
            if not all(t.get("status") in TERMINAL_STATES for t in group):
                return  # noch nicht alle fertig

            # Mark as fired *innerhalb* des Locks
            self._fired_supervisor_dispatches.add(dispatch_id)

        sender_id = task.get("sender_agent_id", "")
        sender_agent = self._agents.get(sender_id) if sender_id else None
        if not sender_agent or not sender_agent.get("operator", False):
            logger.warning(
                "Supervisor-Callback: Dispatch %s hat keinen Operator-Sender (%s)",
                dispatch_id[:8], sender_id,
            )
            return

        current_turn = max((t.get("supervisor_turn", 0) for t in group), default=0)
        spawn_background(
            self._trigger_supervisor_callback,
            dispatch_id, sender_agent, group, current_turn,
        )

    def _trigger_supervisor_callback(
        self,
        dispatch_id: str,
        sender_agent: dict,
        group: list,
        current_turn: int,
    ):
        """
        Baut eine synthetische User-Message mit allen Ergebnissen der Gruppe
        und reicht sie via ChatService.handle_message beim Operator ein. Dort
        entscheidet der Operator: weitere TASKLIST dispatchen oder finale Antwort.
        """
        group_sorted = sorted(group, key=lambda t: t.get("created_at", ""))
        parts = [
            f"[SUPERVISOR-CALLBACK — Dispatch {dispatch_id[:8]}, Runde {current_turn}]",
            "",
            f"Alle {len(group_sorted)} Tasks der letzten Runde sind abgeschlossen. Ergebnisse:",
            "",
        ]
        for i, t in enumerate(group_sorted):
            recipient = t.get("recipient_agent_name", "?")
            status = t.get("status", "?")
            task_msg = (t.get("message") or "").split("\n---\n")[-1].strip()[:200]
            result = (t.get("result_text") or "").strip()
            err = (t.get("error") or "").strip()
            parts.append(f"### {i+1}. @{recipient} — {status}")
            parts.append(f"**Auftrag:** {task_msg}")
            if result:
                parts.append(f"**Ergebnis:**\n{result}")
            if err:
                parts.append(f"**Fehler:** {err}")
            parts.append("")

        parts.append("---")
        if current_turn >= MAX_SUPERVISOR_TURNS:
            parts.append(
                f"⚠ **MAX ROUNDS ({MAX_SUPERVISOR_TURNS}) ERREICHT.** "
                "Du darfst KEINE weitere [tasklist] oder @Mention dispatchen. "
                "Liefere jetzt eine finale Zusammenfassung für den User."
            )
        else:
            parts.append(
                "Entscheide: (a) Wenn alles passt → liefere eine knappe finale "
                "Antwort an den User ohne weitere Delegation. "
                "(b) Wenn nachgebessert werden muss → dispatche eine neue "
                "[tasklist] mit den nötigen Revisionen. "
                f"Diese Runde ist {current_turn}/{MAX_SUPERVISOR_TURNS}."
            )

        synthetic_msg = "\n".join(parts)
        sender_id = sender_agent["id"]

        logger.info(
            "Supervisor-Callback: @%s turn=%d, %d tasks in dispatch %s",
            sender_agent.get("name"), current_turn, len(group), dispatch_id[:8],
        )

        try:
            self._chat_service.handle_message(
                sender_id,
                synthetic_msg,
                _supervisor_turn=current_turn,
            )
        except Exception as e:
            logger.exception("Supervisor-Callback an @%s fehlgeschlagen: %s",
                             sender_agent.get("name"), e)
