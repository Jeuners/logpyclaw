"""
lab/core/mock_agent.py — Leichtgewichtiger Test-Agent.

Kein LLM, keine DB. Hat eine Inbox (Queue) und einen Worker-Thread.
Verarbeitet Messages nach einer Policy.

Policies (klein und kombinierbar):
- "echo"      : antwortet sofort mit "done: <content>"
- "delegator" : delegiert an delegate_to[], wartet auf alle Antworten, gibt Ergebnis zurück
- "slow"      : braucht delay_sec Sekunden, dann echo
- "silent"    : antwortet nie (für Timeout-Test)
- "flaky"     : wirft mit Wahrscheinlichkeit p einen Error
"""
from __future__ import annotations
import queue
import random
import threading
import time
from dataclasses import dataclass, field

from . import store, tracer
from .protocol import (
    CausalDilationClock, Message, MessageType, TaskRecord, TaskState, agent_id, new_task_id
)


@dataclass
class AgentConfig:
    name: str                          # "martin"
    policy: str = "echo"               # echo | delegator | slow | silent | flaky | reviewer | qc_delegator
    delegates_to: list[str] = field(default_factory=list)  # nur Namen, ohne lab:-Prefix
    delay_sec: float = 0.0             # für slow/echo: künstliche Verarbeitungszeit
    error_prob: float = 0.0            # für flaky: 0..1
    label: str = ""                    # menschenlesbar in der UI
    # QC-Felder (für qc_delegator Policy)
    qc_agent: str = ""                 # Name des Reviewer-Agenten
    qc_rate: float = 0.6              # Wahrscheinlichkeit dass QC ausgelöst wird
    qc_min_score: int = 7             # Mindestscore für Approval (1-10)
    qc_max_retries: int = 2           # Max. Wiederholungsversuche bei Ablehnung


