"""Tests für core/temporal_policy.py — §4.3 Re-Synchronisation Policy."""
from datetime import datetime, timedelta

import pytest

from core.temporal_policy import (
    DEFAULT_DRIFT_THRESHOLD_SECONDS,
    SIDE_EFFECT_SKILLS,
    TemporalPolicy,
    agent_has_side_effect_skill,
    drift_seconds,
    evaluate_drift,
    policy_for_task,
)


# ── drift_seconds ─────────────────────────────────────────────────────────────

def test_drift_zero_for_equal_timestamps():
    iso = datetime(2026, 5, 6, 12, 0, 0).isoformat()
    assert drift_seconds(iso, iso) == 0.0


def test_drift_returns_absolute_value():
    a = datetime(2026, 5, 6, 12, 0, 0).isoformat()
    b = datetime(2026, 5, 6, 12, 0, 30).isoformat()
    assert drift_seconds(a, b) == 30.0
    assert drift_seconds(b, a) == 30.0  # absolut, vorzeichenlos


def test_drift_none_for_missing_timestamps():
    assert drift_seconds(None, "x") is None
    assert drift_seconds("y", None) is None
    assert drift_seconds("garbage", "garbage") is None


# ── agent_has_side_effect_skill ──────────────────────────────────────────────

def test_agent_with_gmail_is_side_effect():
    assert agent_has_side_effect_skill({"skills": ["gmail", "wiki_read"]})


def test_agent_with_only_read_skills_is_not():
    assert not agent_has_side_effect_skill({"skills": ["wiki_read", "url_fetch"]})


def test_empty_agent_is_not_side_effect():
    assert not agent_has_side_effect_skill({})
    assert not agent_has_side_effect_skill(None)  # type: ignore[arg-type]


# ── policy_for_task ───────────────────────────────────────────────────────────

def test_policy_default_is_log_only():
    assert policy_for_task({}, {}) == TemporalPolicy.LOG_ONLY


def test_policy_explicit_overrides():
    out = policy_for_task({"temporal_policy": "recalibrate"}, {"skills": ["gmail"]})
    assert out == TemporalPolicy.RECALIBRATE


def test_policy_invalid_explicit_falls_back_to_skill_check():
    out = policy_for_task({"temporal_policy": "garbage"}, {"skills": ["gmail"]})
    assert out == TemporalPolicy.REJECT_ON_DRIFT


def test_policy_side_effect_skill_triggers_reject():
    out = policy_for_task({}, {"skills": list(SIDE_EFFECT_SKILLS)[:1]})
    assert out == TemporalPolicy.REJECT_ON_DRIFT


# ── evaluate_drift ────────────────────────────────────────────────────────────

def test_evaluate_drift_no_reference_returns_log_only_no_reject():
    out = evaluate_drift({}, {})
    assert out["should_reject"] is False
    assert out["drift_seconds"] is None


def test_evaluate_drift_within_threshold_does_not_reject():
    fresh = (datetime.now() - timedelta(seconds=10)).isoformat()
    out = evaluate_drift(
        {"reference_now": fresh},
        {"skills": ["gmail"]},
        threshold_seconds=600.0,
    )
    assert out["policy"] == TemporalPolicy.REJECT_ON_DRIFT
    assert out["should_reject"] is False
    assert out["drift_seconds"] is not None
    assert out["drift_seconds"] < 600


def test_evaluate_drift_above_threshold_rejects_for_side_effect():
    stale = (datetime.now() - timedelta(seconds=3000)).isoformat()
    out = evaluate_drift(
        {"reference_now": stale},
        {"skills": ["gmail"]},
        threshold_seconds=600.0,
    )
    assert out["should_reject"] is True
    assert out["drift_seconds"] > 600
    assert "exceeds" in out["reason"]


def test_evaluate_drift_above_threshold_does_not_reject_for_read_only():
    """Read-only-Agent → LOG_ONLY → should_reject muss False bleiben, auch
    bei hohem Drift."""
    stale = (datetime.now() - timedelta(seconds=3000)).isoformat()
    out = evaluate_drift(
        {"reference_now": stale},
        {"skills": ["wiki_read"]},
        threshold_seconds=600.0,
    )
    assert out["policy"] == TemporalPolicy.LOG_ONLY
    assert out["should_reject"] is False


def test_default_threshold_is_600():
    assert DEFAULT_DRIFT_THRESHOLD_SECONDS == 600.0
