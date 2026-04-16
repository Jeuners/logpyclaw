"""
tests/test_skills_registry.py — SkillRegistry Matching Tests.
"""
import pytest
from skills.base import BaseSkill, SkillResult
from skills.registry import SkillRegistry


class DummySkill(BaseSkill):
    def __init__(self, id_, triggers, requires=None):
        self.id = id_
        self.name = id_.title()
        self.triggers = triggers
        self.requires = requires or []

    def execute(self, agent, message, **context):
        return SkillResult(text="ok", skill_used=self.id)


def test_register_and_get():
    reg = SkillRegistry()
    s = DummySkill("foo", [r"foo"])
    reg.register(s)
    assert reg.get("foo") is s
    assert reg.get("bar") is None


def test_register_empty_id_raises():
    reg = SkillRegistry()
    with pytest.raises(ValueError):
        reg.register(DummySkill("", [r"."]))


def test_all_returns_skills():
    reg = SkillRegistry()
    reg.register(DummySkill("a", [r"a"]))
    reg.register(DummySkill("b", [r"b"]))
    assert {s.id for s in reg.all()} == {"a", "b"}


def test_find_matching_best_by_length():
    """Längerer Regex-Match gewinnt gegen kürzeren."""
    reg = SkillRegistry()
    short = DummySkill("url", [r"https://"])
    long_ = DummySkill("chrome", [r"https://linkedin\.com/in/\S+"])
    reg.register(short)
    reg.register(long_)

    agent = {"skills": ["url", "chrome"]}
    best = reg.find_matching(agent, "schau mal https://linkedin.com/in/demo")
    assert best is not None
    assert best.id == "chrome"


def test_find_matching_no_agent_skills():
    """Agent ohne Skills-Config → None."""
    reg = SkillRegistry()
    reg.register(DummySkill("url", [r"https://"]))
    assert reg.find_matching({}, "https://example.com") is None


def test_available_for_filters_by_provider():
    reg = SkillRegistry()
    needs_mistral = DummySkill("tts", [r"sprich"], requires=["mistral"])
    free = DummySkill("fetch", [r"fetch"])
    reg.register(needs_mistral)
    reg.register(free)

    agent = {"skills": ["tts", "fetch"]}
    # Kein mistral konfiguriert → nur free verfügbar
    avail = reg.available_for(agent, providers={})
    ids = {s.id for s in avail}
    assert "fetch" in ids
    assert "tts" not in ids


def test_list_for_api_shape():
    reg = SkillRegistry()
    reg.register(DummySkill("x", [r"x"]))
    items = reg.list_for_api()
    assert len(items) == 1
    keys = set(items[0].keys())
    assert {"id", "name", "icon", "description", "requires"}.issubset(keys)
