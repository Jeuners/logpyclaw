"""tests/test_api_factions.py — Tests für Faction REST-API."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core.faction_protocol import FactionRegistry


@pytest.fixture(autouse=True)
def fresh_reg():
    FactionRegistry.reset()
    FactionRegistry.load_defaults()
    yield
    FactionRegistry.reset()


@pytest.fixture
def client():
    from fastapi import FastAPI

    from backend.api.factions import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestFactionAPI:
    def test_list_factions_returns_six(self, client):
        r = client.get("/api/factions")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 6
        ids = {f["id"] for f in data}
        assert "operators" in ids
        assert "makers" in ids

    def test_get_faction_detail(self, client):
        r = client.get("/api/factions/operators")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == "operators"
        assert "relations" in data

    def test_get_unknown_faction_404(self, client):
        r = client.get("/api/factions/unknown_faction_xyz")
        assert r.status_code == 404

    def test_set_stance(self, client):
        r = client.post("/api/factions/stance", json={
            "source_faction": "makers",
            "target_faction": "scribes",
            "stance": "allied",
        })
        assert r.status_code == 200
        assert r.json()["stance"] == "allied"

    def test_set_invalid_stance(self, client):
        r = client.post("/api/factions/stance", json={
            "source_faction": "makers",
            "target_faction": "scribes",
            "stance": "nonsense",
        })
        assert r.status_code == 400

    def test_record_outcome(self, client):
        FactionRegistry.get().assign("agent:a", "makers")
        FactionRegistry.get().assign("agent:b", "scribes")
        r = client.post("/api/factions/outcome", json={
            "source_agent": "agent:a",
            "target_agent": "agent:b",
            "success": True,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
