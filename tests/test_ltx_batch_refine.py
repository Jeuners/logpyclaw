"""Tests für die Just-in-Time Prompt-Refinement-Helpers (api/ltx_batch.py)."""
from unittest.mock import patch

import pytest

from api.ltx_batch import (
    _split_transcript_per_chunk,
    _try_extract_json,
    _refine_prompt_for_segment,
)


# ── _split_transcript_per_chunk ───────────────────────────────────────────────

def test_split_empty_transcript():
    assert _split_transcript_per_chunk("", 3) == ["", "", ""]


def test_split_zero_segments():
    assert _split_transcript_per_chunk("hello world", 0) == []


def test_split_proportional():
    txt = "one two three four five six"
    out = _split_transcript_per_chunk(txt, 3)
    assert len(out) == 3
    assert out[0] == "one two"
    assert out[1] == "three four"
    assert out[2] == "five six"


def test_split_remainder_goes_to_last_chunk():
    txt = "a b c d e"
    out = _split_transcript_per_chunk(txt, 2)
    assert len(out) == 2
    # Erstes Chunk: 5 // 2 = 2 Worte; Rest geht ans letzte
    assert out[0] == "a b"
    assert out[1] == "c d e"


def test_split_more_segments_than_words():
    out = _split_transcript_per_chunk("hello", 5)
    assert len(out) == 5
    # Erstes Chunk = "hello", restliche bekommen leeren Rest
    assert "hello" in out[0]


# ── _try_extract_json ─────────────────────────────────────────────────────────

def test_extract_clean_json():
    out = _try_extract_json('{"prompt": "x", "image_mode": "prev"}')
    assert out == {"prompt": "x", "image_mode": "prev"}


def test_extract_fenced_json():
    s = '```json\n{"prompt": "x"}\n```'
    out = _try_extract_json(s)
    assert out == {"prompt": "x"}


def test_extract_with_pre_post_text():
    s = 'Sure, here is the JSON:\n{"prompt": "y"}\n\nLet me know!'
    out = _try_extract_json(s)
    assert out == {"prompt": "y"}


def test_extract_garbage_returns_none():
    assert _try_extract_json("not json at all") is None
    assert _try_extract_json("") is None


# ── _refine_prompt_for_segment ────────────────────────────────────────────────

def test_refine_no_image_returns_empty():
    """Ohne abrufbares Eingangsbild → kein Refinement (Fallback auf Original)."""
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=None):
        out = _refine_prompt_for_segment(
            image_fn=None, original_prompt="orig", chunk_text="hello",
            concept="c", segment_index=1, n_segments=3,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn="start.png",
        )
    assert out["refined_prompt"] == ""
    assert out["suggested_image_mode"] is None


def test_refine_returns_parsed_json():
    fake_image = b"fake-image-bytes"
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=fake_image), \
         patch("api.ltx_batch._describe_image", return_value="A man in a blue shirt"), \
         patch("api.ltx_batch._ollama_chat", return_value=(
             '{"prompt": "A man in a blue shirt smiles and gestures, cinematic close-up, '
             'warm light, 24mm lens, gentle camera push-in, expressing welcome", '
             '"image_mode": "prev", '
             '"reason": "frame is clean — chain motion"}'
         )):
        out = _refine_prompt_for_segment(
            image_fn="seg1_last.png", original_prompt="old prompt",
            chunk_text="hi there", concept="welcome video",
            segment_index=1, n_segments=3,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn="start.png",
        )
    assert "smiles and gestures" in out["refined_prompt"]
    # mode war schon prev → kein Switch-Vorschlag
    assert out["suggested_image_mode"] is None
    assert out["image_desc"] == "A man in a blue shirt"
    assert "clean" in out["reason"]


def test_refine_suggests_switch_when_frame_broken():
    fake_image = b"x"
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=fake_image), \
         patch("api.ltx_batch._describe_image", return_value="A heavily blurred frame"), \
         patch("api.ltx_batch._ollama_chat", return_value=(
             '{"prompt": "A man stands tall, sharp portrait, soft sunlight, gentle smile, '
             'cinematic medium shot capturing the welcoming gesture", '
             '"image_mode": "start", '
             '"reason": "last frame is unusable, recovering with start image"}'
         )):
        out = _refine_prompt_for_segment(
            image_fn="seg1_last.png", original_prompt="old",
            chunk_text="hi", concept="c",
            segment_index=2, n_segments=4,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn="start.png",
        )
    assert out["suggested_image_mode"] == "start"
    assert "start image" in out["reason"]


def test_refine_does_not_suggest_start_without_start_image():
    """Wenn start_image_fn fehlt, wird der „start"-Switch verworfen."""
    fake_image = b"x"
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=fake_image), \
         patch("api.ltx_batch._describe_image", return_value="something"), \
         patch("api.ltx_batch._ollama_chat", return_value=(
             '{"prompt": "long enough rewritten prompt for the segment with new motion", '
             '"image_mode": "start", "reason": "blurred"}'
         )):
        out = _refine_prompt_for_segment(
            image_fn="seg1_last.png", original_prompt="o",
            chunk_text="t", concept="c", segment_index=1, n_segments=2,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn=None,
        )
    # Switch-Vorschlag verworfen, weil kein start_image
    assert out["suggested_image_mode"] is None


def test_refine_rejects_too_short_refined_prompt():
    """Wenn das LLM einen zu kurzen Prompt liefert, wird er verworfen."""
    fake_image = b"x"
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=fake_image), \
         patch("api.ltx_batch._describe_image", return_value="ok"), \
         patch("api.ltx_batch._ollama_chat", return_value='{"prompt": "too short"}'):
        out = _refine_prompt_for_segment(
            image_fn="x", original_prompt="o", chunk_text="t",
            concept="c", segment_index=1, n_segments=2,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn="s",
        )
    # 8 chars < 15 char minimum → verworfen
    assert out["refined_prompt"] == ""


def test_refine_handles_non_json_output():
    fake_image = b"x"
    with patch("api.ltx_batch._fetch_comfyui_input_bytes", return_value=fake_image), \
         patch("api.ltx_batch._describe_image", return_value="ok"), \
         patch("api.ltx_batch._ollama_chat", return_value="Sorry, I cannot generate JSON"):
        out = _refine_prompt_for_segment(
            image_fn="x", original_prompt="o", chunk_text="t",
            concept="c", segment_index=1, n_segments=2,
            current_image_mode="prev", has_prev_last_frame=True,
            start_image_fn="s",
        )
    assert out["refined_prompt"] == ""
    assert "non-JSON" in out["reason"]
