"""End-to-End: ChatService + Heartbeat dispatchen Tasks mit Eigenzeit-Frame.

Validiert, dass die fork()-Wiring tatsächlich greift und die Tasks in der DB
mit reference_now/parent_reference_now/dilation_factor/frame_id landen.
"""
import pytest

from core.a2a_protocol import A2ADispatch
from core.task_list import TaskItem
from core.time_provider import WallClockProvider, estimate_dilation


def test_chat_service_fork_helper(container, make_agent):
    """ChatService._fork_for_recipient erzeugt einen sinnvollen Child-Provider."""
    agent = {
        "id": "test-id",
        "name": "TestBot",
        "provider": "openrouter",
        "model": "anthropic/claude-opus",
    }
    chat = container.chat
    chat.set_time_provider(WallClockProvider(agent_id="orchestrator"))
    child = chat._fork_for_recipient(agent, kind="interactive")
    assert child is not None
    assert child.frame.agent_id == "test-id"
    assert child.frame.parent_frame_id is not None
    assert child.frame.metadata.get("kind") == "interactive"
    # γ aus estimate_dilation
    assert child.dilation() == pytest.approx(estimate_dilation(agent))


def test_chat_service_fork_returns_none_for_unknown_recipient(container):
    chat = container.chat
    chat.set_time_provider(WallClockProvider())
    assert chat._fork_for_recipient(None) is None


def test_a2a_dispatch_with_estimated_dilation_writes_to_db(tmp_data_dir):
    from storage.database import init_db, upsert_task, load_open_tasks
    init_db()

    parent = WallClockProvider(agent_id="orchestrator")
    target_agent = {
        "id": "frontier-id",
        "name": "Frontier",
        "provider": "openrouter",
        "model": "claude-sonnet",
    }
    child = parent.fork(
        agent_id=target_agent["id"],
        dilation_factor=estimate_dilation(target_agent),
        metadata={"kind": "interactive"},
    )

    dispatch = A2ADispatch(
        recipient_id=target_agent["id"],
        recipient_name=target_agent["name"],
        task_text="Recherche-Task fürs Frontier-Modell.",
    )
    task = dispatch.to_task_dict(time_provider=child)
    task["status"] = "queued"
    upsert_task(task)

    loaded = next(t for t in load_open_tasks() if t["id"] == task["id"])
    assert loaded["dilation_factor"] == pytest.approx(estimate_dilation(target_agent))
    assert loaded["frame_id"] == child.frame.frame_id
    assert loaded["parent_reference_now"] is not None


def test_heartbeat_frame_marker_round_trip(tmp_data_dir):
    """Heartbeat-Frame metadata.kind=heartbeat erreicht den Task — implizit
    über frame_id, weil metadata derzeit nicht in der DB landet (frame_id ist
    der Stable-Identifier; metadata bleibt Provider-lokal). Wir prüfen, dass
    Heartbeat- und Interactive-Frames unterschiedliche frame_ids erzeugen."""
    parent = WallClockProvider(agent_id="orchestrator")
    target = {"id": "x", "name": "X", "provider": "ollama", "model": "gemma4:e4b"}

    interactive = parent.fork(
        agent_id=target["id"],
        dilation_factor=estimate_dilation(target),
        metadata={"kind": "interactive"},
    )
    heartbeat = parent.fork(
        agent_id=target["id"],
        dilation_factor=estimate_dilation(target),
        metadata={"kind": "heartbeat"},
    )
    assert interactive.frame.frame_id != heartbeat.frame.frame_id
    assert interactive.frame.metadata["kind"] == "interactive"
    assert heartbeat.frame.metadata["kind"] == "heartbeat"
