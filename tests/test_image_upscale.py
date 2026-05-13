"""
tests/test_image_upscale.py — ImageUpscaleSkill + parse_upscale_factor.

Verifiziert:
  - Faktor-Parsing aus Message-Text (2x / 3X / faktor 4 / vierfach)
  - Skill-Trigger feuert NUR bei explizitem Upscale-Intent
  - execute() ohne Bild → klarer Fehler statt stiller Halt
  - Workflow-Struktur stimmt (LoadImage → ImageScaleBy → SaveImage)
"""
from skills.comfyui import (
    ImageUpscaleSkill,
    parse_upscale_factor,
    build_upscale_workflow,
)


# ── parse_upscale_factor — Pattern-Tests ─────────────────────────────────


def test_parse_factor_2x():
    assert parse_upscale_factor("Mach das Bild 2x größer") == 2


def test_parse_factor_3x():
    assert parse_upscale_factor("Hochskalieren auf 3X bitte") == 3


def test_parse_factor_4x_no_space():
    assert parse_upscale_factor("upscale 4x") == 4


def test_parse_factor_with_keyword():
    assert parse_upscale_factor("Bild upscalen mit Faktor 3") == 3


def test_parse_factor_kebab_dash():
    assert parse_upscale_factor("2-fach upscale") == 2


def test_parse_factor_german_word():
    assert parse_upscale_factor("Bitte vierfach skalieren") == 4


def test_parse_factor_default_when_unspecified():
    assert parse_upscale_factor("Bild upscalen") == 2


def test_parse_factor_caps_invalid_numbers():
    assert parse_upscale_factor("upscale 5x bitte") == 2
    assert parse_upscale_factor("upscale 100x bitte") == 2


def test_parse_factor_empty_message():
    assert parse_upscale_factor("") == 2
    assert parse_upscale_factor(None) == 2


# ── Skill-Trigger ─────────────────────────────────────────────────────────


def test_skill_matches_explicit_upscale_de():
    skill = ImageUpscaleSkill()
    assert skill.matches("Bitte hochskalieren") is True


def test_skill_matches_explicit_upscale_en():
    skill = ImageUpscaleSkill()
    assert skill.matches("Please upscale this") is True


def test_skill_matches_factor_with_image_context():
    skill = ImageUpscaleSkill()
    assert skill.matches("Mach das Bild 3x") is True


def test_skill_does_not_match_unrelated_message():
    skill = ImageUpscaleSkill()
    assert skill.matches("Erkläre mir Quantenmechanik") is False


# ── execute() Verhalten ──────────────────────────────────────────────────


def test_execute_without_image_returns_error():
    """Ohne image_b64 im Kontext: klarer Fehler, kein stiller passthrough."""
    skill = ImageUpscaleSkill()
    result = skill.execute({"name": "Tester"}, "upscale 2x")
    assert result.error is not None
    assert "Bild" in result.error
    assert result.skill_used == "image_upscale"


# ── Workflow-Struktur ────────────────────────────────────────────────────


def test_workflow_has_expected_nodes():
    """Template stammt aus ComfyUI-Export — Node-IDs 2 (LoadImage),
    3 (ImageScaleBy), 5 (SaveImage). Re-Import in ComfyUI-UI muss möglich
    bleiben, daher Format nicht eigenmächtig ändern."""
    wf = build_upscale_workflow("foo.png", 3)
    assert wf["2"]["class_type"] == "LoadImage"
    assert wf["3"]["class_type"] == "ImageScaleBy"
    assert wf["5"]["class_type"] == "SaveImage"


def test_workflow_patches_image_filename():
    wf = build_upscale_workflow("hero_xyz.png", 2)
    assert wf["2"]["inputs"]["image"] == "hero_xyz.png"


def test_workflow_passes_factor_as_float():
    wf = build_upscale_workflow("foo.png", 4)
    scale = wf["3"]["inputs"]["scale_by"]
    assert isinstance(scale, float)
    assert scale == 4.0


def test_workflow_uses_lanczos():
    wf = build_upscale_workflow("foo.png", 2)
    assert wf["3"]["inputs"]["upscale_method"] == "lanczos"


def test_workflow_template_isolation():
    """Mehrfach-Aufrufe dürfen das Template-Dict nicht kreuzweise ändern."""
    wf1 = build_upscale_workflow("a.png", 2)
    wf2 = build_upscale_workflow("b.png", 4)
    assert wf1["2"]["inputs"]["image"] == "a.png"
    assert wf2["2"]["inputs"]["image"] == "b.png"
    assert wf1["3"]["inputs"]["scale_by"] == 2.0
    assert wf2["3"]["inputs"]["scale_by"] == 4.0
