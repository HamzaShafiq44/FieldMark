"""Tests for YAML-backed prompt configuration."""

from pathlib import Path

import pytest

from app.ai import prompt_config
from app.ai.gemini_report import FARMER_AUDIENCE_GUIDANCE, REPORT_SYSTEM_PROMPT
from app.ai.prompts import DEFAULT_SUPPORTED_TYPES, get_localization_prompt, get_system_prompt
from app.ai.voice_analysis import ANALYSIS_SYSTEM_PROMPT
from app.core.config import settings


@pytest.fixture(autouse=True)
def clear_prompt_cache() -> None:
    """Avoid leaking cached templates between tests."""
    prompt_config._load.cache_clear()


def test_bundled_templates_are_valid() -> None:
    """All shipped prompt files parse and contain their required placeholders."""
    prompt_config.validate_prompt_templates()


def test_inspection_prompt_uses_active_categories() -> None:
    """Category slugs are substituted into the loaded system prompt."""
    prompt = get_system_prompt(["guava", "mango"])
    assert "guava, and mango" in prompt
    assert '"guava"|"mango"' in prompt
    assert list(DEFAULT_SUPPORTED_TYPES) == ["tomato", "apple", "banana", "orange"]


def test_localization_prompt_substitutes_defects_and_evidence() -> None:
    """The second-stage prompt receives the reported defect and observation."""
    prompt = get_localization_prompt("bruise", "A dark sunken patch is visible.")
    assert "bruise" in prompt
    assert "A dark sunken patch is visible." in prompt
    assert "${" not in prompt


def test_missing_template_fails_clearly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A missing YAML file is a configuration error, not a silent fallback."""
    monkeypatch.setattr(settings, "prompt_templates_dir", tmp_path)
    with pytest.raises(RuntimeError, match="missing"):
        prompt_config.validate_prompt_templates()


def test_invalid_template_fails_clearly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Unknown keys or versions are rejected by the schema."""
    (tmp_path / "inspection.yaml").write_text("version: 2\nsystem: hi\n", encoding="utf-8")
    monkeypatch.setattr(settings, "prompt_templates_dir", tmp_path)
    with pytest.raises(RuntimeError, match="invalid"):
        prompt_config.validate_prompt_templates()


def test_unknown_key_in_template_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Typos in prompt files do not pass silently."""
    (tmp_path / "report.yaml").write_text(
        "version: 1\nsystem: hi\nuser: there\nfarmer_audience_guidance: farmer\ntypo_field: oops\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "prompt_templates_dir", tmp_path)
    with pytest.raises(RuntimeError, match="invalid"):
        prompt_config.load_prompt("report")


def test_unresolved_placeholder_is_rejected() -> None:
    """Forgetting a value never sends a literal placeholder to the model."""
    with pytest.raises(RuntimeError, match="Unresolved prompt placeholder"):
        prompt_config.render("Hello ${name}", other="value")


def test_report_and_voice_prompts_come_from_templates() -> None:
    """Module-level constants are sourced from the YAML files."""
    report = prompt_config.load_prompt("report")
    voice = prompt_config.load_prompt("voice_analysis")
    assert REPORT_SYSTEM_PROMPT == report.system
    assert FARMER_AUDIENCE_GUIDANCE == report.farmer_audience_guidance
    assert ANALYSIS_SYSTEM_PROMPT == voice.system
