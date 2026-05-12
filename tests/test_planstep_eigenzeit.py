"""Tests für Eigenzeit-Felder in TaskItem + A2ADispatch + DB-Roundtrip.

Bezug: Dillenberg, Time Dilation in LLM Agent Systems §4.3 (PlanStep extension).

Akzeptanzkriterien:
- Ohne TimeProvider verhalten sich to_task_dict()-Methoden bit-identisch wie vorher.
- Mit TimeProvider erscheinen reference_now/parent_reference_now/dilation_factor/frame_id.
- DB schreibt+liest die neuen Felder verlustfrei (None bleibt None).
"""
from datetime import datetime

from core.a2a_protocol import A2ADispatch
from core.task_list import TaskItem
from core.time_provider import WallClockProvider


# ── TaskItem ──────────────────────────────────────────────────────────────────

def test_taskitem_to_dict_without_provider_has_no_eigenzeit_fields():
    item = TaskItem(
        line_index=0,
        recipient_id="r1",
        recipient_name="Picasso",
        task_text="ein langer Test-Task der mehr als zehn zeichen ist",
    )
    d = item.to_task_dict()
    # Eigenzeit-Felder dürfen NICHT auftauchen, wenn kein Provider übergeben wurde
    for key in ("reference_now", "parent_reference_now", "dilation_factor", "frame_id"):
        assert key not in d, f"{key} sollte ohne TimeProvider fehlen"
    # Standardfelder bleiben
    assert d["recipient_agent_id"] == "r1"
    assert d["created_at"]


def test_taskitem_to_dict_with_provider_writes_eigenzeit():
    parent = WallClockProvider(agent_id="orchestrator")
    parent.tick()  # τ vorrücken
    child = parent.fork(agent_id="picasso", dilation_factor=0.5)

    item = TaskItem(
        line_index=0,
        recipient_id="picasso-id",
        recipient_name="Picasso",
        task_text="Bild generieren — Monarchfalter mit Flügeln",
    )
    d = item.to_task_dict(time_provider=child)

    assert "reference_now" in d
    datetime.fromisoformat(d["reference_now"])  # parsbar
    assert d["parent_reference_now"] is not None
    datetime.fromisoformat(d["parent_reference_now"])
    assert d["dilation_factor"] == 0.5
    assert d["frame_id"] == child.frame.frame_id


def test_taskitem_to_dict_root_provider_has_null_parent_reference_now():
    root = WallClockProvider(agent_id="root")
    item = TaskItem(
        line_index=0,
        recipient_id="r",
        recipient_name="R",
        task_text="ein langer Test-Task der mehr als zehn zeichen ist",
    )
    d = item.to_task_dict(time_provider=root)
    assert d["parent_reference_now"] is None
    assert d["dilation_factor"] == 1.0


# ── A2ADispatch ───────────────────────────────────────────────────────────────

def test_a2a_dispatch_to_dict_without_provider_unchanged():
    dispatch = A2ADispatch(
        recipient_id="r",
        recipient_name="Jan",
        task_text="lade das Video herunter",
    )
    d = dispatch.to_task_dict()
    for key in ("reference_now", "parent_reference_now", "dilation_factor", "frame_id"):
        assert key not in d
    assert d["created_at"]
    assert d["timeout_at"]


def test_a2a_dispatch_to_dict_with_provider_writes_eigenzeit():
    parent = WallClockProvider(agent_id="aria")
    child = parent.fork(agent_id="jan", dilation_factor=2.0)
    dispatch = A2ADispatch(
        recipient_id="jan-id",
        recipient_name="Jan",
        task_text="lade das Video herunter",
        sender_id="aria-id",
        sender_name="ARIA",
    )
    d = dispatch.to_task_dict(time_provider=child)
    assert d["frame_id"] == child.frame.frame_id
    assert d["dilation_factor"] == 2.0
    assert d["parent_reference_now"] is not None


# ── DB-Roundtrip ──────────────────────────────────────────────────────────────

def test_db_task_roundtrip_with_eigenzeit_fields(tmp_data_dir):
    """Persist + load: alle vier Eigenzeit-Felder kommen zurück wie geschrieben."""
    from storage.database import init_db, upsert_task, load_open_tasks

    init_db()  # idempotent, fügt fehlende Spalten nach

    parent = WallClockProvider(agent_id="orchestrator")
    child = parent.fork(agent_id="zora", dilation_factor=0.25)
    item = TaskItem(
        line_index=0,
        recipient_id="zora-id",
        recipient_name="Zora",
        task_text="Recherche-Task mit ausreichend Zeichen",
    )
    task = item.to_task_dict(time_provider=child)
    task["status"] = "queued"

    upsert_task(task)

    open_tasks = load_open_tasks()
    found = [t for t in open_tasks if t["id"] == task["id"]]
    assert len(found) == 1
    loaded = found[0]
    assert loaded["frame_id"] == task["frame_id"]
    assert loaded["dilation_factor"] == 0.25
    assert loaded["reference_now"] == task["reference_now"]
    assert loaded["parent_reference_now"] == task["parent_reference_now"]


def test_db_task_roundtrip_without_eigenzeit_keeps_nulls(tmp_data_dir):
    """Bestands-Tasks ohne Eigenzeit-Felder dürfen unverändert persistiert werden."""
    from storage.database import init_db, upsert_task, load_open_tasks

    init_db()

    dispatch = A2ADispatch(
        recipient_id="alt-id",
        recipient_name="Alt",
        task_text="legacy-style dispatch ohne provider",
    )
    task = dispatch.to_task_dict()  # KEIN Provider
    task["status"] = "queued"
    upsert_task(task)

    open_tasks = load_open_tasks()
    found = [t for t in open_tasks if t["id"] == task["id"]]
    assert len(found) == 1
    loaded = found[0]
    # Alle vier Felder bleiben None — keine Halluzination von Default-Werten
    assert loaded["frame_id"] is None
    assert loaded["dilation_factor"] is None
    assert loaded["reference_now"] is None
    assert loaded["parent_reference_now"] is None
