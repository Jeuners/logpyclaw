"""Smoke-Tests für /api/temporal/*."""


def test_orchestrator_endpoint(client):
    r = client.get("/api/temporal/orchestrator")
    assert r.status_code == 200
    body = r.json()
    for key in ("agent_id", "frame_id", "dilation_factor", "tau", "wall_now", "reference_now"):
        assert key in body
    assert body["dilation_factor"] == 1.0  # WallClockProvider default


def test_frames_endpoint_shape(client):
    r = client.get("/api/temporal/frames?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert "frames" in body
    assert isinstance(body["frames"], list)
    assert "now" in body


def test_frames_endpoint_limit_validation(client):
    r = client.get("/api/temporal/frames?limit=99999")
    # max=500 → 422
    assert r.status_code == 422


def test_frames_endpoint_min_validation(client):
    r = client.get("/api/temporal/frames?limit=0")
    assert r.status_code == 422
