"""
L2-Tests für TaskService — Queue-Logik, Dependency-Handling, Delegation-Guard.

Verwendet sync_spawn + mock_llm Fixtures aus conftest.py damit Tests
deterministisch ohne Threads und ohne echten Ollama-Call laufen.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from core.state import _TASKS


def _build_task(recipient_id: str, recipient_name: str, **overrides) -> dict:
    now = datetime.now()
    t = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": "user",
        "sender_agent_name": "User",
        "recipient_agent_id": recipient_id,
        "recipient_agent_name": recipient_name,
        "message": "Sag einfach Hallo.",
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=600)).isoformat(),
        "delegation_depth": 0,
    }
    t.update(overrides)
    return t


# ── enqueue: Grund-Pfade ─────────────────────────────────────────────────────

def test_enqueue_starts_immediately_when_agent_idle(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """Frisch eingereihter Task ohne Konflikt → sofort 'submitted' und verarbeitet."""
    mock_llm.set_reply("Hallo!")
    agent = make_agent("Solo")
    task = _build_task(agent["id"], agent["name"])

    queued, pos = container.tasks.enqueue(task)

    # sync_spawn führt process() direkt aus → Task sollte completed sein
    stored = _TASKS[task["id"]]
    assert stored["status"] == "completed"
    assert stored["result_text"] == "Hallo!"
    assert queued is False  # start_immediately → return (False, 0)
    assert pos == 0


def test_enqueue_queues_when_agent_busy(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """
    Zweiter Task für denselben Agent während erster läuft → 'queued'.
    Wir simulieren 'busy' indem wir direkt in _TASKS einen 'working'-Task legen.
    """
    agent = make_agent("Busy")
    from core.state import _tasks_lock

    blocker = _build_task(agent["id"], agent["name"])
    blocker["status"] = "working"
    with _tasks_lock:
        _TASKS[blocker["id"]] = blocker

    new_task = _build_task(agent["id"], agent["name"])
    queued, pos = container.tasks.enqueue(new_task)

    assert queued is True
    assert pos >= 1
    assert _TASKS[new_task["id"]]["status"] == "queued"


def test_enqueue_rejects_on_max_delegation_depth(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """Delegation-Tiefe >= MAX → status='rejected', process() NICHT gespawnt."""
    from services.task_service import MAX_DELEGATION_DEPTH

    agent = make_agent("DeepBot")
    task = _build_task(
        agent["id"], agent["name"],
        delegation_depth=MAX_DELEGATION_DEPTH,
    )

    queued, pos = container.tasks.enqueue(task)

    assert queued is False
    assert pos == 0
    stored = _TASKS[task["id"]]
    assert stored["status"] == "rejected"
    assert "delegation depth" in stored["error"].lower()
    # LLM darf nicht gerufen worden sein
    assert len(mock_llm.calls) == 0


def test_enqueue_waits_on_unresolved_dependency(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """depends_on verweist auf laufenden Task → status='waiting'."""
    from core.state import _tasks_lock

    agent = make_agent("Waiter")
    dep = _build_task(agent["id"], agent["name"])
    dep["status"] = "working"
    with _tasks_lock:
        _TASKS[dep["id"]] = dep

    dependent = _build_task(agent["id"], agent["name"], depends_on=[dep["id"]])
    queued, pos = container.tasks.enqueue(dependent)

    assert queued is False
    assert pos == 0
    assert _TASKS[dependent["id"]]["status"] == "waiting"


# ── process: Execution-Pfade ─────────────────────────────────────────────────

def test_process_uses_dispatcher_and_stores_llm_reply(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """process() geht über ChatService._dispatcher.execute → LLM-Reply wird gespeichert."""
    mock_llm.set_reply("Ich bin fertig.")
    agent = make_agent("Runner")
    task = _build_task(agent["id"], agent["name"])

    container.tasks.enqueue(task)

    stored = _TASKS[task["id"]]
    assert stored["status"] == "completed"
    assert stored["result_text"] == "Ich bin fertig."
    assert stored["completed_at"] is not None


def test_process_fails_on_missing_agent(
    container, clean_tasks, sync_spawn, mock_llm
):
    """Recipient-Agent existiert nicht → Task wird 'failed' mit Error-Message."""
    task = _build_task("00000000-0000-0000-0000-000000000000", "Ghost")
    # Delegation-Depth 0 → kommt durch enqueue durch
    container.tasks.enqueue(task)

    stored = _TASKS[task["id"]]
    assert stored["status"] == "failed"
    assert "nicht gefunden" in (stored.get("error") or "").lower()


# ── cancel ───────────────────────────────────────────────────────────────────

def test_cancel_marks_task_canceled(
    container, make_agent, clean_tasks, sync_spawn
):
    """cancel() ändert status auf 'canceled' für nicht-terminale Tasks."""
    from core.state import _tasks_lock

    agent = make_agent("Cancel")
    task = _build_task(agent["id"], agent["name"])
    task["status"] = "queued"
    with _tasks_lock:
        _TASKS[task["id"]] = task

    ok = container.tasks.cancel(task["id"])
    assert ok is True
    assert _TASKS[task["id"]]["status"] == "canceled"


def test_cancel_returns_false_for_unknown(container, clean_tasks):
    """cancel() unbekannter Task → False."""
    assert container.tasks.cancel("does-not-exist") is False


# ── list_all / get ──────────────────────────────────────────────────────────

def test_list_all_and_get(
    container, make_agent, clean_tasks, sync_spawn, mock_llm
):
    agent = make_agent("Lister")
    t1 = _build_task(agent["id"], agent["name"])
    t2 = _build_task(agent["id"], agent["name"])
    # Blocker um Queue-Verhalten zu erzwingen — beide gehen rein
    from core.state import _tasks_lock
    blocker = _build_task(agent["id"], agent["name"])
    blocker["status"] = "working"
    with _tasks_lock:
        _TASKS[blocker["id"]] = blocker

    container.tasks.enqueue(t1)
    container.tasks.enqueue(t2)

    all_tasks = container.tasks.list_all()
    ids = {t["id"] for t in all_tasks}
    assert t1["id"] in ids
    assert t2["id"] in ids
    assert container.tasks.get(t1["id"])["id"] == t1["id"]
    assert container.tasks.get("nope") is None


# ══════════════════════════════════════════════════════════════════════════════
# Stale-Task Watchdog — fail Tasks die zu lange in "working" hängen.
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def recording_chat_service(container):
    """Ersetzt TaskService._chat_service mit einer aufzeichnenden Double.
    Nach dem Test wird der Original-Service wiederhergestellt."""
    class _Recorder:
        def __init__(self):
            self.calls: list[tuple] = []

        def handle_message(self, sender_id, message, **kwargs):
            self.calls.append((sender_id, message, kwargs))

    rec = _Recorder()
    original = container.tasks._chat_service
    fired_original = set(container.tasks._fired_supervisor_dispatches)
    container.tasks._chat_service = rec
    container.tasks._fired_supervisor_dispatches.clear()
    yield rec
    container.tasks._chat_service = original
    container.tasks._fired_supervisor_dispatches.clear()
    container.tasks._fired_supervisor_dispatches.update(fired_original)


def _stale_started(seconds_ago: int) -> str:
    return (datetime.now() - timedelta(seconds=seconds_ago)).isoformat()


def test_tick_stale_fails_old_working_task(
    container, make_agent, clean_tasks, sync_spawn,
):
    """Task seit >STALE_WORKING_SEC in 'working' → wird als failed markiert."""
    from core.state import _tasks_lock
    agent = make_agent("Stalled")
    task = _build_task(agent["id"], agent["name"])
    task["status"] = "working"
    task["started_at"] = _stale_started(container.tasks.STALE_WORKING_SEC + 60)
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.tick_stale_tasks()

    stored = _TASKS[task["id"]]
    assert stored["status"] == "failed"
    assert "Stalled" in stored.get("error", "")
    assert stored.get("completed_at")


def test_tick_stale_ignores_young_working_task(
    container, make_agent, clean_tasks, sync_spawn,
):
    """Task seit <STALE_WORKING_SEC in 'working' → unverändert."""
    from core.state import _tasks_lock
    agent = make_agent("Young")
    task = _build_task(agent["id"], agent["name"])
    task["status"] = "working"
    task["started_at"] = _stale_started(60)  # 60s — weit unter Schwelle
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.tick_stale_tasks()

    assert _TASKS[task["id"]]["status"] == "working"
    assert "error" not in _TASKS[task["id"]] or not _TASKS[task["id"]].get("error")


def test_tick_stale_ignores_terminal_tasks(
    container, make_agent, clean_tasks, sync_spawn,
):
    """Bereits completed/failed/canceled Tasks werden vom Watchdog nicht angefasst."""
    from core.state import _tasks_lock
    agent = make_agent("Done")
    completed = _build_task(agent["id"], agent["name"])
    completed["status"] = "completed"
    completed["started_at"] = _stale_started(9999)  # uralt
    canceled = _build_task(agent["id"], agent["name"])
    canceled["status"] = "canceled"
    canceled["started_at"] = _stale_started(9999)
    with _tasks_lock:
        _TASKS[completed["id"]] = completed
        _TASKS[canceled["id"]] = canceled

    container.tasks.tick_stale_tasks()

    assert _TASKS[completed["id"]]["status"] == "completed"
    assert _TASKS[canceled["id"]]["status"] == "canceled"


def test_tick_stale_ignores_missing_started_at(
    container, make_agent, clean_tasks, sync_spawn,
):
    """Working-Task ohne started_at (z.B. aus alter Session) wird nicht spekulativ gefailed."""
    from core.state import _tasks_lock
    agent = make_agent("NoStart")
    task = _build_task(agent["id"], agent["name"])
    task["status"] = "working"
    # bewusst KEIN started_at
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.tick_stale_tasks()

    assert _TASKS[task["id"]]["status"] == "working"


def test_tick_stale_cascades_to_dependents(
    container, make_agent, clean_tasks, sync_spawn,
):
    """Wenn der Watchdog einen Parent failed, propagiert tick_queue() das auf
    waiting-Dependents."""
    from core.state import _tasks_lock
    agent_a = make_agent("Parent")
    agent_b = make_agent("Child")

    parent = _build_task(agent_a["id"], agent_a["name"])
    parent["status"] = "working"
    parent["started_at"] = _stale_started(container.tasks.STALE_WORKING_SEC + 60)

    child = _build_task(agent_b["id"], agent_b["name"])
    child["status"] = "waiting"
    child["depends_on"] = [parent["id"]]

    with _tasks_lock:
        _TASKS[parent["id"]] = parent
        _TASKS[child["id"]] = child

    container.tasks.tick_stale_tasks()

    assert _TASKS[parent["id"]]["status"] == "failed"
    assert _TASKS[child["id"]]["status"] == "failed"
    assert "fehlgeschlagen" in _TASKS[child["id"]].get("error", "").lower()


# ══════════════════════════════════════════════════════════════════════════════
# Supervisor-Callback — muss auch bei Cancel und Cascade-Fail feuern
# (sonst hängt der Operator-Loop wenn Sub-Tasks ohne process() terminal werden).
# ══════════════════════════════════════════════════════════════════════════════


def test_cancel_triggers_supervisor_callback(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """cancel() einer Task in einer Operator-Dispatch-Gruppe → Supervisor feuert."""
    from core.state import _tasks_lock
    operator = make_agent("OpSrc", operator=True)
    worker = make_agent("Worker1")

    dispatch_id = "test-dispatch-cancel"
    task = _build_task(worker["id"], worker["name"])
    task["status"] = "queued"
    task["sender_agent_id"] = operator["id"]
    task["sender_agent_name"] = operator["name"]
    task["parent_dispatch_id"] = dispatch_id
    task["supervisor_turn"] = 1
    with _tasks_lock:
        _TASKS[task["id"]] = task

    assert container.tasks.cancel(task["id"]) is True

    # Gruppe ist mit dem gecancelten Task vollständig terminal → Callback muss feuern
    assert len(recording_chat_service.calls) == 1
    sender_id, msg, _ = recording_chat_service.calls[0]
    assert sender_id == operator["id"]
    assert "SUPERVISOR-CALLBACK" in msg
    assert "canceled" in msg


def test_cancel_skips_callback_without_dispatch_id(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """Cancel auf einem Standalone-Task (kein parent_dispatch_id) → kein Callback."""
    from core.state import _tasks_lock
    agent = make_agent("Standalone")
    task = _build_task(agent["id"], agent["name"])
    task["status"] = "queued"
    # bewusst KEIN parent_dispatch_id
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.cancel(task["id"])

    assert recording_chat_service.calls == []


def test_cascade_fail_triggers_supervisor_callback(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """Wenn eine Dependency failed → waiting-Dependent cascade-failed → Supervisor feuert."""
    from core.state import _tasks_lock
    operator = make_agent("OpCascade", operator=True)
    worker_a = make_agent("WorkerA")
    worker_b = make_agent("WorkerB")

    dispatch_id = "test-dispatch-cascade"
    parent = _build_task(worker_a["id"], worker_a["name"])
    parent["status"] = "failed"
    parent["error"] = "explicit fail"
    parent["sender_agent_id"] = operator["id"]
    parent["sender_agent_name"] = operator["name"]
    parent["parent_dispatch_id"] = dispatch_id
    parent["supervisor_turn"] = 1
    parent["completed_at"] = datetime.now().isoformat()

    child = _build_task(worker_b["id"], worker_b["name"])
    child["status"] = "waiting"
    child["depends_on"] = [parent["id"]]
    child["sender_agent_id"] = operator["id"]
    child["sender_agent_name"] = operator["name"]
    child["parent_dispatch_id"] = dispatch_id
    child["supervisor_turn"] = 1

    with _tasks_lock:
        _TASKS[parent["id"]] = parent
        _TASKS[child["id"]] = child

    container.tasks.tick_queue()

    assert _TASKS[child["id"]]["status"] == "failed"
    # Beide Tasks im Dispatch sind jetzt terminal → genau ein Callback
    assert len(recording_chat_service.calls) == 1
    _, msg, _ = recording_chat_service.calls[0]
    assert "SUPERVISOR-CALLBACK" in msg


def test_supervisor_callback_carries_images_forward(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """Wenn Sub-Tasks ein result_image produziert haben, muss der Callback
    diese Bild-Pfade an handle_message(images=...) durchreichen — sonst
    bekommen Folge-Tasks images=0 und Bild-Skills (upscale/video_gen)
    schlagen mit 'Kein Bild übergeben' fehl. Bug-Fix Image-Bridge."""
    from core.state import _tasks_lock
    operator = make_agent("OpBridge", operator=True)
    worker = make_agent("Renderer")

    dispatch_id = "test-dispatch-bridge"
    task = _build_task(worker["id"], worker["name"])
    task["status"] = "completed"
    task["result_image"] = "/static/img/result_42.png"
    task["sender_agent_id"] = operator["id"]
    task["sender_agent_name"] = operator["name"]
    task["parent_dispatch_id"] = dispatch_id
    task["supervisor_turn"] = 1
    task["completed_at"] = datetime.now().isoformat()
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks._maybe_supervisor_callback(task)

    assert len(recording_chat_service.calls) == 1
    _, msg, kwargs = recording_chat_service.calls[0]
    assert kwargs.get("images") == ["/static/img/result_42.png"], (
        f"Erwartete images=['/static/img/result_42.png'], "
        f"bekam {kwargs.get('images')!r}"
    )
    # Bild auch textuell sichtbar im Brief an Martin, damit das LLM weiß
    # dass das Bild für Folge-Tasks bereit liegt.
    assert "result_42.png" in msg


def test_supervisor_callback_no_images_when_none_produced(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """Pure Text-Tasks (keine result_image) → handle_message(images=None)."""
    from core.state import _tasks_lock
    operator = make_agent("OpTextOnly", operator=True)
    worker = make_agent("Analyst")

    dispatch_id = "test-dispatch-noimg"
    task = _build_task(worker["id"], worker["name"])
    task["status"] = "completed"
    task["result_text"] = "Analyse fertig."
    task["sender_agent_id"] = operator["id"]
    task["sender_agent_name"] = operator["name"]
    task["parent_dispatch_id"] = dispatch_id
    task["supervisor_turn"] = 1
    task["completed_at"] = datetime.now().isoformat()
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks._maybe_supervisor_callback(task)

    assert len(recording_chat_service.calls) == 1
    _, _, kwargs = recording_chat_service.calls[0]
    assert kwargs.get("images") is None


def test_supervisor_callback_double_fire_protection(
    container, make_agent, clean_tasks, sync_spawn, recording_chat_service,
):
    """Ein Dispatch darf nur EINMAL den Supervisor-Callback auslösen, auch wenn
    mehrere Terminal-Pfade (cancel + cascade) ihn parallel triggern wollen."""
    from core.state import _tasks_lock
    operator = make_agent("OpDouble", operator=True)
    worker = make_agent("WorkerDouble")

    dispatch_id = "test-dispatch-double"
    task = _build_task(worker["id"], worker["name"])
    task["status"] = "queued"
    task["sender_agent_id"] = operator["id"]
    task["sender_agent_name"] = operator["name"]
    task["parent_dispatch_id"] = dispatch_id
    task["supervisor_turn"] = 1
    with _tasks_lock:
        _TASKS[task["id"]] = task

    # 1. Cancel triggert den Callback
    container.tasks.cancel(task["id"])
    # 2. Erneuter Callback-Versuch (z.B. weil tick_queue ihn auch sieht) — darf nicht
    container.tasks._maybe_supervisor_callback(_TASKS[task["id"]])

    assert len(recording_chat_service.calls) == 1
