"""
tests/test_pqsign.py — PQC Audit-Log: Sign/Verify, Hash-Chain, Tamper-Detection.
"""

from __future__ import annotations

import pytest

from backend.core import pqsign
from backend.core.cdc import CausalDilationClock
from backend.core.protocol import Message, MessageType
from backend.storage.mission_store import MissionStore


@pytest.fixture
def clean_keys(tmp_path, monkeypatch):
    """Isolated keys dir per test."""
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr(pqsign, "KEYS_DIR", keys_dir)
    monkeypatch.setattr(pqsign, "_current", None)
    yield keys_dir


def _make_msg(mission_id: str, content: str, msg_id: str = "m_test") -> Message:
    return Message(
        msg_id=msg_id,
        mission_id=mission_id,
        task_id="t_test",
        parent_task_id=None,
        type=MessageType.REQUEST,
        sender="ext:user",
        recipient="agent:echo",
        payload={"content": content},
        timestamp=1700000000.0,
        clock=CausalDilationClock(),
    )


# ── pqsign Modul ──────────────────────────────────────────────────────────────

def test_canonical_json_deterministic():
    a = pqsign.canonical_json({"b": 2, "a": 1})
    b = pqsign.canonical_json({"a": 1, "b": 2})
    assert a == b
    assert a == b'{"a":1,"b":2}'


def test_hash_message_stable():
    p = b'{"hello":"world"}'
    h1 = pqsign.hash_message(pqsign.GENESIS_HASH, p)
    h2 = pqsign.hash_message(pqsign.GENESIS_HASH, p)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_keypair_generates_and_persists(clean_keys):
    kp1 = pqsign.get_or_create_keypair()
    assert kp1.signer_id.startswith("signer-")
    assert len(kp1.public_key) == 1952
    assert len(kp1.secret_key) == 4032

    # Reset cache, sollte denselben Key laden
    pqsign._current = None
    kp2 = pqsign.get_or_create_keypair()
    assert kp2.signer_id == kp1.signer_id
    assert kp2.public_key == kp1.public_key


def test_sign_verify_roundtrip(clean_keys):
    payload = b"the dillenberg manifesto"
    signer_id, sig_b64 = pqsign.sign(payload)
    assert pqsign.verify(payload, sig_b64, signer_id) is True


def test_verify_tampered_payload(clean_keys):
    payload = b"original"
    signer_id, sig_b64 = pqsign.sign(payload)
    assert pqsign.verify(b"tampered", sig_b64, signer_id) is False


def test_verify_wrong_signer(clean_keys):
    payload = b"hello"
    _, sig_b64 = pqsign.sign(payload)
    assert pqsign.verify(payload, sig_b64, "signer-fake-id") is False


# ── MissionStore Chain ────────────────────────────────────────────────────────

def test_record_message_signs(clean_keys):
    store = MissionStore()
    msg = _make_msg("mis_x", "hallo", "m_001")
    store.record_message(msg)

    assert msg.chain_idx == 0
    assert msg.prev_hash == pqsign.GENESIS_HASH
    assert msg.msg_hash is not None
    assert msg.signer_id is not None
    assert msg.sig is not None


def test_chain_links_messages(clean_keys):
    store = MissionStore()
    msg1 = _make_msg("mis_y", "first", "m_001")
    msg2 = _make_msg("mis_y", "second", "m_002")
    store.record_message(msg1)
    store.record_message(msg2)

    assert msg1.chain_idx == 0
    assert msg2.chain_idx == 1
    assert msg2.prev_hash == msg1.msg_hash


def test_verify_chain_valid(clean_keys):
    store = MissionStore()
    for i in range(3):
        store.record_message(_make_msg("mis_v", f"step {i}", f"m_{i:03}"))

    r = store.verify_chain("mis_v")
    assert r["valid"] is True
    assert r["count"] == 3
    assert r["signed"] == 3
    assert r["verified"] == 3
    assert r["broken_at"] is None


def test_verify_chain_tamper_detected(clean_keys):
    store = MissionStore()
    msgs = []
    for i in range(3):
        m = _make_msg("mis_t", f"step {i}", f"m_{i:03}")
        store.record_message(m)
        msgs.append(m)

    # Tamper: payload des mittleren Messages ändern
    msgs[1].payload = {"content": "MANIPULATED"}

    r = store.verify_chain("mis_t")
    assert r["valid"] is False
    assert r["broken_at"] == 1
    assert "msg_hash" in r["broken_reason"] or "payload" in r["broken_reason"]


def test_verify_chain_break_after_idx_alters_next(clean_keys):
    """Wenn msg N tampered, ist die Chain ab N kaputt — prev_hash der msgs > N
    war abgeleitet vom ORIGINAL hash."""
    store = MissionStore()
    for i in range(3):
        store.record_message(_make_msg("mis_break", f"step {i}", f"m_{i:03}"))

    # Untausche msg 0's hash → msg 1 wird sich als prev_hash-mismatch outen
    trace = store.get_trace("mis_break")
    trace[0].msg_hash = "0" * 64

    r = store.verify_chain("mis_break")
    assert r["valid"] is False
    assert r["broken_at"] in (0, 1)


def test_legacy_message_skipped(clean_keys):
    """Messages ohne sig (legacy) brechen die Verifikation nicht."""
    store = MissionStore()
    legacy = _make_msg("mis_legacy", "old", "m_old")
    # NICHT durch record_message → bleibt unsigned
    store._traces[legacy.mission_id].append(legacy)

    r = store.verify_chain("mis_legacy")
    assert r["valid"] is True
    assert r["signed"] == 0


def test_signed_after_legacy_in_same_mission(clean_keys):
    """Legacy msg + neue signierte msg → signed=1, valid=True."""
    store = MissionStore()
    legacy = _make_msg("mis_mix", "old", "m_old")
    store._traces[legacy.mission_id].append(legacy)

    new_msg = _make_msg("mis_mix", "new", "m_new")
    store.record_message(new_msg)

    r = store.verify_chain("mis_mix")
    assert r["valid"] is True
    assert r["count"] == 2
    assert r["signed"] == 1