class MockAgent:
    """Inbox-basierter Test-Agent. Läuft in eigenem Thread."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.id = agent_id(config.name)
        self.label = config.label or config.name.title()
        self._inbox: queue.Queue[Message] = queue.Queue()
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # ── Causal-Dilation Clock — Eigenzeit dieses Agenten ──────────────
        self._clock = CausalDilationClock()
        self._ops: int = 0
        self._born_at: float = time.time()
        self._last_op_at: float = time.time()

        # Pending-Delegation State (für delegator policy):
        # task_id → {
        #   "request": <original Message>,
        #   "expecting": set of sub_task_ids noch ausstehend,
        #   "results": {target_name: result_str},
        #   "errors":  {target_name: reason},
        # }
        self._pending_delegations: dict[str, dict] = {}
        # sub_task_id → parent_task_id (für schnellen Lookup wenn Antwort eintrifft)
        self._sub_to_parent: dict[str, str] = {}
        # sub_task_id → threading.Event + result für _wait_for_sub (qc_delegator)
        self._sync_waits: dict[str, dict] = {}

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"MockAgent-{self.config.name}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        # Wake up the inbox-blocking get() with a sentinel
        try:
            self._inbox.put_nowait(None)  # type: ignore
        except Exception:
            pass

    # ── Public API: Message senden ─────────────────────────────────────────

    def receive(self, msg: Message) -> None:
        """Wird vom Sender aufgerufen — packt Message in Inbox."""
        self._inbox.put(msg)

    # ── Causal-Dilation Clock Helpers ──────────────────────────────────────

    def _advance_clock(self, incoming: CausalDilationClock | None = None) -> CausalDilationClock:
        """Eigenzeit-Tick: Operation zählen, Dilation berechnen, Clock mergen."""
        now = time.time()
        self._ops += 1
        # Dilation-Rate = Eigenzeit-Ops pro Sekunde seit Geburt
        age = max(now - self._born_at, 0.001)
        rate = self._ops / age
        self._last_op_at = now

        # Merge mit eingehender Clock, dann eigenen Tick (in-place, Core-API)
        if incoming:
            self._clock.merge_lab(incoming)
        self._clock.tick_lab(self.id, rate)
        return self._clock

    # ── Worker-Loop ────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                msg = self._inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg is None:
                break
            try:
                self._handle(msg)
            except Exception as e:
                tracer.emit(msg.mission_id, "agent_crash",
                            agent=self.id, task_id=msg.task_id, error=str(e))

    def _handle(self, msg: Message) -> None:
        if msg.type == MessageType.REQUEST:
            self._handle_request(msg)
        elif msg.type in (MessageType.RESPONSE, MessageType.ERROR):
            self._handle_response(msg)
        elif msg.type == MessageType.CANCEL:
            self._handle_cancel(msg)

    def _handle_request(self, msg: Message) -> None:
        """Eingehender Auftrag — nach Policy verarbeiten."""
        # Clock advance: eingehende Clock mergen + eigenen Tick
        clock = self._advance_clock(msg.clock)

        # Task-Record anlegen
        task = TaskRecord(
            task_id=msg.task_id,
            mission_id=msg.mission_id,
            parent_task_id=msg.parent_task_id,
            owner=self.id,
            requester=msg.sender,
            content=msg.payload.get("content", ""),
            state=TaskState.ASSIGNED,
        )
        store.upsert_task(task)
        tracer.emit(msg.mission_id, "task_assigned",
                    task_id=task.task_id, agent=self.id, requester=msg.sender,
                    content=task.content, parent=task.parent_task_id,
                    clock=clock.to_dict(), time_feel=clock.llm_summary())

        # State: RUNNING
        task.transition(TaskState.RUNNING)
        store.upsert_task(task)
        tracer.emit(msg.mission_id, "task_started",
                    task_id=task.task_id, agent=self.id,
                    clock=clock.to_dict(), time_feel=clock.llm_summary())

        # Policy-Auswahl
        policy = self.config.policy
        try:
            if policy == "silent":
                return  # Watchdog timeoutet
            if policy == "flaky" and random.random() < self.config.error_prob:
                self._reply_error(msg, task, "flaky failure")
                return
            if policy == "slow":
                self._sleep_with_heartbeat(msg.mission_id, task)
                self._reply_ok(msg, task, f"done: {task.content}")
                return
            if policy == "delegator":
                self._do_delegate(msg, task)
                return
            if policy == "qc_delegator":
                # Eigener Thread — Worker-Loop bleibt frei für eingehende Responses
                t = threading.Thread(
                    target=self._do_qc_delegate, args=(msg, task), daemon=True,
                    name=f"QCDelegate-{self.config.name}-{task.task_id[:6]}"
                )
                t.start()
                return
            if policy == "reviewer":
                self._do_review(msg, task)
                return
            # default: echo
            if self.config.delay_sec > 0:
                self._sleep_with_heartbeat(msg.mission_id, task)
            self._reply_ok(msg, task, f"done: {task.content}")
        except Exception as e:
            self._reply_error(msg, task, f"handler crash: {e}")

    def _sleep_with_heartbeat(self, mission_id: str, task: TaskRecord) -> None:
        """Verzögerung mit periodischen Heartbeats (für Watchdog)."""
        end = time.time() + self.config.delay_sec
        while time.time() < end:
            if self._stop_evt.is_set():
                return
            time.sleep(min(1.0, end - time.time()))
            task.heartbeat()
            store.upsert_task(task)
            tracer.emit(mission_id, "task_heartbeat",
                        task_id=task.task_id, agent=self.id)

    def _do_delegate(self, msg: Message, task: TaskRecord) -> None:
        """Delegator: schickt Sub-Task an jeden in delegates_to. KEINE Blockierung —
        der Worker-Loop kehrt sofort zur Inbox zurück. Antworten werden in
        _handle_response gesammelt und _maybe_aggregate dort ausgelöst."""
        if not self.config.delegates_to:
            self._reply_error(msg, task, "delegator hat keine delegates_to")
            return

        sub_ids: set[str] = set()
        for target_name in self.config.delegates_to:
            target_id = agent_id(target_name)
            target = store.get_agent(target_id)
            if target is None:
                self._reply_error(msg, task, f"unbekannter Empfänger: {target_id}")
                return

            clock = self._advance_clock()
            sub = Message.request(
                mission_id=msg.mission_id,
                sender=self.id,
                recipient=target_id,
                content=f"[sub of {task.task_id[:8]}] {task.content}",
                parent_task_id=task.task_id,
                clock=clock,
            )
            task.sub_task_ids.append(sub.task_id)
            sub_ids.add(sub.task_id)
            with self._lock:
                self._sub_to_parent[sub.task_id] = task.task_id
            self._send(sub)

        store.upsert_task(task)
        # State: WAITING auf alle Subs
        task.transition(TaskState.WAITING)
        store.upsert_task(task)
        tracer.emit(msg.mission_id, "task_waiting",
                    task_id=task.task_id, agent=self.id,
                    sub_tasks=list(sub_ids))

        # Pending-State merken — wird in _handle_response abgearbeitet
        with self._lock:
            self._pending_delegations[task.task_id] = {
                "request": msg,
                "expecting": sub_ids,
                "results": {},
                "errors": {},
                "targets": {agent_id(n): n for n in self.config.delegates_to},
            }

    def _do_review(self, msg: Message, task: TaskRecord) -> None:
        """Reviewer-Policy: bewertet das Ergebnis im Payload mit Score 1-10.

        Payload-Konvention: {"content": "Bewerte: <original_task> | Ergebnis: <result>"}
        Antwort:            {"score": 8, "approved": True, "feedback": "..."}
        """
        content = task.content
        # Score-Simulation: basierend auf Inhaltslänge + Zufallsfaktor
        # In echtem System: LLM-Call mit Rubric
        if self.config.delay_sec > 0:
            self._sleep_with_heartbeat(msg.mission_id, task)

        base = 6 + random.randint(0, 4)          # Basisqualität 6-10
        penalty = 2 if len(content) < 20 else 0  # Kurze Ergebnisse schlechter
        score = max(1, min(10, base - penalty))
        approved = score >= self.config.qc_min_score

        result = f"qc_score={score}/10 approved={approved} feedback={'solid work' if approved else 'needs improvement'}"
        tracer.emit(msg.mission_id, "qc_review",
                    task_id=task.task_id, agent=self.id,
                    score=score, approved=approved,
                    clock=self._clock.to_dict(), time_feel=self._clock.llm_summary())
        self._reply_ok(msg, task, result)

    def _do_qc_delegate(self, msg: Message, task: TaskRecord) -> None:
        """QC-Delegator: schickt Task an Executor. Bei 60% der Fälle prüft
        der Reviewer das Ergebnis. Bei Score < min_score → Retry bis max_retries."""
        if not self.config.delegates_to:
            self._reply_error(msg, task, "qc_delegator hat keine delegates_to (executor)")
            return

        executor_name = self.config.delegates_to[0]
        executor_id = agent_id(executor_name)
        executor = store.get_agent(executor_id)
        if not executor:
            self._reply_error(msg, task, f"Executor {executor_id} nicht gefunden")
            return

        reviewer_id = agent_id(self.config.qc_agent) if self.config.qc_agent else None
        reviewer = store.get_agent(reviewer_id) if reviewer_id else None

        for attempt in range(1, self.config.qc_max_retries + 2):
            # ── Executor beauftragen ──────────────────────────────────────────
            clock = self._advance_clock()
            exec_msg = Message.request(
                mission_id=msg.mission_id,
                sender=self.id,
                recipient=executor_id,
                content=f"[attempt {attempt}] {task.content}",
                parent_task_id=task.task_id,
                clock=clock,
            )
            with self._lock:
                self._sub_to_parent[exec_msg.task_id] = task.task_id
                self._pending_delegations[task.task_id] = {
                    "request": msg,
                    "task": task,
                    "expecting": {exec_msg.task_id},
                    "results": {},
                    "errors": {},
                    "targets": {executor_id: executor_name},
                    "_qc_mode": True,      # Flag: nach Aggregation QC durchführen
                    "_attempt": attempt,
                }
            self._send(exec_msg)
            task.transition(TaskState.WAITING)
            store.upsert_task(task)
            tracer.emit(msg.mission_id, "qc_executor_sent",
                        task_id=task.task_id, attempt=attempt, agent=self.id)

            # ── Auf Executor warten (via Inbox — blockiert diesen Thread) ────
            exec_result = self._wait_for_sub(exec_msg.task_id, msg.mission_id, task)
            if exec_result is None:
                self._reply_error(msg, task, f"executor timeout (attempt {attempt})")
                return

            tracer.emit(msg.mission_id, "qc_executor_result",
                        task_id=task.task_id, attempt=attempt,
                        result=exec_result[:80], agent=self.id)

            # ── QC-Trigger: 60% der Fälle ────────────────────────────────────
            do_qc = reviewer is not None and random.random() < self.config.qc_rate
            if not do_qc:
                tracer.emit(msg.mission_id, "qc_skipped",
                            task_id=task.task_id, attempt=attempt,
                            reason=f"qc_rate={self.config.qc_rate:.0%} — not triggered")
                self._reply_ok(msg, task, f"[no-qc] {exec_result}")
                return

            # ── Reviewer beauftragen ──────────────────────────────────────────
            clock = self._advance_clock()
            review_content = f"Bewerte: {task.content} | Ergebnis: {exec_result}"
            rev_msg = Message.request(
                mission_id=msg.mission_id,
                sender=self.id,
                recipient=reviewer_id,
                content=review_content,
                parent_task_id=task.task_id,
                clock=clock,
            )
            with self._lock:
                self._sub_to_parent[rev_msg.task_id] = task.task_id
            self._send(rev_msg)
            task.transition(TaskState.WAITING)
            store.upsert_task(task)

            review_result = self._wait_for_sub(rev_msg.task_id, msg.mission_id, task)
            if review_result is None:
                # Reviewer timeout → trotzdem Result durchreichen
                tracer.emit(msg.mission_id, "qc_reviewer_timeout",
                            task_id=task.task_id, attempt=attempt)
                self._reply_ok(msg, task, f"[reviewer-timeout] {exec_result}")
                return

            # Score parsen
            score = 7  # Default wenn Parsing fehlschlägt
            try:
                for part in review_result.split():
                    if part.startswith("qc_score="):
                        score = int(part.split("=")[1].split("/")[0])
            except Exception:
                pass

            approved = score >= self.config.qc_min_score
            tracer.emit(msg.mission_id, "qc_decision",
                        task_id=task.task_id, attempt=attempt,
                        score=score, approved=approved,
                        min_score=self.config.qc_min_score)

            if approved:
                self._reply_ok(msg, task,
                               f"[qc approved score={score}/10 attempt={attempt}] {exec_result}")
                return

            # Nicht approved → retry wenn noch Versuche übrig
            if attempt >= self.config.qc_max_retries + 1:
                self._reply_error(msg, task,
                                  f"qc failed after {attempt} attempts, last score={score}/10")
                return

            tracer.emit(msg.mission_id, "qc_retry",
                        task_id=task.task_id, attempt=attempt, score=score)
            task.transition(TaskState.RUNNING)
            store.upsert_task(task)

    def _wait_for_sub(self, sub_task_id: str, mission_id: str,
                      task: TaskRecord, timeout: float = 30.0) -> str | None:
        """Blockiert (mit Heartbeat) bis Sub-Task antwortet. None bei Timeout."""
        import threading as _th
        evt = _th.Event()
        slot: dict = {"result": None, "error": None}
        with self._lock:
            self._sync_waits[sub_task_id] = {"evt": evt, "slot": slot}

        deadline = time.time() + timeout
        while time.time() < deadline:
            if evt.wait(timeout=1.0):
                break
            task.heartbeat()
            store.upsert_task(task)

        with self._lock:
            self._sync_waits.pop(sub_task_id, None)

        if slot["error"]:
            return None
        return slot["result"]

    def _handle_response(self, msg: Message) -> None:
        """Antwort auf einen Sub-Task.
        1. _sync_waits prüfen (qc_delegator blockiert dort)
        2. Dann normale Delegator-Aggregation."""
        # ── Sync-Wait Signal (für qc_delegator) ──────────────────────────────
        with self._lock:
            wait_slot = self._sync_waits.get(msg.task_id)
        if wait_slot:
            slot = wait_slot["slot"]
            if msg.type == MessageType.ERROR:
                slot["error"] = msg.payload.get("reason", "error")
            else:
                slot["result"] = str(msg.payload.get("result", ""))
            wait_slot["evt"].set()
            # sub_to_parent cleanup
            with self._lock:
                self._sub_to_parent.pop(msg.task_id, None)
            return

        # ── Normale Delegator-Aggregation ─────────────────────────────────────
        with self._lock:
            parent_id = self._sub_to_parent.pop(msg.task_id, None)
            pending = self._pending_delegations.get(parent_id) if parent_id else None

        if not pending:
            tracer.emit(msg.mission_id, "orphan_response",
                        task_id=msg.task_id, agent=self.id,
                        info="kein passender Delegator gefunden")
            return

        target_name = pending["targets"].get(msg.sender, msg.sender)
        with self._lock:
            pending["expecting"].discard(msg.task_id)
            if msg.type == MessageType.ERROR:
                pending["errors"][target_name] = msg.payload.get("reason", "")
            else:
                pending["results"][target_name] = str(msg.payload.get("result", ""))

        self._maybe_aggregate(parent_id)

    def _maybe_aggregate(self, parent_task_id: str) -> None:
        """Wenn alle Sub-Antworten eingetroffen sind: Parent-Task abschließen."""
        with self._lock:
            pending = self._pending_delegations.get(parent_task_id)
            if not pending or pending["expecting"]:
                return  # noch nicht alle Antworten da
            # Aus Pending entfernen damit _maybe_aggregate idempotent ist
            self._pending_delegations.pop(parent_task_id, None)

        original = pending["request"]
        parent_task = store.get_task(parent_task_id)
        if parent_task is None:
            return

        if pending["errors"]:
            err_summary = " | ".join(f"{k}: {v}" for k, v in pending["errors"].items())
            self._reply_error(original, parent_task,
                              f"sub-error(s): {err_summary}")
            return

        agg = " | ".join(f"{k}: {v}" for k, v in pending["results"].items())
        self._reply_ok(original, parent_task,
                       f"aggregated[{self.config.name}]: {agg}")

    def _handle_cancel(self, msg: Message) -> None:
        task = store.get_task(msg.task_id)
        if task and task.owner == self.id and not task.state.is_terminal:
            task.transition(TaskState.CANCELED)
            store.upsert_task(task)
            tracer.emit(msg.mission_id, "task_canceled",
                        task_id=task.task_id, agent=self.id)

    # ── Reply Helpers ─────────────────────────────────────────────────────

    def _reply_ok(self, original: Message, task: TaskRecord, result: str) -> None:
        clock = self._advance_clock()
        task.result = result
        task.transition(TaskState.COMPLETED)
        store.upsert_task(task)
        tracer.emit(original.mission_id, "task_completed",
                    task_id=task.task_id, agent=self.id, result=result,
                    clock=clock.to_dict(), time_feel=clock.llm_summary())
        self._send(Message.response(original, result, clock=clock))

    def _reply_error(self, original: Message, task: TaskRecord, reason: str) -> None:
        clock = self._advance_clock()
        task.error = reason
        task.transition(TaskState.FAILED)
        store.upsert_task(task)
        tracer.emit(original.mission_id, "task_failed",
                    task_id=task.task_id, agent=self.id, error=reason,
                    clock=clock.to_dict(), time_feel=clock.llm_summary())
        self._send(Message.error(original, reason, clock=clock))

    def _send(self, msg: Message) -> None:
        """Message an Empfänger zustellen + im Trace recorden."""
        store.record_message(msg)
        tracer.emit(msg.mission_id, "message",
                    msg_id=msg.msg_id, type=msg.type.value,
                    sender=msg.sender, recipient=msg.recipient,
                    task_id=msg.task_id, payload=msg.payload,
                    clock=msg.clock.to_dict(),
                    time_feel=msg.clock.llm_summary())
        recipient = store.get_agent(msg.recipient)
        if recipient is None:
            tracer.emit(msg.mission_id, "delivery_failed",
                        msg_id=msg.msg_id, recipient=msg.recipient,
                        reason="recipient not registered")
            return
        recipient.receive(msg)

    # ── Snapshot für UI ───────────────────────────────────────────────────

    def to_dict(self) -> dict:
        clock = self._clock.to_dict()
        # Core CDC: vector = Lamport-Ticks (= Eigenzeit), dilation = rate
        ez = clock.get("vector", {}).get(self.id, 0)
        rate = clock.get("dilation", {}).get(self.id, 0.0)
        return {
            "id": self.id,
            "name": self.config.name,
            "label": self.label,
            "policy": self.config.policy,
            "delegates_to": [agent_id(n) for n in self.config.delegates_to],
            "delay_sec": self.config.delay_sec,
            "error_prob": self.config.error_prob,
            "qc_agent": self.config.qc_agent,
            "qc_rate": self.config.qc_rate,
            "running": self._thread.is_alive() if self._thread else False,
            "inbox_size": self._inbox.qsize(),
            "ops": self._ops,
            "eigenzeit": ez,
            "dilation_rate": round(rate, 4),
            "time_feel": self._clock.llm_summary(),
        }
