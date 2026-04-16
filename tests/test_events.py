"""
tests/test_events.py — EventService Pub/Sub Tests.
"""
from services.event_service import EventService


def test_emit_and_get_since():
    svc = EventService()
    # Ausgangs-Version holen (danach startet unsere Zählung)
    baseline = svc.get_since(0)
    start_v = max((e["v"] for e in baseline), default=0)

    svc.emit("test_event", {"msg": "hello"})
    svc.emit("test_event_2", {"foo": 42})

    new_events = svc.get_since(start_v)
    types = [e["type"] for e in new_events]
    assert "test_event" in types
    assert "test_event_2" in types


def test_event_has_version_and_timestamp():
    svc = EventService()
    baseline_max = max((e["v"] for e in svc.get_since(0)), default=0)
    svc.emit("version_test", {})
    evs = svc.get_since(baseline_max)
    assert len(evs) >= 1
    e = evs[-1]
    assert "v" in e and e["v"] > baseline_max
    assert "ts" in e
    assert e["type"] == "version_test"


def test_get_since_filters_by_version():
    svc = EventService()
    svc.emit("a", {})
    mid = max(e["v"] for e in svc.get_since(0))
    svc.emit("b", {})
    svc.emit("c", {})
    after_mid = svc.get_since(mid)
    types = [e["type"] for e in after_mid]
    assert "b" in types
    assert "c" in types
    assert "a" not in types  # bereits vor mid
