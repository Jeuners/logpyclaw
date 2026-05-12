"""Tests für EventService-Erweiterung um Eigenzeit-Felder (§4.3 Logging-Tuple)."""
from datetime import datetime

from core.time_provider import WallClockProvider
from services.event_service import EventService


def test_emit_without_provider_keeps_legacy_shape():
    """Ohne TimeProvider werden keine Eigenzeit-Felder injiziert."""
    svc = EventService()
    svc.emit("test", {"x": 1})
    # Wir prüfen über den globalen Buffer
    from core.state import _EVENTS, _events_lock
    with _events_lock:
        last = _EVENTS[-1]
    assert last["type"] == "test"
    # Legacy-Felder unverändert
    assert "ts" in last
    assert last["wall_clock"] == last["ts"]
    # Keine Eigenzeit-Felder
    assert "frame" not in last
    assert "frame_id" not in last
    assert "agent_reference_now" not in last


def test_emit_with_provider_attaches_frame_data():
    svc = EventService()
    p = WallClockProvider(agent_id="orchestrator", dilation_factor=1.0)
    svc.set_time_provider(p)
    svc.emit("test_eigenzeit", {"x": 2})
    from core.state import _EVENTS, _events_lock
    with _events_lock:
        last = _EVENTS[-1]
    assert last["type"] == "test_eigenzeit"
    assert "frame_id" in last
    assert last["frame_id"] == p.frame.frame_id
    assert "agent_reference_now" in last
    datetime.fromisoformat(last["agent_reference_now"])  # parsbar
    assert last["dilation_factor"] == 1.0
    assert last["frame"]["agent_id"] == "orchestrator"


def test_emit_explicit_frame_overrides_default():
    """Explicit frame= Argument überschreibt den Provider-Default."""
    svc = EventService()
    p = WallClockProvider(agent_id="orchestrator")
    svc.set_time_provider(p)
    custom = {"frame_id": "heartbeat-frame", "agent_id": "x", "metadata": {"kind": "heartbeat"}}
    svc.emit("test_custom_frame", {"y": 9}, frame=custom)
    from core.state import _EVENTS, _events_lock
    with _events_lock:
        last = _EVENTS[-1]
    assert last["frame"] == custom
    # Kein automatisches frame_id-Top-Level wenn frame explizit übergeben
    # (der Caller bekommt was er reingibt — keine Vermischung).
    assert "frame_id" not in last


def test_emit_a2a_dispatch_preserves_frame():
    svc = EventService()
    p = WallClockProvider(agent_id="aria")
    svc.set_time_provider(p)
    custom = {"frame_id": "child-frame", "agent_id": "jan"}
    svc.emit_a2a_dispatch(
        "aria-id", "ARIA", "Jan", "test task",
        task_id="t-1", frame=custom,
    )
    from core.state import _EVENTS, _events_lock
    with _events_lock:
        last = _EVENTS[-1]
    assert last["type"] == "a2a_dispatch"
    assert last["frame"] == custom
