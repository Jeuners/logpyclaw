"""tests/test_api_teams.py — Tests für Team REST-API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from backend.api import teams as teams_module
    teams_module._teams.clear()   # reset state between tests

    from fastapi import FastAPI

    from backend.api.teams import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestTeamAPI:
    def test_list_empty(self, client):
        r = client.get("/api/teams")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_team(self, client):
        r = client.post("/api/teams", json={"name": "Alpha"})
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "Alpha"
        assert "team_id" in data

    def test_get_team(self, client):
        create = client.post("/api/teams", json={"name": "Beta"})
        team_id = create.json()["team_id"]
        r = client.get(f"/api/teams/{team_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "Beta"

    def test_get_unknown_team_404(self, client):
        r = client.get("/api/teams/nonexistent")
        assert r.status_code == 404

    def test_add_member(self, client):
        create = client.post("/api/teams", json={"name": "Gamma"})
        team_id = create.json()["team_id"]
        r = client.post(f"/api/teams/{team_id}/members", json={"agent_id": "agent:alice"})
        assert r.status_code == 200
        assert any(m["agent_id"] == "agent:alice" for m in r.json()["members"])

    def test_remove_member(self, client):
        create = client.post("/api/teams", json={"name": "Delta"})
        team_id = create.json()["team_id"]
        client.post(f"/api/teams/{team_id}/members", json={"agent_id": "agent:alice"})
        r = client.delete(f"/api/teams/{team_id}/members/agent:alice")
        assert r.status_code == 200
        assert not any(m["agent_id"] == "agent:alice" for m in r.json()["members"])

    def test_recommend_returns_structure(self, client):
        create = client.post("/api/teams", json={"name": "Epsilon"})
        team_id = create.json()["team_id"]
        client.post(f"/api/teams/{team_id}/members", json={"agent_id": "agent:alice"})
        r = client.get(f"/api/teams/{team_id}/recommend")
        assert r.status_code == 200
        data = r.json()
        assert "recommended" in data
        assert "details" in data
        assert "gamma_matrix" in data
