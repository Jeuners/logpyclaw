"""Tests für estimate_dilation Heuristik (§3.3)."""
import pytest

from core.time_provider import estimate_dilation


def test_unknown_agent_returns_one():
    assert estimate_dilation({}) == 1.0
    assert estimate_dilation({"provider": "ollama"}) == 1.0  # kein model-hint


def test_ollama_default_is_one():
    assert estimate_dilation({"provider": "ollama", "model": "gemma4:e4b"}) < 1.0  # e4b multiplier


def test_openrouter_frontier_is_higher():
    g_local = estimate_dilation({"provider": "ollama", "model": "gemma4:e4b"})
    g_frontier = estimate_dilation({"provider": "openrouter", "model": "anthropic/claude-opus"})
    assert g_frontier > g_local
    assert g_frontier > 4.0  # opus*1.8 von base 4.0


def test_haiku_smaller_than_opus():
    g_haiku = estimate_dilation({"provider": "anthropic", "model": "claude-haiku-4-5"})
    g_opus = estimate_dilation({"provider": "anthropic", "model": "claude-opus-4-7"})
    assert g_haiku < g_opus


def test_unknown_provider_falls_back_to_one():
    g = estimate_dilation({"provider": "myprovider", "model": ""})
    assert g == 1.0


def test_none_safe():
    assert estimate_dilation(None) == 1.0  # type: ignore[arg-type]
