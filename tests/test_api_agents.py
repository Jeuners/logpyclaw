"""
tests/test_api_agents.py — CRUD-Tests für /api/agents.
"""
import pytest
import uuid


def _create_agent(client, prefix="Test", **kwargs):
    """Hilfs-Funktion: Agent mit eindeutigem Namen anlegen."""
    name = f"{prefix}_{uuid.uuid4().hex[:6]}"
    payload = {"name": name, "model": "llama3", "provider": "ollama", **kwargs}
    r = client.post("/api/agents", json=payload)
    assert r.status_code in (200, 201), f"Create failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    assert "id" in data, f"Response hat kein 'id': {data}"
    return data


def test_list_agents_empty_or_existing(client):
    """GET /api/agents gibt immer eine Liste zurück."""
    r = client.get("/api/agents")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, (list, dict))


def test_create_agent(client):
    """POST /api/agents legt neuen Agent an."""
    data = _create_agent(client, "TestAgent", color="#00e676")
    assert data["name"].startswith("TestAgent_")


def test_get_agent(client):
    """GET /api/agents/{id} gibt den Agent zurück."""
    agent = _create_agent(client, "GetTest")
    r = client.get(f"/api/agents/{agent['id']}")
    assert r.status_code == 200
    assert r.json()["name"].startswith("GetTest_")


def test_get_agent_not_found(client):
    """GET /api/agents/{id} mit unbekannter ID → 404."""
    r = client.get("/api/agents/does-not-exist-xyz")
    assert r.status_code == 404


def test_update_agent(client):
    """PUT /api/agents/{id} aktualisiert Agent-Felder."""
    agent = _create_agent(client, "UpdateTest")
    r = client.put(f"/api/agents/{agent['id']}", json={"name": "UpdatedName", "model": "gemma2"})
    assert r.status_code == 200
    assert r.json()["name"] == "UpdatedName"
    assert r.json()["model"] == "gemma2"


def test_delete_agent(client):
    """DELETE /api/agents/{id} entfernt den Agent."""
    agent = _create_agent(client, "DeleteTest")
    r = client.delete(f"/api/agents/{agent['id']}")
    assert r.status_code in (200, 204)
    r = client.get(f"/api/agents/{agent['id']}")
    assert r.status_code == 404


def test_create_agent_missing_name(client):
    """POST /api/agents ohne Name → 422 Validation Error."""
    r = client.post("/api/agents", json={"model": "llama3"})
    assert r.status_code == 422


def test_history_empty(client):
    """GET /api/history/{id} gibt leere Liste für neuen Agent."""
    agent = _create_agent(client, "HistTest")
    r = client.get(f"/api/history/{agent['id']}")
    assert r.status_code == 200
    data = r.json()
    history = data if isinstance(data, list) else data.get("history", [])
    assert isinstance(history, list)
    assert len(history) == 0
