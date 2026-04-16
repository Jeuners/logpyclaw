"""
tests/test_api_health.py — Health + Theme API Tests.
"""


def test_ping(client):
    """GET /api/health gibt App-Status zurück."""
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("app") == "ok"


def test_themes_list(client):
    """GET /api/themes listet verfügbare Themes."""
    r = client.get("/api/themes")
    assert r.status_code == 200
    themes = r.json()
    assert isinstance(themes, list)
    assert len(themes) > 0
    # Default-Theme muss vorhanden sein
    ids = [t["id"] for t in themes]
    assert "default" in ids


def test_theme_activate(client):
    """PUT /api/themes/default setzt aktives Theme."""
    r = client.put("/api/themes/default")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["active"] == "default"


def test_theme_not_found(client):
    """PUT /api/themes/nonexistent → 404."""
    r = client.put("/api/themes/nonexistent-theme")
    assert r.status_code == 404
