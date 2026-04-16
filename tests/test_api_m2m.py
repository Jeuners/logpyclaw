"""
L2-Tests für M2M-API + Chat-API via TestClient.

Deckt ab: Discovery, /api/m2m/agents (lokal+remote merge),
         /api/m2m/dispatch (eingehender Remote-Task),
         /api/chat und /api/chat/stream (SSE) mit mock_llm.
"""
import json

import pytest

from core.state import _TASKS


# ── M2M Discovery ────────────────────────────────────────────────────────────

def test_well_known_discovery_returns_version(client):
    """GET /.well-known/martin-agent.json → {version, node_id, ...}"""
    r = client.get("/.well-known/martin-agent.json")
    assert r.status_code == 200
    data = r.json()
    assert data.get("version") == "2.0"
    # Felder sind strings (leer erlaubt)
    assert "node_id" in data
    assert "node_name" in data


def test_m2m_agents_returns_local_and_remote(client, make_agent):
    """GET /api/m2m/agents liefert {local, remote}-Splits."""
    agent = make_agent("M2MLocal")
    r = client.get("/api/m2m/agents")
    assert r.status_code == 200
    data = r.json()
    assert "local" in data
    assert "remote" in data
    local_ids = {a["id"] for a in data["local"]}
    assert agent["id"] in local_ids
    # Remote ist eine Liste (leer oder nicht, je nach Cache)
    assert isinstance(data["remote"], list)


def test_m2m_dispatch_creates_remote_flagged_task(
    client, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """POST /api/m2m/dispatch → Task landet in _TASKS mit m2m=True."""
    mock_llm.set_reply("Remote-Antwort")
    agent = make_agent("RemoteTarget")

    payload = {
        "target_agent_name": agent["name"],
        "message": "Beantworte das hier",
        # Kein callback_url — sonst blockiert DNS-Resolve remote.local ~5s
        "sender_agent_name": "Peer",
        "origin_node": "remote-node-1",
        "delegation_depth": 1,
    }
    r = client.post("/api/m2m/dispatch", json=payload)
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]

    task = _TASKS.get(task_id)
    assert task is not None
    assert task["m2m"] is True
    assert task["remote_node"] == "remote-node-1"
    assert task["sender_agent_id"] == "remote::remote-node-1"


def test_m2m_dispatch_unknown_agent_returns_404(client):
    """POST /api/m2m/dispatch auf unbekannten Agent → 404."""
    payload = {
        "target_agent_name": "DoesNotExist__",
        "message": "irrelevant",
        "origin_node": "remote",
    }
    r = client.post("/api/m2m/dispatch", json=payload)
    assert r.status_code == 404


# ── /api/chat (Non-Stream) ───────────────────────────────────────────────────

def test_api_chat_returns_mocked_reply(
    client, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """POST /api/chat → {reply, agent_id, ...} mit unserer Mock-Antwort."""
    mock_llm.set_reply("Mock antwortet.")
    agent = make_agent("ApiChat")

    r = client.post("/api/chat", json={"agent_id": agent["id"], "message": "Hi"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reply"] == "Mock antwortet."
    assert data["agent_id"] == agent["id"]


def test_api_chat_unknown_agent_returns_404(client):
    """POST /api/chat auf unbekannten Agent → 404 (AgentNotFoundError-Handler)."""
    r = client.post(
        "/api/chat",
        json={"agent_id": "unknown-xyz", "message": "Hallo"},
    )
    # AgentNotFoundError wird in 404 umgewandelt
    assert r.status_code == 404


# ── /api/chat/stream (SSE) ──────────────────────────────────────────────────

def test_api_chat_stream_yields_sse_chunks_and_done(
    client, make_agent, clean_tasks, sync_spawn, mock_llm
):
    """GET /api/chat/stream liefert data:-Events und schlussendlich done=true."""
    mock_llm.set_reply("Abcdefghijklmnop" * 3)  # >20 Zeichen → mehrere Chunks
    agent = make_agent("Streamer")

    with client.stream(
        "GET",
        "/api/chat/stream",
        params={"agent_id": agent["id"], "message": "Stream mal"},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            # Stop wenn done
            if events and events[-1].get("done"):
                break

    assert events, "Kein SSE-Event empfangen"
    # Mindestens ein Chunk + ein done
    has_chunk = any("chunk" in e for e in events)
    has_done = any(e.get("done") for e in events)
    assert has_chunk, f"Kein chunk-Event: {events[:3]}"
    assert has_done, "Kein done-Event"


def test_api_chat_stream_unknown_agent_emits_error(client):
    """Stream auf unbekannten Agent → 'error' im SSE statt HTTP 404."""
    with client.stream(
        "GET",
        "/api/chat/stream",
        params={"agent_id": "ghost-id", "message": "Hallo"},
    ) as resp:
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1].get("done"):
                break

    assert any("error" in e for e in events), f"Kein error-Event: {events}"
