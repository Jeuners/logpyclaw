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
