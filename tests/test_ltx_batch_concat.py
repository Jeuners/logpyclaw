"""Smoke + Validierung für /api/ltx-batch/concat und /master."""
import os


def test_concat_unknown_job_returns_404(client):
    r = client.post("/api/ltx-batch/concat/does-not-exist")
    assert r.status_code == 404
    assert "unbekannt" in r.json().get("error", "").lower()


def test_master_unknown_job_returns_404(client):
    r = client.get("/api/ltx-batch/master/some-job/master_123.mp4")
    # Job-Verzeichnis existiert nicht → 404
    assert r.status_code == 404


def test_master_rejects_path_traversal(client):
    r = client.get("/api/ltx-batch/master/x/..%2Fpasswd")
    # FastAPI dekodiert URL-encoded — ../passwd → "ungültiger filename"
    assert r.status_code in (400, 404)


def test_master_rejects_non_master_prefix(client):
    r = client.get("/api/ltx-batch/master/x/random.mp4")
    assert r.status_code == 400
    assert "ungültig" in r.json().get("error", "").lower()


def test_concat_no_finished_segments(client, tmp_path, monkeypatch):
    """Job existiert, aber kein video_url → 400."""
    from api import ltx_batch
    fake_job_id = "no-segs-test"
    ltx_batch._prep_jobs[fake_job_id] = {
        "segments": [
            {"idx": 0, "video_url": None},
            {"idx": 1, "video_url": ""},
        ],
    }
    try:
        r = client.post(f"/api/ltx-batch/concat/{fake_job_id}")
        assert r.status_code == 400
        assert "fertigen" in r.json().get("error", "").lower()
    finally:
        ltx_batch._prep_jobs.pop(fake_job_id, None)
