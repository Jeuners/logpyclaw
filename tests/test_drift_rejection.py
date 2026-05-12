"""Integration: TaskService rejected Side-Effect-Task mit zu altem reference_now.

Validiert §4.3 Re-Synchronisation: Tasks mit Drift > Threshold deren Empfänger
ein Side-Effect-Skill hat (z.B. gmail) werden vor Skill-Ausführung kontrolliert
abgelehnt — ohne den Skill überhaupt zu starten.
"""
import uuid
from datetime import datetime, timedelta

import pytest


def _make_task(recipient_id: str, recipient_name: str, reference_now_iso: str | None = None) -> dict:
    """Baut ein minimales Task-Dict für TaskService.process()."""
    now = datetime.now()
    t = {
        "id": str(uuid.uuid4()),
        "sender_agent_id": "test-sender",
        "sender_agent_name": "TestSender",
        "recipient_agent_id": recipient_id,
        "recipient_agent_name": recipient_name,
        "message": "Bitte sende eine Mail an foo@bar.com",
        "skill_used": None,
        "result_text": None,
        "result_image": None,
        "error": None,
        "created_at": now.isoformat(),
        "completed_at": None,
        "timeout_at": (now + timedelta(seconds=600)).isoformat(),
        "delegation_depth": 1,
        "priority": 5,
        "depends_on": [],
        "status": "queued",
    }
    if reference_now_iso:
        t["reference_now"] = reference_now_iso
        t["dilation_factor"] = 1.0
        t["frame_id"] = uuid.uuid4().hex
    return t


def test_side_effect_task_with_stale_reference_is_rejected(
    container, make_agent, sync_spawn, clean_tasks, mock_llm,
):
    """gmail-Agent + reference_now vor 1h → Drift > 600s → status=failed."""
    agent = make_agent("MailBot", skills=["gmail"])

    stale_ref = (datetime.now() - timedelta(seconds=3700)).isoformat()
    task = _make_task(agent["id"], agent["name"], reference_now_iso=stale_ref)

    # Direkt in den Task-Store legen und process() rufen — wir wollen den
    # Drift-Check vor _dispatcher.execute() validieren.
    from core.state import _TASKS, _tasks_lock
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.process(task["id"])

    # Task muss als failed (oder rejected) markiert sein, mit Drift-Begründung
    out = container.tasks.get(task["id"])
    assert out is not None
    assert out["status"] == "failed"
    assert "Temporal drift rejected" in (out.get("error") or "")
    # Skill darf NICHT ausgeführt worden sein
    assert out.get("skill_used") in (None, "")


def test_read_only_task_with_stale_reference_runs(
    container, make_agent, sync_spawn, clean_tasks, mock_llm,
):
    """Agent ohne Side-Effect-Skill läuft trotz Drift weiter (LOG_ONLY)."""
    agent = make_agent("ReadBot", skills=["wiki_read"])
    mock_llm.set_reply("OK, hier eine Antwort.")

    stale_ref = (datetime.now() - timedelta(seconds=3700)).isoformat()
    task = _make_task(agent["id"], agent["name"], reference_now_iso=stale_ref)

    from core.state import _TASKS, _tasks_lock
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.process(task["id"])

    out = container.tasks.get(task["id"])
    assert out is not None
    # Nicht durch den Drift-Check geblockt — entweder completed oder failed
    # aus anderem Grund (mock_llm sollte aber laufen). Wichtig: kein
    # Drift-Reject-Marker.
    assert "Temporal drift rejected" not in (out.get("error") or "")


def test_task_without_reference_now_runs_normally(
    container, make_agent, sync_spawn, clean_tasks, mock_llm,
):
    """Bestands-Tasks ohne reference_now laufen wie vor der Erweiterung."""
    agent = make_agent("LegacyBot", skills=["gmail"])
    mock_llm.set_reply("OK")

    task = _make_task(agent["id"], agent["name"], reference_now_iso=None)

    from core.state import _TASKS, _tasks_lock
    with _tasks_lock:
        _TASKS[task["id"]] = task

    container.tasks.process(task["id"])

    out = container.tasks.get(task["id"])
    assert out is not None
    # Pre-Eigenzeit-Tasks dürfen NIE wegen Drift abgelehnt werden
    assert "Temporal drift rejected" not in (out.get("error") or "")
